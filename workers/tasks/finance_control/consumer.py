from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from modules.finance_control.domain import ConflictError, ValidationError, canonical_hash
from modules.finance_control.service import FinanceControlService


_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "application.company_decided": ("M02", "application", {"application_id", "acceptance_id", "decision", "contract_id"}),
    "contract.effective": ("M02", "contract", {"contract_id", "customer_id", "effective_at", "service_starts_at", "service_ends_at"}),
    "contract.terminated": ("M02", "contract", {"contract_id", "customer_id", "terminated_at", "refund_evaluation_required"}),
    "contract.service_ended": ("M02", "contract_service", {"contract_service_id", "contract_id", "customer_id", "ended_at"}),
    "commission.run_approved": ("M12", "commission_run", {"run_id", "policy_id", "approval_id", "ledger_manifest_sha256"}),
    "commission.adjustment_recorded": ("M12", "commission_adjustment", {"adjustment_id", "original_entry_id", "refund_id", "original_recipient_id"}),
    "finance.accounting_export_acknowledged": ("M14", "accounting_export", {"export_id", "status", "acknowledgement_id", "export_sha256"}),
    "audit.legal_hold_changed": ("M14", "legal_hold", {"legal_hold_id", "state", "scope"}),
}

_ENVELOPE_FIELDS = {
    "event_id", "event_type", "schema_version", "tenant_id", "producer", "aggregate_type",
    "aggregate_id", "aggregate_version", "sequence_no", "occurred_at", "emitted_at", "correlation_id",
    "causation_id", "idempotency_key", "classification", "payload_sha256", "payload",
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str | None


class M14EventConsumer:
    """Routes validated M14 consumer-side envelopes to the service's idempotent
    inbox projections. Envelope shape, contract metadata and payload hash are
    checked here; dedup, sequence ordering and tenant isolation are enforced
    inside ``FinanceControlService._apply_event``.
    """

    def __init__(self, service: FinanceControlService) -> None:
        self.service = service
        self._dispatch: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "application.company_decided": service.apply_application_company_decided,
            "contract.effective": service.apply_contract_effective,
            "contract.terminated": service.apply_contract_terminated,
            "contract.service_ended": service.apply_contract_service_ended,
            "commission.run_approved": service.apply_commission_run_approved,
            "commission.adjustment_recorded": service.apply_commission_adjustment_recorded,
            "finance.accounting_export_acknowledged": service.apply_accounting_export_acknowledged,
            "audit.legal_hold_changed": service.apply_legal_hold_changed,
        }

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        handler = self._dispatch[envelope["event_type"]]
        result = handler(envelope)
        return ConsumeResult(result["status"], result.get("result_code"))

    @staticmethod
    def _validate_envelope(envelope: dict[str, Any]) -> None:
        if set(envelope) != _ENVELOPE_FIELDS:
            raise ValidationError("event envelope fields do not match schema", code="event_schema_invalid")
        contract = _CONTRACTS.get(envelope["event_type"])
        if not contract:
            raise ValidationError("event type is not consumed by M14", code="unsupported_event_type")
        producer, aggregate_type, payload_fields = contract
        if envelope["schema_version"] != "1.0.0" or envelope["producer"] != producer or envelope["aggregate_type"] != aggregate_type:
            raise ValidationError("event contract metadata is invalid", code="event_schema_invalid")
        if set(envelope["payload"]) != payload_fields:
            raise ValidationError("event payload fields do not match schema", code="event_schema_invalid")
        if canonical_hash(envelope["payload"]) != envelope["payload_sha256"]:
            raise ConflictError("event payload hash is invalid", code="event_payload_hash_invalid")
        if not isinstance(envelope["sequence_no"], int) or envelope["sequence_no"] < 1:
            raise ValidationError("sequence_no must be positive", field_name="sequence_no")
