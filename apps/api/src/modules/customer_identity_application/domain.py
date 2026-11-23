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


@dataclass
class Customer:
    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    status: str
    display_name: str
    masked_email: str | None = None
    masked_phone: str | None = None
    maintenance_owner_id: str | None = None
    original_marketing_attribution_id: str | None = None
    original_marketing_source_ref: str | None = None
    merged_into_customer_id: str | None = None
    preferred_locale: str = "zh-TW"
    contact_lookup_hmacs: set[str] = field(default_factory=set)
    acquisition_touches: list[str] = field(default_factory=list)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        status = "blocked" if self.status == "restricted" else self.status
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "status": status,
            "display_name": self.display_name,
            "masked_email": self.masked_email,
            "masked_phone": self.masked_phone,
            "maintenance_owner_id": self.maintenance_owner_id,
            "original_marketing_attribution_id": self.original_marketing_attribution_id,
            "merged_into_customer_id": self.merged_into_customer_id,
        }


@dataclass
class ExternalIdentity:
    id: str
    tenant_id: str
    customer_id: str
    provider: str
    subject_hash: str
    verified_at: datetime
    proof_ref: str
    version: int = 1
    status: str = "verified"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "provider": self.provider,
            "subject_hash": self.subject_hash,
            "verified_at": wire_time(self.verified_at),
            "status": self.status,
        }


@dataclass
class Application:
    id: str
    tenant_id: str
    customer_id: str
    package_version_id: str
    service_selections: list[dict[str, Any]]
    intent: str
    preceding_contract_id: str | None
    original_acquisition_touch_ref: str | None
    version: int = 1
    state: str = "draft"
    customer_signed_at: datetime | None = None
    company_accepted_at: datetime | None = None
    kyc_status: str = "not_started"
    suitability_status: str = "pending"
    business_clearance: str = "pending"
    payment_status: str = "not_required"
    contract_id: str | None = None
    withdrawal_reason_code: str | None = None
    withdrawn_at: datetime | None = None
    acquisition_touches: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "package_version_id": self.package_version_id,
            "state": self.state,
            "customer_signed_at": wire_time(self.customer_signed_at) if self.customer_signed_at else None,
            "company_accepted_at": wire_time(self.company_accepted_at) if self.company_accepted_at else None,
            "kyc_status": self.kyc_status,
            "suitability_status": self.suitability_status,
            "business_clearance": self.business_clearance,
            "payment_status": self.payment_status,
            "contract_id": self.contract_id,
        }


@dataclass
class Consent:
    id: str
    tenant_id: str
    customer_id: str
    purpose: str
    document_version: str
    document_sha256: str
    granted: bool
    evidence_asset_id: str
    recorded_at: datetime
    channel: str
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": [],
            "customer_id": self.customer_id,
            "purpose": self.purpose,
            "document_version": self.document_version,
            "document_sha256": self.document_sha256,
            "granted": self.granted,
            "recorded_at": wire_time(self.recorded_at),
            "evidence_asset_id": self.evidence_asset_id,
        }


@dataclass
class MergeCase:
    id: str
    tenant_id: str
    source_customer_id: str
    target_customer_id: str
    proposer_id: str
    evidence_asset_ids: list[str]
    reason_code: str
    version: int = 1
    status: str = "proposed"
    reviewer_id: str | None = None
    reviewer_evidence_asset_ids: list[str] = field(default_factory=list)
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        status = "applied" if self.status == "executed" else self.status
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "source_customer_id": self.source_customer_id,
            "target_customer_id": self.target_customer_id,
            "status": status,
            "proposer_id": self.proposer_id,
            "reviewer_id": self.reviewer_id,
        }


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
            "producer": "M01",
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
