from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Any, Protocol

from modules.pantheon_intake.domain import ConflictError, DependencyUnavailableError, Job, NotFoundError, utc_now
from modules.pantheon_intake.ports import SsrfBlockedError, SsrfSafeEvidenceFetcher
from modules.pantheon_intake.repository import InMemoryRepository, StoreState
from modules.pantheon_intake.service import PantheonIntakeService


class RetryableTaskError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EvidenceSourcePort(Protocol):
    """Resolves the https URL this platform's own bridge exposes for a candidate's snapshot."""

    is_fake: bool

    def resolve_url(self, tenant_id: str, candidate_id: str) -> str: ...


class FakeEvidenceSource:
    is_fake = True

    def __init__(self) -> None:
        self._urls: dict[tuple[str, str], str] = {}

    def set_url(self, tenant_id: str, candidate_id: str, url: str) -> None:
        self._urls[(tenant_id, candidate_id)] = url

    def resolve_url(self, tenant_id: str, candidate_id: str) -> str:
        url = self._urls.get((tenant_id, candidate_id))
        if url is None:
            raise DependencyUnavailableError("pantheon source has no registered snapshot URL", code="pantheon_source_unavailable")
        return url


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


class TaskRunner:
    """Lease and run M05 operational jobs with bounded retry and safe redrive."""

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

    def complete(self, tenant_id: str, job_id: str, worker_id: str) -> None:
        def finish(state: StoreState) -> None:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id or job.status != "leased":
                raise ConflictError("job lease was lost", code="worker_lease_lost")
            job.status = "completed"
            job.lease_owner = None
            job.lease_until = None
            job.last_error_code = None
            job.updated_at = utc_now()

        self.repository.atomic(finish)

    def fail(self, tenant_id: str, job_id: str, worker_id: str, code: str) -> bool:
        now = utc_now()

        def mark_failed(state: StoreState) -> bool:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id or job.status != "leased":
                raise ConflictError("job lease was lost", code="worker_lease_lost")
            job.attempt_count += 1
            job.last_error_code = code
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = now
            if job.attempt_count >= job.max_attempts:
                job.status = "dead_letter"
                return True
            job.status = "retry"
            job.next_attempt_at = now + timedelta(seconds=min(300, 2 ** job.attempt_count))
            return False

        return self.repository.atomic(mark_failed)

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

    def dead_letter_jobs(self, tenant_id: str) -> list[Job]:
        return self.repository.read(
            lambda state: [job for job in state.jobs.values() if job.tenant_id == tenant_id and job.status == "dead_letter"]
        )


class CandidateVerificationWorker:
    """Leases ``verify_candidate_evidence`` jobs, fetches the snapshot through an
    SSRF-safe transport, and reports the outcome to ``validate_research_candidate``.

    Fetch failures retry with backoff; exhaustion dead-letters the job without
    discarding the candidate (REQ-M05-012) -- ``received_at`` and the case ID are
    never recomputed once a successful attempt lands.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        service: PantheonIntakeService,
        evidence_source: EvidenceSourcePort,
        fetcher: SsrfSafeEvidenceFetcher,
        *,
        service_actor_context: Any,
    ) -> None:
        self.repository = repository
        self.service = service
        self.evidence_source = evidence_source
        self.fetcher = fetcher
        self.service_context = service_actor_context
        self.runner = TaskRunner(repository)

    def run_once(self, worker_id: str, limit: int = 20) -> WorkerResult:
        jobs = [job for job in self.runner.lease(worker_id, limit) if job.kind == "verify_candidate_evidence"]
        processed = retried = dead = 0
        for job in jobs:
            try:
                self._process(job)
                self.runner.complete(job.tenant_id, job.id, worker_id)
                processed += 1
            except DependencyUnavailableError as exc:
                is_dead = self.runner.fail(job.tenant_id, job.id, worker_id, exc.code)
                dead += int(is_dead)
                retried += int(not is_dead)
            except SsrfBlockedError as exc:
                self._report_blocked(job)
                self._force_dead_letter(job.tenant_id, job.id, worker_id, exc.code)
                dead += 1
        return WorkerResult(processed, retried, dead)

    def _process(self, job: Job) -> None:
        candidate_id = job.payload["candidate_id"]
        candidate = self.repository.read(lambda state: state.candidates.get((job.tenant_id, candidate_id)))
        if candidate is None:
            raise NotFoundError("candidate referenced by job was not found")
        url = self.evidence_source.resolve_url(job.tenant_id, candidate_id)
        body = self.fetcher.fetch(url)
        observed_sha256 = sha256(body).hexdigest()
        self.service.validate_research_candidate(
            self.service_context,
            candidate_id,
            {
                "observed_content_sha256": observed_sha256,
                "evidence_access_confirmed": True,
                "license_scope_confirmed": True,
                "source_snapshot_asset_id": candidate.snapshot_asset_id,
            },
            expected_version=candidate.version,
            idempotency_key=f"verify:{job.id}",
        )

    def _report_blocked(self, job: Job) -> None:
        candidate_id = job.payload["candidate_id"]
        candidate = self.repository.read(lambda state: state.candidates.get((job.tenant_id, candidate_id)))
        if candidate is None:
            return
        self.service.validate_research_candidate(
            self.service_context,
            candidate_id,
            {
                "observed_content_sha256": candidate.content_sha256,
                "evidence_access_confirmed": False,
                "license_scope_confirmed": False,
                "source_snapshot_asset_id": candidate.snapshot_asset_id,
            },
            expected_version=candidate.version,
            idempotency_key=f"verify-blocked:{job.id}",
        )

    def _force_dead_letter(self, tenant_id: str, job_id: str, worker_id: str, code: str) -> None:
        def mark(state: StoreState) -> None:
            stored = state.jobs[(tenant_id, job_id)]
            if stored.lease_owner != worker_id or stored.status != "leased":
                raise ConflictError("job lease was lost", code="worker_lease_lost")
            stored.status = "dead_letter"
            stored.attempt_count += 1
            stored.last_error_code = code
            stored.lease_owner = None
            stored.lease_until = None
            stored.updated_at = utc_now()

        self.repository.atomic(mark)
