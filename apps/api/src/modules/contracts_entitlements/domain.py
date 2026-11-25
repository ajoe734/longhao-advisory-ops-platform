from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
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


_SHA_RE = re.compile(r"^[a-f0-9]{64}$")


def require_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise ValidationError(f"{field_name} must be a lowercase SHA-256", field_name=field_name)
    return value


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
        if self.principal_type not in {"customer", "staff", "service", "integration"}:
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


# ---- application read model (owned by M01; M02 only has a read grant) ----


@dataclass(frozen=True)
class ApplicationSnapshot:
    id: str
    tenant_id: str
    version: int
    customer_id: str
    package_version_id: str
    service_selections: tuple[dict[str, Any], ...]
    intent: str
    preceding_contract_id: str | None
    state: str


@dataclass(frozen=True)
class KycStatus:
    submission_id: str
    decision: str
    version: int


@dataclass(frozen=True)
class SuitabilityStatus:
    assessment_id: str
    state: str
    version: int


@dataclass(frozen=True)
class ClearanceStatus:
    clearance_id: str
    decision: str
    version: int
    expires_at: datetime | None


# ---- M02-owned aggregates ----


@dataclass
class PackageVersion:
    id: str
    tenant_id: str
    package_code: str
    version_label: str
    sequence_no: int
    documents: list[dict[str, Any]]
    service_codes: list[str]
    effective_from: datetime
    acceptance_terms_version: str
    proposed_by: str
    version: int = 1
    state: str = "draft"
    package_sha256: str = ""
    approved_by: str | None = None
    approved_at: datetime | None = None
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
            "package_code": self.package_code,
            "version_label": self.version_label,
            "documents": self.documents,
            "state": self.state,
            "approved_by": self.approved_by,
            "effective_from": wire_time(self.effective_from),
            "acceptance_terms_version": self.acceptance_terms_version,
        }


@dataclass
class SignatureSession:
    id: str
    tenant_id: str
    application_id: str
    package_version_id: str
    customer_id: str
    package_sha256: str
    electronic_signing_consent_id: str
    return_route: str
    provider_session_ref: str
    redirect_url: str
    expires_at: datetime
    version: int = 1
    purpose: str = "customer_application"
    status: str = "pending"
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
            "purpose": self.purpose,
            "subject_id": self.customer_id,
            "provider_session_ref": self.provider_session_ref,
            "redirect_url": self.redirect_url,
            "expires_at": wire_time(self.expires_at),
            "status": self.status,
        }


@dataclass
class CustomerSignature:
    """Immutable evidence; created only after a provider verification succeeds."""

    id: str
    tenant_id: str
    application_id: str
    package_version_id: str
    customer_id: str
    session_id: str
    content_sha256: str
    signed_file_sha256: str
    signed_asset_id: str
    provider: str
    provider_transaction_id: str
    assurance_level: str
    customer_signed_at: datetime
    evidence_asset_id: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class AcceptanceDecision:
    """Every company decision (accept/reject/request_changes); immutable history."""

    id: str
    tenant_id: str
    application_id: str
    customer_signature_id: str
    decision: str
    decided_by: str
    decided_at: datetime
    safe_reason_code: str
    evidence_snapshot: dict[str, Any]
    contract_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": 1,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.created_at),
            "allowed_actions": allowed_actions,
            "application_id": self.application_id,
            "decision": self.decision,
            "actor_id": self.decided_by,
            "decided_at": wire_time(self.decided_at),
            "decision_evidence_ids": self.evidence_snapshot.get("evidence_asset_ids", [self.customer_signature_id]),
            "intended_effective_at": self.evidence_snapshot.get("intended_effective_at"),
            "contract_id": self.contract_id,
        }


@dataclass
class CompanyAcceptance:
    """Only materialized when ``decision == accept``; separate from customer signing."""

    id: str
    tenant_id: str
    decision_id: str
    application_id: str
    customer_signature_id: str
    kyc_submission_id: str
    suitability_assessment_id: str
    aml_clearance_id: str
    accepted_by: str
    accepted_at: datetime
    conditions_snapshot: dict[str, Any]
    intended_effective_at: datetime
    payment_condition_version: str
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class Contract:
    id: str
    tenant_id: str
    application_id: str
    customer_id: str
    customer_signature_id: str
    company_acceptance_id: str
    package_version_id: str
    contract_number: str
    currency: str
    terms_snapshot: dict[str, Any]
    intended_effective_at: datetime
    version: int = 1
    status: str = "accepted"
    effective_at: datetime | None = None
    terminated_at: datetime | None = None
    obligations_ended_at: datetime | None = None
    preceding_contract_id: str | None = None
    service_selections: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str], *, customer_signed_at: datetime, company_accepted_at: datetime) -> dict[str, Any]:
        starts = min((row["starts_at"] for row in self.terms_snapshot.get("service_windows", [])), default=None)
        ends = max((row["ends_at"] for row in self.terms_snapshot.get("service_windows", [])), default=None)
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "application_id": self.application_id,
            "package_version_id": self.package_version_id,
            "state": self.status,
            "customer_signed_at": wire_time(customer_signed_at),
            "company_accepted_at": wire_time(company_accepted_at),
            "effective_at": wire_time(self.effective_at) if self.effective_at else None,
            "service_starts_at": starts,
            "service_ends_at": ends,
            "terminated_at": wire_time(self.terminated_at) if self.terminated_at else None,
            "service_selections": self.service_selections,
            "preceding_contract_id": self.preceding_contract_id,
        }


@dataclass
class ContractService:
    id: str
    tenant_id: str
    contract_id: str
    service_code: str
    service_chain_id: str
    starts_at: datetime
    ends_at: datetime
    untaxed_contract_amount: str
    tax_profile_ref: str
    features_snapshot: dict[str, Any]
    predecessor_service_id: str | None = None
    auto_renew: bool = False
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class Entitlement:
    id: str
    tenant_id: str
    customer_id: str
    contract_id: str
    contract_service_id: str
    feature_code: str
    valid_from: datetime
    valid_until: datetime
    status: str = "locked"
    payment_condition_ref: str | None = None
    restriction_codes: list[str] = field(default_factory=list)
    last_decision_id: str | None = None
    version: int = 1
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
            "contract_id": self.contract_id,
            "service_code": self.feature_code,
            "state": self.status,
            "valid_from": wire_time(self.valid_from),
            "valid_until": wire_time(self.valid_until),
            "last_decision_id": self.last_decision_id or "00000000-0000-0000-0000-000000000000",
            "restriction_codes": self.restriction_codes,
        }


@dataclass
class EntitlementDecision:
    id: str
    tenant_id: str
    contract_id: str
    decision: str
    policy_version: str
    condition_snapshots: list[dict[str, Any]]
    entitlement_ids: list[str]
    decided_at: datetime
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.created_at),
            "allowed_actions": allowed_actions,
            "contract_id": self.contract_id,
            "decision": self.decision,
            "policy_version": self.policy_version,
            "condition_snapshots": self.condition_snapshots,
            "entitlement_ids": self.entitlement_ids,
            "decided_at": wire_time(self.decided_at),
        }


@dataclass
class AmendmentRecord:
    """Pending renewal/upgrade projection returned in the ``Application`` shape.

    M02 does not own ``customer.application``; a real upgrade/renewal that needs a
    fresh signature or company review must still go through M01. This record only
    represents a same-contract service extension that reuses the existing company
    acceptance (see README NOT_CONNECTED notes).
    """

    id: str
    tenant_id: str
    customer_id: str
    package_version_id: str
    contract_id: str
    new_contract_service_id: str
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": 1,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": [],
            "customer_id": self.customer_id,
            "package_version_id": self.package_version_id,
            "state": "draft",
            "customer_signed_at": None,
            "company_accepted_at": None,
            "kyc_status": "approved",
            "suitability_status": "suitable",
            "business_clearance": "approved",
            "payment_status": "not_required",
            "contract_id": self.contract_id,
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
            "producer": "M02",
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
