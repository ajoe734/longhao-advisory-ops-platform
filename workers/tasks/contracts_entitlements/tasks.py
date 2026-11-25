from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from modules.contracts_entitlements.domain import ActorContext, ConflictError, Job, utc_now
from modules.contracts_entitlements.repository import InMemoryRepository, StoreState
from modules.contracts_entitlements.service import ContractsEntitlementsService


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
    """Local adapter; records synthetic envelopes and never reaches a network.

    Stands in for both the shared event bus and the LegendFlow entitlement
    projection notifier. Real delivery to LegendFlow remains NOT_CONNECTED.
    """

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
    def __init__(
        self, repository: InMemoryRepository, publisher: EventPublisherPort, *,
        max_attempts: int = 5, clock: Callable[[], datetime] = utc_now,
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
                    if (
                        (event.status in {"pending", "retry"} and (event.next_attempt_at is None or event.next_attempt_at <= now))
                        or (event.status == "leased" and event.lease_until is not None and event.lease_until <= now)
                    )
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
    """Lease and run M02 operational jobs with bounded retry and safe redrive."""

    def __init__(self, repository: InMemoryRepository, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.repository = repository
        self._clock = clock

    def lease(self, worker_id: str, limit: int = 20, *, kinds: set[str] | None = None) -> list[Job]:
        now = self._clock()

        def lease_jobs(state: StoreState) -> list[Job]:
            selected = [
                job for job in state.jobs.values()
                if (kinds is None or job.kind in kinds)
                and (
                    (job.status in {"pending", "retry"} and (job.next_attempt_at is None or job.next_attempt_at <= now))
                    or (job.status == "leased" and job.lease_until is not None and job.lease_until <= now)
                )
            ][:limit]
            for job in selected:
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_until = now + timedelta(seconds=30)
                job.updated_at = now
            return selected

        return self.repository.atomic(lease_jobs)

    def complete(self, tenant_id: str, job_id: str, worker_id: str) -> None:
        def finish(state: StoreState) -> None:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id or job.status != "leased":
                raise ConflictError("job lease was lost", code="worker_lease_lost")
            job.status = "done"
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = self._clock()

        self.repository.atomic(finish)

    def redrive(self, tenant_id: str, job_id: str, reason: str) -> Job:
        if not reason.strip():
            raise ValueError("manual redrive requires a reason")
        now = self._clock()

        def redrive_job(state: StoreState) -> Job:
            job = state.jobs[(tenant_id, job_id)]
            if job.status != "dead_letter":
                raise ConflictError("only dead-letter jobs can be redriven", code="job_not_dead_letter")
            job.status = "retry"
            job.next_attempt_at = now
            job.lease_owner = None
            job.lease_until = None
            job.redrive_reason = reason[:500]
            job.updated_at = now
            return job

        return self.repository.atomic(redrive_job)


def _entitlement_service_identity(tenant_id: str) -> ActorContext:
    return ActorContext(
        tenant_id, "00000000-0000-0000-0000-000000000003",
        frozenset({"entitlement_service"}), principal_type="service", purpose="gate_recheck",
    )


class EntitlementRecheckWorker:
    """Processes gate-change jobs produced by the inbound event consumer.

    It only re-triggers ``evaluate_entitlements`` for a contract that already
    exists (i.e. the application was already accepted); it never grants
    entitlements on its own authority and never acts before a human company
    acceptance decision has been recorded.
    """

    def __init__(self, repository: InMemoryRepository, service: ContractsEntitlementsService, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.repository = repository
        self.service = service
        self._clock = clock

    _KINDS = {"gate_changed_recheck", "payment_confirmed_recheck", "refund_confirmed_recheck"}

    def run_once(self, worker_id: str, limit: int = 20) -> WorkerResult:
        runner = TaskRunner(self.repository, clock=self._clock)
        jobs = runner.lease(worker_id, limit, kinds=self._KINDS)
        processed = retried = dead = 0
        for job in jobs:
            try:
                if job.kind == "refund_confirmed_recheck":
                    contract_id = job.payload["contract_id"]
                else:
                    application_id = job.payload["application_id"]
                    contract_id = self.repository.read(lambda state: state.contract_by_application.get((job.tenant_id, application_id)))
                if contract_id is None:
                    runner.complete(job.tenant_id, job.id, worker_id)
                    processed += 1
                    continue
                contract = self.repository.read(lambda state: state.contracts.get((job.tenant_id, contract_id)))
                if contract is None:
                    runner.complete(job.tenant_id, job.id, worker_id)
                    processed += 1
                    continue
                self.service.evaluate_entitlements(
                    _entitlement_service_identity(job.tenant_id), contract_id,
                    # job.id is deterministic across retries, unlike a fresh new_id(); otherwise the
                    # idempotency fingerprint would differ on retry and a safe replay would 409 instead.
                    {"cause_event_id": job.id, "policy_version": "worker-auto-recheck-v1"},
                    expected_version=contract.version, idempotency_key=f"worker-recheck:{job.id}",
                )
                runner.complete(job.tenant_id, job.id, worker_id)
                processed += 1
            except Exception:
                job_dead = self._fail(job.tenant_id, job.id, worker_id)
                dead += int(job_dead)
                retried += int(not job_dead)
        return WorkerResult(processed, retried, dead)

    def _fail(self, tenant_id: str, job_id: str, worker_id: str) -> bool:
        now = self._clock()

        def fail(state: StoreState) -> bool:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id or job.status != "leased":
                raise ConflictError("job lease was lost", code="worker_lease_lost")
            job.attempt_count += 1
            job.lease_owner = None
            job.lease_until = None
            if job.attempt_count >= job.max_attempts:
                job.status = "dead_letter"
                return True
            job.status = "retry"
            job.next_attempt_at = now + timedelta(seconds=min(300, 2 ** job.attempt_count))
            return False

        return self.repository.atomic(fail)
