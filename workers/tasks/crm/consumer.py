from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from modules.crm.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.crm.repository import InMemoryRepository, StoreState
from modules.crm.service import CrmService


#: event_type -> (producer, aggregate_type, required payload fields)
_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "customer.created": ("M01", "customer", {"customer_id", "marketing_attribution_id"}),
    "customer.consent_changed": ("M01", "customer", {"customer_id", "consent_id", "purpose", "granted"}),
    "application.created": ("M01", "application", {"application_id", "customer_id", "package_version_id", "intent"}),
    "application.customer_signed": ("M02", "application", {"application_id", "customer_signature_id", "package_version_id", "document_sha256", "customer_signed_at"}),
    "application.company_decided": ("M02", "application", {"application_id", "acceptance_id", "decision", "contract_id"}),
    "contract.effective": ("M02", "contract", {"contract_id", "customer_id", "effective_at", "service_starts_at", "service_ends_at"}),
    "contract.terminated": ("M02", "contract", {"contract_id", "customer_id", "terminated_at", "refund_evaluation_required"}),
    "contract.service_ended": ("M02", "contract_service", {"contract_service_id", "contract_id", "customer_id", "ended_at"}),
    "entitlement.changed": ("M02", "entitlement", {"entitlement_id", "customer_id", "contract_id", "state", "decision_id", "valid_until"}),
    "ownership.assignment_changed": ("M11", "maintenance_assignment", {"assignment_id", "customer_id", "salesperson_id", "effective_at", "state"}),
    "ownership.pool_entered": ("M11", "pool_entry", {"pool_entry_id", "customer_id", "entry_reason", "policy_version"}),
    "ownership.employee_departed": ("M11", "employee", {"employee_id", "departure_id", "effective_at"}),
    "support.case_opened": ("M13", "service_case", {"case_id", "customer_id", "category", "priority"}),
    "support.case_resolved": ("M13", "service_case", {"case_id", "customer_id", "resolution_record_id"}),
    "finance.payment_confirmed": ("M14", "payment", {"payment_id", "application_id", "invoice_id", "confirmed_at", "evidence_snapshot_id"}),
    "finance.payment_failed": ("M14", "payment", {"payment_id", "application_id", "customer_id", "retry_allowed", "customer_safe_reason_code"}),
    "finance.refund_confirmed": ("M14", "refund", {"refund_id", "contract_id", "original_payment_id", "allocation_snapshot_id", "confirmed_at"}),
    "kyc.refresh_required": ("M03", "kyc_submission", {"customer_id", "submission_id", "refresh_case_id", "due_at"}),
    "crm.opportunity_changed": ("M10", "opportunity", {"opportunity_id", "customer_id", "state", "owner_id"}),
    "crm.suppression_changed": ("M10", "suppression", {"suppression_id", "customer_id", "scope", "active"}),
}

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _ROOT / "events" / "schemas"
_SCHEMA_VALIDATORS: dict[str, Draft202012Validator] = {}


def _validator_for(event_type: str) -> Draft202012Validator:
    """Authoritative per-event validator built from the canonical ``events/schemas``.

    Reuses the exact contract schemas (uuid/enum/date-time formats, classification
    consts, additionalProperties:false) instead of re-deriving a weaker, hand-rolled
    check that can drift from the published contract.
    """
    validator = _SCHEMA_VALIDATORS.get(event_type)
    if validator is None:
        schema_path = _SCHEMA_DIR / f"{event_type}.v1.schema.json"
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationError("event schema could not be loaded", code="event_schema_invalid") from exc
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        _SCHEMA_VALIDATORS[event_type] = validator
    return validator


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M10EventConsumer:
    """Inbox-deduplicated consumer for the events M10 subscribes to.

    ``finance.refund_confirmed`` is the only event that triggers a
    cross-aggregate business effect (reversing a previously *approved*
    conversion's attribution); it still commits inside the same transaction
    as the inbox receipt because the reversal only ever touches this
    module's own ``crm.conversion`` row -- unlike M09's publication dispatch,
    there is no external provider call to keep out of the DB transaction.
    Every other event's dedup check, projection update, inbox receipt and
    audit row commit together in a single repository transaction so a crash
    (or a concurrent redelivery) can never apply the projection without also
    recording the inbox receipt, or vice versa.
    """

    def __init__(self, repository: InMemoryRepository, service: CrmService) -> None:
        self.repository = repository
        self.service = service

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        event_type = envelope["event_type"]
        key = (tenant_id, "M10", envelope["producer"], event_id)

        def transaction(state: StoreState) -> ConsumeResult:
            decision, info = self._decide(state, envelope, key)
            if decision == "duplicate":
                return ConsumeResult("duplicate", info or "")
            if decision == "sequence_gap":
                state.inbox[key] = self._receipt(envelope, "retry", "sequence_gap", None)
                return ConsumeResult("retry", "sequence_gap")
            if decision == "stale":
                self._finalize(state, envelope, key, "stale_sequence_ignored")
                return ConsumeResult("applied", "stale_sequence_ignored")
            result_code = self._apply(state, envelope)
            self._finalize(state, envelope, key, result_code)
            return ConsumeResult("applied", result_code)

        return self.repository.atomic(transaction)

    def _decide(self, state: StoreState, envelope: dict[str, Any], key: tuple[str, str, str, str]) -> tuple[str, str | None]:
        existing = state.inbox.get(key)
        if existing:
            if existing.payload_sha256 != envelope["payload_sha256"]:
                raise ConflictError("event_id was replayed with a different payload", code="inbox_payload_mismatch")
            if existing.status != "retry":
                return "duplicate", existing.result_code
        tenant_id = envelope["tenant_id"]
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
            return "sequence_gap", None
        if envelope["sequence_no"] <= last_sequence:
            return "stale", None
        return "proceed", None

    def _finalize(self, state: StoreState, envelope: dict[str, Any], key: tuple[str, str, str, str], result_code: str) -> None:
        applied_at = utc_now()
        state.inbox[key] = self._receipt(envelope, "applied", result_code, applied_at)
        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=envelope["tenant_id"], actor_id="00000000-0000-0000-0000-000000000001",
                purpose="event_projection", action=f"m10.consume.{envelope['event_type']}", aggregate_type=envelope["aggregate_type"],
                aggregate_id=envelope["aggregate_id"], aggregate_version=envelope["aggregate_version"],
                correlation_id=envelope["correlation_id"], result=result_code, reason_code=None, recorded_at=applied_at,
            )
        )

    def _apply(self, state: StoreState, envelope: dict[str, Any]) -> str:
        event_type = envelope["event_type"]
        payload = envelope["payload"]
        tenant_id = envelope["tenant_id"]
        now = utc_now()

        if event_type == "customer.created":
            self.service.record_customer_created(state, tenant_id, payload["customer_id"], payload["marketing_attribution_id"], now)
            return "marketing_attribution_recorded"

        if event_type == "customer.consent_changed":
            self.service.record_consent_changed(
                state, tenant_id, payload["customer_id"], payload["consent_id"], payload["purpose"], payload["granted"],
                correlation_id=envelope["correlation_id"],
            )
            return "consent_recorded"

        if event_type == "application.created":
            self.service.record_application_created(state, tenant_id, payload["application_id"], payload["customer_id"], payload["package_version_id"], payload["intent"])
            return "application_projection_created"

        if event_type == "application.customer_signed":
            self.service.record_application_customer_signed(state, tenant_id, payload["application_id"])
            return "application_marked_signed"

        if event_type == "application.company_decided":
            self.service.record_application_company_decided(state, tenant_id, payload["application_id"], payload["decision"], payload["contract_id"])
            return "application_decision_recorded"

        if event_type == "contract.effective":
            self.service.record_contract_effective(
                state, tenant_id, payload["contract_id"], payload["customer_id"], self._time(payload["effective_at"]), self._time(payload["service_ends_at"]),
            )
            return "contract_projection_updated"

        if event_type == "contract.terminated":
            self.service.record_contract_terminated(state, tenant_id, payload["contract_id"], payload["customer_id"], self._time(payload["terminated_at"]))
            return "contract_termination_recorded"

        if event_type == "contract.service_ended":
            self.service.record_contract_service_ended(state, tenant_id, payload["contract_service_id"], payload["contract_id"])
            return "contract_service_mapping_recorded"

        if event_type == "entitlement.changed":
            self.service.record_entitlement_changed(
                state, tenant_id, payload["entitlement_id"], payload["customer_id"], payload["contract_id"], payload["state"], self._time(payload["valid_until"]),
            )
            return "entitlement_projection_updated"

        if event_type == "ownership.assignment_changed":
            self.service.record_ownership_assignment_changed(state, tenant_id, payload["customer_id"], payload["salesperson_id"], payload["state"], now)
            return "ownership_assignment_recorded"

        if event_type == "ownership.pool_entered":
            self.service.record_ownership_pool_entered(state, tenant_id, payload["customer_id"], payload["entry_reason"], now)
            return "pool_entry_recorded"

        if event_type == "ownership.employee_departed":
            # No customer_id on this event: downstream Pool re-entry for that
            # employee's book is M11's own responsibility; M10 only needs the
            # fact recorded for audit continuity, not a worklist effect here.
            return "employee_departure_noted"

        if event_type == "support.case_opened":
            if payload["customer_id"] is None:
                return "support_case_ignored_no_customer"
            self.service.record_support_case_opened(
                state, tenant_id, payload["case_id"], payload["customer_id"], payload["category"], payload["priority"],
                now, envelope["correlation_id"],
            )
            return "support_case_recorded"

        if event_type == "support.case_resolved":
            self.service.record_support_case_resolved(state, tenant_id, payload["case_id"], now, envelope["correlation_id"])
            return "support_case_resolution_recorded"

        if event_type == "finance.payment_confirmed":
            self.service.record_payment_confirmed(state, tenant_id, payload["application_id"])
            return "payment_confirmation_recorded"

        if event_type == "finance.payment_failed":
            self.service.record_payment_failed(state, tenant_id, payload["application_id"], payload["customer_id"], payload["customer_safe_reason_code"], now)
            return "payment_failure_worklist_updated"

        if event_type == "finance.refund_confirmed":
            return self.service.reverse_conversion_for_refund(state, tenant_id, payload["contract_id"], now, envelope["correlation_id"])

        if event_type == "kyc.refresh_required":
            self.service.record_kyc_refresh_required(state, tenant_id, payload["customer_id"], payload["refresh_case_id"], self._time(payload["due_at"]), now)
            return "incomplete_application_worklist_updated"

        if event_type == "crm.opportunity_changed":
            # M10 both produces and consumes this event (events/registry.json
            # lists M10 as its own consumer); the producing command already
            # wrote the opportunity row synchronously in the same
            # transaction that emitted it, so there is nothing left to
            # project here -- this consumption only exists for inbox/audit
            # continuity on deployments where the command path and this
            # worker are separate processes sharing one durable store.
            return "opportunity_change_acknowledged"

        if event_type == "crm.suppression_changed":
            return "suppression_change_acknowledged"

        raise ValidationError("event type is not consumed by M10", code="unsupported_event_type")

    def _validate_envelope(self, envelope: dict[str, Any]) -> None:
        required = {
            "event_id", "event_type", "schema_version", "tenant_id", "producer", "aggregate_type",
            "aggregate_id", "aggregate_version", "sequence_no", "occurred_at", "emitted_at", "correlation_id",
            "causation_id", "idempotency_key", "classification", "payload_sha256", "payload",
        }
        if not isinstance(envelope, dict) or set(envelope) != required:
            raise ValidationError("event envelope fields do not match schema", code="event_schema_invalid")
        contract = _CONTRACTS.get(envelope["event_type"])
        if not contract:
            raise ValidationError("event type is not consumed by M10", code="unsupported_event_type")
        producer, aggregate_type, payload_fields = contract
        # Authoritative structural/type/enum/uuid/classification check against
        # the same JSON Schema published in events/schemas for this event type.
        errors = sorted(_validator_for(envelope["event_type"]).iter_errors(envelope), key=lambda error: list(error.absolute_path))
        if errors:
            raise ValidationError("event envelope fields do not match schema", code="event_schema_invalid")
        if envelope["schema_version"] != "1.0.0" or envelope["producer"] != producer or envelope["aggregate_type"] != aggregate_type:
            raise ValidationError("event contract metadata is invalid", code="event_schema_invalid")
        if set(envelope["payload"]) != payload_fields:
            raise ValidationError("event payload fields do not match schema", code="event_schema_invalid")
        if canonical_hash(envelope["payload"]) != envelope["payload_sha256"]:
            raise ConflictError("event payload hash is invalid", code="event_payload_hash_invalid")

    @staticmethod
    def _receipt(envelope: dict[str, Any], status: str, code: str, applied_at: datetime | None) -> InboxReceipt:
        return InboxReceipt(
            tenant_id=envelope["tenant_id"], consumer="M10", producer=envelope["producer"], event_id=envelope["event_id"],
            event_type=envelope["event_type"], payload_sha256=envelope["payload_sha256"], aggregate_id=envelope["aggregate_id"],
            sequence_no=envelope["sequence_no"], status=status, result_code=code, applied_at=applied_at,
        )

    @staticmethod
    def _time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValidationError("event time must include timezone", code="event_schema_invalid")
        return parsed.astimezone(timezone.utc)
