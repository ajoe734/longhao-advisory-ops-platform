from __future__ import annotations

from longhao_platform.event_ordering import is_sequence_gap

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.pantheon_intake.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    Job,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.pantheon_intake.repository import InMemoryRepository, StoreState


# research.candidate_received is produced AND consumed by M05 itself (events/registry.json):
# the command transaction persists the candidate and emits the event; this consumer turns
# that durable fact into a leaseable evidence-verification job, decoupling the synchronous
# ingest response from the asynchronous Pantheon fetch.
_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "research.candidate_received": (
        "M05",
        "research_candidate",
        {"candidate_id", "external_event_id", "artifact_content_sha256", "origin"},
    ),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class PantheonIntakeEventConsumer:
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M05", envelope["producer"], event_id)

        def transaction(state: StoreState) -> ConsumeResult:
            existing = state.inbox.get(key)
            if existing:
                if existing.payload_sha256 != envelope["payload_sha256"]:
                    raise ConflictError("event_id was replayed with a different payload", code="inbox_payload_mismatch")
                if existing.status != "retry":
                    return ConsumeResult("duplicate", existing.result_code)

            last_sequence = max(
                (
                    receipt.sequence_no for receipt in state.inbox.values()
                    if receipt.tenant_id == tenant_id
                    and receipt.producer == envelope["producer"]
                    and receipt.aggregate_id == envelope["aggregate_id"]
                    and receipt.status == "applied"
                ),
                default=0,
            )
            if is_sequence_gap("M05", envelope, last_sequence):
                state.inbox[key] = self._receipt(envelope, "retry", "sequence_gap", None)
                return ConsumeResult("retry", "sequence_gap")
            if envelope["sequence_no"] <= last_sequence:
                state.inbox[key] = self._receipt(envelope, "applied", "stale_sequence_ignored", utc_now())
                return ConsumeResult("applied", "stale_sequence_ignored")

            result_code = self._apply(state, envelope)
            applied_at = utc_now()
            state.inbox[key] = self._receipt(envelope, "applied", result_code, applied_at)
            state.audit_events.append(
                AuditEvent(
                    id=new_id(),
                    tenant_id=tenant_id,
                    actor_id="00000000-0000-0000-0000-000000000001",
                    purpose="event_projection",
                    action=f"m05.consume.{envelope['event_type']}",
                    aggregate_type=envelope["aggregate_type"],
                    aggregate_id=envelope["aggregate_id"],
                    aggregate_version=envelope["aggregate_version"],
                    correlation_id=envelope["correlation_id"],
                    result=result_code,
                    reason_code=None,
                    recorded_at=applied_at,
                )
            )
            return ConsumeResult("applied", result_code)

        return self.repository.atomic(transaction)

    def _apply(self, state: StoreState, envelope: dict[str, Any]) -> str:
        event_type = envelope["event_type"]
        payload = envelope["payload"]
        tenant_id = envelope["tenant_id"]
        if event_type == "research.candidate_received":
            existing_job = next(
                (
                    job for (tenant, _), job in state.jobs.items()
                    if tenant == tenant_id
                    and job.kind == "verify_candidate_evidence"
                    and job.payload.get("candidate_id") == payload["candidate_id"]
                ),
                None,
            )
            if existing_job:
                return "verification_job_already_queued"
            job = Job(
                id=new_id(),
                tenant_id=tenant_id,
                kind="verify_candidate_evidence",
                payload={"candidate_id": payload["candidate_id"], "external_event_id": payload["external_event_id"]},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            state.jobs[(tenant_id, job.id)] = job
            return "verification_job_queued"
        raise ValidationError("event type is not consumed by M05", code="unsupported_event_type")

    @staticmethod
    def _validate_envelope(envelope: dict[str, Any]) -> None:
        required = {
            "event_id", "event_type", "schema_version", "tenant_id", "producer", "aggregate_type",
            "aggregate_id", "aggregate_version", "sequence_no", "occurred_at", "emitted_at", "correlation_id",
            "causation_id", "idempotency_key", "classification", "payload_sha256", "payload",
        }
        if set(envelope) != required:
            raise ValidationError("event envelope fields do not match schema", code="event_schema_invalid")
        contract = _CONTRACTS.get(envelope["event_type"])
        if not contract:
            raise ValidationError("event type is not consumed by M05", code="unsupported_event_type")
        producer, aggregate_type, payload_fields = contract
        if envelope["schema_version"] != "1.0.0" or envelope["producer"] != producer or envelope["aggregate_type"] != aggregate_type:
            raise ValidationError("event contract metadata is invalid", code="event_schema_invalid")
        if set(envelope["payload"]) != payload_fields:
            raise ValidationError("event payload fields do not match schema", code="event_schema_invalid")
        if canonical_hash(envelope["payload"]) != envelope["payload_sha256"]:
            raise ConflictError("event payload hash is invalid", code="event_payload_hash_invalid")
        if envelope["aggregate_id"] != envelope["payload"].get("candidate_id", envelope["aggregate_id"]):
            raise ValidationError("aggregate_id does not match candidate_id", code="event_aggregate_mismatch")
        if not isinstance(envelope["sequence_no"], int) or envelope["sequence_no"] < 1:
            raise ValidationError("sequence_no must be positive", field_name="sequence_no")

    @staticmethod
    def _receipt(envelope: dict[str, Any], status: str, code: str, applied_at: datetime | None) -> InboxReceipt:
        return InboxReceipt(
            tenant_id=envelope["tenant_id"],
            consumer="M05",
            producer=envelope["producer"],
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            payload_sha256=envelope["payload_sha256"],
            aggregate_id=envelope["aggregate_id"],
            sequence_no=envelope["sequence_no"],
            status=status,
            result_code=code,
            applied_at=applied_at,
        )
