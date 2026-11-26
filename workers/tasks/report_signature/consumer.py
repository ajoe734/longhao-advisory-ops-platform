from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from modules.report_signature.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    NotFoundError,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.report_signature.repository import InMemoryRepository, StoreState


_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "research.candidate_superseded": (
        "M05",
        "research_candidate",
        {"candidate_id", "replacement_candidate_id", "previous_artifact_sha256", "replacement_artifact_sha256"},
    ),
    "review.revision_received": (
        "M06",
        "review_case",
        {"review_case_id", "revision_id", "candidate_id", "result_content_sha256", "stale"},
    ),
    "report.version_frozen": (
        "M07",
        "report_version",
        {"report_version_id", "report_content_sha256", "bundle_manifest_sha256", "review_decision_ids"},
    ),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M07EventConsumer:
    """Consumes M05/M06 staleness signals and M07's own ``report.version_frozen``.

    A superseded or stale source marks ``source_candidate_id`` so
    ``freeze_report_version``/``request_analyst_signature`` refuse to sign
    against data that a later source has invalidated (REQ-M07-007). The
    self-consumed ``report.version_frozen`` event is a durability check: the
    frozen hashes recorded in the event must still match the stored version.
    """

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M07", envelope["producer"], event_id)

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
            if envelope["sequence_no"] > last_sequence + 1:
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
                    id=new_id(), tenant_id=tenant_id, actor_id="00000000-0000-0000-0000-000000000001",
                    purpose="event_projection", action=f"m07.consume.{envelope['event_type']}",
                    aggregate_type=envelope["aggregate_type"], aggregate_id=envelope["aggregate_id"],
                    aggregate_version=envelope["aggregate_version"], correlation_id=envelope["correlation_id"],
                    result=result_code, reason_code=None, recorded_at=applied_at,
                )
            )
            return ConsumeResult("applied", result_code)

        return self.repository.atomic(transaction)

    def _apply(self, state: StoreState, envelope: dict[str, Any]) -> str:
        event_type = envelope["event_type"]
        payload = envelope["payload"]
        tenant_id = envelope["tenant_id"]
        if event_type == "research.candidate_superseded":
            state.superseded_candidates.add((tenant_id, payload["candidate_id"]))
            return "candidate_marked_superseded"
        if event_type == "review.revision_received":
            if payload["stale"]:
                state.superseded_candidates.add((tenant_id, payload["candidate_id"]))
                return "review_marked_stale"
            return "revision_received_not_stale"
        if event_type == "report.version_frozen":
            version = state.versions.get((tenant_id, payload["report_version_id"]))
            if not version:
                raise NotFoundError("report version referenced by event was not found")
            if version.report_content_sha256 != payload["report_content_sha256"] or version.bundle_manifest_sha256 != payload["bundle_manifest_sha256"]:
                raise ConflictError("frozen projection payload does not match stored version", code="frozen_projection_mismatch")
            return "frozen_projection_confirmed"
        raise ValidationError("event type is not consumed by M07", code="unsupported_event_type")

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
            raise ValidationError("event type is not consumed by M07", code="unsupported_event_type")
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
    def _receipt(envelope: dict[str, Any], status: str, code: str, applied_at: datetime | None) -> InboxReceipt:
        return InboxReceipt(
            tenant_id=envelope["tenant_id"], consumer="M07", producer=envelope["producer"],
            event_id=envelope["event_id"], event_type=envelope["event_type"], payload_sha256=envelope["payload_sha256"],
            aggregate_id=envelope["aggregate_id"], sequence_no=envelope["sequence_no"], status=status,
            result_code=code, applied_at=applied_at,
        )

    @staticmethod
    def _time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValidationError("event time must include timezone", code="event_schema_invalid")
        return parsed.astimezone(timezone.utc)
