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


def parse_time(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc


@dataclass(frozen=True)
class ActorContext:
    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    principal_type: str = "staff"
    customer_id: str | None = None
    purpose: str = "operations"

    def __post_init__(self) -> None:
        require_uuid(self.tenant_id, "tenant_id")
        require_uuid(self.actor_id, "actor_id")
        if self.customer_id is not None:
            require_uuid(self.customer_id, "customer_id")
        if self.principal_type not in {"staff", "service"}:
            raise ValidationError("unsupported principal_type", field_name="principal_type")

    def has_any(self, roles: set[str]) -> bool:
        return self.principal_type == "staff" and bool(self.roles & roles)


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


# ---- aggregates -----------------------------------------------------------


@dataclass
class RecommendationSeries:
    id: str
    tenant_id: str
    version: int
    instrument_ref: dict[str, Any]
    strategy_scope_ref: str
    research_origin_ref: dict[str, Any]
    series_code: str
    created_at: datetime
    updated_at: datetime
    latest_version_id: str | None = None

    def to_api(self, allowed_actions: list[str], state: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "instrument_ref": self.instrument_ref,
            "strategy_scope_ref": self.strategy_scope_ref,
            "state": state,
            "latest_version_id": self.latest_version_id,
        }


@dataclass
class RecommendationVersion:
    id: str
    tenant_id: str
    series_id: str
    version_number: int
    action: str
    rationale_asset_id: str
    content_sha256: str
    supporting_report_version_ids: list[str]
    valid_from: datetime
    valid_until: datetime
    source_candidate_id: str
    supersedes_version_id: str | None
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def to_api(self, allowed_actions: list[str], state: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "series_id": self.series_id,
            "version_number": self.version_number,
            "action": self.action,
            "rationale_asset_id": self.rationale_asset_id,
            "supporting_report_version_ids": list(self.supporting_report_version_ids),
            "state": state,
            "valid_from": wire_time(self.valid_from),
            "valid_until": wire_time(self.valid_until),
            "supersedes_version_id": self.supersedes_version_id,
        }


@dataclass
class Publication:
    id: str
    tenant_id: str
    recommendation_version_id: str
    report_version_id: str
    signature_id: str
    recommendation_version_ids: list[str]
    bundle_manifest_sha256: str
    created_from_signature_event_id: str
    audience_snapshot_id: str
    valid_until: datetime
    created_at: datetime
    updated_at: datetime
    version: int = 1
    state: str = "queued"
    recipient_count: int = 0
    published_at: datetime | None = None
    correction_publication_id: str | None = None
    withdrawal_reason_code: str | None = None

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "report_version_id": self.report_version_id,
            "signature_id": self.signature_id,
            "recommendation_version_ids": list(self.recommendation_version_ids),
            "bundle_manifest_sha256": self.bundle_manifest_sha256,
            "state": self.state,
            "created_from_signature_event_id": self.created_from_signature_event_id,
            "audience_snapshot_id": self.audience_snapshot_id,
            "recipient_count": self.recipient_count,
            "published_at": wire_time(self.published_at) if self.published_at else None,
            "valid_until": wire_time(self.valid_until),
            "correction_publication_id": self.correction_publication_id,
            "withdrawal_reason_code": self.withdrawal_reason_code,
        }


@dataclass
class AudienceSnapshot:
    id: str
    tenant_id: str
    publication_id: str
    rule_version: str
    evaluated_at: datetime
    snapshot_sha256: str
    recipient_count: int
    created_at: datetime
    updated_at: datetime
    version: int = 1
    reason_summary: dict[str, int] = field(default_factory=dict)


@dataclass
class Recipient:
    id: str
    tenant_id: str
    snapshot_id: str
    publication_id: str
    customer_id: str
    entitlement_id: str
    entitlement_version: int
    eligibility_reason: str
    created_at: datetime
    updated_at: datetime
    version: int = 1


@dataclass
class ExcludedCandidate:
    customer_id: str
    reason_code: str


@dataclass
class EntitlementRecord:
    tenant_id: str
    customer_id: str
    entitlement_id: str
    contract_id: str
    state: str
    version: int
    valid_until: datetime
    updated_at: datetime


@dataclass
class RevokedSignature:
    """Tombstone recorded the moment ``report.signature_revoked`` arrives.

    Kept independently of :class:`VerifiedReport` so a revocation that
    arrives before its matching ``report.signature_verified`` (out-of-order
    delivery across the ``signature``/``report_version`` aggregates) still
    blocks that signature once it is later verified.
    """

    tenant_id: str
    signature_id: str
    report_version_id: str
    revocation_id: str
    effective_at: datetime


@dataclass
class VerifiedReport:
    tenant_id: str
    report_version_id: str
    signature_id: str
    report_content_sha256: str
    bundle_manifest_sha256: str
    verified_at: datetime
    valid_until: datetime
    revoked: bool = False
    revocation_id: str | None = None


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
    result_code: str | None = None
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
            "producer": "M08",
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
    response_etag: str | None = None
