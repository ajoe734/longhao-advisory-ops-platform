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
    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()
    principal_type: str = "staff"
    customer_id: str | None = None
    purpose: str = "operations"

    def __post_init__(self) -> None:
        require_uuid(self.tenant_id, "tenant_id")
        require_uuid(self.actor_id, "actor_id")
        if self.customer_id is not None:
            require_uuid(self.customer_id, "customer_id")
        if self.principal_type not in {"customer", "staff", "service"}:
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


CASE_STATES = {"new", "triaged", "assigned", "waiting_customer", "waiting_internal", "resolved", "closed", "reopened"}
CASE_CATEGORIES = {
    "application", "contract", "access", "research_question", "billing", "refund", "complaint", "technical", "other",
}
CASE_PRIORITIES = {"normal", "high", "urgent"}
INTAKE_CHANNELS = {"web", "line", "email", "phone", "onsite"}
INTERACTION_DIRECTIONS = {"inbound", "outbound", "internal"}
ESCALATION_QUEUES = {"analyst", "finance", "compliance", "technical", "service_supervisor"}
DRAFT_STATES = {"queued", "drafted", "requires_human", "blocked", "failed"}
DISPATCH_CHANNELS = {"web", "line", "sms", "email"}
APPROVAL_MODES = {"human_reviewed", "approved_low_risk_template"}
DISPATCH_STATES = {"queued", "accepted", "sent", "delivered", "failed", "unknown", "cancelled", "expired"}
RECEIPT_PROVIDER_STATUSES = {"accepted", "sent", "delivered", "failed", "read"}
_DISPATCH_RANK = {"queued": 0, "accepted": 1, "sent": 2, "delivered": 3}


@dataclass
class ServiceCase:
    id: str
    tenant_id: str
    case_number: str
    version: int
    created_at: datetime
    updated_at: datetime
    status: str
    customer_id: str | None
    intake_channel: str
    category: str
    subject: str
    description: str
    priority: str
    sla_policy_version: str
    first_response_due_at: datetime
    resolution_due_at: datetime | None
    assignee_id: str | None = None
    maintenance_owner_id: str | None = None
    sla_paused_reason: str | None = None
    contract_id: str | None = None
    publication_id: str | None = None
    payment_id: str | None = None
    linked_refund_id: str | None = None
    original_content_asset_id: str | None = None
    escalated_queue: str | None = None
    escalated_due_at: datetime | None = None
    escalation_evidence_asset_id: str | None = None
    resolved_at: datetime | None = None
    resolution_code: str | None = None

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        verified = self.customer_id is not None
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "intake_channel": self.intake_channel,
            "category": self.category,
            "subject": self.subject,
            "state": self.status,
            "priority": self.priority,
            "assignee_id": self.assignee_id,
            "maintenance_owner_id": self.maintenance_owner_id,
            "first_response_due_at": wire_time(self.first_response_due_at),
            "resolution_due_at": wire_time(self.resolution_due_at) if self.resolution_due_at else None,
            "sla_paused_reason": self.sla_paused_reason,
            "linked_contract_id": self.contract_id if verified else None,
            "linked_publication_id": self.publication_id if verified else None,
            "linked_refund_id": self.linked_refund_id if verified else None,
        }


@dataclass
class CaseInteraction:
    id: str
    tenant_id: str
    case_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    channel: str
    direction: str
    occurred_at: datetime
    recorded_by: str
    content_asset_id: str
    summary: str
    publication_reference_ids: list[str] = field(default_factory=list)
    external_message_id: str | None = None
    recording_consent_ref: str | None = None
    ai_draft: bool = False

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "case_id": self.case_id,
            "channel": self.channel,
            "direction": self.direction,
            "occurred_at": wire_time(self.occurred_at),
            "recorded_by": self.recorded_by,
            "content_asset_id": self.content_asset_id,
            "summary": self.summary,
            "publication_reference_ids": list(self.publication_reference_ids),
        }

    def to_customer_timeline_api(self) -> dict[str, Any]:
        """Return the deliberately small, customer-safe interaction projection.

        The original-content asset, staff identity and publication references are
        internal evidence.  A customer timeline may show only an interaction
        which was actually between the customer and the service channel; callers
        enforce that ``direction`` is never ``internal`` before using this view.
        """
        return {
            "id": self.id,
            "case_id": self.case_id,
            "channel": self.channel,
            "direction": self.direction,
            "occurred_at": wire_time(self.occurred_at),
            "summary": self.summary,
        }


@dataclass
class ResponseDraft:
    id: str
    tenant_id: str
    case_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    status: str
    created_by: str
    knowledge_version: str
    model_version: str
    prompt_version: str
    source_refs: list[str] = field(default_factory=list)
    text: str | None = None
    content_asset_id: str | None = None
    content_sha256: str | None = None
    contains_new_investment_judgment: bool = False
    supersedes_id: str | None = None

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "case_id": self.case_id,
            "state": self.status,
            "text": self.text,
            "citations": list(self.source_refs),
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "contains_new_investment_judgment": self.contains_new_investment_judgment,
            "send_authorized": False,
        }


@dataclass
class ResponseApproval:
    id: str
    tenant_id: str
    draft_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    draft_content_sha256: str
    decision: str
    approval_mode: str
    reviewer_id: str
    template_version: str
    policy_version: str
    evidence_asset_id: str
    approved_at: datetime
    valid_until: datetime

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "draft_id": self.draft_id,
            "draft_content_sha256": self.draft_content_sha256,
            "decision": self.decision,
            "approval_mode": self.approval_mode,
            "reviewer_id": self.reviewer_id,
            "template_version": self.template_version,
            "policy_version": self.policy_version,
            "evidence_asset_id": self.evidence_asset_id,
            "approved_at": wire_time(self.approved_at),
            "valid_until": wire_time(self.valid_until),
        }


@dataclass
class ResponseDispatch:
    id: str
    tenant_id: str
    case_id: str
    customer_id: str
    draft_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    status: str
    content_sha256: str
    channel_binding_id: str
    channel: str
    approval_id: str
    approval_mode: str
    idempotency_key: str
    requested_by: str
    valid_until: datetime
    publication_reference_ids: list[str] = field(default_factory=list)
    provider_message_ref: str | None = None
    accepted_at: datetime | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    outbound_interaction_id: str | None = None
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "case_id": self.case_id,
            "draft_id": self.draft_id,
            "content_sha256": self.content_sha256,
            "channel_binding_id": self.channel_binding_id,
            "channel": self.channel,
            "approval_mode": self.approval_mode,
            "approval_id": self.approval_id,
            "state": self.status,
            "provider_message_ref": self.provider_message_ref,
            "accepted_at": wire_time(self.accepted_at) if self.accepted_at else None,
            "sent_at": wire_time(self.sent_at) if self.sent_at else None,
            "delivered_at": wire_time(self.delivered_at) if self.delivered_at else None,
            "read_at": wire_time(self.read_at) if self.read_at else None,
            "valid_until": wire_time(self.valid_until),
            "outbound_interaction_id": self.outbound_interaction_id,
        }

    def to_customer_timeline_api(self) -> dict[str, Any]:
        """Return delivery facts that are safe for the receiving customer.

        Draft identifiers, approval evidence, provider references and content
        hashes are workflow-internal.  The service applies this projection only
        after a provider reports ``sent`` or ``delivered``.
        """
        return {
            "id": self.id,
            "case_id": self.case_id,
            "occurred_at": wire_time(self.created_at),
            "channel": self.channel,
            "state": self.status,
            "sent_at": wire_time(self.sent_at) if self.sent_at else None,
            "delivered_at": wire_time(self.delivered_at) if self.delivered_at else None,
        }


@dataclass
class ResponseAttempt:
    id: str
    tenant_id: str
    response_id: str
    attempt_no: int
    provider: str
    provider_request_id: str
    outcome: str
    started_at: datetime
    finished_at: datetime | None = None
    error_code: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ResponseReceipt:
    id: str
    tenant_id: str
    response_id: str
    provider: str
    event_id: str
    provider_message_ref: str
    provider_status: str
    provider_occurred_at: datetime
    received_at: datetime
    provider_receipt_asset_id: str
    payload_sha256: str
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class CaseResolution:
    id: str
    tenant_id: str
    case_id: str
    resolution_code: str
    resolution_summary: str
    customer_notice_evidence_id: str
    resolved_by: str
    resolved_at: datetime
    linked_refund_id: str | None = None
    follow_up_due_at: datetime | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class SlaEvent:
    id: str
    tenant_id: str
    case_id: str
    action: str
    occurred_at: datetime
    actor_id: str
    reason_code: str
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class AuditEvent:
    id: str
    tenant_id: str
    actor_id: str
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
            "producer": "M13",
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


@dataclass
class Job:
    id: str
    tenant_id: str
    kind: str
    payload: dict[str, Any]
    status: str = "pending"
    attempt_count: int = 0
    max_attempts: int = 5
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None
    redrive_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
