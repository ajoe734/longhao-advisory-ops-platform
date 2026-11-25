from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from modules.publication.domain import ActorContext, ConflictError, NotFoundError, new_id, utc_now
from modules.publication.repository import InMemoryRepository, StoreState
from modules.publication.service import PublicationService, PublishOutcome


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
    """Leases M08's own outbox (publication.created/withdrawn/corrected) to the shared bus."""

    def __init__(
        self,
        repository: InMemoryRepository,
        publisher: EventPublisherPort,
        *,
        max_attempts: int = 5,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.max_attempts = max_attempts
        self.clock = clock

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
        now = self.clock()

        def eligible(event) -> bool:
            if event.next_attempt_at is not None and event.next_attempt_at > now:
                return False
            if event.status in {"pending", "retry"}:
                return True
            # A "leased" row whose lease expired (worker crashed/stalled)
            # must be reclaimable, or it is stuck forever.
            return event.status == "leased" and event.lease_until is not None and event.lease_until <= now

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
        now = self.clock()

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


class PublicationWorker:
    """Leases ``publish_recommendation_version`` jobs with bounded retry, timeout,

    deadletter and safe redrive. All the publish decision logic (hash
    cross-check, expiry, audience resolution) lives in
    :class:`PublicationService.process_publish_job`; this worker only owns
    scheduling.
    """

    def __init__(self, service: PublicationService) -> None:
        self.service = service
        self.repository = service.repository
        self.clock = service._clock

    def lease(self, worker_id: str, limit: int = 20) -> list[tuple[str, str]]:
        now = self.clock()

        def eligible(job) -> bool:
            if job.kind != "publish_recommendation_version":
                return False
            if job.next_attempt_at is not None and job.next_attempt_at > now:
                return False
            if job.status in {"pending", "retry"}:
                return True
            # A "leased" job whose lease expired (worker crashed/stalled)
            # must be reclaimable, or it is stuck forever (UAT-M08-011).
            return job.status == "leased" and job.lease_until is not None and job.lease_until <= now

        def lease_jobs(state: StoreState) -> list[tuple[str, str]]:
            selected = [(tenant, job.id) for (tenant, _), job in state.jobs.items() if eligible(job)][:limit]
            for tenant, job_id in selected:
                job = state.jobs[(tenant, job_id)]
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_until = now + timedelta(seconds=30)
                job.updated_at = now
            return selected

        return self.repository.atomic(lease_jobs)

    def run_once(self, worker_id: str, limit: int = 20) -> WorkerResult:
        leased = self.lease(worker_id, limit)
        published = retried = dead = 0
        for tenant_id, job_id in leased:
            outcome = self.service.process_publish_job(job_id, tenant_id, worker_id)
            if outcome.status == "published":
                published += 1
            elif outcome.status == "retry":
                retried += 1
            elif outcome.status in {"dead_letter"}:
                dead += 1
        return WorkerResult(published, retried, dead)

    def redrive(self, tenant_id: str, job_id: str, actor: ActorContext, reason: str) -> PublishOutcome:
        if not reason.strip():
            raise ValueError("manual redrive requires a reason")
        # Reuses the same role gate as withdraw_publication: redrive overrides
        # an automated dead-letter decision and must stay compliance-ops-only.
        self.service._authorize(actor, {"compliance_supervisor"}, operation="redrive_publish_job")
        worker_id = f"redrive:{actor.actor_id}"
        now = self.clock()
        audit_correlation_id = new_id()

        def redrive_job(state: StoreState) -> None:
            job = state.jobs.get((tenant_id, job_id))
            if job is None:
                raise NotFoundError("publish job was not found")
            if job.status != "dead_letter":
                raise ConflictError("only dead-letter jobs can be redriven", code="job_not_dead_letter")
            job.status = "leased"
            job.lease_owner = worker_id
            job.lease_until = now + timedelta(seconds=30)
            job.next_attempt_at = None
            job.redrive_reason = reason[:500]
            job.updated_at = now
            self.service._audit_as(
                state, tenant_id, actor.actor_id, actor.purpose, "redrive_publish_job",
                "job", job.id, job.attempt_count, audit_correlation_id, "accepted", reason[:500],
            )

        self.repository.atomic(redrive_job)
        return self.service.process_publish_job(job_id, tenant_id, worker_id)
