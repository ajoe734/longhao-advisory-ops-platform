from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from modules.report_signature.domain import (
    AuditEvent,
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    new_id,
    utc_now,
)
from modules.report_signature.repository import InMemoryRepository, StoreState


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
    """Leases ``report.*`` outbox rows with bounded retry and no duplicate publish."""

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

        def eligible(event) -> bool:
            if event.status in {"pending", "retry"}:
                return event.next_attempt_at is None or event.next_attempt_at <= now
            if event.status == "leased":
                # A worker that crashed or was killed mid-publish never calls _complete/_fail,
                # so without this branch an expired lease would be stuck "leased" forever and
                # the event would never be reclaimed by any worker.
                return event.lease_until is not None and event.lease_until <= now
            return False

        def lease(state: StoreState) -> list[tuple[str, str]]:
            candidates = sorted(
                ((key, event) for key, event in state.outbox.items() if eligible(event)),
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

    def redrive(self, tenant_id: str, event_id: str, operator_id: str) -> None:
        """Explicit, audited redrive of a dead-lettered event (packet §4 "安全重驅").

        Dead-lettering is a deliberate stop after ``max_attempts`` unknown/retryable
        publish failures; nothing re-queues a dead-lettered event automatically, so an
        operator must take this action and it is audited like any other state change.
        """
        now = utc_now()

        def transition(state: StoreState) -> None:
            event = state.outbox.get((tenant_id, event_id))
            if not event:
                raise NotFoundError("outbox event was not found")
            if event.status != "dead_letter":
                raise ConflictError("only dead-lettered events can be redriven", code="outbox_event_not_dead_letter")
            event.status = "retry"
            event.attempt_count = 0
            event.next_attempt_at = now
            event.last_error_code = None
            state.audit_events.append(
                AuditEvent(
                    id=new_id(), tenant_id=tenant_id, actor_id=operator_id, purpose="operations",
                    action="m07.outbox.redrive", aggregate_type="outbox_event", aggregate_id=event_id,
                    aggregate_version=event.sequence_no, correlation_id=new_id(), result="redriven", reason_code=None,
                )
            )

        self.repository.atomic(transition)

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
class ReconcileResult:
    expired: int
    manual_review: int
    skipped: int = 0


class SignatureExpiryReconciler:
    """Expires signature requests whose provider challenge lapsed without a callback.

    ``provider簽章成功但callback遺失：以request id查證，不要求分析師盲目重簽`` — if the
    provider still reports anything other than its own ``pending`` state for a
    request we believe has expired, the request is routed to manual review
    instead of being silently re-signed or silently expired.
    """

    def __init__(self, repository: InMemoryRepository, provider) -> None:
        self.repository = repository
        self.provider = provider

    def run_once(self, worker_id: str, limit: int = 50) -> ReconcileResult:
        now = utc_now()

        def lease(state: StoreState) -> list[tuple[str, str]]:
            candidates = [
                key for key, request in state.signature_requests.items()
                if request.status == "pending" and request.expires_at <= now
            ][:limit]
            return candidates

        due = self.repository.atomic(lease)
        expired = manual = skipped = 0
        for tenant_id, request_id in due:
            request = self.repository.read(lambda state: state.signature_requests[(tenant_id, request_id)])
            if request.manual_review_flagged_at is not None:
                # Already parked for manual reconciliation; re-flagging on every poll
                # would duplicate the audit trail without the human having acted yet.
                continue
            try:
                lookup = self.provider.lookup(request.provider_transaction_id)
            except DependencyUnavailableError:
                # Provider lookup itself is unavailable — this is transient dependency
                # failure, not evidence either way; leave the request pending and retry
                # on the next poll rather than guessing expired or flagging manual review.
                skipped += 1
                continue
            if lookup not in {"pending", "not_found"}:
                self._flag_manual_review(tenant_id, request_id, worker_id)
                manual += 1
                continue
            self._expire(tenant_id, request_id, worker_id)
            expired += 1
        return ReconcileResult(expired, manual, skipped)

    def _expire(self, tenant_id: str, request_id: str, worker_id: str) -> None:
        now = utc_now()

        def transition(state: StoreState) -> None:
            request = state.signature_requests[(tenant_id, request_id)]
            if request.status != "pending":
                return
            request.status = "expired"
            request.version += 1
            request.updated_at = now
            version = state.versions.get((tenant_id, request.report_version_id))
            if version and version.status == "signature_pending":
                version.status = "ready_to_sign"
                version.version += 1
                version.updated_at = now

        self.repository.atomic(transition)

    def _flag_manual_review(self, tenant_id: str, request_id: str, worker_id: str) -> None:
        now = utc_now()

        def transition(state: StoreState) -> None:
            request = state.signature_requests[(tenant_id, request_id)]
            if request.manual_review_flagged_at is not None:
                return
            request.manual_review_flagged_at = now
            request.version += 1
            request.updated_at = now
            state.audit_events.append(
                AuditEvent(
                    id=new_id(), tenant_id=tenant_id, actor_id="00000000-0000-0000-0000-000000000002", purpose="operations",
                    action="m07.signature_reconciliation.manual_review_required",
                    aggregate_type="signature_request", aggregate_id=request.id, aggregate_version=request.version,
                    correlation_id=new_id(), result="requires_manual_reconciliation",
                    reason_code="provider_reports_non_pending_after_expiry",
                )
            )

        self.repository.atomic(transition)
