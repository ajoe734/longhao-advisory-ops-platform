from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from modules.kyc_suitability.domain import ConflictError, ValidationError, utc_now
from modules.kyc_suitability.ports import DocumentScannerPort, ExternalVerificationPort
from modules.kyc_suitability.repository import InMemoryRepository, StoreState


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


class DocumentScanWorker:
    """Simulates the isolation-zone virus/MIME scan completion callback.

    A quarantined document never becomes readable or review-eligible until
    this worker (standing in for the storage provider's async callback)
    records a clean or rejected outcome with a safe, non-leaking reason code.
    """

    def __init__(self, repository: InMemoryRepository, scanner: DocumentScannerPort, *, max_attempts: int = 5) -> None:
        self.repository = repository
        self.scanner = scanner
        self.max_attempts = max_attempts
        self.alerts: list[str] = []

    def run_once(self, worker_id: str, limit: int = 50) -> WorkerResult:
        leased = self._lease(worker_id, limit)
        processed = retried = dead = 0
        for tenant_id, document_id in leased:
            document = self.repository.read(lambda state: state.documents[(tenant_id, document_id)])
            try:
                outcome = self.scanner.scan(document.id, document.sha256, document.media_type)
                self._complete(tenant_id, document_id, worker_id, outcome)
                processed += 1
            except Exception as exc:  # DependencyUnavailableError and friends
                code = getattr(exc, "code", "document_scanner_failed")
                is_dead = self._fail(tenant_id, document_id, worker_id, code)
                dead += int(is_dead)
                retried += int(not is_dead)
        return WorkerResult(processed, retried, dead)

    def _lease(self, worker_id: str, limit: int) -> list[tuple[str, str]]:
        now = utc_now()

        def lease(state: StoreState) -> list[tuple[str, str]]:
            candidates = sorted(
                (
                    (key, document) for key, document in state.documents.items()
                    if document.scan_status == "pending"
                    and (document.next_attempt_at is None or document.next_attempt_at <= now)
                    and (document.lease_until is None or document.lease_until <= now)
                ),
                key=lambda item: item[1].created_at,
            )[:limit]
            keys: list[tuple[str, str]] = []
            for key, document in candidates:
                document.lease_owner = worker_id
                document.lease_until = now + timedelta(seconds=30)
                keys.append(key)
            return keys

        return self.repository.atomic(lease)

    def _complete(self, tenant_id: str, document_id: str, worker_id: str, outcome) -> None:
        def complete(state: StoreState) -> None:
            document = state.documents[(tenant_id, document_id)]
            if document.lease_owner != worker_id:
                raise ConflictError("document scan lease was lost", code="worker_lease_lost")
            document.scan_status = outcome.status
            document.scan_rejection_code = outcome.rejection_code
            document.scanned_at = utc_now()
            document.attempt_count = 0
            document.last_error_code = None
            document.lease_owner = None
            document.lease_until = None
            document.next_attempt_at = None
            document.version += 1
            document.updated_at = document.scanned_at

        self.repository.atomic(complete)

    def _fail(self, tenant_id: str, document_id: str, worker_id: str, code: str) -> bool:
        # Transient scanner failures keep the document pending (not readable,
        # not review-eligible); never interpreted as clean. Attempts and the
        # last error code are tracked for alerting; the lease is released
        # with a backoff so another worker can retry once it is due.
        now = utc_now()

        def fail(state: StoreState) -> bool:
            document = state.documents[(tenant_id, document_id)]
            if document.lease_owner != worker_id:
                raise ConflictError("document scan lease was lost", code="worker_lease_lost")
            document.attempt_count += 1
            document.last_error_code = code
            document.lease_owner = None
            document.lease_until = None
            document.next_attempt_at = now + timedelta(seconds=min(300, 2 ** document.attempt_count))
            document.updated_at = now
            return document.attempt_count >= self.max_attempts

        exhausted = self.repository.atomic(fail)
        if exhausted:
            self.alerts.append(f"m03_document_scanner_unavailable:{document_id}:{code}")
        return False


class DueDiligenceWorker:
    """Bounded-retry external recheck for refresh cases.

    Repeated external timeouts move the case to manual_required and alert;
    they never silently resolve a refresh case as passed.
    """

    def __init__(self, repository: InMemoryRepository, verifier: ExternalVerificationPort, *, max_attempts: int = 3) -> None:
        self.repository = repository
        self.verifier = verifier
        self.max_attempts = max_attempts
        self.alerts: list[str] = []

    def run_once(self, worker_id: str, limit: int = 50) -> WorkerResult:
        leased = self._lease(worker_id, limit)
        processed = retried = dead = 0
        for tenant_id, case_id in leased:
            case = self.repository.read(lambda state: state.refresh_cases[(tenant_id, case_id)])
            try:
                outcome = self.verifier.check(tenant_id, case.customer_id)
                if outcome.status == "cleared":
                    self._complete(tenant_id, case_id, worker_id)
                    processed += 1
                else:
                    self._require_manual(tenant_id, case_id, worker_id, "verification_flagged")
                    dead += 1
            except Exception as exc:
                code = getattr(exc, "code", "external_verification_failed")
                is_manual = self._fail(tenant_id, case_id, worker_id, code)
                dead += int(is_manual)
                retried += int(not is_manual)
        return WorkerResult(processed, retried, dead)

    def _lease(self, worker_id: str, limit: int) -> list[tuple[str, str]]:
        now = utc_now()

        def lease(state: StoreState) -> list[tuple[str, str]]:
            candidates = sorted(
                (
                    (key, case) for key, case in state.refresh_cases.items()
                    if case.status == "pending"
                    and (case.next_attempt_at is None or case.next_attempt_at <= now)
                    and (case.lease_until is None or case.lease_until <= now)
                ),
                key=lambda item: item[1].created_at,
            )[:limit]
            keys: list[tuple[str, str]] = []
            for key, case in candidates:
                case.lease_owner = worker_id
                case.lease_until = now + timedelta(seconds=30)
                keys.append(key)
            return keys

        return self.repository.atomic(lease)

    def _complete(self, tenant_id: str, case_id: str, worker_id: str) -> None:
        def complete(state: StoreState) -> None:
            case = state.refresh_cases[(tenant_id, case_id)]
            if case.lease_owner != worker_id:
                raise ConflictError("refresh case lease was lost", code="worker_lease_lost")
            case.status = "completed"
            case.lease_owner = None
            case.lease_until = None
            case.updated_at = utc_now()
            submission = state.submissions.get((tenant_id, case.submission_id))
            if submission is not None and submission.status == "refresh_required":
                submission.status = "approved"
                submission.version += 1
                submission.updated_at = case.updated_at

        self.repository.atomic(complete)

    def _require_manual(self, tenant_id: str, case_id: str, worker_id: str, code: str) -> None:
        def require(state: StoreState) -> None:
            case = state.refresh_cases[(tenant_id, case_id)]
            if case.lease_owner != worker_id:
                raise ConflictError("refresh case lease was lost", code="worker_lease_lost")
            case.status = "manual_required"
            case.last_error_code = code
            case.lease_owner = None
            case.lease_until = None
            case.updated_at = utc_now()

        self.repository.atomic(require)
        self.alerts.append(f"m03_due_diligence_manual_required:{case_id}:{code}")

    def _fail(self, tenant_id: str, case_id: str, worker_id: str, code: str) -> bool:
        now = utc_now()

        def fail(state: StoreState) -> bool:
            case = state.refresh_cases[(tenant_id, case_id)]
            if case.lease_owner != worker_id:
                raise ConflictError("refresh case lease was lost", code="worker_lease_lost")
            case.attempt_count += 1
            case.last_error_code = code
            case.lease_owner = None
            case.lease_until = None
            if case.attempt_count >= self.max_attempts:
                case.status = "manual_required"
                case.updated_at = now
                return True
            case.next_attempt_at = now + timedelta(seconds=min(300, 2 ** case.attempt_count))
            case.updated_at = now
            return False

        is_manual = self.repository.atomic(fail)
        if is_manual:
            self.alerts.append(f"m03_due_diligence_manual_required:{case_id}:{code}")
        return is_manual

    def redrive(self, tenant_id: str, case_id: str, reason: str) -> None:
        """Manual requeue of a ``manual_required`` case; the runbook requires a non-empty reason."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("redrive reason must not be empty", field_name="reason")

        def requeue(state: StoreState) -> None:
            case = state.refresh_cases[(tenant_id, case_id)]
            if case.status != "manual_required":
                raise ConflictError("only manual_required cases can be redriven", code="refresh_case_not_manual")
            case.status = "pending"
            case.attempt_count = 0
            case.last_error_code = None
            case.lease_owner = None
            case.lease_until = None
            case.next_attempt_at = None
            case.updated_at = utc_now()

        self.repository.atomic(requeue)
        self.alerts.append(f"m03_due_diligence_redriven:{case_id}:{reason}")
