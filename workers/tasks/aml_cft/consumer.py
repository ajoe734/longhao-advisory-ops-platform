from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from modules.aml_cft.domain import (
    AmlCaseAction,
    AuditEvent,
    BusinessClearance,
    ConflictError,
    InboxReceipt,
    OutboxEvent,
    SYSTEM_ACTOR_ID,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
    wire_time,
)
from modules.aml_cft.repository import InMemoryRepository, StoreState


_RISK_RELEVANT_FIELDS = {"identity_evidence", "beneficial_owner", "residency"}
_CASE_RESOLVED = {"cleared", "rejected", "closed"}
_REFRESH_VALIDITY = timedelta(days=7)

_CONTRACTS: dict[str, tuple[str, str, set[str], str]] = {
    "application.customer_signed": (
        "M02", "application",
        {"application_id", "customer_signature_id", "package_version_id", "document_sha256", "customer_signed_at"},
        "customer_confidential",
    ),
    "kyc.decision_recorded": (
        "M03", "kyc_submission",
        {"submission_id", "application_id", "decision", "next_review_due_at"},
        "customer_confidential",
    ),
    "customer.profile_updated": (
        "M01", "customer",
        {"customer_id", "changed_field_codes", "profile_evidence_ref"},
        "customer_confidential",
    ),
    # M04 is both producer and consumer of its own restricted case event
    # (task packet §6): a downstream AML-only replica/projection re-applies
    # it idempotently without ever treating receipt as a new authorization.
    "aml.case_changed": (
        "M04", "aml_case",
        {"case_id", "screening_id", "restricted_record_version"},
        "aml_restricted",
    ),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class AmlEventConsumer:
    """M04 inbox for cross-module projections.

    Every applied event is a plain internal projection: an incoming event is
    never trusted as an authorization grant, and nothing here produces a
    clearance ``approved`` decision — only a ``pending`` refresh trigger when
    risk-relevant customer facts change on an already-approved application.
    """

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M04", envelope["producer"], event_id)

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
                    id=new_id(), tenant_id=tenant_id, actor_id=SYSTEM_ACTOR_ID, purpose="event_projection",
                    action=f"m04.consume.{envelope['event_type']}", aggregate_type=envelope["aggregate_type"],
                    aggregate_id=envelope["aggregate_id"], aggregate_version=envelope["aggregate_version"],
                    correlation_id=envelope["correlation_id"], result=result_code, reason_code=None, recorded_at=applied_at,
                )
            )
            return ConsumeResult("applied", result_code)

        return self.repository.atomic(transaction)

    def _apply(self, state: StoreState, envelope: dict[str, Any]) -> str:
        event_type = envelope["event_type"]
        payload = envelope["payload"]
        tenant_id = envelope["tenant_id"]
        if event_type == "application.customer_signed":
            return "signature_acknowledged"
        if event_type == "kyc.decision_recorded":
            if payload["decision"] != "rejected":
                return "decision_recorded_no_action"
            case = next(
                (
                    item for (tenant, _), item in state.cases.items()
                    if tenant == tenant_id and item.application_id == payload["application_id"] and item.state not in _CASE_RESOLVED
                ),
                None,
            )
            if case is None:
                return "no_open_case_to_annotate"
            now = utc_now()
            action_id = new_id()
            state.case_actions[(tenant_id, action_id)] = AmlCaseAction(
                id=action_id, tenant_id=tenant_id, version=1, created_at=now, updated_at=now, case_id=case.id,
                actor_id=SYSTEM_ACTOR_ID, action_code="record_investigation", reason_code="kyc_rejected_context",
                restricted_note_asset_id=payload["submission_id"], occurred_at=now,
            )
            case.version += 1
            case.updated_at = now
            self._emit(
                state, event_type="aml.case_changed", tenant_id=tenant_id, aggregate_type="aml_case", aggregate_id=case.id,
                aggregate_version=case.version, correlation_id=envelope["correlation_id"],
                idempotency_key=f"{envelope['event_id']}:aml.case_changed:{case.id}", classification="aml_restricted",
                payload={"case_id": case.id, "screening_id": case.screening_ids[-1], "restricted_record_version": case.version},
                now=now,
            )
            return "kyc_rejection_annotated"
        if event_type == "customer.profile_updated":
            if not set(payload["changed_field_codes"]) & _RISK_RELEVANT_FIELDS:
                return "no_refresh_needed"
            refreshed = 0
            now = utc_now()
            for (tenant, application_id), ids in list(state.clearance_by_application.items()):
                if tenant != tenant_id or not ids:
                    continue
                latest = state.clearances[(tenant, ids[-1])]
                if latest.customer_id != payload["customer_id"] or latest.decision != "approved":
                    continue
                refreshed_clearance = BusinessClearance(
                    id=new_id(), tenant_id=tenant_id, version=len(ids) + 1, created_at=now, updated_at=now,
                    application_id=application_id, customer_id=latest.customer_id, decision="pending",
                    policy_version=latest.policy_version, decided_by=SYSTEM_ACTOR_ID, decided_at=now,
                    valid_until=now + _REFRESH_VALIDITY, safe_reason_code="profile_changed_requires_refresh",
                    case_id=latest.case_id,
                )
                state.clearances[(tenant, refreshed_clearance.id)] = refreshed_clearance
                ids.append(refreshed_clearance.id)
                self._emit(
                    state, event_type="business_clearance.changed", tenant_id=tenant_id, aggregate_type="business_clearance",
                    aggregate_id=refreshed_clearance.id, aggregate_version=refreshed_clearance.version,
                    correlation_id=envelope["correlation_id"],
                    idempotency_key=f"{envelope['event_id']}:business_clearance.changed:{refreshed_clearance.id}",
                    classification="customer_confidential",
                    payload={
                        "clearance_id": refreshed_clearance.id, "application_id": refreshed_clearance.application_id,
                        "decision": refreshed_clearance.decision, "expires_at": wire_time(refreshed_clearance.valid_until),
                    },
                    now=now,
                )
                refreshed += 1
            return f"clearances_flagged_for_refresh:{refreshed}" if refreshed else "no_refresh_needed"
        if event_type == "aml.case_changed":
            # Self-produced restricted projection: a receiving M04 replica
            # never re-derives case state from the event (the authoritative
            # case row is only ever written by this module's own commands),
            # so this is purely an idempotent acknowledgment.
            return "case_change_acknowledged"
        raise ValidationError("event type is not consumed by M04", code="unsupported_event_type")

    @staticmethod
    def _emit(
        state: StoreState, *, event_type: str, tenant_id: str, aggregate_type: str, aggregate_id: str,
        aggregate_version: int, correlation_id: str, idempotency_key: str, classification: str,
        payload: dict[str, Any], now: datetime,
    ) -> None:
        sequence = 1 + max(
            (
                event.sequence_no for (tenant, _), event in state.outbox.items()
                if tenant == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id
            ),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(), event_type=event_type, tenant_id=tenant_id, aggregate_type=aggregate_type,
            aggregate_id=aggregate_id, aggregate_version=aggregate_version, sequence_no=sequence,
            correlation_id=correlation_id, causation_id=None, idempotency_key=idempotency_key,
            classification=classification, payload=payload, occurred_at=now, emitted_at=now,
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
            raise ValidationError("event type is not consumed by M04", code="unsupported_event_type")
        producer, aggregate_type, payload_fields, classification = contract
        if (
            envelope["schema_version"] != "1.0.0"
            or envelope["producer"] != producer
            or envelope["aggregate_type"] != aggregate_type
            or envelope["classification"] != classification
        ):
            raise ValidationError("event contract metadata is invalid", code="event_schema_invalid")
        if set(envelope["payload"]) != payload_fields:
            raise ValidationError("event payload fields do not match schema", code="event_schema_invalid")
        if canonical_hash(envelope["payload"]) != envelope["payload_sha256"]:
            raise ConflictError("event payload hash is invalid", code="event_payload_hash_invalid")
        if not isinstance(envelope["sequence_no"], int) or envelope["sequence_no"] < 1:
            raise ValidationError("sequence_no must be positive", field_name="sequence_no")
        if not isinstance(envelope["aggregate_version"], int) or envelope["aggregate_version"] < 1:
            raise ValidationError("aggregate_version must be positive", field_name="aggregate_version")
        for uuid_field in ("event_id", "tenant_id", "aggregate_id", "correlation_id"):
            try:
                UUID(str(envelope[uuid_field]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValidationError(f"{uuid_field} must be a UUID", code="event_schema_invalid", field_name=uuid_field) from exc
        if envelope["causation_id"] is not None:
            try:
                UUID(str(envelope["causation_id"]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValidationError("causation_id must be a UUID", code="event_schema_invalid", field_name="causation_id") from exc
        AmlEventConsumer._time(envelope["occurred_at"])
        AmlEventConsumer._time(envelope["emitted_at"])

    @staticmethod
    def _receipt(envelope: dict[str, Any], status: str, code: str, applied_at: datetime | None) -> InboxReceipt:
        return InboxReceipt(
            tenant_id=envelope["tenant_id"], consumer="M04", producer=envelope["producer"], event_id=envelope["event_id"],
            event_type=envelope["event_type"], payload_sha256=envelope["payload_sha256"], aggregate_id=envelope["aggregate_id"],
            sequence_no=envelope["sequence_no"], status=status, result_code=code, applied_at=applied_at,
        )

    @staticmethod
    def _time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValidationError("event time must be an RFC3339 timestamp", code="event_schema_invalid") from exc
        if parsed.tzinfo is None:
            raise ValidationError("event time must include timezone", code="event_schema_invalid")
        return parsed.astimezone(timezone.utc)
