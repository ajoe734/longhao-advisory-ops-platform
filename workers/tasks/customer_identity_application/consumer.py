from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from modules.customer_identity_application.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    NotFoundError,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.customer_identity_application.repository import InMemoryRepository, StoreState


_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "application.customer_signed": (
        "M02",
        "application",
        {"application_id", "customer_signature_id", "package_version_id", "document_sha256", "customer_signed_at"},
    ),
    "application.company_decided": (
        "M02",
        "application",
        {"application_id", "acceptance_id", "decision", "contract_id"},
    ),
    "kyc.decision_recorded": (
        "M03",
        "kyc_submission",
        {"submission_id", "application_id", "decision", "next_review_due_at"},
    ),
    "kyc.refresh_required": (
        "M03",
        "kyc_submission",
        {"customer_id", "submission_id", "refresh_case_id", "due_at"},
    ),
    "suitability.assessed": (
        "M03",
        "suitability_assessment",
        {"assessment_id", "application_id", "state", "rule_version"},
    ),
    "business_clearance.changed": (
        "M04",
        "business_clearance",
        {"clearance_id", "application_id", "decision", "expires_at"},
    ),
    "ownership.assignment_changed": (
        "M11",
        "maintenance_assignment",
        {"assignment_id", "customer_id", "salesperson_id", "effective_at", "state"},
    ),
    "ownership.employee_departed": (
        "M11",
        "employee",
        {"employee_id", "departure_id", "effective_at"},
    ),
}

_PARTIAL_VERSIONED_EVENTS = {
    "kyc.decision_recorded",
    "kyc.refresh_required",
    "suitability.assessed",
    "business_clearance.changed",
    "ownership.assignment_changed",
}
_ASSIGNMENT_CLEARED_STATES = {"released", "departure"}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M01EventConsumer:
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M01", envelope["producer"], event_id)

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
            last_aggregate_version = max(
                (
                    receipt.aggregate_version for receipt in state.inbox.values()
                    if receipt.tenant_id == tenant_id
                    and receipt.producer == envelope["producer"]
                    and receipt.aggregate_id == envelope["aggregate_id"]
                    and receipt.status == "applied"
                ),
                default=0,
            )
            if envelope["event_type"] in _PARTIAL_VERSIONED_EVENTS:
                if envelope["aggregate_version"] <= last_aggregate_version:
                    state.inbox[key] = self._receipt(envelope, "applied", "stale_aggregate_version_ignored", utc_now())
                    return ConsumeResult("applied", "stale_aggregate_version_ignored")
            elif envelope["sequence_no"] > last_sequence + 1:
                state.inbox[key] = self._receipt(envelope, "retry", "sequence_gap", None)
                return ConsumeResult("retry", "sequence_gap")
            if envelope["event_type"] not in _PARTIAL_VERSIONED_EVENTS and envelope["sequence_no"] <= last_sequence:
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
                    action=f"m01.consume.{envelope['event_type']}",
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
        if event_type == "application.customer_signed":
            app = state.applications.get((tenant_id, payload["application_id"]))
            if not app:
                raise NotFoundError("application referenced by event was not found")
            if app.package_version_id != payload["package_version_id"]:
                raise ConflictError("signed package version does not match application", code="signed_package_mismatch")
            if app.customer_signature_id and app.customer_signature_id != payload["customer_signature_id"]:
                raise ConflictError("application already has a different customer signature", code="customer_signature_conflict")
            if app.state == "draft":
                app.state = "customer_signed"
                app.customer_signed_at = self._time(payload["customer_signed_at"])
                app.customer_signature_id = payload["customer_signature_id"]
                app.version += 1
                app.updated_at = utc_now()
                return "customer_signature_applied"
            if app.customer_signature_id is None:
                app.customer_signature_id = payload["customer_signature_id"]
                app.version += 1
                app.updated_at = utc_now()
                return "customer_signature_evidence_projected"
            return "customer_signature_already_projected"
        if event_type == "application.company_decided":
            app = state.applications.get((tenant_id, payload["application_id"]))
            if not app:
                raise NotFoundError("application referenced by event was not found")
            decision = payload["decision"]
            if decision not in {"accept", "reject", "request_changes"}:
                raise ValidationError("company decision is invalid", field_name="payload.decision")
            if app.state == "withdrawn":
                return "withdrawn_application_ignored"
            if decision == "accept" and (app.state == "draft" or app.customer_signed_at is None):
                raise ConflictError(
                    "company cannot accept an application the customer has not signed",
                    code="application_unsigned",
                )
            app.state = {"accept": "accepted", "reject": "rejected", "request_changes": "review_in_progress"}[decision]
            app.company_accepted_at = self._time(envelope["occurred_at"]) if decision == "accept" else None
            app.contract_id = payload["contract_id"] if decision == "accept" else None
            app.version += 1
            app.updated_at = utc_now()
            return "company_decision_applied"
        if event_type == "kyc.decision_recorded":
            app = self._application(state, tenant_id, payload["application_id"])
            if app.state == "withdrawn":
                return "withdrawn_application_ignored"
            app.kyc_submission_id = payload["submission_id"]
            app.kyc_status = {"approved": "approved", "rejected": "rejected", "incomplete": "pending"}[payload["decision"]]
            app.version += 1
            app.updated_at = utc_now()
            return "kyc_gate_projected"
        if event_type == "kyc.refresh_required":
            app = self._application_for_customer(state, tenant_id, payload["submission_id"], payload["customer_id"])
            if app.state == "withdrawn":
                return "withdrawn_application_ignored"
            app.kyc_submission_id = payload["submission_id"]
            app.kyc_status = "refresh_required"
            app.version += 1
            app.updated_at = utc_now()
            return "kyc_refresh_projected"
        if event_type == "suitability.assessed":
            app = self._application(state, tenant_id, payload["application_id"])
            if app.state == "withdrawn":
                return "withdrawn_application_ignored"
            app.suitability_assessment_id = payload["assessment_id"]
            app.suitability_status = payload["state"]
            app.version += 1
            app.updated_at = utc_now()
            return "suitability_gate_projected"
        if event_type == "business_clearance.changed":
            app = self._application(state, tenant_id, payload["application_id"])
            if app.state == "withdrawn":
                return "withdrawn_application_ignored"
            app.business_clearance_id = payload["clearance_id"]
            app.business_clearance = payload["decision"]
            app.version += 1
            app.updated_at = utc_now()
            return "business_clearance_projected"
        if event_type == "ownership.assignment_changed":
            customer = state.customers.get((tenant_id, payload["customer_id"]))
            if not customer:
                raise NotFoundError("customer referenced by assignment was not found")
            if payload["state"] == "superseded":
                return "maintenance_owner_superseded_ignored"
            customer.maintenance_owner_id = (
                None if payload["state"] in _ASSIGNMENT_CLEARED_STATES else payload["salesperson_id"]
            )
            customer.version += 1
            customer.updated_at = utc_now()
            return "maintenance_owner_projected"
        if event_type == "ownership.employee_departed":
            changed = 0
            for (tenant, _), customer in state.customers.items():
                if tenant == tenant_id and customer.maintenance_owner_id == payload["employee_id"]:
                    customer.maintenance_owner_id = None
                    customer.version += 1
                    customer.updated_at = utc_now()
                    changed += 1
            return f"departed_owner_cleared:{changed}"
        raise ValidationError("event type is not consumed by M01", code="unsupported_event_type")

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
            raise ValidationError("event type is not consumed by M01", code="unsupported_event_type")
        producer, aggregate_type, payload_fields = contract
        if envelope["schema_version"] != "1.0.0" or envelope["producer"] != producer or envelope["aggregate_type"] != aggregate_type:
            raise ValidationError("event contract metadata is invalid", code="event_schema_invalid")
        if set(envelope["payload"]) != payload_fields:
            raise ValidationError("event payload fields do not match schema", code="event_schema_invalid")
        if canonical_hash(envelope["payload"]) != envelope["payload_sha256"]:
            raise ConflictError("event payload hash is invalid", code="event_payload_hash_invalid")
        if envelope["event_type"].startswith("application."):
            if envelope["aggregate_id"] != envelope["payload"]["application_id"]:
                raise ValidationError("aggregate_id does not match application_id", code="event_aggregate_mismatch")
        elif envelope["event_type"] == "kyc.decision_recorded":
            if envelope["aggregate_id"] != envelope["payload"]["submission_id"]:
                raise ValidationError("aggregate_id does not match submission_id", code="event_aggregate_mismatch")
        elif envelope["event_type"] == "kyc.refresh_required":
            if envelope["aggregate_id"] != envelope["payload"]["submission_id"]:
                raise ValidationError("aggregate_id does not match submission_id", code="event_aggregate_mismatch")
        elif envelope["event_type"] == "suitability.assessed":
            if envelope["aggregate_id"] != envelope["payload"]["assessment_id"]:
                raise ValidationError("aggregate_id does not match assessment_id", code="event_aggregate_mismatch")
        elif envelope["event_type"] == "business_clearance.changed":
            if envelope["aggregate_id"] != envelope["payload"]["clearance_id"]:
                raise ValidationError("aggregate_id does not match clearance_id", code="event_aggregate_mismatch")
        elif envelope["event_type"] == "ownership.assignment_changed":
            if envelope["aggregate_id"] != envelope["payload"]["assignment_id"]:
                raise ValidationError("aggregate_id does not match assignment_id", code="event_aggregate_mismatch")
        elif envelope["event_type"] == "ownership.employee_departed":
            if envelope["aggregate_id"] != envelope["payload"]["employee_id"]:
                raise ValidationError("aggregate_id does not match employee_id", code="event_aggregate_mismatch")
        if not isinstance(envelope["sequence_no"], int) or envelope["sequence_no"] < 1:
            raise ValidationError("sequence_no must be positive", field_name="sequence_no")
        if not isinstance(envelope["aggregate_version"], int) or envelope["aggregate_version"] < 1:
            raise ValidationError("aggregate_version must be positive", field_name="aggregate_version")

    @staticmethod
    def _application(state: StoreState, tenant_id: str, application_id: str):
        app = state.applications.get((tenant_id, application_id))
        if not app:
            raise NotFoundError("application referenced by event was not found")
        return app

    @staticmethod
    def _application_for_customer(state: StoreState, tenant_id: str, submission_id: str, customer_id: str):
        app = next(
            (
                item for (tenant, _), item in state.applications.items()
                if tenant == tenant_id and item.customer_id == customer_id and item.kyc_submission_id == submission_id
            ),
            None,
        )
        if not app:
            raise NotFoundError("application referenced by KYC refresh was not found")
        return app

    @staticmethod
    def _receipt(envelope: dict[str, Any], status: str, code: str, applied_at: datetime | None) -> InboxReceipt:
        return InboxReceipt(
            tenant_id=envelope["tenant_id"],
            consumer="M01",
            producer=envelope["producer"],
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            payload_sha256=envelope["payload_sha256"],
            aggregate_id=envelope["aggregate_id"],
            aggregate_version=envelope["aggregate_version"],
            sequence_no=envelope["sequence_no"],
            status=status,
            result_code=code,
            applied_at=applied_at,
        )

    @staticmethod
    def _time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValidationError("event time must include timezone", code="event_schema_invalid")
        return parsed.astimezone(timezone.utc)
