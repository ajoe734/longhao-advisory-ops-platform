from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from modules.aml_cft.domain import ConflictError, DependencyUnavailableError, utc_now
from modules.aml_cft.ports import ScreeningProviderPort
from modules.aml_cft.repository import InMemoryRepository, StoreState
from modules.aml_cft.telemetry import M04Telemetry


class RetryableTaskError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UnknownExternalResult(Exception):
    def __init__(self, event_id: str) -> None:
        super().__init__(event_id)
        self.event_id = event_id


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
    """Publishes aml.case_changed and business_clearance.changed with lease/bounded retry.

    No raw case content ever enters the envelope here: the service layer has
    already restricted ``aml.case_changed`` payloads to case_id/screening_id/
    restricted_record_version and ``business_clearance.changed`` to the safe
    decision/expiry summary before the event reaches the outbox.
    """

    def __init__(self, repository: InMemoryRepository, publisher: EventPublisherPort, *, max_attempts: int = 5) -> None:
        self.repository = repository
        self.publisher = publisher
        self.max_attempts = max_attempts

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
        now = utc_now()

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
        now = utc_now()

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


@dataclass(frozen=True)
class DispatchResult:
    dispatched: int
    manual_required: int


class ScreeningDispatchWorker:
    """Calls the external screening provider after the command transaction commits.

    A provider timeout never gets silently retried into a false "clear": the
    screening is immediately marked ``error``/``manual_required`` and the job
    dead-letters so an officer must start a fresh screening request.
    """

    def __init__(self, repository: InMemoryRepository, provider: ScreeningProviderPort, telemetry: M04Telemetry | None = None) -> None:
        self.repository = repository
        self.provider = provider
        self.telemetry = telemetry or M04Telemetry()

    def run_once(self, worker_id: str, limit: int = 20) -> DispatchResult:
        jobs = self._lease(worker_id, limit)
        dispatched = manual_required = 0
        for tenant_id, job_id in jobs:
            job = self.repository.read(lambda state: state.jobs[(tenant_id, job_id)])
            screening_id = job.payload["screening_id"]
            try:
                acceptance = self.provider.start(tenant_id, job.payload["request_sha256"], job.payload["scope"])
                self._apply_acceptance(tenant_id, screening_id, job_id, worker_id, acceptance)
                dispatched += 1
            except DependencyUnavailableError:
                self._mark_manual_required(tenant_id, screening_id, job_id, worker_id)
                self.telemetry.dependency_failure("screening_provider")
                manual_required += 1
        return DispatchResult(dispatched, manual_required)

    def _lease(self, worker_id: str, limit: int) -> list[tuple[str, str]]:
        now = utc_now()

        def lease(state: StoreState) -> list[tuple[str, str]]:
            selected = [
                (tenant, job.id) for (tenant, _), job in state.jobs.items()
                if job.kind == "dispatch_aml_screening"
                and job.status in {"pending", "retry"}
                and (job.next_attempt_at is None or job.next_attempt_at <= now)
                and (job.lease_until is None or job.lease_until <= now)
            ][:limit]
            for tenant, job_id in selected:
                job = state.jobs[(tenant, job_id)]
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_until = now + timedelta(seconds=30)
                job.updated_at = now
            return selected

        return self.repository.atomic(lease)

    def _apply_acceptance(self, tenant_id: str, screening_id: str, job_id: str, worker_id: str, acceptance) -> None:
        def apply(state: StoreState) -> None:
            screening = state.screenings[(tenant_id, screening_id)]
            screening.provider = acceptance.provider
            screening.provider_request_id = acceptance.provider_request_id
            screening.provider_ref = acceptance.provider_ref
            screening.updated_at = utc_now()
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id:
                raise ConflictError("dispatch job lease was lost", code="worker_lease_lost")
            job.status = "done"
            job.updated_at = utc_now()

        self.repository.atomic(apply)

    def _mark_manual_required(self, tenant_id: str, screening_id: str, job_id: str, worker_id: str) -> None:
        def apply(state: StoreState) -> None:
            screening = state.screenings[(tenant_id, screening_id)]
            screening.state = "error"
            screening.manual_required = True
            screening.updated_at = utc_now()
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id:
                raise ConflictError("dispatch job lease was lost", code="worker_lease_lost")
            job.status = "dead_letter"
            job.last_error_code = "screening_provider_timeout"
            job.updated_at = utc_now()

        self.repository.atomic(apply)


@dataclass(frozen=True)
class RetentionSweepResult:
    retained: int
    skipped_hold: int


class RetentionSweeper:
    """Demonstrates REQ-M04-011: an open legal hold always wins over routine retention."""

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def sweep(self, tenant_id: str) -> RetentionSweepResult:
        def run(state: StoreState) -> RetentionSweepResult:
            retained = skipped = 0
            for (tenant, _), case in state.cases.items():
                if tenant != tenant_id:
                    continue
                if case.legal_hold:
                    skipped += 1
                else:
                    retained += 1
            return RetentionSweepResult(retained, skipped)

        return self.repository.atomic(run)
