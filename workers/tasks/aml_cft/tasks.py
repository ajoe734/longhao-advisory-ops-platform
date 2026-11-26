from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from modules.aml_cft.domain import (
    AuditEvent,
    BusinessClearance,
    ConflictError,
    DependencyUnavailableError,
    Job,
    OutboxEvent,
    SYSTEM_ACTOR_ID,
    new_id,
    utc_now,
    wire_time,
)
from modules.aml_cft.ports import ScreeningProviderPort
from modules.aml_cft.repository import InMemoryRepository, StoreState
from modules.aml_cft.telemetry import M04Telemetry


_PENDING_VALIDITY = timedelta(days=1)


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
                    if (
                        event.status in {"pending", "retry"}
                        and (event.next_attempt_at is None or event.next_attempt_at <= now)
                    )
                    # A worker that died mid-lease never completes or fails the
                    # row: once its lease expires, a later worker must be able
                    # to safely redrive it instead of leaving it stuck forever.
                    or (event.status == "leased" and event.lease_until is not None and event.lease_until <= now)
                ),
                key=lambda item: (item[1].emitted_at, item[1].sequence_no),
            )[:limit]
            keys: list[tuple[str, str]] = []
            for key, event in candidates:
                if event.status == "leased":
                    event.last_error_code = "lease_expired_redrive"
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

    def dead_letters(self, tenant_id: str) -> list[OutboxEvent]:
        """Operator-facing query: outbox rows that exhausted retries and need a decision."""
        return self.repository.read(
            lambda state: sorted(
                (event for (tenant, _), event in state.outbox.items() if tenant == tenant_id and event.status == "dead_letter"),
                key=lambda event: (event.emitted_at, event.sequence_no),
            )
        )

    def redrive(self, tenant_id: str, event_id: str, *, operator_id: str, reason_code: str) -> None:
        """Safe operator redrive: only ever moves a row that is actually ``dead_letter``.

        An unknown/ambiguous external publish outcome must stay dead-lettered
        for manual reconciliation rather than being blindly re-sent.
        """
        now = utc_now()

        def apply(state: StoreState) -> None:
            event = state.outbox[(tenant_id, event_id)]
            if event.status != "dead_letter":
                raise ConflictError("outbox row is not dead-lettered", code="not_dead_letter")
            event.status = "retry"
            event.attempt_count = 0
            event.next_attempt_at = now
            event.redrive_reason = reason_code
            state.audit_events.append(AuditEvent(
                id=new_id(), tenant_id=tenant_id, actor_id=operator_id, purpose="operator_redrive",
                action="m04.outbox_redrive", aggregate_type=event.aggregate_type, aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version, correlation_id=new_id(), result="redriven",
                reason_code=reason_code, recorded_at=now,
            ))

        self.repository.atomic(apply)


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
                and (
                    (
                        job.status in {"pending", "retry"}
                        and (job.next_attempt_at is None or job.next_attempt_at <= now)
                    )
                    # Reclaim a dispatch job stuck under a dead worker's expired
                    # lease instead of leaving it "leased" forever.
                    or (job.status == "leased" and job.lease_until is not None and job.lease_until <= now)
                )
            ][:limit]
            for tenant, job_id in selected:
                job = state.jobs[(tenant, job_id)]
                if job.status == "leased":
                    job.redrive_reason = "lease_expired_redrive"
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
        now = utc_now()

        def apply(state: StoreState) -> None:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id:
                raise ConflictError("dispatch job lease was lost", code="worker_lease_lost")
            job.status = "dead_letter"
            job.last_error_code = "screening_provider_timeout"
            job.updated_at = now
            screening = state.screenings[(tenant_id, screening_id)]
            if screening.state != "pending":
                # Already resolved by another path (e.g. a duplicate dispatch
                # race or a manual override): timeout handling must never
                # regress a screening that has moved past "pending", nor
                # publish a second manual-required clearance for it.
                return
            screening.state = "error"
            screening.manual_required = True
            screening.version += 1
            screening.updated_at = now

            # A provider timeout must never leave a stale earlier "approved"
            # clearance looking current: force a fresh pending/manual-required
            # clearance and publish it, exactly as the synchronous error path
            # in AmlCftService.receive_aml_screening does.
            clearance = BusinessClearance(
                id=new_id(), tenant_id=tenant_id,
                version=1 + len(state.clearance_by_application.get((tenant_id, screening.application_id), [])),
                created_at=now, updated_at=now, application_id=screening.application_id, customer_id=screening.customer_id,
                decision="pending", policy_version=screening.policy_version, decided_by=SYSTEM_ACTOR_ID,
                decided_at=now, valid_until=now + _PENDING_VALIDITY, safe_reason_code="manual_required", case_id=None,
            )
            state.clearances[(tenant_id, clearance.id)] = clearance
            state.clearance_by_application.setdefault((tenant_id, screening.application_id), []).append(clearance.id)

            payload = {
                "clearance_id": clearance.id, "application_id": clearance.application_id,
                "decision": clearance.decision, "expires_at": wire_time(clearance.valid_until),
            }
            correlation_id = new_id()
            sequence = 1 + max(
                (
                    event.sequence_no for (tenant, _), event in state.outbox.items()
                    if tenant == tenant_id and event.aggregate_type == "business_clearance" and event.aggregate_id == clearance.id
                ),
                default=0,
            )
            event = OutboxEvent(
                event_id=new_id(), event_type="business_clearance.changed", tenant_id=tenant_id,
                aggregate_type="business_clearance", aggregate_id=clearance.id, aggregate_version=clearance.version,
                sequence_no=sequence, correlation_id=correlation_id, causation_id=None,
                idempotency_key=f"dispatch_timeout:{job_id}:business_clearance.changed:{clearance.id}",
                classification="customer_confidential", payload=payload, occurred_at=now, emitted_at=now,
            )
            state.outbox[(tenant_id, event.event_id)] = event

            state.audit_events.append(AuditEvent(
                id=new_id(), tenant_id=tenant_id, actor_id=SYSTEM_ACTOR_ID, purpose="automated_dispatch",
                action="m04.screening_provider_timeout", aggregate_type="aml_screening", aggregate_id=screening.id,
                aggregate_version=screening.version, correlation_id=correlation_id, result="manual_required",
                reason_code="screening_provider_timeout", recorded_at=now,
            ))

        self.repository.atomic(apply)

    def dead_letters(self, tenant_id: str) -> list[Job]:
        """Operator-facing query: dispatch jobs that exhausted retries and need a decision."""
        return self.repository.read(
            lambda state: sorted(
                (job for (tenant, _), job in state.jobs.items() if tenant == tenant_id and job.status == "dead_letter"),
                key=lambda job: job.updated_at,
            )
        )

    def redrive(self, tenant_id: str, job_id: str, *, operator_id: str, reason_code: str) -> None:
        """Safe operator redrive: only ever moves a job that is actually ``dead_letter``.

        An unknown/ambiguous provider outcome must stay dead-lettered for
        manual reconciliation rather than being blindly re-dispatched.
        """
        now = utc_now()

        def apply(state: StoreState) -> None:
            job = state.jobs[(tenant_id, job_id)]
            if job.status != "dead_letter":
                raise ConflictError("dispatch job is not dead-lettered", code="not_dead_letter")
            job.status = "pending"
            job.attempt_count = 0
            job.next_attempt_at = now
            job.redrive_reason = reason_code
            job.updated_at = now
            state.audit_events.append(AuditEvent(
                id=new_id(), tenant_id=tenant_id, actor_id=operator_id, purpose="operator_redrive",
                action="m04.dispatch_job_redrive", aggregate_type="aml_screening",
                aggregate_id=job.payload.get("screening_id"), aggregate_version=None, correlation_id=new_id(),
                result="redriven", reason_code=reason_code, recorded_at=now,
            ))

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
