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


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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


REVIEW_TYPES = {"professional", "compliance"}
REVIEW_DECISIONS = {"approve", "request_changes", "reject"}
VERSION_STATUSES = {
    "draft", "in_review", "changes_requested", "ready_to_sign",
    "signature_pending", "signed", "superseded", "withdrawn",
}
REPORT_TYPES = {"initial", "update", "exit", "supplement", "correction"}
SIGNATURE_REQUEST_STATUSES = {"pending", "verified", "failed", "expired"}


@dataclass
class AdvisoryReport:
    id: str
    tenant_id: str
    review_case_id: str
    title: str
    instrument_refs: list[dict[str, Any]]
    report_type: str
    report_number: str
    created_by: str
    preceding_report_id: str | None = None
    current_version_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str], state: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "review_case_id": self.review_case_id,
            "title": self.title,
            "instrument_refs": self.instrument_refs,
            "report_type": self.report_type,
            "current_version_id": self.current_version_id,
            "state": state,
        }


@dataclass
class ReportVersion:
    id: str
    tenant_id: str
    report_id: str
    version_number: int
    source_candidate_id: str
    source_artifact_ref: dict[str, Any]
    report_asset_id: str
    report_content_sha256: str
    bundle_manifest_asset_id: str
    bundle_manifest: dict[str, Any]
    bundle_manifest_sha256: str
    channel_texts: list[dict[str, Any]]
    audience_policy: dict[str, Any]
    recommendation_version_ids: list[str]
    evidence_snapshot_asset_ids: list[str]
    valid_from: datetime
    valid_until: datetime
    created_by: str
    review_policy_version: str = "m07-review-policy-v1"
    requires_compliance_review: bool = True
    supersedes_version_id: str | None = None
    status: str = "draft"
    frozen_at: datetime | None = None
    signed_at: datetime | None = None
    signature_id: str | None = None
    signed_file_sha256: str | None = None
    required_review_ids: list[str] = field(default_factory=list)
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
            "report_id": self.report_id,
            "version_number": self.version_number,
            "source_candidate_id": self.source_candidate_id,
            "source_artifact_ref": self.source_artifact_ref,
            "report_asset_id": self.report_asset_id,
            "report_content_sha256": self.report_content_sha256,
            "bundle_manifest_asset_id": self.bundle_manifest_asset_id,
            "bundle_manifest_sha256": self.bundle_manifest_sha256,
            "signed_file_sha256": self.signed_file_sha256,
            "state": self.status,
            "channel_texts": self.channel_texts,
            "audience_policy": self.audience_policy,
            "valid_from": wire_time(self.valid_from),
            "valid_until": wire_time(self.valid_until),
            "frozen_at": wire_time(self.frozen_at) if self.frozen_at else None,
            "signed_at": wire_time(self.signed_at) if self.signed_at else None,
            "signature_id": self.signature_id,
            "required_review_ids": self.required_review_ids,
        }


@dataclass
class ReviewDecision:
    id: str
    tenant_id: str
    report_version_id: str
    review_type: str
    decision: str
    reviewer_id: str
    reviewed_at: datetime
    report_content_sha256: str
    bundle_manifest_sha256: str
    checklist_result_asset_id: str
    reason_code: str
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
            "report_version_id": self.report_version_id,
            "review_type": self.review_type,
            "decision": self.decision,
            "reviewer_id": self.reviewer_id,
            "reviewed_at": wire_time(self.reviewed_at),
            "report_content_sha256": self.report_content_sha256,
            "bundle_manifest_sha256": self.bundle_manifest_sha256,
            "checklist_result_asset_id": self.checklist_result_asset_id,
        }


@dataclass
class SignatureRequest:
    id: str
    tenant_id: str
    report_version_id: str
    signer_id: str
    credential_id: str
    bundle_sha256: str
    provider: str
    provider_transaction_id: str
    redirect_url: str
    requested_at: datetime
    expires_at: datetime
    status: str = "pending"
    last_receipt_id: str | None = None
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
            "purpose": "analyst_report",
            "subject_id": self.report_version_id,
            "provider_session_ref": self.provider_transaction_id,
            "redirect_url": self.redirect_url,
            "expires_at": wire_time(self.expires_at),
            "status": self.status,
        }


@dataclass
class Signature:
    id: str
    tenant_id: str
    request_id: str
    report_version_id: str
    signer_id: str
    credential_id: str
    content_sha256: str
    bundle_sha256: str
    signed_file_sha256: str
    signed_asset_id: str
    verification_asset_id: str
    signed_at: datetime
    verified_at: datetime
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_internal_api(self) -> dict[str, Any]:
        """Internal-only projection; no public Signature wire schema is published."""
        return {
            "id": self.id,
            "report_version_id": self.report_version_id,
            "signer_id": self.signer_id,
            "content_sha256": self.content_sha256,
            "bundle_sha256": self.bundle_sha256,
            "signed_file_sha256": self.signed_file_sha256,
            "signed_at": wire_time(self.signed_at),
            "verified_at": wire_time(self.verified_at),
        }


@dataclass
class SignatureRevocation:
    id: str
    tenant_id: str
    signature_id: str
    reason_code: str
    evidence_asset_id: str
    effective_at: datetime
    revoked_by: str
    publication_withdrawal_event_id: str
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
            "signature_id": self.signature_id,
            "reason_code": self.reason_code,
            "evidence_asset_id": self.evidence_asset_id,
            "effective_at": wire_time(self.effective_at),
            "revoked_by": self.revoked_by,
            "publication_withdrawal_event_id": self.publication_withdrawal_event_id,
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
            "producer": "M07",
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
