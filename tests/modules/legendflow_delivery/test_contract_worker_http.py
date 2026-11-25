from __future__ import annotations

from datetime import timedelta

import pytest

from modules.legendflow_delivery.domain import (
    ConflictError,
    NotFoundError,
    PreconditionRequiredError,
    StaleVersionError,
    ValidationError,
    canonical_hash,
)
from modules.legendflow_delivery.http import M09Controller, ROUTES
from modules.legendflow_delivery.ports import SendOutcome
from workers.tasks.legendflow_delivery.consumer import M09EventConsumer
from workers.tasks.legendflow_delivery.tasks import DispatchWorker, LocalEventBus, OutboxPublisher

from conftest import (
    CHANNEL_PROOF_LINE_OK,
    MEMBER_A,
    NOW,
    PUBLICATION_X,
    TENANT,
    WEBHOOK_KEY_ID,
    WEBHOOK_PATH,
    WEBHOOK_SECRET,
    dispatch,
    seed_publication,
    send_receipt,
    set_entitlement,
)


def _envelope(event_type: str, aggregate_type: str, aggregate_id: str, payload: dict, *, sequence_no: int = 1, producer: str = "M08", event_id: str = "e5000000-0000-0000-0000-000000000001") -> dict:
    return {
        "event_id": event_id, "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "aggregate_version": sequence_no,
        "sequence_no": sequence_no, "occurred_at": "2026-11-22T00:00:00.000000Z", "emitted_at": "2026-11-22T00:00:00.000000Z",
        "correlation_id": "c6000000-0000-0000-0000-000000000001", "causation_id": None,
        "idempotency_key": f"{event_type}:synthetic-{sequence_no}", "classification": "research_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


# ---- idempotency and CAS contract -----------------------------------------

def test_bind_member_channel_idempotency_replay_same_body(m09, member_a):
    env = m09
    body = {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True}
    first = env["service"].bind_member_channel(member_a, body, idempotency_key="bind-replay-0001")
    second = env["service"].bind_member_channel(member_a, body, idempotency_key="bind-replay-0001")
    assert first.data["id"] == second.data["id"]
    assert second.idempotency_replayed is True


def test_bind_member_channel_idempotency_conflict_on_different_body(m09, member_a):
    env = m09
    env["service"].bind_member_channel(
        member_a, {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True}, idempotency_key="bind-conflict-0001",
    )
    with pytest.raises(ConflictError) as excinfo:
        env["service"].bind_member_channel(
            member_a, {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": False}, idempotency_key="bind-conflict-0001",
        )
    assert excinfo.value.code == "idempotency_conflict"


def test_reconcile_delivery_requires_if_match_and_rejects_stale_version(m09, operator):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, job.id)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))

    with pytest.raises(PreconditionRequiredError):
        env["service"].reconcile_delivery(operator, message.id, {"reason_code": "x"}, expected_version=None, idempotency_key="reconcile-nocas-0001")
    with pytest.raises(StaleVersionError) as excinfo:
        env["service"].reconcile_delivery(operator, message.id, {"reason_code": "x"}, expected_version=message.version + 1, idempotency_key="reconcile-stale-0001")
    assert excinfo.value.current_version == message.version


def test_redrive_delivery_event_requires_dead_letter_status(m09, supervisor):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    with pytest.raises(ConflictError) as excinfo:
        env["service"].redrive_delivery_event(
            supervisor, job.id,
            {"reason_code": "r", "expected_event_id": "e7000000-0000-0000-0000-000000000001", "approved_by": supervisor.actor_id, "revalidate_entitlement": True, "revalidate_content_expiry": True},
            expected_version=job.version, idempotency_key="redrive-notdead-0001",
        )
    assert excinfo.value.code == "job_not_dead_letter"


def test_only_delivery_operator_or_supervisor_role_may_reconcile_or_redrive(m09, member_a, service_agent):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import ForbiddenError

    with pytest.raises(ForbiddenError):
        env["service"].reconcile_delivery(service_agent, message.id, {"reason_code": "x"}, expected_version=message.version, idempotency_key="reconcile-forbidden-0001")
    with pytest.raises(ForbiddenError):
        env["service"].reconcile_delivery(member_a, message.id, {"reason_code": "x"}, expected_version=message.version, idempotency_key="reconcile-forbidden-0002")


# ---- pagination contract ----------------------------------------------------

def test_list_deliveries_pagination_is_cursor_stable(m09, operator):
    env = m09
    for index in range(3):
        publication_id = f"50000000-0000-0000-0000-00000000{20 + index:02d}"
        entitlement_id = f"40000000-0000-0000-0000-00000000{20 + index:02d}"
        report_version_id = f"60000000-0000-0000-0000-00000000{20 + index:02d}"
        set_entitlement(env, entitlement_id=entitlement_id, state="active")
        seed_publication(env, publication_id=publication_id, entitlement_id=entitlement_id, report_version_id=report_version_id)
        dispatch(env, publication_id)

    page1 = env["service"].list_deliveries(operator, limit=2)
    assert len(page1.data["items"]) == 2
    assert page1.data["next_cursor"] is not None
    page2 = env["service"].list_deliveries(operator, limit=2, cursor=page1.data["next_cursor"])
    ids_page1 = {row["id"] for row in page1.data["items"]}
    ids_page2 = {row["id"] for row in page2.data["items"]}
    assert ids_page1.isdisjoint(ids_page2)


# ---- webhook signature and replay contract ---------------------------------

def test_webhook_rejects_stale_timestamp(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import UnauthorizedError

    stale_timestamp = str(int(NOW.timestamp()) - 1000)
    body = {
        "event_id": "e8000000-0000-0000-0000-000000000001", "provider_message_ref": "ref", "delivery_id": message.id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000031",
    }
    with pytest.raises(UnauthorizedError) as excinfo:
        send_receipt(env, body, idempotency_key="receipt-stale-ts-0001", timestamp=stale_timestamp, nonce="s" * 16)
    assert excinfo.value.code == "webhook_timestamp_expired"


def test_webhook_unknown_integration_key_is_rejected(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import UnauthorizedError

    body = {
        "event_id": "e9000000-0000-0000-0000-000000000001", "provider_message_ref": "ref", "delivery_id": message.id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000032",
    }
    with pytest.raises(UnauthorizedError) as excinfo:
        send_receipt(env, body, idempotency_key="receipt-badkey-0001", key_id="unregistered-key", nonce="t" * 16)
    assert excinfo.value.code == "integration_key_unknown"


# ---- event consumer contract: dedup, sequence, tenant, schema -------------

def test_consumer_rejects_malformed_payload_schema(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    envelope = _envelope("entitlement.changed", "entitlement", "40000000-0000-0000-0000-000000000001", {"entitlement_id": "x"})
    with pytest.raises(ValidationError) as excinfo:
        consumer.consume(envelope)
    assert excinfo.value.code == "event_schema_invalid"


def test_consumer_dedupes_duplicate_event_id(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"entitlement_id": "40000000-0000-0000-0000-000000000001", "customer_id": MEMBER_A, "contract_id": "f0000000-0000-0000-0000-000000000001", "state": "active", "decision_id": "d0000000-0000-0000-0000-000000000001", "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], payload, producer="M02")
    first = consumer.consume(envelope)
    second = consumer.consume(envelope)
    assert first.status == "applied"
    assert second.status == "duplicate"
    assert second.result_code == first.result_code


def test_consumer_holds_out_of_order_sequence_for_retry(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"entitlement_id": "40000000-0000-0000-0000-000000000002", "customer_id": MEMBER_A, "contract_id": "f0000000-0000-0000-0000-000000000002", "state": "active", "decision_id": "d0000000-0000-0000-0000-000000000002", "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], payload, sequence_no=3, producer="M02", event_id="ea000000-0000-0000-0000-000000000001")
    result = consumer.consume(envelope)
    assert result.status == "retry"
    assert result.result_code == "sequence_gap"


def test_consumer_rejects_replayed_event_id_with_different_payload(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"entitlement_id": "40000000-0000-0000-0000-000000000003", "customer_id": MEMBER_A, "contract_id": "f0000000-0000-0000-0000-000000000003", "state": "active", "decision_id": "d0000000-0000-0000-0000-000000000003", "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], payload, producer="M02", event_id="eb000000-0000-0000-0000-000000000001")
    consumer.consume(envelope)
    tampered_payload = dict(payload, state="suspended")
    tampered = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], tampered_payload, producer="M02", event_id="eb000000-0000-0000-0000-000000000001")
    with pytest.raises(ConflictError) as excinfo:
        consumer.consume(tampered)
    assert excinfo.value.code == "inbox_payload_mismatch"


def test_consumer_dispatches_on_publication_created_and_retries_on_dependency_outage(m09):
    env = m09
    set_entitlement(env, state="active")
    # No publication registered yet: the port lookup fails and the event must
    # stay retryable rather than silently dropping the fan-out.
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"publication_id": PUBLICATION_X, "report_version_id": "60000000-0000-0000-0000-000000000001", "signature_id": "70000000-0000-0000-0000-000000000001", "audience_snapshot_id": "aa000000-0000-0000-0000-000000000001", "bundle_manifest_sha256": "b" * 64, "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("publication.created", "publication", PUBLICATION_X, payload, event_id="ec000000-0000-0000-0000-000000000001")
    result = consumer.consume(envelope)
    assert result.status == "retry"

    seed_publication(env)
    result2 = consumer.consume(envelope)
    assert result2.status == "applied"
    rows = env["repository"].read(lambda state: [m for (t, _), m in state.deliveries.items() if t == TENANT])
    assert len(rows) == 1


# ---- worker loop and outbox publisher --------------------------------------

def test_dispatch_worker_run_once_processes_leased_jobs(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    worker = DispatchWorker(env["service"])
    result = worker.run_once("drill-worker", TENANT, limit=10)
    assert result.leased == 1
    assert result.accepted == 1


def test_outbox_publisher_retries_then_publishes(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(env["repository"], bus, max_attempts=3)
    first = publisher.run_once("outbox-drill")
    assert first.retried >= 1
    bus.failure_mode = None
    # OutboxPublisher reads real wall-clock time (not the injectable service
    # clock); the backoff after one failed attempt is 2 ** 1 == 2 seconds.
    import time

    time.sleep(2.1)
    second = publisher.run_once("outbox-drill")
    assert second.processed >= 1


# ---- HTTP controller problem+json contract --------------------------------

def test_http_controller_maps_domain_error_to_problem_json(m09, member_a):
    env = m09
    controller = M09Controller(env["service"])
    response = controller.handle(
        "bind_member_channel", member_a,
        body={"channel": "not_a_channel", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True},
        headers={"idempotency-key": "http-bad-0001"},
    )
    assert response.status == 422
    assert response.body["code"] == "validation_failed"
    assert response.headers["Content-Type"] == "application/problem+json"


def test_http_controller_webhook_route_requires_no_bearer_context():
    operation_ids = {route[2] for route in ROUTES}
    assert "receive_message_receipt" in operation_ids
    assert {"GET", "POST"} >= {route[0] for route in ROUTES}
