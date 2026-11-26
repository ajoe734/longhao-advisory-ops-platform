"""M11 domain model: maintenance ownership, company pool and lapse policy.

Mirrors ``ownership.policy_version`` / ``ownership.assignment`` / ``ownership.pool_entry``
(database/migrations/0001_domains.sql, 0003_business_guards.sql) and the published
M11 operationIds in api/openapi.yaml. Company-pool-owned intervals (``owner_type ==
"company_pool"``) exist so a customer never has a gap in ownership history, but the
wire ``MaintenanceAssignment`` resource always represents a *sales*-owned interval
(``salesperson_id`` is a required UUID on the wire, never null) — see
database/proposals/M11/README.md for the reconciliation this implies against the
shared migration's older ``reason_code`` vocabulary.
"""
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
    """Identity/scope the trusted server resolved for this request.

    M11 commands never accept a customer-, tenant- or actor-controlled body field
    as authorization; ``tenant_id``/``actor_id``/``roles`` always come from verified
    session state.
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


POOL_ENTRY_REASONS = {"unassigned_web", "employee_departure", "paid_service_lapse", "manual_release"}
POOL_ENTRY_STATES = {"available", "claimed", "suppressed"}
ASSIGNMENT_STATES = {"active", "released", "departure", "superseded"}
OWNER_TYPES = {"company_pool", "sales"}


@dataclass
class PolicyVersion:
    """``ownership.policy_version``: the versioned, never-edited-in-place lapse rule.

    ``approval_status`` gates every *live* (non-preview) pool transition; draft
    policies may always be previewed (``dry_run``) but never applied.
    """

    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime
    policy_code: str
    policy_version: int
    proposed_by: str
    lapse_basis: str = "proposed_all_paid_services_ended"
    lapse_days: int = 7
    departure_immediate: bool = True
    version: int = 1
    approval_status: str = "draft"
    approved_by: str | None = None
    approved_at: datetime | None = None

    @property
    def wire_code(self) -> str:
        return f"{self.policy_code}:{self.policy_version}"


@dataclass
class Assignment:
    """``ownership.assignment``: one maintenance-ownership interval.

    ``owner_type == "company_pool"`` rows keep the customer's ownership timeline
    gap-free (exactly one row with ``valid_until is None`` per customer at a time)
    but are never surfaced as a ``MaintenanceAssignment`` API resource — only
    ``owner_type == "sales"`` rows are, because the wire schema requires a concrete
    ``salesperson_id``.
    """

    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime
    customer_id: str
    owner_type: str
    valid_from: datetime
    reason_code: str
    policy_version_id: str
    assigned_by: str
    version: int = 1
    owner_actor_id: str | None = None
    valid_until: datetime | None = None
    end_state: str | None = None
    predecessor_id: str | None = None
    source_pool_entry_id: str | None = None

    def __post_init__(self) -> None:
        if self.owner_type not in OWNER_TYPES:
            raise ValidationError("owner_type is invalid", field_name="owner_type")
        if (self.owner_type == "sales") != (self.owner_actor_id is not None):
            raise ValidationError("owner_type and owner_actor_id are inconsistent")

    @property
    def wire_state(self) -> str:
        if self.valid_until is None:
            return "active"
        return self.end_state or "released"

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        if self.owner_type != "sales" or not self.owner_actor_id:
            raise ValidationError("only sales-owned assignments are exposed on the wire")
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "salesperson_id": self.owner_actor_id,
            "valid_from": wire_time(self.valid_from),
            "valid_until": wire_time(self.valid_until) if self.valid_until else None,
            "reason_code": self.reason_code,
            "state": self.wire_state,
            "source_pool_entry_id": self.source_pool_entry_id,
            "commission_entitlement_granted": False,
        }


@dataclass
class PoolEntry:
    """``ownership.pool_entry``: evidence a customer is available for redevelopment."""

    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime
    customer_id: str
    assignment_id: str
    entry_reason: str
    policy_version_id: str
    policy_version: str
    entered_at: datetime
    evaluation_snapshot: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    all_paid_services_ended_at: datetime | None = None
    state: str = "available"
    suppressed_reason: str | None = None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    claim_activity_id: str | None = None
    first_contact_due_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.entry_reason not in POOL_ENTRY_REASONS:
            raise ValidationError("entry_reason is invalid", field_name="entry_reason")
        if self.entry_reason == "paid_service_lapse" and self.all_paid_services_ended_at is None:
            raise ValidationError("paid_service_lapse requires all_paid_services_ended_at")

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "entered_at": wire_time(self.entered_at),
            "entry_reason": self.entry_reason,
            "paid_service_lapse_started_at": wire_time(self.all_paid_services_ended_at) if self.all_paid_services_ended_at else None,
            "state": self.state,
            "claimed_by": self.claimed_by,
            "policy_version": self.policy_version,
        }


@dataclass
class EmployeeDeparture:
    """Idempotent receipt for one ``record_employee_departure`` command."""

    id: str
    tenant_id: str
    employee_id: str
    effective_at: datetime
    hr_evidence_asset_id: str
    reason_code: str
    recorded_at: datetime
    assignments_closed: int
    pool_entries_created: int
    access_revocation_event_id: str

    def to_api(self) -> dict[str, Any]:
        return {
            "departure_id": self.id,
            "employee_id": self.employee_id,
            "effective_at": wire_time(self.effective_at),
            "assignments_closed": self.assignments_closed,
            "pool_entries_created": self.pool_entries_created,
            "access_revocation_event_id": self.access_revocation_event_id,
        }


@dataclass
class PoolEvaluation:
    """Internal audit record of one ``evaluate_company_pool`` run (not on the wire)."""

    id: str
    tenant_id: str
    policy_version_id: str
    policy_version: str
    dry_run: bool
    rule_status: str
    as_of: datetime
    created_at: datetime
    evaluated: list[dict[str, Any]] = field(default_factory=list)
    evidence_export_asset_id: str = field(default_factory=new_id)

    def to_api(self) -> dict[str, Any]:
        eligible = sum(1 for row in self.evaluated if row["outcome"] == "eligible")
        blocked = sum(1 for row in self.evaluated if row["outcome"] == "blocked")
        return {
            "evaluation_id": self.id,
            "policy_version": self.policy_version,
            "dry_run": self.dry_run,
            "rule_status": self.rule_status,
            "evaluated_count": len(self.evaluated),
            "eligible_count": eligible,
            "blocked_count": blocked,
            "evidence_export_asset_id": self.evidence_export_asset_id,
        }


@dataclass
class ContractWindow:
    """Local read-model row built from consumed ``contract.effective``/``contract.terminated``.

    ``contract.service_ended`` purposefully does not let M11 conclude a full
    interruption on its own (events/registry.json's note on that event); M11 always
    recomputes every known paid-service window for the customer before deciding
    ``all_paid_services_lapsed``. ``contract_service`` is a *child* of
    ``contract`` (one ``contract_id`` can have several), so a single
    ``contract.service_ended`` only tells us one child stopped — never that
    every service under this ``contract_id`` has (M02 does not publish a
    service count or an "all services ended" signal this consumer could use to
    confirm that). ``service_ended_ats`` therefore keeps every known
    ``contract_service_id -> ended_at`` it has observed, keyed so a later
    event for one service can never silently discard an earlier event for a
    different one (the historical bug: a single scalar field, last-write-wins,
    overwritten on every event regardless of which ``contract_service_id`` it
    was for). ``effective_end()`` uses the *latest* of those as its signal —
    the most conservative approximation available from this event alone — and
    callers (``_evaluate_customer``) still only decide "all paid services
    lapsed" after aggregating every known contract for the customer, not from
    any single contract's ``service_ended`` event in isolation.
    """

    tenant_id: str
    contract_id: str
    customer_id: str
    service_starts_at: datetime
    service_ends_at: datetime
    terminated_at: datetime | None = None
    service_ended_ats: dict[str, datetime] = field(default_factory=dict)

    def effective_end(self) -> datetime:
        candidates = [self.service_ends_at]
        if self.terminated_at is not None:
            candidates.append(self.terminated_at)
        if self.service_ended_ats:
            candidates.append(max(self.service_ended_ats.values()))
        return min(candidates)


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
    redrive_reason: str | None = None

    @property
    def payload_sha256(self) -> str:
        return canonical_hash(self.payload)

    def envelope(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": "1.0.0",
            "tenant_id": self.tenant_id,
            "producer": "M11",
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
    aggregate_type: str
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
