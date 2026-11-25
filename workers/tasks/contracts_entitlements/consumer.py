from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.contracts_entitlements.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    Job,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.contracts_entitlements.repository import InMemoryRepository, StoreState


# producer, aggregate_type, required payload fields, enqueued job kind (None = audit only)
_CONTRACTS: dict[str, tuple[str, str, set[str], str | None]] = {
    "application.created": ("M01", "application", {"application_id", "customer_id", "package_version_id", "intent"}, None),
    "kyc.decision_recorded": ("M03", "kyc_submission", {"submission_id", "application_id", "decision", "next_review_due_at"}, "gate_changed_recheck"),
    "suitability.assessed": ("M03", "suitability_assessment", {"assessment_id", "application_id", "state", "rule_version"}, "gate_changed_recheck"),
    "business_clearance.changed": ("M04", "business_clearance", {"clearance_id", "application_id", "decision", "expires_at"}, "gate_changed_recheck"),
    "finance.payment_confirmed": ("M14", "payment", {"payment_id", "application_id", "invoice_id", "confirmed_at", "evidence_snapshot_id"}, "payment_confirmed_recheck"),
    "finance.refund_confirmed": ("M14", "refund", {"refund_id", "contract_id", "original_payment_id", "allocation_snapshot_id", "confirmed_at"}, None),
    "kyc.refresh_required": ("M03", "kyc_submission", {"customer_id", "submission_id", "refresh_case_id", "due_at"}, "kyc_refresh_pending_review"),
}

_SCHEMA_FIELDS = {
    "event_id", "event_type", "schema_version", "tenant_id", "producer", "aggregate_type",
    "aggregate_id", "aggregate_version", "sequence_no", "occurred_at", "emitted_at", "correlation_id",
    "causation_id", "idempotency_key", "classification", "payload_sha256", "payload",
}

_AGGREGATE_KEY_FIELD = {
    "application.created": "application_id",
    "kyc.decision_recorded": "application_id",
    "suitability.assessed": "application_id",
    "business_clearance.changed": "application_id",
    "finance.payment_confirmed": "application_id",
    "finance.refund_confirmed": "contract_id",
    "kyc.refresh_required": "submission_id",
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M02EventConsumer:
    """Applies the inbox pattern; never trusts the event alone for gate authorization.

    A matching event only enqueues a re-check job (or an audit note). The
    authoritative gate state is still re-read live from its owning module's
    port when ``evaluate_entitlements``/``decide_company_acceptance`` run.
    """

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M02", envelope["producer"], event_id)

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
                    if receipt.tenant_id == tenant_id and receipt.producer == envelope["producer"]
                    and receipt.aggregate_id == envelope["aggregate_id"] and receipt.status == "applied"
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
                    purpose="event_projection", action=f"m02.consume.{envelope['event_type']}",
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
        _, _, _, job_kind = _CONTRACTS[event_type]
        if event_type == "application.created":
            return "application_noted"
        if job_kind is None:
            return f"{event_type}_noted"
        job = Job(
            id=new_id(), tenant_id=tenant_id, kind=job_kind,
            payload={"application_id": payload.get(_AGGREGATE_KEY_FIELD[event_type], envelope["aggregate_id"]), "source_event_type": event_type},
            created_at=utc_now(), updated_at=utc_now(),
        )
        state.jobs[(tenant_id, job.id)] = job
        return f"{job_kind}_enqueued"

    @staticmethod
    def _validate_envelope(envelope: dict[str, Any]) -> None:
        if set(envelope) != _SCHEMA_FIELDS:
            raise ValidationError("event envelope fields do not match schema", code="event_schema_invalid")
        contract = _CONTRACTS.get(envelope["event_type"])
        if not contract:
            raise ValidationError("event type is not consumed by M02", code="unsupported_event_type")
        producer, aggregate_type, payload_fields, _ = contract
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
            tenant_id=envelope["tenant_id"], consumer="M02", producer=envelope["producer"], event_id=envelope["event_id"],
            event_type=envelope["event_type"], payload_sha256=envelope["payload_sha256"], aggregate_id=envelope["aggregate_id"],
            sequence_no=envelope["sequence_no"], status=status, result_code=code, applied_at=applied_at,
        )
