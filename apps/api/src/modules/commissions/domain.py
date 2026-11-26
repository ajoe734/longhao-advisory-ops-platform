from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def wire_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def wire_date(value: date) -> str:
    return value.isoformat()


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


def require_decimal(value: Any, field_name: str, *, allow_negative: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a decimal string", field_name=field_name)
    try:
        parsed = Decimal(value)
    except Exception as exc:  # noqa: BLE001 - decimal raises many subclasses
        raise ValidationError(f"{field_name} is not a valid decimal", field_name=field_name) from exc
    if not allow_negative and parsed < 0:
        raise ValidationError(f"{field_name} must not be negative", field_name=field_name)
    if parsed.as_tuple().exponent < -6:
        raise ValidationError(f"{field_name} supports at most 6 decimal places", field_name=field_name)
    return parsed


def decimal_str(value: Decimal, scale: int = 6) -> str:
    quantum = Decimal(1).scaleb(-scale)
    quantized = value.quantize(quantum, rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return text


def money(amount: Decimal, currency: str) -> dict[str, Any]:
    return {"amount": decimal_str(amount), "currency": currency}


def apply_rounding(value: Decimal, mode: str, scale: int) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    rounding = ROUND_HALF_EVEN if mode == "ROUND_HALF_EVEN" else ROUND_HALF_UP
    return value.quantize(quantum, rounding=rounding)


def require_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be an ISO date", field_name=field_name) from exc


def require_rfc3339(value: Any, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc


@dataclass(frozen=True)
class ActorContext:
    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()
    principal_type: str = "staff"
    actor_type: str = "human"
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


# ---- policy ----------------------------------------------------------------

UNRESOLVED_POLICY_FIELDS = (
    "base_pool_rate",
    "allocation_interpretation",
    "age_anchor",
    "service_lineage_rule",
    "year3_assisted_marketing_rule",
)


@dataclass
class CommissionPolicy:
    id: str
    tenant_id: str
    policy_code: str
    policy_version: int
    effective_from: datetime
    assisted_marketing_share: Decimal
    assisted_sales_share: Decimal
    allocation_interpretation: str
    age_anchor: str
    service_lineage_rule: str
    year3_assisted_marketing_rule: str
    pool_policy_version: str
    recognition_policy_version: str
    rounding_mode: str
    rounding_scale: int
    full_policy_asset_id: str
    proposed_by: str
    marketing_year_1: Decimal = Decimal("1.0")
    marketing_year_2: Decimal = Decimal("0.5")
    marketing_year_3_auto: Decimal = Decimal("0.25")
    effective_until: datetime | None = None
    base_pool_rate: Decimal | None = None
    notes: str = ""
    version: int = 1
    state: str = "draft"
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def unresolved_parameters(self) -> list[str]:
        unresolved: list[str] = []
        if self.base_pool_rate is None:
            unresolved.append("base_pool_rate")
        if self.allocation_interpretation == "unresolved":
            unresolved.append("allocation_interpretation")
        if self.age_anchor == "unresolved":
            unresolved.append("age_anchor")
        if self.service_lineage_rule == "unresolved":
            unresolved.append("service_lineage_rule")
        if self.year3_assisted_marketing_rule == "unresolved":
            unresolved.append("year3_assisted_marketing_rule")
        return unresolved

    def live_eligible(self) -> bool:
        return self.state == "approved" and not self.unresolved_parameters()

    def content_hash(self) -> str:
        return canonical_hash(
            {
                "policy_code": self.policy_code,
                "policy_version": self.policy_version,
                "effective_from": wire_time(self.effective_from),
                "effective_until": wire_time(self.effective_until) if self.effective_until else None,
                "base_pool_rate": decimal_str(self.base_pool_rate) if self.base_pool_rate is not None else None,
                "marketing_year_1": decimal_str(self.marketing_year_1),
                "marketing_year_2": decimal_str(self.marketing_year_2),
                "marketing_year_3_auto": decimal_str(self.marketing_year_3_auto),
                "assisted_marketing_share": decimal_str(self.assisted_marketing_share),
                "assisted_sales_share": decimal_str(self.assisted_sales_share),
                "allocation_interpretation": self.allocation_interpretation,
                "age_anchor": self.age_anchor,
                "service_lineage_rule": self.service_lineage_rule,
                "year3_assisted_marketing_rule": self.year3_assisted_marketing_rule,
                "rounding_mode": self.rounding_mode,
                "rounding_scale": self.rounding_scale,
            }
        )

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "policy_code": self.policy_code,
            "state": self.state,
            "effective_from": wire_time(self.effective_from),
            "effective_until": wire_time(self.effective_until) if self.effective_until else None,
            "base_pool_rate": decimal_str(self.base_pool_rate) if self.base_pool_rate is not None else None,
            "allocation_interpretation": self.allocation_interpretation,
            "age_anchor": self.age_anchor,
            "service_lineage_rule": self.service_lineage_rule,
            "year3_assisted_marketing_rule": self.year3_assisted_marketing_rule,
            "full_policy_asset_id": self.full_policy_asset_id,
            "policy_sha256": self.content_hash(),
            "approved_by": self.approved_by,
            "approved_at": wire_time(self.approved_at) if self.approved_at else None,
            "unresolved_parameters": self.unresolved_parameters(),
        }


# ---- calculation run --------------------------------------------------------

RUN_STATES = {"queued", "calculating", "calculated", "review_pending", "approved", "rejected", "exported", "failed"}


@dataclass
class CommissionRun:
    id: str
    tenant_id: str
    policy_id: str
    period_start: date
    period_end: date
    revenue_cutoff_at: datetime
    run_key: str
    recognition_ids: tuple[str, ...]
    prepared_by: str
    version: int = 1
    state: str = "queued"
    input_sha256: str = ""
    policy_sha256: str = ""
    approved_by: str | None = None
    approved_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None
    currency: str = "TWD"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str], totals: dict[str, Decimal], entry_count: int, exception_count: int) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "policy_id": self.policy_id,
            "period_start": wire_date(self.period_start),
            "period_end": wire_date(self.period_end),
            "revenue_cutoff_at": wire_time(self.revenue_cutoff_at),
            "state": self.state,
            "basis_total": money(totals["basis"], self.currency),
            "pool_total": money(totals["pool"], self.currency),
            "marketing_total": money(totals["marketing"], self.currency),
            "sales_total": money(totals["sales"], self.currency),
            "company_retained_total": money(totals["company"], self.currency),
            "prepared_by": self.prepared_by,
            "approved_by": self.approved_by,
            "entry_count": entry_count,
            "exception_count": exception_count,
        }


@dataclass
class CommissionEntry:
    id: str
    tenant_id: str
    run_id: str
    recognition_id: str
    beneficiary_type: str
    award_amount: Decimal
    base_pool_amount: Decimal
    eligible_untaxed_amount: Decimal
    currency: str
    customer_year: int
    age_coefficient: Decimal
    calculation_snapshot: dict[str, Any]
    conversion_id: str | None = None
    assignment_id: str | None = None
    attribution_id: str | None = None
    beneficiary_actor_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str], *, state: str, original_entry_id: str | None) -> dict[str, Any]:
        wire_recipient = {"marketing": "marketing", "sales": "sales", "company": "company_retained"}[self.beneficiary_type]
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "run_id": self.run_id,
            "calculation": self.calculation_snapshot,
            "recipient_type": wire_recipient,
            "recipient_id": self.beneficiary_actor_id,
            "amount": money(self.award_amount, self.currency),
            "state": state,
            "original_entry_id": original_entry_id,
        }


@dataclass
class CommissionAdjustment:
    id: str
    tenant_id: str
    original_entry_id: str
    refund_id: str
    amount: Decimal
    currency: str
    reason_code: str
    dedupe_key: str
    recovery_method: str
    prepared_by: str
    approved_by: str
    original_recipient_id: str | None = None
    exceeds_original_award: bool = False
    version: int = 1
    state: str = "approved"
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
            "original_entry_id": self.original_entry_id,
            "refund_id": self.refund_id,
            "original_recipient_id": self.original_recipient_id,
            "amount": money(self.amount, self.currency),
            "reason_code": self.reason_code,
            "state": self.state,
            "payroll_offset_allowed": self.recovery_method == "ledger_offset" and not self.exceeds_original_award,
        }


PAYOUT_BATCH_DB_TO_WIRE = {
    "draft": "prepared",
    "approval_pending": "approval_pending",
    "approved": "approved",
    "exported": "exported",
    "paid": "acknowledged",
    "reversed": "failed",
}


@dataclass
class PayoutBatch:
    id: str
    tenant_id: str
    run_ids: tuple[str, ...]
    payout_period: str
    payroll_mapping_version: str
    prepared_by: str
    currency: str = "TWD"
    version: int = 1
    status: str = "draft"
    total_amount: Decimal = Decimal("0")
    line_count: int = 0
    control_sha256: str = ""
    approved_by: str | None = None
    export_asset_id: str | None = None
    payroll_export_ref: str | None = None
    paid_at: datetime | None = None
    ack_event_ids: tuple[str, ...] = ()
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
            "run_ids": list(self.run_ids),
            "state": PAYOUT_BATCH_DB_TO_WIRE[self.status],
            "total": money(self.total_amount, self.currency),
            "line_count": self.line_count,
            "export_asset_id": self.export_asset_id,
            "control_sha256": self.control_sha256,
        }


@dataclass
class PayoutLine:
    id: str
    tenant_id: str
    batch_id: str
    entry_id: str
    amount: Decimal
    adjustment_ids: tuple[str, ...] = ()
    accepted: bool | None = None
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
            "producer": "M12",
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


@dataclass
class PendingAdjustmentDraft:
    """Service-prepared reversal candidate from a consumed ``finance.refund_confirmed`` event.

    Not an official ``commission.adjustment`` row; it only lets the human
    ``create_commission_adjustment`` call reuse the same dedupe key so the maker
    (service) and checker (staff) stay distinct actor types.
    """

    tenant_id: str
    original_entry_id: str
    refund_id: str
    dedupe_key: str
    prepared_by: str
    created_at: datetime = field(default_factory=utc_now)
