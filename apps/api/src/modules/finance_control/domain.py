from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
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


def require_uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(f"{field_name} must be a UUID", field_name=field_name) from exc


_MONEY_RE = re.compile(r"^-?(?:0|[1-9]\d{0,13})(?:\.\d{1,6})?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def parse_money(value: Any, field_name: str) -> tuple[Decimal, str]:
    if not isinstance(value, dict) or set(value) != {"amount", "currency"}:
        raise ValidationError(f"{field_name} must be a Money object", field_name=field_name)
    amount, currency = value["amount"], value["currency"]
    if not isinstance(amount, str) or not _MONEY_RE.fullmatch(amount):
        raise ValidationError(f"{field_name}.amount must be a canonical decimal string", field_name=field_name)
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
        raise ValidationError(f"{field_name}.currency must be a 3-letter ISO code", field_name=field_name)
    try:
        return Decimal(amount), currency
    except InvalidOperation as exc:
        raise ValidationError(f"{field_name}.amount is not a valid decimal", field_name=field_name) from exc


def money_to_api(amount: Decimal, currency: str) -> dict[str, str]:
    if amount == 0:
        return {"amount": "0", "currency": currency}
    text = format(amount.normalize(), "f")
    if not _MONEY_RE.fullmatch(text):
        raise ValidationError("computed amount exceeds the wire decimal contract")
    return {"amount": text, "currency": currency}


def parse_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a date string", field_name=field_name)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO date", field_name=field_name) from exc


def parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc


# ---- actor and roles -----------------------------------------------------

FINANCE_OPERATOR = "finance_operator"
FINANCE_CHECKER = "finance_checker"
SERVICE_AGENT = "service_agent"
PAYMENT_ADAPTER = "payment_adapter"
REFUND_SERVICE = "refund_service"
INTERNAL_AUDITOR = "internal_auditor"
COMPLIANCE_REVIEWER = "compliance_reviewer"
COMPLIANCE_SUPERVISOR = "compliance_supervisor"
CONTROL_REMEDIATION_OWNER = "control_remediation_owner"
ACCOUNTING_ADAPTER = "accounting_adapter"


@dataclass(frozen=True)
class ActorContext:
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


# ---- domain entities ------------------------------------------------------


@dataclass
class InvoiceLine:
    line_no: int
    contract_service_id: str
    service_code: str
    service_period_start: date
    service_period_end: date
    quantity: int
    untaxed_amount: Decimal
    tax_amount: Decimal
    tax_code: str
    tax_mapping_version: str

    def to_api(self, currency: str) -> dict[str, Any]:
        return {
            "contract_service_id": self.contract_service_id,
            "service_code": self.service_code,
            "service_period_start": wire_date(self.service_period_start),
            "service_period_end": wire_date(self.service_period_end),
            "quantity": self.quantity,
            "untaxed_service_amount": money_to_api(self.untaxed_amount, currency),
            "tax_amount": money_to_api(self.tax_amount, currency),
            "tax_code": self.tax_code,
            "tax_mapping_version": self.tax_mapping_version,
        }


@dataclass
class Invoice:
    id: str
    tenant_id: str
    customer_id: str
    contract_id: str
    invoice_number: str
    invoice_date: date
    due_date: date
    invoice_type: str
    currency: str
    lines: list[InvoiceLine]
    status: str = "issued"
    paid_amount: Decimal = Decimal("0")
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def untaxed_amount(self) -> Decimal:
        return sum((line.untaxed_amount for line in self.lines), Decimal("0"))

    @property
    def tax_amount(self) -> Decimal:
        return sum((line.tax_amount for line in self.lines), Decimal("0"))

    @property
    def total_amount(self) -> Decimal:
        return self.untaxed_amount + self.tax_amount

    @property
    def state(self) -> str:
        if self.status == "issued" and self.paid_amount > 0 and self.paid_amount < self.total_amount:
            return "partially_paid"
        return self.status

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
            "invoice_date": wire_date(self.invoice_date),
            "due_date": wire_date(self.due_date),
            "state": self.state,
            "untaxed_service_amount": money_to_api(self.untaxed_amount, self.currency),
            "tax_amount": money_to_api(self.tax_amount, self.currency),
            "total_amount": money_to_api(self.total_amount, self.currency),
            "lines": [line.to_api(self.currency) for line in self.lines],
        }


@dataclass
class Payment:
    """Backs both the pre-capture PaymentIntent view and the matured finance.payment row."""

    id: str
    tenant_id: str
    customer_id: str
    application_id: str | None
    invoice_id: str | None
    amount: Decimal
    currency: str
    method: str
    return_route: str
    provider: str
    provider_transaction_id: str
    checkout_url: str
    expires_at: datetime
    status: str = "pending"
    paid_at: datetime | None = None
    payment_method_ref: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def intent_state(self) -> str:
        if self.status == "pending" and utc_now() > self.expires_at:
            return "expired"
        if self.status in {"authorized", "paid", "failed"}:
            return self.status
        return "pending"

    def to_intent_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "application_id": self.application_id,
            "provider_intent_ref": self.provider_transaction_id,
            "state": self.intent_state,
            "checkout_url": self.checkout_url,
            "amount": money_to_api(self.amount, self.currency),
            "expires_at": wire_time(self.expires_at),
        }


@dataclass
class Settlement:
    id: str
    tenant_id: str
    payment_id: str
    provider_settlement_id: str
    gross_amount: Decimal
    fee_amount: Decimal
    net_amount: Decimal
    currency: str
    settled_at: datetime
    bank_line_ref: str
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class SettlementImport:
    id: str
    tenant_id: str
    provider: str
    batch_ref: str
    settlement_date: date
    statement_asset_id: str
    statement_sha256: str
    declared_gross: Decimal
    declared_fee: Decimal
    declared_net: Decimal
    declared_count: int
    format_version: str
    currency: str = "TWD"
    status: str = "received"
    matched_count: int = 0
    exception_count: int = 0
    gross: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    net: Decimal = Decimal("0")
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
            "provider": self.provider,
            "batch_ref": self.batch_ref,
            "state": self.status,
            "settlement_date": wire_date(self.settlement_date),
            "matched_count": self.matched_count,
            "exception_count": self.exception_count,
            "gross": money_to_api(self.gross, self.currency),
            "fee": money_to_api(self.fee, self.currency),
            "net": money_to_api(self.net, self.currency),
        }


@dataclass
class ReconciliationItem:
    id: str
    tenant_id: str
    settlement_id: str
    difference_type: str
    expected_amount: Decimal
    actual_amount: Decimal
    currency: str
    payment_id: str | None = None
    status: str = "open"
    owner_id: str | None = None
    resolved_by: str | None = None
    reason_code: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        state = {"open": "open", "investigating": "investigating", "resolved": "resolved", "escalated": "escalated"}[self.status]
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "settlement_id": self.settlement_id,
            "payment_id": self.payment_id,
            "difference_type": self.difference_type,
            "expected_amount": money_to_api(self.expected_amount, self.currency),
            "actual_amount": money_to_api(self.actual_amount, self.currency),
            "state": state,
            "owner_id": self.owner_id,
            "resolved_by": self.resolved_by,
        }


@dataclass
class ServiceSchedule:
    id: str
    tenant_id: str
    contract_service_id: str
    invoice_id: str
    schedule_version: int
    period_start: date
    period_end: date
    untaxed_amount: Decimal
    currency: str
    recognition_policy_version: str
    approved_by: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class RevenueRun:
    id: str
    tenant_id: str
    run_key: str
    period_start: date
    period_end: date
    recognition_policy_version: str
    cutoff_at: datetime
    dry_run: bool
    prepared_by: str
    currency: str = "TWD"
    status: str = "queued"
    recognized_untaxed_total: Decimal = Decimal("0")
    deferred_remaining: Decimal = Decimal("0")
    entry_count: int = 0
    exception_count: int = 0
    journal_ids: list[str] = field(default_factory=list)
    calculation_manifest_sha256: str = ""
    included_schedule_ids: list[str] = field(default_factory=list)
    exception_schedule_ids: list[str] = field(default_factory=list)
    approved_by: str | None = None
    approved_at: datetime | None = None
    posted_at: datetime | None = None
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
            "period_start": wire_date(self.period_start),
            "period_end": wire_date(self.period_end),
            "recognition_policy_version": self.recognition_policy_version,
            "state": self.status,
            "recognized_untaxed_total": money_to_api(self.recognized_untaxed_total, self.currency),
            "deferred_remaining": money_to_api(self.deferred_remaining, self.currency),
            "entry_count": self.entry_count,
            "exception_count": self.exception_count,
            "journal_ids": list(self.journal_ids),
            "calculation_manifest_sha256": self.calculation_manifest_sha256,
        }


@dataclass
class RevenueRecognition:
    id: str
    tenant_id: str
    run_id: str
    schedule_id: str
    recognition_key: str
    recognized_at: datetime
    untaxed_amount: Decimal
    currency: str
    confirmed_by: str
    policy_version: str
    commission_eligible: bool = True
    reverses_id: str | None = None
    contract_id: str | None = None
    contract_service_id: str | None = None


@dataclass
class RefundAllocation:
    invoice_line_ref: str
    unearned_amount: Decimal
    earned_reversal_amount: Decimal
    tax_adjustment_amount: Decimal
    currency: str
    recognition_id: str | None = None

    def to_api(self) -> dict[str, Any]:
        return {
            "invoice_line_ref": self.invoice_line_ref,
            "recognition_id": self.recognition_id,
            "unearned_amount": money_to_api(self.unearned_amount, self.currency),
            "earned_reversal_amount": money_to_api(self.earned_reversal_amount, self.currency),
            "tax_adjustment_amount": money_to_api(self.tax_adjustment_amount, self.currency),
        }


@dataclass
class Refund:
    id: str
    tenant_id: str
    customer_id: str
    contract_id: str
    original_payment_id: str
    reason_code: str
    requested_amount: Decimal
    currency: str
    allocations: list[RefundAllocation]
    calculation_policy_version: str
    customer_request_evidence_id: str
    idempotency_key: str
    prepared_by: str
    case_id: str | None = None
    status: str = "approval_pending"
    approved_amount: Decimal | None = None
    approved_by: str | None = None
    provider_refund_ref: str | None = None
    provider_idempotency_key: str | None = None
    confirmed_at: datetime | None = None
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
            "original_payment_id": self.original_payment_id,
            "state": self.status,
            "requested_amount": money_to_api(self.requested_amount, self.currency),
            "approved_amount": money_to_api(self.approved_amount, self.currency) if self.approved_amount is not None else None,
            "allocations": [item.to_api() for item in self.allocations],
            "prepared_by": self.prepared_by,
            "approved_by": self.approved_by,
            "provider_refund_ref": self.provider_refund_ref,
            "confirmed_at": wire_time(self.confirmed_at) if self.confirmed_at else None,
        }


@dataclass
class JournalLine:
    line_no: int
    account_code: str
    debit: Decimal
    credit: Decimal
    cost_center: str | None = None


@dataclass
class Journal:
    id: str
    tenant_id: str
    journal_number: str
    source_type: str
    source_id: str
    currency: str
    accounting_date: date
    prepared_by: str
    lines: list[JournalLine]
    status: str = "posted"
    approved_by: str | None = None
    posted_at: datetime | None = None
    reverses_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def debit_total(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0"))

    @property
    def credit_total(self) -> Decimal:
        return sum((line.credit for line in self.lines), Decimal("0"))


@dataclass
class AccountingExport:
    id: str
    tenant_id: str
    destination: str
    period_start: date
    period_end: date
    journal_ids: list[str]
    account_mapping_version: str
    tax_mapping_version: str
    export_format: str
    prepared_by: str
    currency: str
    debit_total: Decimal
    credit_total: Decimal
    entry_count: int
    export_asset_id: str
    export_sha256: str
    status: str = "prepared"
    acknowledgement_ref: str | None = None
    accepted_journal_ids: set[str] = field(default_factory=set)
    rejected_journal_ids: set[str] = field(default_factory=set)
    applied_ack_event_ids: set[str] = field(default_factory=set)
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
            "state": self.status,
            "account_mapping_version": self.account_mapping_version,
            "tax_mapping_version": self.tax_mapping_version,
            "debit_total": money_to_api(self.debit_total, self.currency),
            "credit_total": money_to_api(self.credit_total, self.currency),
            "entry_count": self.entry_count,
            "export_asset_id": self.export_asset_id,
            "export_sha256": self.export_sha256,
            "acknowledgement_ref": self.acknowledgement_ref,
        }


@dataclass
class ControlException:
    id: str
    tenant_id: str
    control_code: str
    severity: str
    description: str
    evidence_asset_ids: list[str]
    remediation_owner_id: str
    due_at: datetime
    created_by: str
    status: str = "open"
    closed_by: str | None = None
    closure_evidence_id: str | None = None
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
            "control_code": self.control_code,
            "severity": self.severity,
            "state": self.status,
            "remediation_owner_id": self.remediation_owner_id,
            "due_at": wire_time(self.due_at),
            "closed_by": self.closed_by,
            "closure_evidence_id": self.closure_evidence_id,
        }


@dataclass
class LegalHold:
    id: str
    tenant_id: str
    scope: str
    subject_ids: list[str]
    reason_code: str
    authority_evidence_asset_id: str
    created_by: str
    review_due_at: datetime
    status: str = "active"
    released_by: str | None = None
    released_reason_code: str | None = None
    released_at: datetime | None = None
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
            "scope": self.scope,
            "subject_ids": list(self.subject_ids),
            "state": self.status,
            "reason_code": self.reason_code,
            "authority_evidence_asset_id": self.authority_evidence_asset_id,
            "created_by": self.created_by,
            "review_due_at": wire_time(self.review_due_at),
        }


@dataclass
class EvidenceExportManifestEntry:
    ref_type: str
    ref_id: str
    sha256: str
    occurred_at: datetime


@dataclass
class EvidenceExport:
    id: str
    tenant_id: str
    scope: str
    subject_id: str
    purpose: str
    include_restricted_aml: bool
    approval_record_id: str
    expires_at: datetime
    requester_id: str
    manifest: list[EvidenceExportManifestEntry]
    manifest_asset_id: str | None
    export_asset_id: str | None
    manifest_sha256: str | None
    status: str = "ready"
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        status = self.status
        if status == "ready" and utc_now() > self.expires_at:
            status = "expired"
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "scope": self.scope,
            "subject_id": self.subject_id,
            "state": status,
            "manifest_asset_id": self.manifest_asset_id,
            "export_asset_id": self.export_asset_id,
            "manifest_sha256": self.manifest_sha256,
            "expires_at": wire_time(self.expires_at),
            "requester_id": self.requester_id,
        }


@dataclass
class ModuleAuditEvent:
    id: str
    tenant_id: str
    actor_id: str | None
    actor_type: str
    action: str
    aggregate_type: str
    aggregate_id: str | None
    occurred_at: datetime
    correlation_id: str
    outcome: str
    classification: str
    evidence_asset_ids: list[str] = field(default_factory=list)
    before_sha256: str | None = None
    after_sha256: str | None = None

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": 1,
            "created_at": wire_time(self.occurred_at),
            "updated_at": wire_time(self.occurred_at),
            "allowed_actions": [],
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "action": self.action,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "occurred_at": wire_time(self.occurred_at),
            "correlation_id": self.correlation_id,
            "outcome": self.outcome,
            "evidence_asset_ids": list(self.evidence_asset_ids),
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "classification": self.classification,
        }


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
            "producer": "M14",
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
