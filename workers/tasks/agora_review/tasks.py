from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from modules.agora_review.domain import ConflictError, DependencyUnavailableError, utc_now
from modules.agora_review.ports import ServantPort
from modules.agora_review.repository import InMemoryRepository, StoreState


class RetryableTaskError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EventPublisherPort(Protocol):
    is_fake: bool

    def publish(self, envelope: dict[str, Any]) -> str: ...


class LocalEventBus:
    """Local adapter; records synthetic envelopes and never reaches a network.

    Stands in for the cross-module bus delivering ``review.revision_received``
    to M07, which is NOT_CONNECTED in this repository.
    """

    is_fake = True

    def __init__(self) -> None:
        self.envelopes: dict[str, dict[str, Any]] = {}
        self.failure_mode: str | None = None

    def publish(self, envelope: dict[str, Any]) -> str:
        if self.failure_mode == "timeout":
            raise RetryableTaskError("local_bus_timeout")
        self.envelopes.setdefault(envelope["event_id"], envelope)
        return "accepted"


class ServantDispatchPublisher:
    """Turns a durable ``review.revision_requested`` outbox event into the one
    external Servant job submission it represents, then forwards
    ``review.revision_received`` to the (NOT_CONNECTED) M07 bus.

    ``review.revision_requested`` is self-consumed by M06 per the event
    registry: the HTTP command only persists the request; this is the async
    step that actually calls the external adapter so the command transaction
    never waits on the network.
    """

    def __init__(self, repository: InMemoryRepository, servant: ServantPort, downstream_bus: EventPublisherPort) -> None:
        self.repository = repository
        self.servant = servant
        self.downstream_bus = downstream_bus

    @property
    def is_fake(self) -> bool:
        return getattr(self.servant, "is_fake", True)

    def publish(self, envelope: dict[str, Any]) -> str:
        if envelope["event_type"] == "review.revision_requested":
            return self._dispatch_revision(envelope)
        if envelope["event_type"] == "review.revision_received":
            return self.downstream_bus.publish(envelope)
        return "accepted"

    def _dispatch_revision(self, envelope: dict[str, Any]) -> str:
        tenant_id = envelope["tenant_id"]
        payload = envelope["payload"]
        revision_id = payload["revision_id"]

        def read_revision(state: StoreState):
            revision = state.revisions[(tenant_id, revision_id)]
            return revision.external_job_ref, revision.status

        job_ref, status = self.repository.read(read_revision)
        if status not in {"requested", "accepted"}:
            return "accepted"  # already progressed by a prior tick; nothing to resubmit

        probe = self.servant.get_revision_status(job_ref)
        if probe.status == "unknown":
            try:
                self.servant.submit_revision(
                    job_ref,
                    {
                        "revision_id": revision_id,
                        "conversation_id": payload["conversation_id"],
                        "base_content_sha256": payload["base_content_sha256"],
                    },
                )
            except DependencyUnavailableError as exc:
                raise RetryableTaskError(exc.code) from exc

        def mark_running(state: StoreState) -> None:
            revision = state.revisions[(tenant_id, revision_id)]
            if revision.status in {"requested", "accepted"}:
                revision.status = "running"
                revision.version += 1
                revision.updated_at = utc_now()

        self.repository.atomic(mark_running)
        return "accepted"


@dataclass(frozen=True)
class WorkerResult:
    processed: int
    retried: int
    dead_lettered: int


class OutboxPublisher:
    """Leases ``review.*`` outbox rows with bounded retry; never double-publishes.

    A single ``publisher.publish(envelope)`` call drives both the Servant job
    dispatch for ``review.revision_requested`` and the downstream delivery of
    ``review.revision_received``, so one lease/retry/dead-letter mechanism
    covers both event types uniformly.
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

    def redrive(self, tenant_id: str, event_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("manual redrive requires a non-empty reason")

        def apply(state: StoreState) -> None:
            event = state.outbox[(tenant_id, event_id)]
            if event.status != "dead_letter":
                raise ConflictError("only dead-letter events can be redriven", code="event_not_dead_letter")
            event.status = "retry"
            event.next_attempt_at = utc_now()
            event.lease_owner = None
            event.lease_until = None

        self.repository.atomic(apply)
