"""M11 operational workers: durable outbox publishing and the policy sweep.

Mirrors workers/tasks/customer_identity_application/tasks.py's lease/bounded
retry/deadletter discipline (docs/modules/M11-ownership_pool.md §15: "Worker具
lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from modules.ownership_pool.domain import ActorContext, ConflictError, Job, utc_now
from modules.ownership_pool.repository import InMemoryRepository, StoreState
from modules.ownership_pool.service import OwnershipPoolService


class RetryableTaskError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UnknownExternalResult(Exception):
    def __init__(self, provider_ref: str) -> None:
        super().__init__(provider_ref)
        self.provider_ref = provider_ref


class EventPublisherPort(Protocol):
    is_fake: bool

    def publish(self, envelope: dict[str, Any]) -> str: ...

    def lookup(self, event_id: str) -> str: ...


class LocalEventBus:
    """Local adapter; records synthetic envelopes and never reaches a network."""

    is_fake = True

    def __init__(self) -> None:
        self.envelopes: dict[str, dict[str, Any]] = {}
        self.failure_mode: str | None = None

    def publish(self, envelope: dict[str, Any]) -> str:
        if self.failure_mode == "timeout":
            raise RetryableTaskError("local_bus_timeout")
        if self.failure_mode == "unknown":
            raise UnknownExternalResult(envelope["event_id"])
        self.envelopes.setdefault(envelope["event_id"], envelope)
        return "accepted"

    def lookup(self, event_id: str) -> str:
        return "accepted" if event_id in self.envelopes else "not_found"


@dataclass(frozen=True)
class WorkerResult:
    processed: int
    retried: int
    dead_lettered: int


class OutboxPublisher:
    """Leases ``ownership.*`` outbox rows and publishes them with bounded retry."""

    def __init__(
        self,
        repository: InMemoryRepository,
        publisher: EventPublisherPort,
        *,
        max_attempts: int = 5,
        clock=utc_now,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.max_attempts = max_attempts
        self._clock = clock

    def run_once(self, worker_id: str, limit: int = 50) -> WorkerResult:
        leased = self._lease(worker_id, limit)
        processed = retried = dead = 0
        for tenant_id, event_id in leased:
            event = self.repository.read(lambda state: state.outbox[(tenant_id, event_id)])
            try:
                result = self.publisher.publish(event.envelope())
                if result != "accepted":
                    raise RetryableTaskError("publisher_not_accepted")
                self._complete(tenant_id, event_id, worker_id)
                processed += 1
            except UnknownExternalResult:
                if self.publisher.lookup(event.event_id) == "accepted":
                    self._complete(tenant_id, event_id, worker_id)
                    processed += 1
                else:
                    is_dead = self._fail(tenant_id, event_id, worker_id, "publisher_unknown")
                    dead += int(is_dead)
                    retried += int(not is_dead)
            except RetryableTaskError as exc:
                is_dead = self._fail(tenant_id, event_id, worker_id, exc.code)
                dead += int(is_dead)
                retried += int(not is_dead)
        return WorkerResult(processed, retried, dead)

    def _lease(self, worker_id: str, limit: int) -> list[tuple[str, str]]:
        now = self._clock()

        def lease(state: StoreState) -> list[tuple[str, str]]:
            candidates = sorted(
                (
                    (key, event) for key, event in state.outbox.items()
                    if event.status in {"pending", "retry"}
                    and (event.next_attempt_at is None or event.next_attempt_at <= now)
                    and (event.lease_until is None or event.lease_until <= now)
                ),
                key=lambda item: (item[1].emitted_at, item[1].sequence_no),
            )[:limit]
            keys: list[tuple[str, str]] = []
            for key, event in candidates:
                event.status = "leased"
                event.lease_owner = worker_id
                event.lease_until = now + timedelta(seconds=30)
                keys.append(key)
            return keys

        return self.repository.atomic(lease)

    def _complete(self, tenant_id: str, event_id: str, worker_id: str) -> None:
        def complete(state: StoreState) -> None:
            event = state.outbox[(tenant_id, event_id)]
            if event.lease_owner != worker_id or event.status != "leased":
                raise ConflictError("outbox lease was lost", code="worker_lease_lost")
            event.status = "published"
            event.lease_owner = None
            event.lease_until = None
            event.last_error_code = None

        self.repository.atomic(complete)

    def _fail(self, tenant_id: str, event_id: str, worker_id: str, code: str) -> bool:
        now = self._clock()

        def fail(state: StoreState) -> bool:
            event = state.outbox[(tenant_id, event_id)]
            if event.lease_owner != worker_id or event.status != "leased":
                raise ConflictError("outbox lease was lost", code="worker_lease_lost")
            event.attempt_count += 1
            event.last_error_code = code
            event.lease_owner = None
            event.lease_until = None
            if event.attempt_count >= self.max_attempts:
                event.status = "dead_letter"
                return True
            event.status = "retry"
            event.next_attempt_at = now + timedelta(seconds=min(300, 2 ** event.attempt_count))
            return False

        return self.repository.atomic(fail)


class TaskRunner:
    """Lease and run M11 operational jobs with bounded retry and safe redrive."""

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def lease(self, worker_id: str, limit: int = 20) -> list[Job]:
        now = utc_now()

        def lease_jobs(state: StoreState) -> list[Job]:
            selected = [
                job for job in state.jobs.values()
                if job.status in {"pending", "retry"}
                and (job.next_attempt_at is None or job.next_attempt_at <= now)
                and (job.lease_until is None or job.lease_until <= now)
            ][:limit]
            for job in selected:
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_until = now + timedelta(seconds=30)
                job.updated_at = now
            return selected

        return self.repository.atomic(lease_jobs)

    def redrive(self, tenant_id: str, job_id: str, reason: str) -> Job:
        if not reason.strip():
            raise ValueError("manual redrive requires a reason")

        def redrive_job(state: StoreState) -> Job:
            job = state.jobs[(tenant_id, job_id)]
            if job.status != "dead_letter":
                raise ConflictError("only dead-letter jobs can be redriven", code="job_not_dead_letter")
            job.status = "retry"
            job.next_attempt_at = utc_now()
            job.lease_owner = None
            job.lease_until = None
            job.redrive_reason = reason[:500]
            job.updated_at = utc_now()
            return job

        return self.repository.atomic(redrive_job)


@dataclass(frozen=True)
class SweepResult:
    policy_version: str
    customers_evaluated: int
    eligible_count: int
    blocked_count: int


def run_policy_sweep(
    service: OwnershipPoolService,
    repository: InMemoryRepository,
    tenant_id: str,
    policy_version: str,
    *,
    as_of,
    idempotency_key: str,
    service_actor_id: str,
) -> SweepResult:
    """Run one live ``evaluate_company_pool`` pass over every sales-owned customer.

    Scheduled (e.g. daily) by ops tooling, not by a human — the ``ownership_service``
    role matches ``x-allowed-roles`` on the ``evaluate_company_pool`` operationId.
    Re-running with the same ``idempotency_key`` (e.g. one key per UTC day) never
    double-applies a lapse because the service's idempotency receipt replays.
    """
    context = ActorContext(tenant_id, service_actor_id, frozenset({"ownership_service"}), principal_type="service")
    response = service.evaluate_company_pool(
        context,
        {"as_of": as_of, "policy_version": policy_version, "dry_run": False},
        idempotency_key=idempotency_key,
    )
    body = response.data
    return SweepResult(body["policy_version"], body["evaluated_count"], body["eligible_count"], body["blocked_count"])
