from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from modules.legendflow_delivery.domain import (
    AuditEvent,
    ConflictError,
    DependencyUnavailableError,
    EntitlementProjection,
    InboxReceipt,
    ValidationError,
    canonical_hash,
    new_id,
    utc_now,
)
from modules.legendflow_delivery.repository import InMemoryRepository, StoreState
from modules.legendflow_delivery.service import LegendflowDeliveryService


#: event_type -> (producer, aggregate_type, required payload fields)
_CONTRACTS: dict[str, tuple[str, str, set[str]]] = {
    "customer.identity_linked": ("M01", "customer", {"customer_id", "identity_id", "provider"}),
    "customer.consent_changed": ("M01", "customer", {"customer_id", "consent_id", "purpose", "granted"}),
    "contract.terminated": ("M02", "contract", {"contract_id", "customer_id", "terminated_at", "refund_evaluation_required"}),
    "entitlement.changed": ("M02", "entitlement", {"entitlement_id", "customer_id", "contract_id", "state", "decision_id", "valid_until"}),
    "report.signature_revoked": ("M07", "signature", {"signature_id", "report_version_id", "revocation_id", "effective_at"}),
    "publication.created": ("M08", "publication", {"publication_id", "report_version_id", "signature_id", "audience_snapshot_id", "bundle_manifest_sha256", "valid_until"}),
    "publication.withdrawn": ("M08", "publication", {"publication_id", "withdrawal_record_id", "effective_at", "notify_recipients"}),
    "publication.corrected": ("M08", "publication", {"publication_id", "replacement_publication_id", "replacement_report_version_id"}),
    "crm.suppression_changed": ("M10", "suppression", {"suppression_id", "customer_id", "scope", "active"}),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M09EventConsumer:
    """Inbox-deduplicated consumer for the nine events M09 subscribes to.

    ``publication.created`` is the only event that triggers a cross-module
    business dispatch (``service.dispatch_publication``); it runs outside the
    inbox-write transaction so a provider/port outage leaves the event
    un-acknowledged for safe redelivery instead of silently losing the fan-out.
    All other events are pure local projection updates applied atomically.
    """

    def __init__(self, repository: InMemoryRepository, service: LegendflowDeliveryService) -> None:
        self.repository = repository
        self.service = service

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M09", envelope["producer"], event_id)

        def check(state: StoreState) -> tuple[str, str | None]:
            existing = state.inbox.get(key)
            if existing:
                if existing.payload_sha256 != envelope["payload_sha256"]:
                    raise ConflictError("event_id was replayed with a different payload", code="inbox_payload_mismatch")
                if existing.status != "retry":
                    return "duplicate", existing.result_code
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

        decision, info = self.repository.read(check)
        if decision == "duplicate":
            return ConsumeResult("duplicate", info or "")
        if decision == "sequence_gap":
            self.repository.atomic(lambda state: state.inbox.__setitem__(key, self._receipt(envelope, "retry", "sequence_gap", None)))
            return ConsumeResult("retry", "sequence_gap")
        if decision == "stale":
            self.repository.atomic(lambda state: state.inbox.__setitem__(key, self._receipt(envelope, "applied", "stale_sequence_ignored", utc_now())))
            return ConsumeResult("applied", "stale_sequence_ignored")

        event_type = envelope["event_type"]
        if event_type == "publication.created":
            try:
                summary = self.service.dispatch_publication(
                    tenant_id, envelope["payload"]["publication_id"],
                    correlation_id=envelope["correlation_id"], causation_id=event_id,
                )
            except DependencyUnavailableError:
                return ConsumeResult("retry", "publication_dependency_unavailable")
            result_code = f"dispatched:created={summary.created},suppressed={summary.suppressed},skipped={summary.skipped_existing}"
        else:
            result_code = self.repository.atomic(lambda state: self._apply(state, envelope))

        def finalize(state: StoreState) -> None:
            applied_at = utc_now()
            state.inbox[key] = self._receipt(envelope, "applied", result_code, applied_at)
            state.audit_events.append(
                AuditEvent(
                    id=new_id(), tenant_id=tenant_id, actor_id="00000000-0000-0000-0000-000000000001",
                    purpose="event_projection", action=f"m09.consume.{event_type}", aggregate_type=envelope["aggregate_type"],
                    aggregate_id=envelope["aggregate_id"], aggregate_version=envelope["aggregate_version"],
                    correlation_id=envelope["correlation_id"], result=result_code, reason_code=None, recorded_at=applied_at,
                )
            )

        self.repository.atomic(finalize)
        return ConsumeResult("applied", result_code)

    def _apply(self, state: StoreState, envelope: dict[str, Any]) -> str:
        event_type = envelope["event_type"]
        payload = envelope["payload"]
        tenant_id = envelope["tenant_id"]

        if event_type == "customer.identity_linked":
            state.identity_links.setdefault((tenant_id, payload["customer_id"]), set()).add(payload["provider"])
            return "identity_link_recorded"

        if event_type == "customer.consent_changed":
            state.consents[(tenant_id, payload["consent_id"])] = {
                "customer_id": payload["customer_id"], "purpose": payload["purpose"], "granted": payload["granted"],
            }
            if not payload["granted"]:
                changed = 0
                for (tenant, _), preference in state.preferences.items():
                    if tenant == tenant_id and preference.consent_id == payload["consent_id"] and preference.purpose == "marketing" and preference.enabled:
                        preference.enabled = False
                        preference.version += 1
                        preference.updated_at = utc_now()
                        changed += 1
                return f"consent_recorded:marketing_preferences_revoked={changed}"
            return "consent_recorded"

        if event_type == "contract.terminated":
            state.terminated_customers[(tenant_id, payload["customer_id"])] = payload["contract_id"]
            return "contract_termination_recorded"

        if event_type == "entitlement.changed":
            state.entitlements[(tenant_id, payload["entitlement_id"])] = EntitlementProjection(
                tenant_id=tenant_id, entitlement_id=payload["entitlement_id"], customer_id=payload["customer_id"],
                contract_id=payload["contract_id"], state=payload["state"], decision_id=payload["decision_id"],
                valid_until=self._time(payload["valid_until"]), updated_at=utc_now(),
            )
            return "entitlement_projection_updated"

        if event_type == "report.signature_revoked":
            state.revoked_signatures[(tenant_id, payload["report_version_id"])] = payload["revocation_id"]
            return "signature_revocation_recorded"

        if event_type == "publication.withdrawn":
            projection = state.publications.get((tenant_id, payload["publication_id"]))
            if projection is not None and projection.state == "published":
                projection.state = "withdrawn"
                projection.withdrawal_record_id = payload["withdrawal_record_id"]
                projection.updated_at = utc_now()
                return "publication_marked_withdrawn"
            return "publication_withdrawal_ignored_not_published"

        if event_type == "publication.corrected":
            projection = state.publications.get((tenant_id, payload["publication_id"]))
            if projection is not None and projection.state == "published":
                projection.state = "corrected"
                projection.correction_publication_id = payload["replacement_publication_id"]
                projection.updated_at = utc_now()
                return "publication_marked_corrected"
            return "publication_correction_ignored_not_published"

        if event_type == "crm.suppression_changed":
            state.suppressions[(tenant_id, payload["customer_id"], payload["scope"])] = payload["active"]
            return "suppression_projection_updated"

        raise ValidationError("event type is not consumed by M09", code="unsupported_event_type")

    def _validate_envelope(self, envelope: dict[str, Any]) -> None:
        required = {
            "event_id", "event_type", "schema_version", "tenant_id", "producer", "aggregate_type",
            "aggregate_id", "aggregate_version", "sequence_no", "occurred_at", "emitted_at", "correlation_id",
            "causation_id", "idempotency_key", "classification", "payload_sha256", "payload",
        }
        if set(envelope) != required:
            raise ValidationError("event envelope fields do not match schema", code="event_schema_invalid")
        contract = _CONTRACTS.get(envelope["event_type"])
        if not contract:
            raise ValidationError("event type is not consumed by M09", code="unsupported_event_type")
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
            tenant_id=envelope["tenant_id"], consumer="M09", producer=envelope["producer"], event_id=envelope["event_id"],
            event_type=envelope["event_type"], payload_sha256=envelope["payload_sha256"], aggregate_id=envelope["aggregate_id"],
            sequence_no=envelope["sequence_no"], status=status, result_code=code, applied_at=applied_at,
        )

    @staticmethod
    def _time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValidationError("event time must include timezone", code="event_schema_invalid")
        return parsed.astimezone(timezone.utc)
