from __future__ import annotations

from longhao_platform.event_ordering import is_sequence_gap

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from modules.agora_review.domain import (
    AuditEvent,
    CandidateProjection,
    ConflictError,
    InboxReceipt,
    ReviewCase,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.agora_review.repository import InMemoryRepository, StoreState


_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "research.candidate_validated": (
        "M05",
        "research_candidate",
        {"candidate_id", "review_case_id", "artifact_content_sha256", "validation_result"},
    ),
    "research.candidate_superseded": (
        "M05",
        "research_candidate",
        {"candidate_id", "replacement_candidate_id", "previous_artifact_sha256", "replacement_artifact_sha256"},
    ),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M06EventConsumer:
    def __init__(self, repository: InMemoryRepository, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.repository = repository
        self._clock = clock

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M06", envelope["producer"], event_id)

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
            if is_sequence_gap("M06", envelope, last_sequence):
                state.inbox[key] = self._receipt(envelope, "retry", "sequence_gap", None)
                return ConsumeResult("retry", "sequence_gap")
            if envelope["sequence_no"] <= last_sequence:
                state.inbox[key] = self._receipt(envelope, "applied", "stale_sequence_ignored", self._clock())
                return ConsumeResult("applied", "stale_sequence_ignored")

            result_code = self._apply(state, envelope)
            applied_at = self._clock()
            state.inbox[key] = self._receipt(envelope, "applied", result_code, applied_at)
            state.audit_events.append(
                AuditEvent(
                    id=new_id(),
                    tenant_id=tenant_id,
                    actor_id=None,
                    purpose="event_projection",
                    action=f"m06.consume.{envelope['event_type']}",
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
        if event_type == "research.candidate_validated":
            return self._apply_candidate_validated(state, tenant_id, payload)
        if event_type == "research.candidate_superseded":
            return self._apply_candidate_superseded(state, tenant_id, payload)
        raise ValidationError("event type is not consumed by M06", code="unsupported_event_type")

    def _apply_candidate_validated(self, state: StoreState, tenant_id: str, payload: dict[str, Any]) -> str:
        candidate_id = payload["candidate_id"]
        review_case_id = payload["review_case_id"]
        if (tenant_id, review_case_id) in state.cases:
            return "case_already_exists"
        if payload["validation_result"] != "accepted":
            return f"candidate_not_accepted:{payload['validation_result']}"
        now = self._clock()
        case = ReviewCase(
            id=review_case_id,
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            artifact_ref={"system": "pantheon", "external_id": candidate_id, "kind": "research_candidate"},
            content_sha256=payload["artifact_content_sha256"],
            created_at=now,
            updated_at=now,
        )
        state.cases[(tenant_id, case.id)] = case
        state.candidates[(tenant_id, candidate_id)] = CandidateProjection(
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            review_case_id=review_case_id,
            content_sha256=payload["artifact_content_sha256"],
            validation_result=payload["validation_result"],
            received_at=now,
        )
        return "case_created"

    def _apply_candidate_superseded(self, state: StoreState, tenant_id: str, payload: dict[str, Any]) -> str:
        candidate_id = payload["candidate_id"]
        projection = state.candidates.get((tenant_id, candidate_id))
        if projection is None:
            return "candidate_not_tracked"
        case = state.cases.get((tenant_id, projection.review_case_id))
        if case is None:
            return "case_not_found"
        if case.stale:
            return "case_already_stale"
        case.stale = True
        if case.status == "ready_to_sign":
            case.status = "reviewing"
        case.version += 1
        case.updated_at = self._clock()
        projection.superseded_by = payload["replacement_candidate_id"]
        return "case_marked_stale"

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
            raise ValidationError("event type is not consumed by M06", code="unsupported_event_type")
        producer, aggregate_type, payload_fields = contract
        if envelope["schema_version"] != "1.0.0" or envelope["producer"] != producer or envelope["aggregate_type"] != aggregate_type:
            raise ValidationError("event contract metadata is invalid", code="event_schema_invalid")
        if set(envelope["payload"]) != payload_fields:
            raise ValidationError("event payload fields do not match schema", code="event_schema_invalid")
        if canonical_hash(envelope["payload"]) != envelope["payload_sha256"]:
            raise ConflictError("event payload hash is invalid", code="event_payload_hash_invalid")
        if not isinstance(envelope["sequence_no"], int) or envelope["sequence_no"] < 1:
            raise ValidationError("sequence_no must be positive", field_name="sequence_no")

    @staticmethod
    def _receipt(envelope: dict[str, Any], status: str, code: str, applied_at: datetime | None):
        return InboxReceipt(
            tenant_id=envelope["tenant_id"],
            consumer="M06",
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
