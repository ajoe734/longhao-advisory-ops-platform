from __future__ import annotations

from longhao_platform.event_ordering import is_sequence_gap

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from modules.kyc_suitability.domain import (
    AuditEvent,
    ConflictError,
    InboxReceipt,
    OutboxEvent,
    RefreshCase,
    SignedApplicationProjection,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.kyc_suitability.repository import InMemoryRepository, StoreState


_RISK_RELEVANT_FIELDS = {"residency", "beneficial_owner", "identity_evidence"}

_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "application.customer_signed": (
        "M02",
        "application",
        {"application_id", "customer_signature_id", "package_version_id", "document_sha256", "customer_signed_at"},
    ),
    "customer.profile_updated": (
        "M01",
        "customer",
        {"customer_id", "changed_field_codes", "profile_evidence_ref"},
    ),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M03EventConsumer:
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M03", envelope["producer"], event_id)

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
            if is_sequence_gap("M03", envelope, last_sequence):
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
                    action=f"m03.consume.{envelope['event_type']}",
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
            # application.customer_signed does not carry customer_id (see the
            # published schema). The projection therefore only proves the
            # inbox dedup/ordering mechanics here; a production gateway must
            # resolve customer_id from a real M01/M02 call, not this event.
            existing = state.signed_applications.get((tenant_id, payload["application_id"]))
            customer_id = existing.customer_id if existing else None
            state.signed_applications[(tenant_id, payload["application_id"])] = SignedApplicationProjection(
                tenant_id=tenant_id,
                application_id=payload["application_id"],
                customer_id=customer_id,
                customer_signature_id=payload["customer_signature_id"],
                signed_at=self._time(payload["customer_signed_at"]),
            )
            return "signed_application_projected"
        if event_type == "customer.profile_updated":
            customer_id = payload["customer_id"]
            changed = set(payload["changed_field_codes"])
            if not changed & _RISK_RELEVANT_FIELDS:
                return "non_risk_field_ignored"
            submission = self._latest_approved_submission(state, tenant_id, customer_id)
            if submission is None:
                return "no_approved_kyc_to_refresh"
            now = utc_now()
            submission.status = "refresh_required"
            submission.version += 1
            submission.updated_at = now
            case = RefreshCase(
                id=new_id(),
                tenant_id=tenant_id,
                customer_id=customer_id,
                submission_id=submission.id,
                trigger="risk_change",
                reason_code=f"profile_changed:{'+'.join(sorted(changed))}"[:80],
                due_at=now + timedelta(days=30),
                next_form_version=submission.form_version,
                requested_by="00000000-0000-0000-0000-000000000001",
                created_at=now,
                updated_at=now,
            )
            state.refresh_cases[(tenant_id, case.id)] = case
            assessment = self._latest_assessment_for_application(state, tenant_id, submission.application_id)
            if assessment and assessment.status in {"suitable", "conditional"}:
                assessment.status = "reassessment_due"
                assessment.version += 1
                assessment.updated_at = now
            self._emit_refresh_required(state, tenant_id, submission.id, submission.version, customer_id, case.id, case.due_at, envelope["correlation_id"], now)
            return "risk_change_refresh_created"
        raise ValidationError("event type is not consumed by M03", code="unsupported_event_type")

    @staticmethod
    def _latest_approved_submission(state: StoreState, tenant_id: str, customer_id: str):
        rows = [
            row for (tenant, _), row in state.submissions.items()
            if tenant == tenant_id and row.customer_id == customer_id and row.status == "approved"
        ]
        return max(rows, key=lambda row: row.created_at) if rows else None

    @staticmethod
    def _latest_assessment_for_application(state: StoreState, tenant_id: str, application_id: str):
        rows = [
            row for (tenant, _), row in state.assessments.items()
            if tenant == tenant_id and row.application_id == application_id
        ]
        return max(rows, key=lambda row: row.version) if rows else None

    @staticmethod
    def _emit_refresh_required(
        state: StoreState, tenant_id: str, submission_id: str, submission_version: int,
        customer_id: str, refresh_case_id: str, due_at: datetime, correlation_id: str, now: datetime,
    ) -> None:
        sequence = 1 + max(
            (
                event.sequence_no for (tenant, _), event in state.outbox.items()
                if tenant == tenant_id and event.aggregate_type == "kyc_submission" and event.aggregate_id == submission_id
            ),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(),
            event_type="kyc.refresh_required",
            tenant_id=tenant_id,
            aggregate_type="kyc_submission",
            aggregate_id=submission_id,
            aggregate_version=submission_version,
            sequence_no=sequence,
            correlation_id=correlation_id,
            causation_id=None,
            idempotency_key=f"risk-change-refresh:{refresh_case_id}",
            classification="customer_confidential",
            payload={
                "customer_id": customer_id,
                "submission_id": submission_id,
                "refresh_case_id": refresh_case_id,
                "due_at": due_at.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            },
            occurred_at=now,
            emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

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
            raise ValidationError("event type is not consumed by M03", code="unsupported_event_type")
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
            consumer="M03",
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

    @staticmethod
    def _time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValidationError("event time must include timezone", code="event_schema_invalid")
        return parsed.astimezone(timezone.utc)
