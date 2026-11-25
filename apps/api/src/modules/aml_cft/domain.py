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


# Deterministic synthetic system actor used for automated screening/outcome
# projections (clear, provider error, inbound event-driven refresh) that are
# not an individual officer's decision. No real human identity is ever used.
SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-00000000a001"


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
    audit_grant_case_ids: frozenset[str] = frozenset()
    audit_grant_expires_at: datetime | None = None

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


# ---- restricted AML aggregates ------------------------------------------


@dataclass
class AmlScreening:
    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    application_id: str
    customer_id: str
    submission_id: str
    provider: str
    provider_request_id: str
    provider_ref: str
    policy_version: str
    scope: list[str]
    request_sha256: str
    reason_code: str
    restricted_identity_asset_id: str
    list_version: str | None = None
    state: str = "pending"
    result_asset_id: str | None = None
    screened_at: datetime | None = None
    expires_at: datetime | None = None
    case_id: str | None = None
    manual_required: bool = False

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "application_id": self.application_id,
            "customer_id": self.customer_id,
            "provider_ref": self.provider_ref,
            "policy_version": self.policy_version,
            "list_version": self.list_version,
            "state": self.state,
            "result_asset_id": self.result_asset_id,
            "screened_at": wire_time(self.screened_at) if self.screened_at else None,
            "expires_at": wire_time(self.expires_at) if self.expires_at else None,
            "case_id": self.case_id,
        }


@dataclass
class AmlCaseFile:
    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    application_id: str
    screening_ids: list[str]
    opened_at: datetime
    state: str = "open"
    risk_level: str = "standard"
    assignee_id: str | None = None
    restricted_evidence_ids: list[str] = field(default_factory=list)
    next_review_due_at: datetime | None = None
    legal_hold: bool = False
    legal_hold_reason_code: str | None = None
    maker_actor_ids: set[str] = field(default_factory=set)
    restricted_notes: list[str] = field(default_factory=list)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "application_id": self.application_id,
            "screening_ids": list(self.screening_ids),
            "state": self.state,
            "risk_level": self.risk_level,
            "assignee_id": self.assignee_id,
            "restricted_evidence_ids": list(self.restricted_evidence_ids),
            "opened_at": wire_time(self.opened_at),
            "next_review_due_at": wire_time(self.next_review_due_at) if self.next_review_due_at else None,
        }


@dataclass
class AmlCaseAction:
    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    case_id: str
    actor_id: str
    action_code: str
    reason_code: str
    restricted_note_asset_id: str
    occurred_at: datetime
    restricted_evidence_ids: list[str] = field(default_factory=list)
    due_at: datetime | None = None
    evidence_asset_id: str | None = None
    independent_reviewer_id: str | None = None


@dataclass
class AmlDeclaration:
    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    application_id: str
    customer_id: str
    form_version: str
    declaration_consent_id: str
    restricted_answer_asset_id: str
    restricted_document_ids: list[str]
    customer_signature_id: str
    received_at: datetime
    state: str = "received"

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "application_id": self.application_id,
            "received_at": wire_time(self.received_at),
            "state": self.state,
            "is_business_clearance": False,
        }


@dataclass
class BusinessClearance:
    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    application_id: str
    customer_id: str
    decision: str
    policy_version: str
    decided_by: str
    decided_at: datetime
    valid_until: datetime
    safe_reason_code: str
    case_id: str | None = None

    def to_api(self, allowed_actions: list[str], *, as_of: datetime | None = None) -> dict[str, Any]:
        now = as_of or utc_now()
        decision = self.decision
        if decision == "approved" and self.valid_until <= now:
            decision = "expired"
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "application_id": self.application_id,
            "customer_id": self.customer_id,
            "decision": decision,
            "decided_at": wire_time(self.decided_at),
            "expires_at": wire_time(self.valid_until),
            "policy_version": self.policy_version,
        }


@dataclass
class AmlCaseReadGrant:
    id: str
    tenant_id: str
    case_id: str
    auditor_actor_id: str
    granted_by: str
    reason_code: str
    created_at: datetime
    expires_at: datetime


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
            "producer": "M04",
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
