from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from modules.service_cases.domain import ConflictError, Job, ResponseAttempt, new_id, utc_now
from modules.service_cases.ports import SuppressionRequestPort
from modules.service_cases.repository import InMemoryRepository, StoreState


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


class MessagingProviderPort(Protocol):
    is_fake: bool

    def send(self, dispatch_id: str, channel: str, content_sha256: str) -> tuple[str, str]: ...

    def lookup(self, provider_request_id: str) -> str: ...


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


class LocalMessagingProvider:
    """Local adapter for service-response dispatch sends; no network reached."""

    is_fake = True

    def __init__(self) -> None:
        self.sent: dict[str, str] = {}
        self.failure_mode: str | None = None

    def send(self, dispatch_id: str, channel: str, content_sha256: str) -> tuple[str, str]:
        if self.failure_mode == "timeout":
            raise RetryableTaskError("messaging_provider_timeout")
        if self.failure_mode == "unknown":
            provider_request_id = f"unknown-{dispatch_id}"
            raise UnknownExternalResult(provider_request_id)
        if self.failure_mode == "rejected":
            return "rejected", f"rejected-{dispatch_id}"
        provider_request_id = f"accepted-{dispatch_id}"
        self.sent[provider_request_id] = dispatch_id
        return "accepted", provider_request_id

    def lookup(self, provider_request_id: str) -> str:
        return "accepted" if provider_request_id in self.sent else "not_found"


@dataclass(frozen=True)
class WorkerResult:
    processed: int
    retried: int
    dead_lettered: int


class OutboxPublisher:
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


class ResponseDispatchSender:
    """Leases queued/retry support.response_dispatch rows and sends them.

    Never resends on an unknown outcome without first calling lookup(); every
    attempt is recorded in support.response_attempt before the lease is
    released, matching the "query before retry" rule for unknown results.
    """

    def __init__(self, repository: InMemoryRepository, provider: MessagingProviderPort, *, max_attempts: int = 5) -> None:
        self.repository = repository
        self.provider = provider
        self.max_attempts = max_attempts

    def run_once(self, worker_id: str, limit: int = 50) -> WorkerResult:
        leased = self._lease(worker_id, limit)
        processed = retried = dead = 0
        for tenant_id, dispatch_id in leased:
            dispatch = self.repository.read(lambda state: state.response_dispatches[(tenant_id, dispatch_id)])
            attempt_no = 1 + sum(
                1 for (tenant, _), attempt in self.repository.snapshot().response_attempts.items()
                if tenant == tenant_id and attempt.response_id == dispatch_id
            )
            started = utc_now()
            try:
                outcome, provider_request_id = self.provider.send(dispatch_id, dispatch.channel, dispatch.content_sha256)
                self._record_attempt(tenant_id, dispatch_id, attempt_no, "accepted" if outcome == "accepted" else "rejected", provider_request_id, started)
                if outcome != "accepted":
                    is_dead = self._fail(tenant_id, dispatch_id, worker_id, "provider_rejected", terminal=True)
                    dead += int(is_dead)
                    retried += int(not is_dead)
                    continue
                self._complete(tenant_id, dispatch_id, worker_id, provider_request_id)
                processed += 1
            except UnknownExternalResult as exc:
                self._record_attempt(tenant_id, dispatch_id, attempt_no, "unknown", exc.provider_ref, started)
                if self.provider.lookup(exc.provider_ref) == "accepted":
                    self._complete(tenant_id, dispatch_id, worker_id, exc.provider_ref)
                    processed += 1
                else:
                    is_dead = self._fail(tenant_id, dispatch_id, worker_id, "provider_unknown", retry_status="unknown")
                    dead += int(is_dead)
                    retried += int(not is_dead)
            except RetryableTaskError as exc:
                self._record_attempt(tenant_id, dispatch_id, attempt_no, "timeout", "", started)
                is_dead = self._fail(tenant_id, dispatch_id, worker_id, exc.code)
                dead += int(is_dead)
                retried += int(not is_dead)
        return WorkerResult(processed, retried, dead)

    def _record_attempt(self, tenant_id: str, dispatch_id: str, attempt_no: int, outcome: str, provider_request_id: str, started) -> None:
        def record(state: StoreState) -> None:
            attempt = ResponseAttempt(
                id=new_id(), tenant_id=tenant_id, response_id=dispatch_id, attempt_no=attempt_no,
                provider="service_messaging_adapter", provider_request_id=provider_request_id or new_id(),
                outcome=outcome, started_at=started, finished_at=utc_now(),
                error_code=None if outcome == "accepted" else outcome,
            )
            state.response_attempts[(tenant_id, attempt.id)] = attempt

        self.repository.atomic(record)

    def _lease(self, worker_id: str, limit: int) -> list[tuple[str, str]]:
        now = utc_now()

        def lease(state: StoreState) -> list[tuple[str, str]]:
            candidates = sorted(
                (
                    (key, dispatch) for key, dispatch in state.response_dispatches.items()
                    if dispatch.status in {"queued", "unknown"}
                    and (dispatch.next_attempt_at is None or dispatch.next_attempt_at <= now)
                    and (dispatch.lease_until is None or dispatch.lease_until <= now)
                ),
                key=lambda item: item[1].created_at,
            )[:limit]
            keys: list[tuple[str, str]] = []
            for key, dispatch in candidates:
                dispatch.lease_owner = worker_id
                dispatch.lease_until = now + timedelta(seconds=30)
                keys.append(key)
            return keys

        return self.repository.atomic(lease)

    def _complete(self, tenant_id: str, dispatch_id: str, worker_id: str, provider_request_id: str) -> None:
        def complete(state: StoreState) -> None:
            dispatch = state.response_dispatches[(tenant_id, dispatch_id)]
            if dispatch.lease_owner != worker_id:
                raise ConflictError("response dispatch lease was lost", code="worker_lease_lost")
            dispatch.status = "accepted"
            dispatch.accepted_at = dispatch.accepted_at or utc_now()
            dispatch.provider_message_ref = provider_request_id
            dispatch.lease_owner = None
            dispatch.lease_until = None
            dispatch.last_error_code = None
            dispatch.version += 1
            dispatch.updated_at = utc_now()

        self.repository.atomic(complete)

    def _fail(
        self, tenant_id: str, dispatch_id: str, worker_id: str, code: str, *,
        terminal: bool = False, retry_status: str = "queued",
    ) -> bool:
        now = utc_now()

        def fail(state: StoreState) -> bool:
            dispatch = state.response_dispatches[(tenant_id, dispatch_id)]
            if dispatch.lease_owner != worker_id:
                raise ConflictError("response dispatch lease was lost", code="worker_lease_lost")
            dispatch.attempt_count += 1
            dispatch.last_error_code = code
            dispatch.lease_owner = None
            dispatch.lease_until = None
            dispatch.version += 1
            dispatch.updated_at = now
            if terminal or dispatch.attempt_count >= self.max_attempts:
                dispatch.status = "failed"
                return True
            dispatch.status = retry_status
            dispatch.next_attempt_at = now + timedelta(seconds=min(300, 2 ** dispatch.attempt_count))
            return False

        return self.repository.atomic(fail)


class SuppressionJobRunner:
    """Leases request_suppression jobs and calls the M10 suppression port.

    The upstream M10 suppression intake is NOT_CONNECTED in this repository;
    the fake adapter records the request for contract-mock verification only.
    """

    def __init__(self, repository: InMemoryRepository, suppression: SuppressionRequestPort, *, max_attempts: int = 5) -> None:
        self.repository = repository
        self.suppression = suppression
        self.max_attempts = max_attempts

    def run_once(self, worker_id: str, limit: int = 50) -> WorkerResult:
        jobs = self._lease(worker_id, limit)
        processed = retried = dead = 0
        for job in jobs:
            try:
                self.suppression.request(job.tenant_id, job.payload["customer_id"], job.payload["scope"], job.payload["reason_code"])
                self._complete(job.tenant_id, job.id, worker_id)
                processed += 1
            except Exception:  # pragma: no cover - defensive; fakes do not raise
                is_dead = self._fail(job.tenant_id, job.id, worker_id, "suppression_request_failed")
                dead += int(is_dead)
                retried += int(not is_dead)
        return WorkerResult(processed, retried, dead)

    def _lease(self, worker_id: str, limit: int) -> list[Job]:
        now = utc_now()

        def lease_jobs(state: StoreState) -> list[Job]:
            selected = [
                job for job in state.jobs.values()
                if job.kind == "request_suppression"
                and job.status in {"pending", "retry"}
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

    def _complete(self, tenant_id: str, job_id: str, worker_id: str) -> None:
        def complete(state: StoreState) -> None:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id:
                raise ConflictError("job lease was lost", code="worker_lease_lost")
            job.status = "completed"
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = utc_now()

        self.repository.atomic(complete)

    def _fail(self, tenant_id: str, job_id: str, worker_id: str, code: str) -> bool:
        now = utc_now()

        def fail(state: StoreState) -> bool:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id:
                raise ConflictError("job lease was lost", code="worker_lease_lost")
            job.attempt_count += 1
            job.last_error_code = code
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = now
            if job.attempt_count >= self.max_attempts:
                job.status = "dead_letter"
                return True
            job.status = "retry"
            job.next_attempt_at = now + timedelta(seconds=min(300, 2 ** job.attempt_count))
            return False

        return self.repository.atomic(fail)


class TaskRunner:
    """Lease and run M13 operational jobs with bounded retry and safe redrive."""

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

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
