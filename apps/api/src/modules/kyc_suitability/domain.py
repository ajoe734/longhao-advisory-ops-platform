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


# ---- aggregates --------------------------------------------------------

_SUBMISSION_STATES = {
    "not_started", "in_progress", "submitted", "incomplete", "under_review", "approved", "rejected", "refresh_required",
}
_SUITABILITY_STATES = {"pending", "suitable", "conditional", "not_suitable", "reassessment_due"}
_SCAN_STATES = {"pending", "clean", "rejected"}


@dataclass
class KycSubmission:
    id: str
    tenant_id: str
    application_id: str
    customer_id: str
    submission_version: int
    form_version: str
    version: int = 1
    status: str = "not_started"
    answers: dict[str, Any] = field(default_factory=dict)
    answer_snapshot_asset_id: str | None = None
    answer_sha256: str | None = None
    document_ids: list[str] = field(default_factory=list)
    missing_field_codes: list[str] = field(default_factory=list)
    declaration_consent_id: str | None = None
    identity_verification_method: str | None = None
    verification_evidence_ids: list[str] = field(default_factory=list)
    submitted_at: datetime | None = None
    submitted_by: str | None = None
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    next_review_due_at: datetime | None = None
    decision_reason_code: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in _SUBMISSION_STATES:
            raise ValidationError("submission status is invalid", field_name="status")

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
            "form_version": self.form_version,
            "state": self.status,
            "answer_snapshot_asset_id": self.answer_snapshot_asset_id,
            "answer_sha256": self.answer_sha256,
            "document_ids": list(self.document_ids),
            "submitted_at": wire_time(self.submitted_at) if self.submitted_at else None,
            "reviewer_id": self.reviewer_id,
            "reviewed_at": wire_time(self.reviewed_at) if self.reviewed_at else None,
            "next_review_due_at": wire_time(self.next_review_due_at) if self.next_review_due_at else None,
            "missing_field_codes": list(self.missing_field_codes),
        }

    def masked_business_view(self, allowed_actions: list[str]) -> dict[str, Any]:
        """UAT-M03-008 projection: missing items and due dates only, no evidence refs."""
        body = self.to_api(allowed_actions)
        body["answer_snapshot_asset_id"] = None
        body["answer_sha256"] = None
        body["document_ids"] = []
        body["reviewer_id"] = None
        return body


@dataclass
class SubmissionDocument:
    """``id`` is the same opaque reference returned to callers as ``asset_id``
    in ``UploadTicket``/``DocumentStatus``; there is no separate internal key."""

    id: str
    tenant_id: str
    submission_id: str
    customer_id: str
    document_type: str
    filename_extension: str
    media_type: str
    size_bytes: int
    sha256: str
    version: int = 1
    scan_status: str = "pending"
    scan_rejection_code: str | None = None
    scanned_at: datetime | None = None
    verification_state: str = "pending"
    verified_by: str | None = None
    verified_at: datetime | None = None
    classification: str = "customer_confidential"
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.scan_status not in _SCAN_STATES:
            raise ValidationError("document scan status is invalid")

    def to_status_api(self) -> dict[str, Any]:
        return {
            "asset_id": self.id,
            "scan_status": self.scan_status,
            "rejection_code": self.scan_rejection_code,
            "scanned_at": wire_time(self.scanned_at) if self.scanned_at else None,
            "classification": self.classification,
        }


@dataclass
class SuitabilityAssessment:
    id: str
    tenant_id: str
    application_id: str
    customer_id: str
    kyc_submission_id: str
    questionnaire_version: str
    rule_version: str
    version: int = 1
    status: str = "pending"
    answers_sha256: str | None = None
    service_codes: list[str] = field(default_factory=list)
    computed_decision: str | None = None
    conditions: list[str] = field(default_factory=list)
    reason_code: str | None = None
    overridden: bool = False
    assessed_by: str | None = None
    assessed_at: datetime | None = None
    next_review_due_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in _SUITABILITY_STATES:
            raise ValidationError("suitability status is invalid")

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
            "kyc_submission_id": self.kyc_submission_id,
            "questionnaire_version": self.questionnaire_version,
            "rule_version": self.rule_version,
            "state": self.status,
            "conditions": list(self.conditions),
            "assessed_by": self.assessed_by,
            "assessed_at": wire_time(self.assessed_at) if self.assessed_at else None,
            "next_review_due_at": wire_time(self.next_review_due_at) if self.next_review_due_at else None,
        }


@dataclass
class RefreshCase:
    """Internal due-diligence tracking; not yet a published wire resource."""

    id: str
    tenant_id: str
    customer_id: str
    submission_id: str
    trigger: str
    reason_code: str
    due_at: datetime
    next_form_version: str
    requested_by: str
    status: str = "pending"
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in {"pending", "manual_required", "completed"}:
            raise ValidationError("refresh case status is invalid")


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
            "producer": "M03",
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


@dataclass(frozen=True)
class SignedApplicationProjection:
    """Local projection fed by the `application.customer_signed` consumer."""

    tenant_id: str
    application_id: str
    customer_id: str
    customer_signature_id: str
    signed_at: datetime


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
    aggregate_version: int | None = None
