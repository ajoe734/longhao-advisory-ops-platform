from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from modules.service_cases.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    SlaEvent,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.service_cases.repository import InMemoryRepository, StoreState


_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "customer.created": ("M01", "customer", {"customer_id", "marketing_attribution_id"}),
    "publication.withdrawn": ("M08", "publication", {"publication_id", "withdrawal_record_id", "effective_at", "notify_recipients"}),
    "publication.corrected": ("M08", "publication", {"publication_id", "replacement_publication_id", "replacement_report_version_id"}),
    "delivery.status_changed": ("M09", "delivery", {"delivery_id", "publication_id", "customer_id", "channel", "state", "receipt_id", "read_at"}),
    "ownership.assignment_changed": ("M11", "maintenance_assignment", {"assignment_id", "customer_id", "salesperson_id", "effective_at", "state"}),
    "finance.refund_confirmed": ("M14", "refund", {"refund_id", "contract_id", "original_payment_id", "allocation_snapshot_id", "confirmed_at"}),
    "support.case_opened": ("M13", "service_case", {"case_id", "customer_id", "category", "priority"}),
    "support.case_escalated": ("M13", "service_case", {"case_id", "target_queue", "due_at"}),
    "support.response_status_changed": ("M13", "service_response", {"response_id", "case_id", "state", "receipt_id"}),
}

_ASSIGNMENT_CLEARED_STATES = {"released", "departure"}
_ASSIGNMENT_IGNORED_STATES = {"superseded"}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M13EventConsumer:
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M13", envelope["producer"], event_id)

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
                    purpose="event_projection", action=f"m13.consume.{envelope['event_type']}",
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

        if event_type in {
            "customer.created", "delivery.status_changed",
            "support.case_opened", "support.case_escalated", "support.response_status_changed",
        }:
            # M13 does not maintain its own customer or delivery projection and
            # already applies case/response escalation state synchronously
            # inside the producing command's own transaction; this consumer
            # path exists for contract completeness and future async readers.
            return "acknowledged_no_projection"

        if event_type == "publication.withdrawn":
            changed = 0
            for (tenant, _), case in state.cases.items():
                if tenant == tenant_id and case.publication_id == payload["publication_id"] and case.status not in {"resolved", "closed"}:
                    sla_event_id = new_id()
                    state.sla_events[(tenant_id, sla_event_id)] = SlaEvent(
                        id=sla_event_id, tenant_id=tenant_id, case_id=case.id, action="escalated",
                        occurred_at=utc_now(), actor_id="00000000-0000-0000-0000-000000000001",
                        reason_code="publication_withdrawn",
                    )
                    changed += 1
            return f"publication_withdrawal_noted:{changed}"

        if event_type == "publication.corrected":
            changed = 0
            for (tenant, _), case in state.cases.items():
                if tenant == tenant_id and case.publication_id == payload["publication_id"]:
                    case.publication_id = payload["replacement_publication_id"]
                    case.version += 1
                    case.updated_at = utc_now()
                    changed += 1
            return f"publication_reference_corrected:{changed}"

        if event_type == "ownership.assignment_changed":
            assignment_state = payload["state"]
            if assignment_state in _ASSIGNMENT_IGNORED_STATES:
                return "maintenance_owner_superseded_ignored:0"
            new_owner_id = None if assignment_state in _ASSIGNMENT_CLEARED_STATES else payload["salesperson_id"]
            changed = 0
            for (tenant, _), case in state.cases.items():
                if tenant == tenant_id and case.customer_id == payload["customer_id"]:
                    case.maintenance_owner_id = new_owner_id
                    case.version += 1
                    case.updated_at = utc_now()
                    changed += 1
            return f"maintenance_owner_projected:{changed}"

        if event_type == "finance.refund_confirmed":
            changed = 0
            for (tenant, _), case in state.cases.items():
                if tenant == tenant_id and case.contract_id == payload["contract_id"]:
                    case.linked_refund_id = payload["refund_id"]
                    case.version += 1
                    case.updated_at = utc_now()
                    changed += 1
            return f"refund_link_confirmed:{changed}"

        raise ValidationError("event type is not consumed by M13", code="unsupported_event_type")

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
            raise ValidationError("event type is not consumed by M13", code="unsupported_event_type")
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
            tenant_id=envelope["tenant_id"], consumer="M13", producer=envelope["producer"],
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
