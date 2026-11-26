from __future__ import annotations

from datetime import timedelta

import pytest

from modules.ownership_pool.domain import ConflictError, Job, wire_time
from workers.tasks.ownership_pool.tasks import LocalEventBus, OutboxPublisher, TaskRunner, run_policy_sweep

from conftest import NOW, SALES_A, TENANT, current_version, seed_customer_with_contract, seed_policy


CUSTOMER = "80000000-0000-0000-0000-000000000001"
CONTRACT = "90000000-0000-0000-0000-000000000001"


def test_outbox_publisher_recovers_after_a_transient_failure(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))

    bus = LocalEventBus()
    publisher = OutboxPublisher(m11["repository"], bus, clock=m11["clock"])
    bus.failure_mode = "timeout"
    first = publisher.run_once("m11-drill")
    assert first.processed == 0
    assert first.retried >= 1

    pending_before = m11["repository"].snapshot().outbox
    assert any(event.status == "retry" and event.attempt_count == 1 for event in pending_before.values())

    bus.failure_mode = None
    m11["clock"].advance(seconds=301)
    second = publisher.run_once("m11-drill")
    assert second.processed >= 1
    published = m11["repository"].snapshot().outbox
    assert all(event.status in {"published", "retry", "pending"} for event in published.values())


def test_outbox_publisher_deadletters_after_max_attempts(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))

    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m11["repository"], bus, max_attempts=2, clock=m11["clock"])
    publisher.run_once("m11-drill")
    m11["clock"].advance(seconds=301)
    publisher.run_once("m11-drill")

    snapshot = m11["repository"].snapshot()
    assert any(event.status == "dead_letter" for event in snapshot.outbox.values())


def test_outbox_publisher_redrive_moves_dead_letter_back_to_retry(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))

    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m11["repository"], bus, max_attempts=1, clock=m11["clock"])
    publisher.run_once("m11-drill")
    snapshot = m11["repository"].snapshot()
    dead = next(event for event in snapshot.outbox.values() if event.status == "dead_letter")

    with pytest.raises(ValueError):
        publisher.redrive(TENANT, dead.event_id, "   ")

    redriven = publisher.redrive(TENANT, dead.event_id, "confirmed assignment state unaffected; safe to redrive")
    assert redriven.status == "retry"
    assert redriven.redrive_reason == "confirmed assignment state unaffected; safe to redrive"

    with pytest.raises(ConflictError) as excinfo:
        publisher.redrive(TENANT, dead.event_id, "already not dead-lettered")
    assert excinfo.value.code == "outbox_not_dead_letter"

    bus.failure_mode = None
    second = publisher.run_once("m11-drill")
    assert second.processed == 1
    assert m11["repository"].snapshot().outbox[(TENANT, dead.event_id)].status == "published"


def _seed_job(m11, job_id: str) -> Job:
    job = Job(id=job_id, tenant_id=TENANT, kind="probe", payload={}, max_attempts=2)

    def save(state):
        state.jobs[(TENANT, job_id)] = job
        return job

    return m11["repository"].atomic(save)


def test_task_runner_lease_complete_and_fail_use_the_injectable_clock(m11):
    runner = TaskRunner(m11["repository"], clock=m11["clock"])
    job = _seed_job(m11, "90000000-0000-0000-0000-000000000001")

    leased = runner.lease("m11-worker")
    assert [item.id for item in leased] == [job.id]
    assert leased[0].lease_until == m11["clock"]() + timedelta(seconds=30)

    completed = runner.complete(TENANT, job.id, "m11-worker")
    assert completed.status == "succeeded"
    assert completed.lease_owner is None

    with pytest.raises(ConflictError) as excinfo:
        runner.complete(TENANT, job.id, "m11-worker")
    assert excinfo.value.code == "worker_lease_lost"


def test_task_runner_fail_deadletters_after_max_attempts_then_redrive(m11):
    runner = TaskRunner(m11["repository"], clock=m11["clock"])
    job = _seed_job(m11, "90000000-0000-0000-0000-000000000002")

    runner.lease("m11-worker")
    first_fail = runner.fail(TENANT, job.id, "m11-worker", "transient_timeout")
    assert first_fail.status == "retry"
    assert first_fail.attempt_count == 1

    m11["clock"].advance(seconds=301)
    runner.lease("m11-worker")
    second_fail = runner.fail(TENANT, job.id, "m11-worker", "transient_timeout")
    assert second_fail.status == "dead_letter"
    assert second_fail.attempt_count == 2

    with pytest.raises(ValueError):
        runner.redrive(TENANT, job.id, "")

    redriven = runner.redrive(TENANT, job.id, "root cause fixed upstream")
    assert redriven.status == "retry"
    assert redriven.redrive_reason == "root cause fixed upstream"


def test_policy_sweep_is_idempotent_per_key(m11, supervisor, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW - timedelta(days=40), ends_at=NOW - timedelta(days=10))
    pool_version = current_version(m11, CUSTOMER)

    service.assign_maintenance_owner(
        # The company-pool baseline always starts at consumer processing time
        # (NOW under the fixed test clock), not the historical contract start.
        supervisor, CUSTOMER,
        {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"},
        expected_version=pool_version, idempotency_key="assign-sweep",
    )

    first = run_policy_sweep(
        service, m11["repository"], TENANT, wire_code,
        as_of=wire_time(NOW), idempotency_key="sweep-2026-11-23", service_actor_id=ownership_service.actor_id,
    )
    second = run_policy_sweep(
        service, m11["repository"], TENANT, wire_code,
        as_of=wire_time(NOW), idempotency_key="sweep-2026-11-23", service_actor_id=ownership_service.actor_id,
    )
    assert first.eligible_count == second.eligible_count == 1
    snapshot = m11["repository"].snapshot()
    pool_entries = [entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER and entry.entry_reason == "paid_service_lapse"]
    assert len(pool_entries) == 1  # replay never double-applies the lapse
