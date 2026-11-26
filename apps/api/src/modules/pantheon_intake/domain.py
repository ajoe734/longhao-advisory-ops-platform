from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5


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


def deterministic_review_case_id(tenant_id: str, candidate_id: str) -> str:
    """Stable ID so M06 can create its own review.case_file row without a round trip.

    M05 never writes review.case_file; this only reserves the identifier so the
    candidate_validated event and the eventual M06 row agree without coordination.
    """

    return str(uuid5(NAMESPACE_URL, f"longhao:{tenant_id}:research_candidate:{candidate_id}:review_case"))


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
    """Caller identity resolved by a trusted boundary; never derived from client claims."""

    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()
    principal_type: str = "staff"
    purpose: str = "operations"

    def __post_init__(self) -> None:
        require_uuid(self.tenant_id, "tenant_id")
        require_uuid(self.actor_id, "actor_id")
        if self.principal_type not in {"staff", "service", "integration"}:
            raise ValidationError("unsupported principal_type", field_name="principal_type")


# Database CHECK (status IN ('received','validating','quarantined','ready','rejected','superseded')).
CANDIDATE_STATUSES = frozenset({"received", "validating", "quarantined", "ready", "rejected", "superseded"})

# Database CHECK (origin IN ('persona_autonomous','analyst_requested')); the OpenAPI/event
# contract instead publishes 'persona_autonomous'/'analyst_initiated'. Mapped at the wire
# boundary the same way M01 maps Customer.status 'restricted' to API 'blocked'.
ORIGIN_DB_VALUES = frozenset({"persona_autonomous", "analyst_requested"})
_ORIGIN_DB_TO_WIRE = {"persona_autonomous": "persona_autonomous", "analyst_requested": "analyst_initiated"}
_ORIGIN_WIRE_TO_DB = {value: key for key, value in _ORIGIN_DB_TO_WIRE.items()}

# Database status 'ready' is published on the wire as 'accepted'; 'superseded' is never
# published directly (the wire ResearchCandidate.state enum has no such value) -- a
# superseded row keeps showing whatever validation outcome it last reached.
_STATUS_DB_TO_WIRE = {
    "received": "received",
    "validating": "validating",
    "ready": "accepted",
    "quarantined": "quarantined",
    "rejected": "rejected",
}

EXTERNAL_REF_SYSTEMS = frozenset(
    {
        "pantheon",
        "agora",
        "legendflow",
        "signature_provider",
        "screening_provider",
        "payment_provider",
        "accounting_provider",
        "messaging_provider",
    }
)


def origin_to_wire(value: str) -> str:
    return _ORIGIN_DB_TO_WIRE[value]


def origin_from_wire(value: str, field_name: str = "origin") -> str:
    mapped = _ORIGIN_WIRE_TO_DB.get(value)
    if mapped is None:
        raise ValidationError("origin is not supported", field_name=field_name)
    return mapped


def status_to_wire(status: str, last_active_state: str | None) -> str:
    if status == "superseded":
        status = last_active_state or "quarantined"
    return _STATUS_DB_TO_WIRE[status]


def external_ref_from_wire(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field_name} must be an object", field_name=field_name)
    allowed = {"system", "external_id", "kind", "version", "checksum_sha256"}
    required = {"system", "external_id", "kind"}
    extra = set(value) - allowed
    missing = required - set(value)
    if extra:
        raise ValidationError(f"{field_name} has unsupported fields: {', '.join(sorted(extra))}", field_name=field_name)
    if missing:
        raise ValidationError(f"{field_name} is missing fields: {', '.join(sorted(missing))}", field_name=field_name)
    if value["system"] not in EXTERNAL_REF_SYSTEMS:
        raise ValidationError(f"{field_name}.system is not supported", field_name=f"{field_name}.system")
    if not isinstance(value["external_id"], str) or not 1 <= len(value["external_id"]) <= 256:
        raise ValidationError(f"{field_name}.external_id must be 1..256 characters", field_name=f"{field_name}.external_id")
    if not isinstance(value["kind"], str) or not 1 <= len(value["kind"]) <= 80:
        raise ValidationError(f"{field_name}.kind must be 1..80 characters", field_name=f"{field_name}.kind")
    version = value.get("version")
    if version is not None and (not isinstance(version, str) or len(version) > 128):
        raise ValidationError(f"{field_name}.version must be a string up to 128 characters", field_name=f"{field_name}.version")
    checksum = value.get("checksum_sha256")
    if checksum is not None and not _is_sha256(checksum):
        raise ValidationError(f"{field_name}.checksum_sha256 must be lowercase SHA-256", field_name=f"{field_name}.checksum_sha256")
    result = {"system": value["system"], "external_id": value["external_id"], "kind": value["kind"]}
    if version is not None:
        result["version"] = version
    if checksum is not None:
        result["checksum_sha256"] = checksum
    return result


def external_ref_key(ref: dict[str, Any]) -> str:
    return f"{ref['system']}:{ref['external_id']}:{ref['kind']}:{ref.get('version', '')}"


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


@dataclass
class CandidateEvidence:
    id: str
    tenant_id: str
    candidate_id: str
    evidence_ref: str
    evidence_sha256: str
    event_time: datetime
    available_time: datetime
    license_scope: str
    snapshot_asset_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self) -> dict[str, Any]:
        return {
            "evidence_ref": self.evidence_ref,
            "checksum_sha256": self.evidence_sha256,
        }


@dataclass
class ResearchCandidate:
    id: str
    tenant_id: str
    producer: str
    external_candidate_id: str
    external_version: int
    external_event_id: str
    origin: str  # internal: persona_autonomous / analyst_requested
    ticket_ref: dict[str, Any]
    artifact_ref: dict[str, Any]
    content_sha256: str
    snapshot_asset_id: str
    source_occurred_at: datetime
    source_available_at: datetime
    received_at: datetime
    license_scope_ref: str
    data_scope: str
    raw_evidence_refs: tuple[dict[str, Any], ...]
    persona_ref: str | None = None
    pool_ref: str | None = None
    plan_ref: dict[str, Any] | None = None
    source_committed_at: datetime | None = None
    status: str = "received"
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    review_case_id: str | None = None
    validation_failure_codes: list[str] = field(default_factory=list)
    supersedes_candidate_id: str | None = None
    superseded_by_candidate_id: str | None = None
    last_active_state: str | None = None
    customer_distribution_allowed: bool = False
    validated_snapshot_asset_id: str | None = None
    conflicting_receipts: list[dict[str, Any]] = field(default_factory=list)
    rejection_reason_code: str | None = None
    rejection_note: str | None = None
    rejection_evidence_asset_ids: list[str] = field(default_factory=list)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "external_event_id": self.external_event_id,
            "origin": origin_to_wire(self.origin),
            "artifact_ref": dict(self.artifact_ref),
            "artifact_content_sha256": self.content_sha256,
            "evidence_bundle_refs": [dict(ref) for ref in self.raw_evidence_refs],
            "state": status_to_wire(self.status, self.last_active_state),
            "source_created_at": wire_time(self.source_occurred_at),
            "source_available_at": wire_time(self.source_available_at),
            "received_at": wire_time(self.received_at),
            "review_case_id": self.review_case_id,
            "validation_failure_codes": list(self.validation_failure_codes),
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
            "producer": "M05",
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
    response_aggregate_version: int | None = None


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
