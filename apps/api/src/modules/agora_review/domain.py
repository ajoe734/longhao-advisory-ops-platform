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


def require_uuid(value: Any, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(f"{field_name} must be a UUID", field_name=field_name) from exc


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


@dataclass(frozen=True)
class ActorContext:
    """Server-trusted identity; tenant/actor/scopes never come from client-chosen body fields."""

    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()
    principal_type: str = "staff"
    purpose: str = "research_review"

    def __post_init__(self) -> None:
        require_uuid(self.tenant_id, "tenant_id")
        require_uuid(self.actor_id, "actor_id")
        if self.principal_type not in {"staff", "service"}:
            raise ValidationError("unsupported principal_type", field_name="principal_type")


EXTERNAL_REF_SYSTEMS = {
    "pantheon",
    "agora",
    "legendflow",
    "signature_provider",
    "screening_provider",
    "payment_provider",
    "accounting_provider",
    "messaging_provider",
}

# Business-level AML/KYC content classes that must never leave the platform through
# a research-scoped Servant conversation or revision request.
RESTRICTED_EVIDENCE_KINDS = {
    "kyc_profile",
    "kyc_document",
    "aml_case",
    "aml_declaration",
    "aml_screening_result",
    "customer_pii",
    "payment_instrument",
}


def require_external_ref(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field_name} must be an object", field_name=field_name)
    allowed = {"system", "external_id", "kind", "version", "checksum_sha256"}
    extra = set(value) - allowed
    if extra:
        raise ValidationError(f"{field_name} has unsupported fields: {', '.join(sorted(extra))}", field_name=field_name)
    missing = {"system", "external_id", "kind"} - set(value)
    if missing:
        raise ValidationError(f"{field_name} is missing fields: {', '.join(sorted(missing))}", field_name=field_name)
    if value["system"] not in EXTERNAL_REF_SYSTEMS:
        raise ValidationError(f"{field_name}.system is not supported", field_name=f"{field_name}.system")
    if not isinstance(value["external_id"], str) or not 1 <= len(value["external_id"]) <= 256:
        raise ValidationError(f"{field_name}.external_id must be 1..256 characters", field_name=f"{field_name}.external_id")
    if not isinstance(value["kind"], str) or not 1 <= len(value["kind"]) <= 80:
        raise ValidationError(f"{field_name}.kind must be 1..80 characters", field_name=f"{field_name}.kind")
    if value["kind"] in RESTRICTED_EVIDENCE_KINDS:
        raise ForbiddenError(
            f"{field_name} references AML/KYC restricted content", code="aml_restricted_content_blocked", field_name=field_name
        )
    if "version" in value and (not isinstance(value["version"], str) or len(value["version"]) > 128):
        raise ValidationError(f"{field_name}.version must be a string up to 128 characters", field_name=f"{field_name}.version")
    if "checksum_sha256" in value and value["checksum_sha256"] is not None:
        if not isinstance(value["checksum_sha256"], str) or len(value["checksum_sha256"]) != 64:
            raise ValidationError(f"{field_name}.checksum_sha256 must be a lowercase SHA-256", field_name=f"{field_name}.checksum_sha256")
    return {key: value[key] for key in ("system", "external_id", "kind") } | {
        key: value[key] for key in ("version", "checksum_sha256") if key in value
    }


def external_ref_matches(stored: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Compares ``ExternalRef``-shaped dicts on their identity fields only.

    Both sides may legally omit optional fields (``version``, ``checksum_sha256``);
    a raw dict ``==`` would false-negative whenever one side carries an optional
    field the other does not, even though they reference the same external object.
    """

    return all(stored.get(key) == candidate.get(key) for key in ("system", "external_id", "kind"))


_CASE_API_STATES = {
    "queued": "queued",
    "assigned": "assigned",
    "reviewing": "under_review",
    "changes_requested": "revision_requested",
    "ready_to_sign": "ready_to_sign",
    "rejected": "rejected",
    "closed": "signed",
}

_REVISION_API_STATES = {
    "requested": "queued",
    "accepted": "queued",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "stale": "stale",
}


def case_api_state(status: str, stale: bool) -> str:
    return "stale" if stale else _CASE_API_STATES[status]


def case_db_status_for_state(state: str) -> str:
    if state == "stale":
        raise ValueError("stale is a flag projection, not a stored status")
    for db_status, api_state in _CASE_API_STATES.items():
        if api_state == state:
            return db_status
    raise ValueError(f"unknown review case state: {state}")


@dataclass
class ReviewCase:
    id: str
    tenant_id: str
    candidate_id: str
    artifact_ref: dict[str, Any]
    content_sha256: str
    version: int = 1
    status: str = "queued"
    assignee_id: str | None = None
    professional_reviewer_id: str | None = None
    compliance_review_required: bool = False
    current_report_version_id: str | None = None
    stale: bool = False
    priority: str = "normal"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def current_artifact_ref(self) -> dict[str, Any]:
        return {**self.artifact_ref, "checksum_sha256": self.content_sha256}

    def matches_artifact_ref(self, ref: dict[str, Any]) -> bool:
        for key in ("system", "external_id", "kind"):
            if ref.get(key) != self.artifact_ref.get(key):
                return False
        checksum = ref.get("checksum_sha256")
        return checksum is None or checksum == self.content_sha256

    def to_api(self, allowed_actions: list[str], pending_revision_ids: list[str]) -> dict[str, Any]:
        state = case_api_state(self.status, self.stale)
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "candidate_id": self.candidate_id,
            "current_artifact_ref": self.current_artifact_ref(),
            "current_report_version_id": self.current_report_version_id,
            "state": state,
            "assignee_id": self.assignee_id,
            "professional_reviewer_id": self.professional_reviewer_id,
            "compliance_review_required": self.compliance_review_required,
            "pending_revision_ids": pending_revision_ids,
            "source_version_stale": self.stale,
        }


@dataclass
class Conversation:
    id: str
    tenant_id: str
    review_case_id: str
    provider: str
    external_conversation_id: str
    kind: str
    scoped_artifact_ref: dict[str, Any]
    allowed_evidence_refs: list[dict[str, Any]]
    purpose: str
    created_by: str
    version: int = 1
    status: str = "opening"
    audit_transcript_asset_id: str | None = None
    last_synced_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def external_conversation_ref(self) -> dict[str, Any]:
        return {"system": self.provider, "external_id": self.external_conversation_id, "kind": self.kind}

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "review_case_id": self.review_case_id,
            "external_conversation_ref": self.external_conversation_ref(),
            "scoped_artifact_ref": self.scoped_artifact_ref,
            "status": self.status,
            "audit_transcript_asset_id": self.audit_transcript_asset_id,
        }


@dataclass
class Revision:
    """Projection of ``review.revision_request`` plus its latest immutable result."""

    id: str
    tenant_id: str
    review_case_id: str
    conversation_id: str
    base_artifact_ref: dict[str, Any]
    base_content_sha256: str
    instructions: str
    required_evidence_refs: list[dict[str, Any]]
    requested_output: str
    requested_by: str
    external_job_ref: str
    retry_of_revision_id: str | None = None
    version: int = 1
    status: str = "requested"
    result_artifact_ref: dict[str, Any] | None = None
    result_content_sha256: str | None = None
    revision_no: int = 0
    attempt_count: int = 0
    last_error_code: str | None = None
    completed_at: datetime | None = None
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        state = "stale" if self.status == "stale" else _REVISION_API_STATES[self.status]
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "review_case_id": self.review_case_id,
            "conversation_id": self.conversation_id,
            "base_artifact_ref": self.base_artifact_ref,
            "state": state,
            "result_artifact_ref": self.result_artifact_ref,
            "result_content_sha256": self.result_content_sha256,
            "requested_by": self.requested_by,
            "completed_at": wire_time(self.completed_at) if self.completed_at else None,
        }


@dataclass
class RevisionResult:
    """Append-only artifact produced by a completed revision; never overwritten."""

    id: str
    tenant_id: str
    request_id: str
    revision_no: int
    artifact_ref: dict[str, Any]
    content_sha256: str
    asset_id: str
    evidence_refs: list[dict[str, Any]]
    significant_diff: bool
    diff_summary: list[str]
    produced_at: datetime = field(default_factory=utc_now)


@dataclass
class CandidateProjection:
    """Local, minimized read cache fed by ``research.candidate_*`` events; M05 owns the write."""

    tenant_id: str
    candidate_id: str
    review_case_id: str
    content_sha256: str
    validation_result: str
    received_at: datetime = field(default_factory=utc_now)
    superseded_by: str | None = None

    def artifact_ref(self) -> dict[str, Any]:
        return {"system": "pantheon", "external_id": self.candidate_id, "kind": "research_candidate"}


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
            "producer": "M06",
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
    aggregate_version: int | None = None
