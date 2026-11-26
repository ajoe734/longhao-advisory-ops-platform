from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def wire_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def new_id() -> str:
    return str(uuid4())


def require_uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(f"{field_name} must be a UUID", field_name=field_name) from exc


@dataclass(frozen=True)
class ActorContext:
    """Identity and scope the trusted server resolved for this request.

    Every M10 command reads ``customer_id``/ownership facts from stored state
    or upstream event projections, never from a client-controlled body field,
    so ``ActorContext`` never carries a customer identity of its own.
    """

    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()
    principal_type: str = "staff"
    purpose: str = "operations"

    def __post_init__(self) -> None:
        require_uuid(self.tenant_id, "tenant_id")
        require_uuid(self.actor_id, "actor_id")
        if self.principal_type not in {"staff", "service"}:
            raise ValidationError("unsupported principal_type", field_name="principal_type")


class DomainError(Exception):
    status = 422
    code = "domain_error"
    retryable = False

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        current_version: int | None = None,
        field_name: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code or self.code
        self.current_version = current_version
        self.field_name = field_name


class ValidationError(DomainError):
    status = 422
    code = "validation_failed"


class UnauthorizedError(DomainError):
    status = 401
    code = "authentication_required"


class ForbiddenError(DomainError):
    status = 403
    code = "forbidden"


class NotFoundError(DomainError):
    status = 404
    code = "resource_not_found"


class ConflictError(DomainError):
    status = 409
    code = "conflict"


class StaleVersionError(DomainError):
    status = 412
    code = "stale_version"


class PreconditionRequiredError(DomainError):
    status = 428
    code = "precondition_required"


class DependencyUnavailableError(DomainError):
    status = 503
    code = "dependency_unavailable"
    retryable = True


# ---- shared vocabularies ---------------------------------------------------

#: OpportunityCreate.type / Opportunity.type (API) <-> crm.opportunity.kind (DB).
#: The baseline CHECK only accepted new/renewal/upgrade/reactivation; this
#: module's DB proposal widens it to also accept retention (additive).
OPPORTUNITY_KIND_TO_DB = {
    "new_subscription": "new",
    "renewal": "renewal",
    "upgrade": "upgrade",
    "retention": "retention",
    "reactivation": "reactivation",
}
OPPORTUNITY_KIND_FROM_DB = {value: key for key, value in OPPORTUNITY_KIND_TO_DB.items()}

#: Opportunity.state (API) <-> crm.opportunity.status (DB). ``cancelled`` is
#: the only DB value this module ever writes for an actively suppressed
#: opportunity (there is no separate client-reachable "withdraw" operation in
#: this module's published API surface), so the DB-to-API direction is
#: unambiguous.
OPPORTUNITY_STATE_TO_DB = {
    "open": "open",
    "qualified": "qualifying",
    "proposal": "proposed",
    "awaiting_customer": "awaiting_customer",
    "converted": "converted",
    "lost": "lost",
    "suppressed": "cancelled",
}
OPPORTUNITY_STATE_FROM_DB = {value: key for key, value in OPPORTUNITY_STATE_TO_DB.items()}

OPPORTUNITY_OPEN_STATES = frozenset({"open", "qualifying", "proposed", "awaiting_customer"})

#: CrmWorkItem.state (API) <-> crm.work_item.status (DB). ``assigned`` and
#: ``snoozed`` are both pre-start variants of "open" that the API does not
#: distinguish on its own (``owner_id``/``due_at`` already carry that detail);
#: an active suppression always wins and is projected to ``suppressed``
#: regardless of the underlying open/assigned/snoozed status.
WORK_ITEM_STATE_FROM_DB = {
    "open": "open",
    "assigned": "open",
    "snoozed": "open",
    "in_progress": "in_progress",
    "done": "completed",
    "cancelled": "cancelled",
}
WORK_ITEM_OPEN_DB_STATUSES = frozenset({"open", "assigned", "snoozed"})

WORKLISTS = frozenset(
    {
        "incomplete_application",
        "pending_review",
        "unpaid",
        "renewal",
        "upgrade",
        "retention",
        "reactivation",
        "company_pool",
    }
)

SALES_ACTIVITY_TYPES = frozenset(
    {"two_way_message", "completed_call", "meeting", "needs_resolution", "proposal", "follow_up"}
)
#: activity types that are, by construction, never evidence of a genuine
#: two-way human exchange (REQ-M10-004 / UAT-M10-004).
_NON_TWO_WAY_ACTIVITY_TYPES = frozenset({"proposal", "follow_up"})

SALES_ACTIVITY_OUTCOMES = frozenset(
    {"contacted", "needs_identified", "proposal_requested", "follow_up", "declined", "converted"}
)

NEXT_ACTION_TYPES = frozenset(
    {"call", "message", "meeting", "complete_documents", "renewal_offer", "upgrade_assessment", "resolve_issue"}
)

CONVERSION_KINDS = frozenset({"new_subscription", "renewal", "upgrade", "reactivation"})

#: Conversion.state (API) is a projection of (crm.conversion.decision_status,
#: len(claim_history)); see ``Conversion.api_state``.
CONVERSION_DECISION_STATUSES = frozenset(
    {"proposed", "under_review", "approved", "rejected", "disputed", "reversed"}
)

SUPPRESSION_SCOPES = frozenset({"marketing_contact", "sales_contact", "all_nonessential_contact"})
SUPPRESSION_REASON_CATEGORIES = frozenset(
    {"opt_out", "open_complaint", "refund_pending", "customer_request", "restricted_process"}
)

FUNNEL_STAGES = (
    "lead",
    "application_started",
    "customer_signed",
    "due_diligence",
    "accepted",
    "paid",
    "active",
    "renewal",
    "upgrade",
    "churn",
)


# ---- entities ---------------------------------------------------------------


@dataclass
class MarketingAttribution:
    """``crm.marketing_attribution``: immutable original-source evidence."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    source_type: str
    acquired_at: datetime
    evidence_sha256: str
    campaign_ref: str | None = None
    channel_ref: str | None = None
    evidence_asset_id: str | None = None
    supersedes_id: str | None = None


@dataclass
class Opportunity:
    """``crm.opportunity``: one renewal/upgrade/reactivation/new-subscription lead."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    kind: str
    target_service_code: str
    due_at: datetime
    status: str = "open"
    owner_actor_id: str | None = None
    contract_service_id: str | None = None
    open_reason_code: str | None = None
    loss_reason_code: str | None = None
    converted_at: datetime | None = None

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "type": OPPORTUNITY_KIND_FROM_DB[self.kind],
            "service_code": self.target_service_code,
            "owner_id": self.owner_actor_id,
            "state": OPPORTUNITY_STATE_FROM_DB[self.status],
            "contract_id": self.contract_service_id,
            "due_at": wire_time(self.due_at),
            "converted_at": wire_time(self.converted_at) if self.converted_at else None,
        }


@dataclass
class SalesActivity:
    """``crm.activity``: append-only evidence of one real sales interaction."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    opportunity_id: str
    actor_id: str
    activity_type: str
    occurred_at: datetime
    evidence_asset_id: str
    two_way: bool
    outcome_code: str
    qualifies_for_claim: bool = False
    customer_response_evidence_id: str | None = None
    needs_summary: str | None = None
    late_entry: bool = False
    late_entry_approved_by: str | None = None
    late_entry_approved_at: datetime | None = None

    def attribution_review_reason(self) -> str | None:
        if self.qualifies_for_claim:
            return None
        if self.late_entry and self.late_entry_approved_at is None:
            return "late_entry_pending_approval"
        if not self.two_way:
            return "evidence_insufficient_not_two_way"
        return "evidence_insufficient"

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "opportunity_id": self.opportunity_id,
            "customer_id": self.customer_id,
            "actor_id": self.actor_id,
            "activity_type": self.activity_type,
            "occurred_at": wire_time(self.occurred_at),
            "evidence_asset_id": self.evidence_asset_id,
            "qualifies_for_attribution": self.qualifies_for_claim,
            "attribution_review_reason": self.attribution_review_reason(),
        }


@dataclass
class ClaimRecord:
    """One append-only ``claim_conversion`` attempt inside ``claim_history``."""

    salesperson_id: str
    activity_ids: tuple[str, ...]
    claimed_at: datetime
    reason_code: str

    def to_json(self) -> dict[str, Any]:
        return {
            "salesperson_id": self.salesperson_id,
            "activity_ids": list(self.activity_ids),
            "claimed_at": wire_time(self.claimed_at),
            "reason_code": self.reason_code,
        }


@dataclass
class Conversion:
    """``crm.conversion``: one contract_service's sales-attribution decision.

    Unique per ``contract_service_id``: a second claim on an already-claimed
    transaction never creates a second row, it appends to ``claim_history``
    and (if the row is still ``proposed``) surfaces as ``under_review``.
    """

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    opportunity_id: str
    contract_service_id: str
    application_id: str
    conversion_kind: str
    finance_transaction_ref: str
    claimed_salesperson_id: str
    claim_history: tuple[ClaimRecord, ...]
    evidence_activity_ids: tuple[str, ...]
    decision_status: str = "proposed"
    sales_actor_id: str | None = None
    decided_by: str | None = None
    decision_at: datetime | None = None
    attribution_id: str | None = None
    policy_version: str = "m10-conversion-policy-v1"

    def api_state(self) -> str:
        if self.decision_status == "proposed" and len(self.claim_history) > 1:
            return "under_review"
        if self.decision_status == "proposed":
            return "claimed"
        return self.decision_status

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "opportunity_id": self.opportunity_id,
            "contract_id": self.contract_service_id,
            "conversion_type": self.conversion_kind,
            "claimed_salesperson_id": self.claimed_salesperson_id,
            "approved_salesperson_id": self.sales_actor_id,
            "state": self.api_state(),
            "evidence_activity_ids": list(self.evidence_activity_ids),
            "decision_at": wire_time(self.decision_at) if self.decision_at else None,
            "finance_transaction_ref": self.finance_transaction_ref,
        }


@dataclass
class CrmWorkItem:
    """``crm.work_item``: one concrete, assignable piece of sales follow-up."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    worklist: str
    dedupe_key: str
    next_action: str
    due_at: datetime
    reason_codes: tuple[str, ...]
    status: str = "open"
    opportunity_id: str | None = None
    owner_actor_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    suggestion_origin: str = "rule"
    note: str | None = None

    @property
    def reason_code(self) -> str:
        return self.reason_codes[0] if self.reason_codes else "unknown"

    def to_api(self, allowed_actions: list[str], *, suppression_applied: bool) -> dict[str, Any]:
        state = "suppressed" if suppression_applied and self.status in WORK_ITEM_OPEN_DB_STATUSES else WORK_ITEM_STATE_FROM_DB[self.status]
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "opportunity_id": self.opportunity_id,
            "worklist": self.worklist,
            "owner_id": self.owner_actor_id,
            "action_type": self.next_action,
            "due_at": wire_time(self.due_at),
            "state": state,
            "reason_codes": list(self.reason_codes),
            "evidence_refs": list(self.evidence_refs),
            "suggestion_origin": self.suggestion_origin,
            "suppression_applied": suppression_applied,
        }


@dataclass
class Suppression:
    """``crm.suppression``: a sales/marketing contact restriction, never a service-notice block."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    scope: str
    reason_code: str
    actor_id: str
    valid_from: datetime
    reason_ref: str | None = None
    valid_until: datetime | None = None

    def active(self, as_of: datetime) -> bool:
        return self.valid_from <= as_of and (self.valid_until is None or as_of < self.valid_until)

    def to_api(self, allowed_actions: list[str], *, as_of: datetime) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "scope": self.scope,
            "reason_category": self.reason_code,
            "active": self.active(as_of),
            "expires_at": wire_time(self.valid_until) if self.valid_until else None,
        }


# ---- upstream projections (read-only local mirrors) ------------------------


@dataclass
class ApplicationProjection:
    tenant_id: str
    application_id: str
    customer_id: str
    package_version_id: str
    intent: str
    customer_signed: bool = False
    company_decision: str | None = None
    contract_id: str | None = None


@dataclass
class ContractProjection:
    tenant_id: str
    contract_id: str
    customer_id: str
    effective_at: datetime | None = None
    terminated_at: datetime | None = None
    service_ends_at: datetime | None = None


@dataclass
class EntitlementProjection:
    tenant_id: str
    entitlement_id: str
    customer_id: str
    contract_id: str
    state: str
    valid_until: datetime


@dataclass
class OwnershipProjection:
    """``ownership.assignment_changed``/``ownership.pool_entered`` local mirror."""

    tenant_id: str
    customer_id: str
    salesperson_id: str | None
    state: str
    updated_at: datetime
    in_company_pool: bool = False


@dataclass
class SupportCaseProjection:
    tenant_id: str
    case_id: str
    customer_id: str
    category: str
    priority: str
    open: bool = True


@dataclass
class PaymentProjection:
    tenant_id: str
    application_id: str
    status: str
    customer_id: str | None = None
    reason_code: str | None = None


# ---- audit / outbox / inbox / idempotency ----------------------------------


@dataclass
class AuditEvent:
    id: str
    tenant_id: str
    actor_id: str | None
    purpose: str
    action: str
    aggregate_type: str
    aggregate_id: str | None
    aggregate_version: int | None
    correlation_id: str
    result: str
    reason_code: str | None
    recorded_at: datetime = field(default_factory=utc_now)


@dataclass
class OutboxEvent:
    event_id: str
    event_type: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    sequence_no: int
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    classification: str
    payload: dict[str, Any]
    occurred_at: datetime
    emitted_at: datetime
    status: str = "pending"
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None

    @property
    def payload_sha256(self) -> str:
        return canonical_hash(self.payload)

    def envelope(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": "1.0.0",
            "tenant_id": self.tenant_id,
            "producer": "M10",
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "aggregate_version": self.aggregate_version,
            "sequence_no": self.sequence_no,
            "occurred_at": wire_time(self.occurred_at),
            "emitted_at": wire_time(self.emitted_at),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "idempotency_key": self.idempotency_key,
            "classification": self.classification,
            "payload_sha256": self.payload_sha256,
            "payload": self.payload,
        }


@dataclass
class InboxReceipt:
    tenant_id: str
    consumer: str
    producer: str
    event_id: str
    event_type: str
    payload_sha256: str
    aggregate_id: str
    sequence_no: int
    status: str
    result_code: str
    applied_at: datetime | None


@dataclass
class IdempotencyReceipt:
    tenant_id: str
    actor_id: str
    operation: str
    key: str
    request_sha256: str
    response_status: int
    response_value: Any
    error: DomainError | None
    completed_at: datetime
