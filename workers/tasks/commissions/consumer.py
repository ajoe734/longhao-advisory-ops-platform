from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.commissions.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    PendingAdjustmentDraft,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.commissions.repository import InMemoryRepository, StoreState
from modules.commissions.service import SERVICE_ACTOR_ID


_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "crm.conversion_decided": (
        "M10",
        "conversion",
        {"conversion_id", "contract_id", "decision", "approved_salesperson_id", "evidence_snapshot_id"},
    ),
    "ownership.assignment_changed": (
        "M11",
        "maintenance_assignment",
        {"assignment_id", "customer_id", "salesperson_id", "effective_at", "state"},
    ),
    "finance.revenue_recognized": (
        "M14",
        "revenue_recognition",
        {"recognition_id", "contract_id", "contract_service_id", "policy_version", "service_period_start", "service_period_end", "posted_journal_id"},
    ),
    "finance.refund_confirmed": (
        "M14",
        "refund",
        {"refund_id", "contract_id", "original_payment_id", "allocation_snapshot_id", "confirmed_at"},
    ),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class CommissionEventConsumer:
    """Consumes M10/M11/M14 projections M12 depends on but does not own.

    ``finance.refund_confirmed`` is the one event with a concrete local effect:
    it prepares a ``PendingAdjustmentDraft`` (service-actor maker) so the later
    human ``create_commission_adjustment`` call acts as a distinct checker —
    see the M12 README's "adjustment maker/checker" note for the fallback when
    no such draft exists.
    """

    def __init__(self, repository: InMemoryRepository, revenue_port: Any) -> None:
        self.repository = repository
        self.revenue_port = revenue_port

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M12", envelope["producer"], event_id)

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
                    id=new_id(),
                    tenant_id=tenant_id,
                    actor_id=SERVICE_ACTOR_ID,
                    purpose="event_projection",
                    action=f"m12.consume.{envelope['event_type']}",
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
        if event_type == "finance.refund_confirmed":
            fact = self.revenue_port.refund_allocation(tenant_id, payload["refund_id"])
            if fact is None:
                return "refund_allocation_not_yet_available"
            dedupe_key = canonical_hash({"original_entry_id": fact.original_entry_id, "refund_id": fact.refund_id})
            draft_key = (tenant_id, dedupe_key)
            if draft_key in state.pending_adjustment_drafts or draft_key in {
                (tenant_id, adj.dedupe_key) for adj in state.adjustments.values()
            }:
                return "pending_adjustment_already_drafted"
            state.pending_adjustment_drafts[draft_key] = PendingAdjustmentDraft(
                tenant_id=tenant_id,
                original_entry_id=fact.original_entry_id,
                refund_id=fact.refund_id,
                dedupe_key=dedupe_key,
                prepared_by=SERVICE_ACTOR_ID,
            )
            return "pending_adjustment_drafted"
        if event_type == "crm.conversion_decided":
            return f"conversion_projection_noted:{payload['decision']}"
        if event_type == "ownership.assignment_changed":
            return f"assignment_projection_noted:{payload['state']}"
        if event_type == "finance.revenue_recognized":
            return "revenue_recognition_noted"
        raise ValidationError("event type is not consumed by M12", code="unsupported_event_type")

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
            raise ValidationError("event type is not consumed by M12", code="unsupported_event_type")
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
            tenant_id=envelope["tenant_id"],
            consumer="M12",
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
