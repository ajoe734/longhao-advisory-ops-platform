from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable
import re

from .domain import (
    AccountingExport,
    ActorContext,
    ACCOUNTING_ADAPTER,
    COMPLIANCE_REVIEWER,
    COMPLIANCE_SUPERVISOR,
    CONTROL_REMEDIATION_OWNER,
    ConflictError,
    ControlException,
    DependencyUnavailableError,
    DomainError,
    EvidenceExport,
    EvidenceExportManifestEntry,
    FINANCE_CHECKER,
    FINANCE_OPERATOR,
    ForbiddenError,
    IdempotencyReceipt,
    InboxReceipt,
    INTERNAL_AUDITOR,
    Invoice,
    InvoiceLine,
    Journal,
    JournalLine,
    LegalHold,
    ModuleAuditEvent,
    NotFoundError,
    OutboxEvent,
    PAYMENT_ADAPTER,
    Payment,
    PreconditionRequiredError,
    ReconciliationItem,
    REFUND_SERVICE,
    Refund,
    RefundAllocation,
    RevenueRecognition,
    RevenueRun,
    SERVICE_AGENT,
    ServiceSchedule,
    Settlement,
    SettlementImport,
    StaleVersionError,
    UnauthorizedError,
    ValidationError,
    canonical_hash,
    money_to_api,
    new_id,
    parse_date,
    parse_money,
    parse_time,
    require_uuid,
    utc_now,
    wire_date,
    wire_time,
)
from .ports import EvidenceAssetPort, RefundProviderPort, SettlementStatementPort, TaxMappingPort
from .repository import ContractSnapshot, InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_RECOGNITION_POLICY_VERSION = "service_period_straight_line_v1"
_EXPORT_DESTINATION = "generic_erp_v1"
_INVOICE_TYPES = {"service", "prepayment", "adjustment"}
_PAYMENT_METHODS = {"card", "bank_transfer", "approved_wallet"}
_RETURN_ROUTES = {"application_progress", "member_billing"}
_PAYMENT_CALLBACK_STATUS = {"authorized", "paid", "failed", "disputed"}
_PAYMENT_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"pending", "authorized", "paid", "failed"},
    "authorized": {"authorized", "paid", "failed"},
    "paid": {"paid", "disputed"},
    "failed": {"failed"},
    "disputed": {"disputed"},
}
_REFUND_CALLBACK_STATUS = {"confirmed", "failed", "pending"}
_REFUND_EXECUTABLE_CALLBACK_STATUSES = {"submitted", "reconciliation_required"}
_REFUND_STATES = {"requested", "calculated", "approval_pending", "approved", "submitted", "confirmed", "failed", "reconciliation_required", "accounting_adjusted", "closed", "rejected"}
_RECON_DECISIONS = {"match", "correct_mapping", "accept_timing", "reject", "escalate"}
_RECON_STATES = {"open", "investigating", "resolved", "escalated"}
_EXPORT_ACK_STATUS = {"accepted", "partial", "rejected"}
_EXPORT_STATES = {"prepared", "approved", "exported", "partially_acknowledged", "acknowledged", "failed"}
_REVENUE_RUN_STATES = {"queued", "calculated", "review_pending", "approved", "posted", "failed"}
_PERIOD_CLOSE_STATES = {"blocked", "revenue_pending", "ready_for_export", "export_pending", "closed"}
_CONTROL_ACTIONS = {"add_remediation_evidence", "request_closure", "approve_closure", "reject_closure", "reopen"}


@dataclass(frozen=True)
class ServiceResponse:
    data: dict[str, Any]
    status: int = 200
    etag: str | None = None
    idempotency_replayed: bool = False


@dataclass
class _Outcome:
    value: dict[str, Any]
    status: int
    aggregate_type: str
    aggregate_id: str | None
    aggregate_version: int | None
    classification: str = "internal"
    error: DomainError | None = None


class FinanceControlService:
    """M14 command/query application service for billing, reconciliation,
    revenue recognition, refunds, accounting export and control evidence.

    Every state-changing operation runs inside one repository callback so
    domain state, the module audit trail, outbox events and the idempotency
    receipt commit atomically. External calls (refund provider, settlement
    statement parsing) happen before or outside that transaction boundary so
    no open transaction waits on the network.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        refund_provider: RefundProviderPort,
        settlement_statements: SettlementStatementPort,
        evidence: EvidenceAssetPort,
        tax_mappings: TaxMappingPort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False)
            for adapter in (repository, refund_provider, settlement_statements, evidence, tax_mappings)
        ):
            raise RuntimeError("fake M14 adapters are forbidden in production")
        self.repository = repository
        self.refund_provider = refund_provider
        self.settlement_statements = settlement_statements
        self.evidence = evidence
        self.tax_mappings = tax_mappings
        self._clock = clock

    # ======================================================================
    # UC-M14-A: billing, payments and reconciliation
    # ======================================================================

    def create_invoice(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR})
        self._body(body, {"customer_id", "contract_id", "invoice_date", "due_date", "lines", "invoice_type"}, {"customer_id", "contract_id", "invoice_date", "due_date", "lines", "invoice_type"})
        customer_id = require_uuid(body["customer_id"], "customer_id")
        contract_id = require_uuid(body["contract_id"], "contract_id")
        invoice_date = parse_date(body["invoice_date"], "invoice_date")
        due_date = parse_date(body["due_date"], "due_date")
        if due_date < invoice_date:
            raise ValidationError("due_date must not precede invoice_date", field_name="due_date")
        if body["invoice_type"] not in _INVOICE_TYPES:
            raise ValidationError("invoice_type is not supported", field_name="invoice_type")
        raw_lines = body["lines"]
        if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 100:
            raise ValidationError("lines must contain 1 to 100 items", field_name="lines")
        lines: list[InvoiceLine] = []
        currency: str | None = None
        for index, raw in enumerate(raw_lines):
            field_prefix = f"lines.{index}"
            self._body(
                raw,
                {"contract_service_id", "service_code", "service_period_start", "service_period_end", "quantity", "untaxed_service_amount", "tax_amount", "tax_code", "tax_mapping_version"},
                {"contract_service_id", "service_code", "service_period_start", "service_period_end", "quantity", "untaxed_service_amount", "tax_amount", "tax_code", "tax_mapping_version"},
            )
            contract_service_id = require_uuid(raw["contract_service_id"], f"{field_prefix}.contract_service_id")
            period_start = parse_date(raw["service_period_start"], f"{field_prefix}.service_period_start")
            period_end = parse_date(raw["service_period_end"], f"{field_prefix}.service_period_end")
            if period_end <= period_start:
                raise ValidationError("service_period_end must be after service_period_start", field_name=f"{field_prefix}.service_period_end")
            quantity = raw["quantity"]
            if not isinstance(quantity, int) or quantity < 1:
                raise ValidationError("quantity must be a positive integer", field_name=f"{field_prefix}.quantity")
            untaxed_amount, line_currency = parse_money(raw["untaxed_service_amount"], f"{field_prefix}.untaxed_service_amount")
            tax_amount, tax_currency = parse_money(raw["tax_amount"], f"{field_prefix}.tax_amount")
            if line_currency != tax_currency:
                raise ValidationError("untaxed_service_amount and tax_amount must share a currency", field_name=f"{field_prefix}.tax_amount")
            if currency is None:
                currency = line_currency
            elif currency != line_currency:
                raise ValidationError("all invoice lines must share one currency", field_name=f"{field_prefix}.untaxed_service_amount")
            if untaxed_amount < 0 or tax_amount < 0:
                raise ValidationError("line amounts must not be negative", field_name=field_prefix)
            tax_code = raw["tax_code"]
            tax_mapping_version = raw["tax_mapping_version"]
            if not self.tax_mappings.approved(context.tenant_id, tax_code, tax_mapping_version):
                raise ConflictError(
                    "tax mapping version is not an approved finance configuration; approve it before billing",
                    code="tax_mapping_not_approved",
                )
            lines.append(
                InvoiceLine(
                    id=new_id(),
                    line_no=index + 1,
                    contract_service_id=contract_service_id,
                    service_code=str(raw["service_code"])[:80],
                    service_period_start=period_start,
                    service_period_end=period_end,
                    quantity=quantity,
                    untaxed_amount=untaxed_amount,
                    tax_amount=tax_amount,
                    tax_code=tax_code,
                    tax_mapping_version=tax_mapping_version,
                )
            )
        assert currency is not None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            snapshot = state.contracts.get((context.tenant_id, contract_id))
            if snapshot and snapshot.terminated_at and now > snapshot.terminated_at:
                raise ConflictError("contract is terminated and cannot be billed further", code="contract_terminated")
            invoice_id = new_id()
            invoice_number = f"INV-{invoice_id[:8].upper()}"
            invoice = Invoice(
                id=invoice_id,
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                contract_id=contract_id,
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                due_date=due_date,
                invoice_type=body["invoice_type"],
                currency=currency,
                lines=lines,
                created_at=now,
                updated_at=now,
            )
            state.invoices[(context.tenant_id, invoice_id)] = invoice
            state.invoice_number_index[(context.tenant_id, invoice_number)] = invoice_id
            for line in lines:
                schedule_id = new_id()
                state.service_schedules[(context.tenant_id, schedule_id)] = ServiceSchedule(
                    id=schedule_id,
                    tenant_id=context.tenant_id,
                    contract_service_id=line.contract_service_id,
                    invoice_id=invoice_id,
                    schedule_version=1,
                    period_start=line.service_period_start,
                    period_end=line.service_period_end,
                    untaxed_amount=line.untaxed_amount,
                    currency=currency,
                    recognition_policy_version=_RECOGNITION_POLICY_VERSION,
                    approved_by=context.actor_id,
                    created_at=now,
                )
            return self._invoice_outcome(invoice, context, 201)

        return self._run_command(context, "create_invoice", idempotency_key, body, correlation_id, mutate)

    def create_payment_intent(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR})
        self._body(body, {"application_id", "invoice_id", "amount", "method", "return_route"}, {"application_id", "amount", "method", "return_route"})
        application_id = require_uuid(body["application_id"], "application_id")
        invoice_id = require_uuid(body["invoice_id"], "invoice_id") if body.get("invoice_id") else None
        amount, currency = parse_money(body["amount"], "amount")
        if amount <= 0:
            raise ValidationError("amount must be positive", field_name="amount")
        if body["method"] not in _PAYMENT_METHODS:
            raise ValidationError("method is not supported", field_name="method")
        if body["return_route"] not in _RETURN_ROUTES:
            raise ValidationError("return_route is not supported", field_name="return_route")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            customer_id: str
            if invoice_id:
                invoice = state.invoices.get((context.tenant_id, invoice_id))
                if invoice is None:
                    raise NotFoundError("invoice was not found")
                if invoice.currency != currency:
                    raise ValidationError("amount currency must match the invoice currency", field_name="amount")
                if invoice.status not in {"issued"}:
                    raise ConflictError("invoice is not payable", code="invoice_not_payable")
                customer_id = invoice.customer_id
            else:
                customer_id = self._placeholder_customer_id(context.tenant_id, application_id)
            payment_id = new_id()
            provider_ref = new_id()
            payment = Payment(
                id=payment_id,
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                application_id=application_id,
                invoice_id=invoice_id,
                amount=amount,
                currency=currency,
                method=body["method"],
                return_route=body["return_route"],
                provider="synthetic_payment_provider",
                provider_transaction_id=provider_ref,
                checkout_url=f"https://payments.example.invalid/checkout/{provider_ref}",
                expires_at=now.replace(microsecond=0) + timedelta(minutes=30),
                created_at=now,
                updated_at=now,
            )
            state.payments[(context.tenant_id, payment_id)] = payment
            state.payment_provider_index[(context.tenant_id, payment.provider, payment.provider_transaction_id)] = payment_id
            return _Outcome(payment.to_intent_api([]), 201, "payment", payment_id, payment.version)

        return self._run_command(context, "create_payment_intent", idempotency_key, body, correlation_id, mutate)

    def receive_payment_webhook(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {PAYMENT_ADAPTER}, require_service=True)
        self._body(body, {"event_id", "provider_transaction_ref", "payment_intent_id", "status", "amount", "provider_occurred_at", "evidence_asset_id"}, {"event_id", "provider_transaction_ref", "payment_intent_id", "status", "amount", "provider_occurred_at", "evidence_asset_id"})
        event_id = require_uuid(body["event_id"], "event_id")
        payment_intent_id = require_uuid(body["payment_intent_id"], "payment_intent_id")
        evidence_asset_id = require_uuid(body["evidence_asset_id"], "evidence_asset_id")
        if body["status"] not in _PAYMENT_CALLBACK_STATUS:
            raise ValidationError("status is not supported", field_name="status")
        amount, currency = parse_money(body["amount"], "amount")
        provider_occurred_at = parse_time(body["provider_occurred_at"], "provider_occurred_at")
        if not self.evidence.exists(context.tenant_id, evidence_asset_id):
            raise ValidationError("evidence_asset_id does not exist", code="evidence_not_found", field_name="evidence_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        fingerprint = canonical_hash(body)

        def mutate(state: StoreState) -> _Outcome:
            receipt_key = (context.tenant_id, f"payment:{event_id}")
            previous = state.webhook_receipts.get(receipt_key)
            if previous is not None:
                if previous["payload_sha256"] != fingerprint:
                    receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "quarantined", "received_at": now, "correlation_id": correlation_id}
                    return _Outcome(self._callback_receipt_api(receipt), 202, "payment", previous.get("payment_id"), None)
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "duplicate", "received_at": now, "correlation_id": correlation_id}
                return _Outcome(self._callback_receipt_api(receipt), 202, "payment", previous.get("payment_id"), None)

            payment = state.payments.get((context.tenant_id, payment_intent_id))
            if payment is None:
                raise NotFoundError("payment intent was not found")

            incoming_status = body["status"]

            def quarantine() -> _Outcome:
                state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "quarantined", "payment_id": payment.id}
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "quarantined", "received_at": now, "correlation_id": correlation_id}
                return _Outcome(self._callback_receipt_api(receipt), 202, "payment", payment.id, payment.version, classification="customer_confidential")

            if incoming_status not in _PAYMENT_ALLOWED_TRANSITIONS.get(payment.status, set()):
                # out-of-order or stale redelivery attempting an invalid/backward status transition
                return quarantine()

            if incoming_status == "paid":
                if payment.amount != amount or payment.currency != currency:
                    item_id = new_id()
                    state.reconciliation_items[(context.tenant_id, item_id)] = ReconciliationItem(
                        id=item_id,
                        tenant_id=context.tenant_id,
                        settlement_id=new_id(),
                        payment_id=payment.id,
                        difference_type="amount_mismatch",
                        expected_amount=payment.amount,
                        actual_amount=amount,
                        currency=currency,
                        created_at=now,
                        updated_at=now,
                    )
                    return quarantine()
                if payment.status == "paid":
                    # the same business fact redelivered under a new event_id; already applied, not a new credit
                    state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "accepted", "payment_id": payment.id}
                    receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "duplicate", "received_at": now, "correlation_id": correlation_id}
                    return _Outcome(self._callback_receipt_api(receipt), 202, "payment", payment.id, payment.version, classification="customer_confidential")
                payment.version += 1
                payment.updated_at = now
                payment.status = "paid"
                payment.paid_at = provider_occurred_at
                if payment.provider_transaction_id != body["provider_transaction_ref"]:
                    payment.provider_transaction_id = body["provider_transaction_ref"]
                    state.payment_provider_index[(context.tenant_id, payment.provider, payment.provider_transaction_id)] = payment.id
                if payment.invoice_id:
                    invoice = state.invoices.get((context.tenant_id, payment.invoice_id))
                    if invoice:
                        invoice.paid_amount += amount
                        invoice.updated_at = now
                        if invoice.status == "issued" and invoice.paid_amount >= invoice.total_amount:
                            invoice.status = "paid"
                self._emit(
                    state,
                    event_type="finance.payment_confirmed",
                    tenant_id=context.tenant_id,
                    aggregate_type="payment",
                    aggregate_id=payment.id,
                    aggregate_version=payment.version,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:payment-confirmed",
                    classification="customer_confidential",
                    payload={
                        "payment_id": payment.id,
                        "application_id": payment.application_id or self._placeholder_application_id(payment.id),
                        "invoice_id": payment.invoice_id,
                        "confirmed_at": wire_time(provider_occurred_at),
                        "evidence_snapshot_id": evidence_asset_id,
                    },
                    now=now,
                )
            elif incoming_status == "failed":
                if payment.status == "failed":
                    # the same business fact redelivered under a new event_id; already
                    # applied, must not bump the version or re-emit finance.payment_failed
                    state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "accepted", "payment_id": payment.id}
                    receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "duplicate", "received_at": now, "correlation_id": correlation_id}
                    return _Outcome(self._callback_receipt_api(receipt), 202, "payment", payment.id, payment.version, classification="customer_confidential")
                payment.version += 1
                payment.updated_at = now
                payment.status = "failed"
                self._emit(
                    state,
                    event_type="finance.payment_failed",
                    tenant_id=context.tenant_id,
                    aggregate_type="payment",
                    aggregate_id=payment.id,
                    aggregate_version=payment.version,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:payment-failed",
                    classification="customer_confidential",
                    payload={
                        "payment_id": payment.id,
                        "application_id": payment.application_id or self._placeholder_application_id(payment.id),
                        "customer_id": payment.customer_id,
                        "retry_allowed": True,
                        "customer_safe_reason_code": "payment_not_authorized",
                    },
                    now=now,
                )
            else:  # authorized / disputed: idempotent status sync, no dedicated side effects yet
                if payment.status == incoming_status:
                    # the same business fact redelivered under a new event_id; no-op
                    state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "accepted", "payment_id": payment.id}
                    receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "duplicate", "received_at": now, "correlation_id": correlation_id}
                    return _Outcome(self._callback_receipt_api(receipt), 202, "payment", payment.id, payment.version, classification="customer_confidential")
                payment.version += 1
                payment.updated_at = now
                payment.status = incoming_status
            state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "accepted", "payment_id": payment.id}
            receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "accepted", "received_at": now, "correlation_id": correlation_id}
            return _Outcome(self._callback_receipt_api(receipt), 202, "payment", payment.id, payment.version, classification="customer_confidential")

        return self._run_command(context, "receive_payment_webhook", idempotency_key, body, correlation_id, mutate)

    def import_settlement(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR})
        self._body(
            body,
            {"provider", "batch_ref", "settlement_date", "statement_asset_id", "statement_sha256", "declared_gross", "declared_fee", "declared_net", "declared_count", "format_version"},
            {"provider", "batch_ref", "settlement_date", "statement_asset_id", "statement_sha256", "declared_gross", "declared_fee", "declared_net", "declared_count", "format_version"},
        )
        provider = str(body["provider"])[:80]
        batch_ref = str(body["batch_ref"])[:160]
        settlement_date = parse_date(body["settlement_date"], "settlement_date")
        statement_asset_id = require_uuid(body["statement_asset_id"], "statement_asset_id")
        statement_sha256 = str(body["statement_sha256"])
        if not _SHA_RE.fullmatch(statement_sha256):
            raise ValidationError("statement_sha256 must be a sha256 hex digest", field_name="statement_sha256")
        declared_gross, currency = parse_money(body["declared_gross"], "declared_gross")
        declared_fee, _ = parse_money(body["declared_fee"], "declared_fee")
        declared_net, _ = parse_money(body["declared_net"], "declared_net")
        declared_count = body["declared_count"]
        if not isinstance(declared_count, int) or declared_count < 0:
            raise ValidationError("declared_count must be a non-negative integer", field_name="declared_count")
        if not self.evidence.exists(context.tenant_id, statement_asset_id):
            raise ValidationError("statement_asset_id does not exist", code="evidence_not_found", field_name="statement_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        lines = self.settlement_statements.parse(context.tenant_id, statement_asset_id, statement_sha256)

        def mutate(state: StoreState) -> _Outcome:
            import_id = new_id()
            record = SettlementImport(
                id=import_id,
                tenant_id=context.tenant_id,
                provider=provider,
                batch_ref=batch_ref,
                settlement_date=settlement_date,
                statement_asset_id=statement_asset_id,
                statement_sha256=statement_sha256,
                declared_gross=declared_gross,
                declared_fee=declared_fee,
                declared_net=declared_net,
                declared_count=declared_count,
                format_version=str(body["format_version"])[:80],
                currency=currency,
                created_at=now,
                updated_at=now,
            )
            raw_total_gross = sum((line.gross_amount for line in lines), Decimal("0"))
            if declared_count != len(lines) or declared_gross != raw_total_gross:
                record.status = "rejected"
                state.settlement_imports[(context.tenant_id, import_id)] = record
                return _Outcome(record.to_api([]), 202, "settlement_import", import_id, record.version)

            matched = 0
            exceptions = 0
            gross_total = fee_total = net_total = Decimal("0")
            for line in lines:
                payment_id = state.payment_provider_index.get((context.tenant_id, provider, line.provider_transaction_ref))
                settlement_ref = f"{batch_ref}:{line.provider_transaction_ref}"
                if payment_id is None:
                    exceptions += 1
                    item_id = new_id()
                    state.reconciliation_items[(context.tenant_id, item_id)] = ReconciliationItem(
                        id=item_id,
                        tenant_id=context.tenant_id,
                        settlement_id=new_id(),
                        difference_type="unmatched_settlement",
                        expected_amount=Decimal("0"),
                        actual_amount=line.gross_amount,
                        currency=line.currency,
                        created_at=now,
                        updated_at=now,
                    )
                    continue
                if (context.tenant_id, line.provider_transaction_ref) in state.settlement_provider_index:
                    exceptions += 1
                    item_id = new_id()
                    state.reconciliation_items[(context.tenant_id, item_id)] = ReconciliationItem(
                        id=item_id,
                        tenant_id=context.tenant_id,
                        settlement_id=new_id(),
                        payment_id=payment_id,
                        difference_type="duplicate",
                        expected_amount=line.gross_amount,
                        actual_amount=line.gross_amount,
                        currency=line.currency,
                        created_at=now,
                        updated_at=now,
                    )
                    continue
                settlement_id = new_id()
                settlement = Settlement(
                    id=settlement_id,
                    tenant_id=context.tenant_id,
                    payment_id=payment_id,
                    provider_settlement_id=settlement_ref,
                    gross_amount=line.gross_amount,
                    fee_amount=line.fee_amount,
                    net_amount=line.net_amount,
                    currency=line.currency,
                    settled_at=now,
                    bank_line_ref=line.bank_line_ref,
                    created_at=now,
                    updated_at=now,
                )
                state.settlements[(context.tenant_id, settlement_id)] = settlement
                state.settlement_provider_index[(context.tenant_id, line.provider_transaction_ref)] = settlement_id
                gross_total += line.gross_amount
                fee_total += line.fee_amount
                net_total += line.net_amount
                payment = state.payments[(context.tenant_id, payment_id)]
                if payment.amount != line.gross_amount or payment.currency != line.currency:
                    exceptions += 1
                    item_id = new_id()
                    state.reconciliation_items[(context.tenant_id, item_id)] = ReconciliationItem(
                        id=item_id,
                        tenant_id=context.tenant_id,
                        settlement_id=settlement_id,
                        payment_id=payment_id,
                        difference_type="amount_mismatch",
                        expected_amount=payment.amount,
                        actual_amount=line.gross_amount,
                        currency=line.currency,
                        created_at=now,
                        updated_at=now,
                    )
                else:
                    matched += 1

            record.matched_count = matched
            record.exception_count = exceptions
            record.gross, record.fee, record.net = gross_total, fee_total, net_total
            record.status = "exceptions" if exceptions else "matched"
            state.settlement_imports[(context.tenant_id, import_id)] = record
            return _Outcome(record.to_api([]), 202, "settlement_import", import_id, record.version)

        return self._run_command(context, "import_settlement", idempotency_key, body, correlation_id, mutate)

    def list_reconciliation_items(self, context: ActorContext, *, state_filter: str | None = None, cursor: str | None = None, limit: int = 50, as_of: datetime | None = None, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR, FINANCE_CHECKER})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in _RECON_STATES:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in state.reconciliation_items.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not state_filter or item.status == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[: limit]
            self._audit(state, context, "list_reconciliation_items", "reconciliation_item", None, correlation_id, "success", "internal")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._reconciliation_actions(item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def resolve_reconciliation_item(self, context: ActorContext, item_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        item_id = require_uuid(item_id, "item_id")
        self._authorize(context, {FINANCE_CHECKER})
        self._body(body, {"decision", "payment_id", "reason_code", "evidence_asset_id"}, {"decision", "reason_code", "evidence_asset_id"})
        if body["decision"] not in _RECON_DECISIONS:
            raise ValidationError("decision is not supported", field_name="decision")
        evidence_asset_id = require_uuid(body["evidence_asset_id"], "evidence_asset_id")
        payment_id = require_uuid(body["payment_id"], "payment_id") if body.get("payment_id") else None
        if not self.evidence.exists(context.tenant_id, evidence_asset_id):
            raise ValidationError("evidence_asset_id does not exist", code="evidence_not_found", field_name="evidence_asset_id")
        if body["decision"] == "match" and payment_id is None:
            raise ValidationError("match requires payment_id", field_name="payment_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            item = self._stored_reconciliation(state, context.tenant_id, item_id)
            self._cas(item.version, expected_version)
            decision = body["decision"]
            item.reason_code = body["reason_code"]
            item.version += 1
            item.updated_at = now
            if decision == "match":
                item.payment_id = payment_id
                item.status = "resolved"
                item.resolved_by = context.actor_id
            elif decision in {"correct_mapping", "accept_timing"}:
                item.status = "resolved"
                item.resolved_by = context.actor_id
            elif decision == "reject":
                item.status = "investigating"
            else:
                item.status = "escalated"
            return _Outcome(item.to_api(self._reconciliation_actions(item, context)), 200, "reconciliation_item", item.id, item.version)

        return self._run_command(context, "resolve_reconciliation_item", idempotency_key, {"item_id": item_id, **body}, correlation_id, mutate)

    # ======================================================================
    # UC-M14-B: revenue recognition
    # ======================================================================

    def run_revenue_recognition(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR})
        self._body(body, {"period_start", "period_end", "recognition_policy_version", "cutoff_at", "dry_run"}, {"period_start", "period_end", "recognition_policy_version", "cutoff_at", "dry_run"})
        period_start = parse_date(body["period_start"], "period_start")
        period_end = parse_date(body["period_end"], "period_end")
        if period_end <= period_start:
            raise ValidationError("period_end must be after period_start", field_name="period_end")
        cutoff_at = parse_time(body["cutoff_at"], "cutoff_at")
        dry_run = body["dry_run"]
        if not isinstance(dry_run, bool):
            raise ValidationError("dry_run must be a boolean", field_name="dry_run")
        policy_version = str(body["recognition_policy_version"])[:80]
        run_key = canonical_hash({"period_start": wire_date(period_start), "period_end": wire_date(period_end), "policy": policy_version, "cutoff_at": wire_time(cutoff_at), "dry_run": dry_run})
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            existing_id = state.revenue_run_key_index.get((context.tenant_id, run_key))
            if existing_id:
                run = state.revenue_runs[(context.tenant_id, existing_id)]
                return _Outcome(run.to_api(self._revenue_run_actions(run, context)), 202, "revenue_run", run.id, run.version)

            included: list[str] = []
            excluded: list[str] = []
            recognized_total = Decimal("0")
            touched_invoices: set[str] = set()
            for (tenant, schedule_id), schedule in state.service_schedules.items():
                if tenant != context.tenant_id:
                    continue
                if schedule.recognition_policy_version != policy_version:
                    continue
                if not (period_start <= schedule.period_start < period_end):
                    continue
                if schedule.period_start > cutoff_at.date():
                    continue
                if (context.tenant_id, schedule_id) in state.recognition_key_index:
                    continue
                invoice = state.invoices.get((context.tenant_id, schedule.invoice_id))
                if invoice is None or invoice.paid_amount <= 0:
                    continue
                snapshot = state.contracts.get((context.tenant_id, invoice.contract_id))
                if snapshot and snapshot.terminated_at and snapshot.terminated_at.date() < schedule.period_end:
                    excluded.append(schedule_id)
                    continue
                if not (
                    snapshot
                    and snapshot.accepted
                    and snapshot.effective_at
                    and snapshot.effective_at <= cutoff_at
                    and snapshot.service_starts_at
                    and snapshot.service_starts_at.date() <= schedule.period_start
                ):
                    # the contract was never confirmed accepted/effective (or service has not started yet)
                    # for this service period; recognizing revenue here would run ahead of the deal itself.
                    excluded.append(schedule_id)
                    continue
                included.append(schedule_id)
                recognized_total += schedule.untaxed_amount
                touched_invoices.add(invoice.id)

            deferred_remaining = Decimal("0")
            for invoice_id in touched_invoices:
                invoice = state.invoices[(context.tenant_id, invoice_id)]
                already = self._invoice_recognized_total(state, context.tenant_id, invoice_id)
                newly = sum(
                    (
                        state.service_schedules[(context.tenant_id, sid)].untaxed_amount
                        for sid in included
                        if state.service_schedules[(context.tenant_id, sid)].invoice_id == invoice_id
                    ),
                    Decimal("0"),
                )
                deferred_remaining += max(invoice.paid_amount - already - newly, Decimal("0"))

            currency = "TWD"
            if included:
                currency = state.service_schedules[(context.tenant_id, included[0])].currency
            manifest = canonical_hash({"included": sorted(included), "excluded": sorted(excluded), "total": str(recognized_total), "run_key": run_key})
            run_id = new_id()
            run = RevenueRun(
                id=run_id,
                tenant_id=context.tenant_id,
                run_key=run_key,
                period_start=period_start,
                period_end=period_end,
                recognition_policy_version=policy_version,
                cutoff_at=cutoff_at,
                dry_run=dry_run,
                prepared_by=context.actor_id,
                currency=currency,
                status="calculated" if dry_run else ("review_pending" if included or excluded else "calculated"),
                recognized_untaxed_total=recognized_total,
                deferred_remaining=deferred_remaining,
                entry_count=len(included),
                exception_count=len(excluded),
                calculation_manifest_sha256=manifest,
                included_schedule_ids=included,
                exception_schedule_ids=excluded,
                created_at=now,
                updated_at=now,
            )
            state.revenue_runs[(context.tenant_id, run_id)] = run
            state.revenue_run_key_index[(context.tenant_id, run_key)] = run_id
            return _Outcome(run.to_api(self._revenue_run_actions(run, context)), 202, "revenue_run", run_id, run.version)

        return self._run_command(context, "run_revenue_recognition", idempotency_key, body, correlation_id, mutate)

    def list_revenue_runs(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR, FINANCE_CHECKER})
        self._validate_finance_list_filters(state_filter, _REVENUE_RUN_STATES, period_start, period_end, limit)
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    run for (tenant, _), run in state.revenue_runs.items()
                    if tenant == context.tenant_id
                    and run.created_at <= snapshot_at
                    and (state_filter is None or run.status == state_filter)
                    and self._period_overlaps(run.period_start, run.period_end, period_start, period_end)
                    and (cursor is None or run.id > cursor)
                ),
                key=lambda run: run.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_revenue_runs", "revenue_run", None, correlation_id, "success", "internal")
            return ServiceResponse(
                {
                    "items": [run.to_api(self._revenue_run_actions(run, context)) for run in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_revenue_run(self, context: ActorContext, run_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        run_id = require_uuid(run_id, "run_id")
        self._authorize(context, {FINANCE_OPERATOR, FINANCE_CHECKER})
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            run = self._stored_revenue_run(state, context.tenant_id, run_id)
            self._audit(state, context, "get_revenue_run", "revenue_run", run.id, correlation_id, "success", "internal")
            return ServiceResponse(run.to_api(self._revenue_run_actions(run, context)), etag=self._etag(run.version))

        return self.repository.atomic(query)

    def approve_revenue_run(self, context: ActorContext, run_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        run_id = require_uuid(run_id, "run_id")
        self._authorize(context, {FINANCE_CHECKER})
        self._body(body, {"decision", "calculation_manifest_sha256", "reason_code", "review_evidence_id"}, {"decision", "calculation_manifest_sha256", "reason_code", "review_evidence_id"})
        if body["decision"] not in {"approve", "reject"}:
            raise ValidationError("decision is not supported", field_name="decision")
        review_evidence_id = require_uuid(body["review_evidence_id"], "review_evidence_id")
        if not self.evidence.exists(context.tenant_id, review_evidence_id):
            raise ValidationError("review_evidence_id does not exist", code="evidence_not_found", field_name="review_evidence_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            run = self._stored_revenue_run(state, context.tenant_id, run_id)
            self._cas(run.version, expected_version)
            if run.prepared_by == context.actor_id:
                raise ForbiddenError("the approver must differ from the preparer", code="maker_checker_violation")
            if run.dry_run:
                raise ConflictError("a dry-run revenue run cannot be approved", code="dry_run_cannot_be_approved")
            if run.status not in {"calculated", "review_pending"}:
                raise ConflictError("revenue run is not awaiting approval", code="revenue_run_not_pending")
            run.version += 1
            run.updated_at = now
            if body["decision"] == "reject":
                run.status = "failed"
                return _Outcome(run.to_api(self._revenue_run_actions(run, context)), 200, "revenue_run", run.id, run.version)
            if body["calculation_manifest_sha256"] != run.calculation_manifest_sha256:
                raise ConflictError("calculation manifest no longer matches the run", code="calculation_manifest_mismatch")
            if run.exception_count:
                raise ConflictError("revenue run has unresolved exceptions", code="run_has_unresolved_exceptions")
            lines: list[JournalLine] = []
            recognitions: list[RevenueRecognition] = []
            for schedule_id in run.included_schedule_ids:
                if (context.tenant_id, schedule_id) in state.recognition_key_index:
                    raise ConflictError("a schedule was already recognized by another run", code="schedule_already_recognized")
                schedule = state.service_schedules[(context.tenant_id, schedule_id)]
                invoice = state.invoices[(context.tenant_id, schedule.invoice_id)]
                recognition_id = new_id()
                recognitions.append(
                    RevenueRecognition(
                        id=recognition_id,
                        tenant_id=context.tenant_id,
                        run_id=run.id,
                        schedule_id=schedule_id,
                        recognition_key=schedule_id,
                        recognized_at=now,
                        untaxed_amount=schedule.untaxed_amount,
                        currency=schedule.currency,
                        confirmed_by=context.actor_id,
                        policy_version=run.recognition_policy_version,
                        contract_id=invoice.contract_id,
                        contract_service_id=schedule.contract_service_id,
                    )
                )
            if run.recognized_untaxed_total > 0:
                lines.append(JournalLine(1, "deferred_revenue_liability", run.recognized_untaxed_total, Decimal("0")))
                lines.append(JournalLine(2, "service_revenue", Decimal("0"), run.recognized_untaxed_total))
            journal_ids: list[str] = []
            if lines:
                journal = self._post_journal(
                    state,
                    source_type="revenue",
                    source_id=run.id,
                    lines=lines,
                    prepared_by=run.prepared_by,
                    approved_by=context.actor_id,
                    accounting_date=run.period_end,
                    currency=run.currency,
                    now=now,
                )
                journal_ids.append(journal.id)
            for recognition in recognitions:
                state.revenue_recognitions[(context.tenant_id, recognition.id)] = recognition
                state.recognition_key_index[(context.tenant_id, recognition.recognition_key)] = recognition.id
                schedule = state.service_schedules[(context.tenant_id, recognition.schedule_id)]
                self._emit(
                    state,
                    event_type="finance.revenue_recognized",
                    tenant_id=context.tenant_id,
                    aggregate_type="revenue_recognition",
                    aggregate_id=recognition.id,
                    aggregate_version=1,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:{recognition.id}",
                    classification="internal",
                    payload={
                        "recognition_id": recognition.id,
                        "contract_id": recognition.contract_id,
                        "contract_service_id": recognition.contract_service_id,
                        "policy_version": recognition.policy_version,
                        "service_period_start": wire_date(schedule.period_start),
                        "service_period_end": wire_date(schedule.period_end),
                        "posted_journal_id": journal_ids[0] if journal_ids else recognition.id,
                    },
                    now=now,
                )
            run.status = "posted"
            run.approved_by = context.actor_id
            run.approved_at = now
            run.posted_at = now
            run.journal_ids = journal_ids
            return _Outcome(run.to_api(self._revenue_run_actions(run, context)), 200, "revenue_run", run.id, run.version, classification="internal")

        return self._run_command(context, "approve_revenue_run", idempotency_key, {"run_id": run_id, **body}, correlation_id, mutate)

    # ======================================================================
    # UC-M14-C: refunds and accounting integration
    # ======================================================================

    def request_refund(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR, SERVICE_AGENT})
        self._body(
            body,
            {"customer_id", "contract_id", "original_payment_id", "case_id", "reason_code", "requested_amount", "allocations", "calculation_policy_version", "customer_request_evidence_id"},
            {"customer_id", "contract_id", "original_payment_id", "reason_code", "requested_amount", "allocations", "calculation_policy_version", "customer_request_evidence_id"},
        )
        customer_id = require_uuid(body["customer_id"], "customer_id")
        contract_id = require_uuid(body["contract_id"], "contract_id")
        original_payment_id = require_uuid(body["original_payment_id"], "original_payment_id")
        case_id = require_uuid(body["case_id"], "case_id") if body.get("case_id") else None
        requested_amount, currency = parse_money(body["requested_amount"], "requested_amount")
        if requested_amount <= 0:
            raise ValidationError("requested_amount must be positive", field_name="requested_amount")
        customer_request_evidence_id = require_uuid(body["customer_request_evidence_id"], "customer_request_evidence_id")
        if not self.evidence.exists(context.tenant_id, customer_request_evidence_id):
            raise ValidationError("customer_request_evidence_id does not exist", code="evidence_not_found", field_name="customer_request_evidence_id")
        raw_allocations = body["allocations"]
        if not isinstance(raw_allocations, list) or not 1 <= len(raw_allocations) <= 100:
            raise ValidationError("allocations must contain 1 to 100 items", field_name="allocations")
        allocations: list[RefundAllocation] = []
        total = Decimal("0")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            payment = state.payments.get((context.tenant_id, original_payment_id))
            if payment is None:
                raise NotFoundError("original payment was not found")
            if payment.status not in {"paid", "refund_pending"}:
                raise ConflictError("payment is not eligible for refund", code="payment_not_refundable")
            if payment.customer_id != customer_id:
                raise ValidationError("customer_id does not match the original payment", field_name="customer_id")
            if currency != payment.currency:
                raise ValidationError("requested_amount currency must match the original payment", field_name="requested_amount")
            if payment.invoice_id is None:
                raise ConflictError("original payment is not associated with an invoice", code="payment_invoice_not_found")
            invoice = state.invoices.get((context.tenant_id, payment.invoice_id))
            if invoice is None:
                raise ConflictError("original payment invoice was not found", code="payment_invoice_not_found")
            already_requested = sum(
                (
                    r.requested_amount for (tenant, _), r in state.refunds.items()
                    if tenant == context.tenant_id and r.original_payment_id == original_payment_id and r.status != "rejected"
                ),
                Decimal("0"),
            )
            if already_requested + requested_amount > payment.amount:
                raise ConflictError("requested_amount exceeds the original payment amount", code="refund_amount_exceeds_payment")
            nonlocal total
            total = Decimal("0")
            for index, raw in enumerate(raw_allocations):
                prefix = f"allocations.{index}"
                self._body(raw, {"invoice_line_ref", "recognition_id", "unearned_amount", "earned_reversal_amount", "tax_adjustment_amount"}, {"invoice_line_ref", "recognition_id", "unearned_amount", "earned_reversal_amount", "tax_adjustment_amount"})
                invoice_line_ref = require_uuid(raw["invoice_line_ref"], f"{prefix}.invoice_line_ref")
                if not any(line.id == invoice_line_ref for line in invoice.lines):
                    raise ValidationError(
                        "invoice_line_ref must reference a line on the original payment invoice",
                        code="invoice_line_not_on_original_payment",
                        field_name=f"{prefix}.invoice_line_ref",
                    )
                recognition_id = require_uuid(raw["recognition_id"], f"{prefix}.recognition_id") if raw.get("recognition_id") else None
                unearned, c1 = parse_money(raw["unearned_amount"], f"{prefix}.unearned_amount")
                earned, c2 = parse_money(raw["earned_reversal_amount"], f"{prefix}.earned_reversal_amount")
                tax_adj, c3 = parse_money(raw["tax_adjustment_amount"], f"{prefix}.tax_adjustment_amount")
                if not (c1 == c2 == c3 == currency):
                    raise ValidationError("allocation amounts must share the refund currency", field_name=prefix)
                if unearned < 0 or earned < 0 or tax_adj < 0:
                    raise ValidationError("allocation amounts must not be negative", field_name=prefix)
                if earned > 0:
                    if recognition_id is None:
                        raise ValidationError("earned_reversal_amount requires recognition_id", field_name=f"{prefix}.recognition_id")
                    recognition = state.revenue_recognitions.get((context.tenant_id, recognition_id))
                    if recognition is None or not recognition.commission_eligible:
                        raise ValidationError("recognition_id does not reference an eligible recognized amount", field_name=f"{prefix}.recognition_id")
                elif recognition_id is not None:
                    raise ValidationError("recognition_id must be omitted when earned_reversal_amount is zero", field_name=f"{prefix}.recognition_id")
                allocations.append(RefundAllocation(invoice_line_ref, unearned, earned, tax_adj, currency, recognition_id))
                total += unearned + earned + tax_adj
            if total != requested_amount:
                raise ValidationError("allocation amounts must sum to requested_amount", field_name="allocations")

            refund_id = new_id()
            refund = Refund(
                id=refund_id,
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                contract_id=contract_id,
                original_payment_id=original_payment_id,
                reason_code=str(body["reason_code"])[:80],
                requested_amount=requested_amount,
                currency=currency,
                allocations=allocations,
                calculation_policy_version=str(body["calculation_policy_version"])[:80],
                customer_request_evidence_id=customer_request_evidence_id,
                idempotency_key=idempotency_key,
                prepared_by=context.actor_id,
                case_id=case_id,
                created_at=now,
                updated_at=now,
            )
            state.refunds[(context.tenant_id, refund_id)] = refund
            state.refund_idempotency_index[(context.tenant_id, idempotency_key)] = refund_id
            return _Outcome(refund.to_api(self._refund_actions(refund, context)), 201, "refund", refund_id, refund.version, classification="customer_confidential")

        return self._run_command(context, "request_refund", idempotency_key, body, correlation_id, mutate)

    def list_refunds(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR, SERVICE_AGENT, FINANCE_CHECKER, REFUND_SERVICE})
        self._validate_finance_list_filters(state_filter, _REFUND_STATES, period_start, period_end, limit)
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    refund for (tenant, _), refund in state.refunds.items()
                    if tenant == context.tenant_id
                    and refund.created_at <= snapshot_at
                    and (state_filter is None or refund.status == state_filter)
                    and self._period_overlaps(refund.created_at.date(), refund.created_at.date() + timedelta(days=1), period_start, period_end)
                    and (cursor is None or refund.id > cursor)
                ),
                key=lambda refund: refund.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_refunds", "refund", None, correlation_id, "success", "customer_confidential")
            return ServiceResponse(
                {
                    "items": [refund.to_api(self._refund_actions(refund, context)) for refund in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_refund(self, context: ActorContext, refund_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        refund_id = require_uuid(refund_id, "refund_id")
        self._authorize(context, {FINANCE_OPERATOR, SERVICE_AGENT, FINANCE_CHECKER, REFUND_SERVICE})
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            refund = self._stored_refund(state, context.tenant_id, refund_id)
            self._audit(state, context, "get_refund", "refund", refund.id, correlation_id, "success", "customer_confidential")
            return ServiceResponse(refund.to_api(self._refund_actions(refund, context)), etag=self._etag(refund.version))

        return self.repository.atomic(query)

    def approve_refund(self, context: ActorContext, refund_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        refund_id = require_uuid(refund_id, "refund_id")
        self._authorize(context, {FINANCE_CHECKER})
        self._body(body, {"decision", "reason_code", "note", "evidence_asset_ids"}, {"decision", "reason_code"})
        if body["decision"] not in {"approve", "reject", "request_changes"}:
            raise ValidationError("decision is not supported", field_name="decision")
        for asset_id in body.get("evidence_asset_ids", []):
            if not self.evidence.exists(context.tenant_id, require_uuid(asset_id, "evidence_asset_ids")):
                raise ValidationError("evidence_asset_ids contains an unknown asset", code="evidence_not_found", field_name="evidence_asset_ids")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            refund = self._stored_refund(state, context.tenant_id, refund_id)
            self._cas(refund.version, expected_version)
            if refund.prepared_by == context.actor_id:
                raise ForbiddenError("the approver must differ from the preparer", code="maker_checker_violation")
            if refund.status != "approval_pending":
                raise ConflictError("refund is not awaiting approval", code="refund_not_pending")
            refund.version += 1
            refund.updated_at = now
            if body["decision"] == "approve":
                refund.status = "approved"
                refund.approved_by = context.actor_id
                refund.approved_amount = refund.requested_amount
            else:
                refund.status = "rejected"
            return _Outcome(refund.to_api(self._refund_actions(refund, context)), 200, "refund", refund.id, refund.version, classification="customer_confidential")

        return self._run_command(context, "approve_refund", idempotency_key, {"refund_id": refund_id, **body}, correlation_id, mutate)

    def execute_refund(self, context: ActorContext, refund_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        refund_id = require_uuid(refund_id, "refund_id")
        self._authorize(context, {REFUND_SERVICE}, require_service=True)
        self._body(body, {"approval_record_id", "original_payment_id", "provider_idempotency_key", "last_provider_status"}, {"approval_record_id", "original_payment_id", "provider_idempotency_key", "last_provider_status"})
        approval_record_id = require_uuid(body["approval_record_id"], "approval_record_id")
        original_payment_id = require_uuid(body["original_payment_id"], "original_payment_id")
        provider_key = str(body["provider_idempotency_key"])
        if not _KEY_RE.fullmatch(provider_key) or len(provider_key) > 128:
            raise ValidationError("provider_idempotency_key must match the published contract", field_name="provider_idempotency_key")
        if body["last_provider_status"] not in {"not_submitted", "confirmed_absent"}:
            raise ValidationError("last_provider_status is not supported", field_name="last_provider_status")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        request = {"refund_id": refund_id, **body}

        if not _KEY_RE.fullmatch(idempotency_key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, "execute_refund", idempotency_key)

        def load_replay(state: StoreState) -> IdempotencyReceipt | None:
            receipt = state.idempotency.get(receipt_key)
            if receipt is not None and receipt.request_sha256 != fingerprint:
                raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
            return receipt

        replay = self.repository.atomic(load_replay)
        if replay is not None:
            if replay.error:
                raise replay.error
            return ServiceResponse(replay.response_value, replay.response_status, None, True)

        # Phase 1: durably commit the provider idempotency key and a "submitted" marker
        # *before* calling the refund provider. If the process crashes or the call times
        # out after this point, the key survives (unlike inside repository.atomic(), which
        # rolls back entirely on exception) so a retry is forced through the same key
        # instead of minting a new one and double-submitting to the provider (UAT-M14-006).
        def prepare(state: StoreState) -> Refund:
            refund = self._stored_refund(state, context.tenant_id, refund_id)
            self._cas(refund.version, expected_version)
            if refund.approved_by == context.actor_id:
                raise ForbiddenError("the executing identity must differ from the approver", code="maker_checker_violation")
            if approval_record_id != refund.id:
                raise ValidationError("approval_record_id does not match this refund", field_name="approval_record_id")
            if original_payment_id != refund.original_payment_id:
                raise ValidationError("original_payment_id does not match this refund", field_name="original_payment_id")
            if refund.status not in {"approved", "submitted", "reconciliation_required"}:
                raise ConflictError("refund is not in an executable state", code="refund_not_executable")
            if refund.provider_idempotency_key is None:
                refund.provider_idempotency_key = provider_key
            elif refund.provider_idempotency_key != provider_key:
                raise ConflictError("provider_idempotency_key must stay constant across retries", code="provider_idempotency_key_mismatch")
            refund.version += 1
            refund.updated_at = now
            if refund.status == "approved":
                refund.status = "submitted"
            return refund

        try:
            prepared = self.repository.atomic(prepare)
        except DomainError as exc:
            self._audit_failure(context, "execute_refund", correlation_id, exc.code)
            raise

        # Phase 2: the provider call itself happens with no open transaction.
        known = self.refund_provider.query(provider_key)
        if known is None:
            try:
                result = self.refund_provider.submit(provider_key, prepared.approved_amount or prepared.requested_amount, prepared.currency, prepared.original_payment_id)
            except DependencyUnavailableError as exc:
                self._audit_failure(context, "execute_refund", correlation_id, exc.code)
                raise
        else:
            result = known

        # Phase 3: commit the provider result.
        def finalize(state: StoreState) -> _Outcome:
            refund = self._stored_refund(state, context.tenant_id, refund_id)
            # a concurrent receive_refund_webhook may already have finalized this
            # refund while the provider call above was in flight (Phase 2 holds no
            # lock); do not re-finalize or double-emit in that case.
            if refund.status not in {"confirmed", "accounting_adjusted", "closed", "rejected"}:
                refund.version += 1
                refund.updated_at = now
                if result.status == "confirmed":
                    self._finalize_refund_confirmed(state, refund, result.provider_refund_ref or new_id(), result.occurred_at or now, correlation_id, idempotency_key, now)
                elif result.status == "failed":
                    refund.status = "failed"
                else:
                    refund.status = "reconciliation_required"
            outcome = _Outcome(refund.to_api(self._refund_actions(refund, context)), 202, "refund", refund.id, refund.version, classification="customer_confidential")
            self._audit(state, context, "execute_refund", outcome.aggregate_type, outcome.aggregate_id, correlation_id, "success", outcome.classification)
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                operation="execute_refund",
                key=idempotency_key,
                request_sha256=fingerprint,
                response_status=outcome.status,
                response_value=outcome.value,
                error=None,
                completed_at=self._clock(),
            )
            return outcome

        outcome = self.repository.atomic(finalize)
        etag = self._etag(outcome.aggregate_version) if outcome.aggregate_version else None
        return ServiceResponse(outcome.value, outcome.status, etag, False)

    def receive_refund_webhook(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {PAYMENT_ADAPTER}, require_service=True)
        self._body(body, {"event_id", "refund_id", "provider_refund_ref", "status", "amount", "provider_occurred_at", "evidence_asset_id"}, {"event_id", "refund_id", "provider_refund_ref", "status", "amount", "provider_occurred_at", "evidence_asset_id"})
        event_id = require_uuid(body["event_id"], "event_id")
        refund_id = require_uuid(body["refund_id"], "refund_id")
        evidence_asset_id = require_uuid(body["evidence_asset_id"], "evidence_asset_id")
        if body["status"] not in _REFUND_CALLBACK_STATUS:
            raise ValidationError("status is not supported", field_name="status")
        amount, currency = parse_money(body["amount"], "amount")
        provider_occurred_at = parse_time(body["provider_occurred_at"], "provider_occurred_at")
        if not self.evidence.exists(context.tenant_id, evidence_asset_id):
            raise ValidationError("evidence_asset_id does not exist", code="evidence_not_found", field_name="evidence_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        fingerprint = canonical_hash(body)

        def mutate(state: StoreState) -> _Outcome:
            receipt_key = (context.tenant_id, f"refund:{event_id}")
            previous = state.webhook_receipts.get(receipt_key)
            if previous is not None:
                status = "duplicate" if previous["payload_sha256"] == fingerprint else "quarantined"
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": status, "received_at": now, "correlation_id": correlation_id}
                return _Outcome(self._callback_receipt_api(receipt), 202, "refund", previous.get("refund_id"), None)

            refund = state.refunds.get((context.tenant_id, refund_id))
            if refund is None:
                raise NotFoundError("refund was not found")
            if refund.status not in _REFUND_EXECUTABLE_CALLBACK_STATUSES:
                # terminal (confirmed/accounting_adjusted/closed/rejected) or never executed
                # (requested/approval_pending/approved without an execute_refund call yet)
                state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "quarantined", "refund_id": refund.id}
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "quarantined", "received_at": now, "correlation_id": correlation_id}
                return _Outcome(self._callback_receipt_api(receipt), 202, "refund", refund.id, refund.version)

            if body["status"] == "confirmed":
                if amount != (refund.approved_amount or refund.requested_amount) or currency != refund.currency:
                    state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "quarantined", "refund_id": refund.id}
                    receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "quarantined", "received_at": now, "correlation_id": correlation_id}
                    return _Outcome(self._callback_receipt_api(receipt), 202, "refund", refund.id, refund.version)
                self._finalize_refund_confirmed(state, refund, body["provider_refund_ref"], provider_occurred_at, correlation_id, idempotency_key, now)
            elif body["status"] == "failed":
                refund.version += 1
                refund.updated_at = now
                refund.status = "failed"

            state.webhook_receipts[receipt_key] = {"payload_sha256": fingerprint, "status": "accepted", "refund_id": refund.id}
            receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "accepted", "received_at": now, "correlation_id": correlation_id}
            return _Outcome(self._callback_receipt_api(receipt), 202, "refund", refund.id, refund.version, classification="customer_confidential")

        return self._run_command(context, "receive_refund_webhook", idempotency_key, body, correlation_id, mutate)

    def _finalize_refund_confirmed(self, state: StoreState, refund: Refund, provider_refund_ref: str, confirmed_at: datetime, correlation_id: str, idempotency_key: str, now: datetime) -> None:
        refund.version += 1
        refund.updated_at = now
        refund.status = "confirmed"
        refund.provider_refund_ref = provider_refund_ref
        refund.confirmed_at = confirmed_at

        lines: list[JournalLine] = []
        line_no = 1
        unearned_total = sum((a.unearned_amount for a in refund.allocations), Decimal("0"))
        earned_total = sum((a.earned_reversal_amount for a in refund.allocations), Decimal("0"))
        tax_total = sum((a.tax_adjustment_amount for a in refund.allocations), Decimal("0"))
        if unearned_total > 0:
            lines.append(JournalLine(line_no, "unearned_prepayment_liability", unearned_total, Decimal("0")))
            line_no += 1
        if earned_total > 0:
            lines.append(JournalLine(line_no, "service_revenue", earned_total, Decimal("0")))
            line_no += 1
        if tax_total > 0:
            lines.append(JournalLine(line_no, "tax_payable", tax_total, Decimal("0")))
            line_no += 1
        refunded_total = unearned_total + earned_total + tax_total
        if refunded_total > 0:
            lines.append(JournalLine(line_no, "cash_refunded", Decimal("0"), refunded_total))

        if lines:
            self._post_journal(
                state,
                source_type="refund",
                source_id=refund.id,
                lines=lines,
                prepared_by=refund.prepared_by,
                approved_by=refund.approved_by or refund.prepared_by,
                accounting_date=now.date(),
                currency=refund.currency,
                now=now,
            )
        refund.status = "accounting_adjusted"

        payment = state.payments.get((refund.tenant_id, refund.original_payment_id))
        if payment is not None:
            payment.version += 1
            payment.updated_at = now
            payment.status = "refunded"

        self._emit(
            state,
            event_type="finance.refund_confirmed",
            tenant_id=refund.tenant_id,
            aggregate_type="refund",
            aggregate_id=refund.id,
            aggregate_version=refund.version,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:refund-confirmed",
            classification="customer_confidential",
            payload={
                "refund_id": refund.id,
                "contract_id": refund.contract_id,
                "original_payment_id": refund.original_payment_id,
                "allocation_snapshot_id": new_id(),
                "confirmed_at": wire_time(confirmed_at),
            },
            now=now,
        )

    # ---- accounting export -------------------------------------------------

    def create_accounting_export(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR})
        self._body(body, {"period_start", "period_end", "journal_ids", "account_mapping_version", "tax_mapping_version", "export_format"}, {"period_start", "period_end", "journal_ids", "account_mapping_version", "tax_mapping_version", "export_format"})
        period_start = parse_date(body["period_start"], "period_start")
        period_end = parse_date(body["period_end"], "period_end")
        if period_end <= period_start:
            raise ValidationError("period_end must be after period_start", field_name="period_end")
        raw_ids = body["journal_ids"]
        if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 10000:
            raise ValidationError("journal_ids must contain 1 to 10000 items", field_name="journal_ids")
        journal_ids = [require_uuid(item, "journal_ids") for item in raw_ids]
        if body["export_format"] not in {"csv_v1", "json_v1"}:
            raise ValidationError("export_format is not supported", field_name="export_format")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            journals: list[Journal] = []
            for journal_id in journal_ids:
                journal = state.journals.get((context.tenant_id, journal_id))
                if journal is None:
                    raise NotFoundError("a journal referenced by this export was not found")
                if journal.status != "posted":
                    raise ConflictError("only posted journals can be exported", code="journal_not_posted")
                if journal.debit_total != journal.credit_total:
                    raise ConflictError("a journal in this export is not balanced", code="journal_unbalanced")
                existing_export = state.exported_journal_index.get((context.tenant_id, journal_id))
                if existing_export is not None:
                    prior = state.accounting_exports.get((context.tenant_id, existing_export))
                    if prior is not None and prior.status != "failed":
                        raise ConflictError("journal was already included in another export", code="journal_already_exported")
                journals.append(journal)
            currency = journals[0].currency
            if any(journal.currency != currency for journal in journals):
                raise ValidationError("all exported journals must share one currency", field_name="journal_ids")
            manifest = canonical_hash({"journals": [{"id": j.id, "lines": [(l.account_code, str(l.debit), str(l.credit)) for l in j.lines]} for j in sorted(journals, key=lambda j: j.id)]})
            existing_id = state.export_hash_index.get((context.tenant_id, _EXPORT_DESTINATION, manifest))
            if existing_id:
                export = state.accounting_exports[(context.tenant_id, existing_id)]
                return _Outcome(export.to_api(self._export_actions(export, context)), 202, "accounting_export", export.id, export.version)

            export_id = new_id()
            export = AccountingExport(
                id=export_id,
                tenant_id=context.tenant_id,
                destination=_EXPORT_DESTINATION,
                period_start=period_start,
                period_end=period_end,
                journal_ids=journal_ids,
                account_mapping_version=str(body["account_mapping_version"])[:80],
                tax_mapping_version=str(body["tax_mapping_version"])[:80],
                export_format=body["export_format"],
                prepared_by=context.actor_id,
                currency=currency,
                debit_total=sum((j.debit_total for j in journals), Decimal("0")),
                credit_total=sum((j.credit_total for j in journals), Decimal("0")),
                entry_count=sum(len(j.lines) for j in journals),
                export_asset_id=new_id(),
                export_sha256=manifest,
                created_at=now,
                updated_at=now,
            )
            state.accounting_exports[(context.tenant_id, export_id)] = export
            state.export_hash_index[(context.tenant_id, _EXPORT_DESTINATION, manifest)] = export_id
            for journal_id in journal_ids:
                state.exported_journal_index[(context.tenant_id, journal_id)] = export_id
            return _Outcome(export.to_api(self._export_actions(export, context)), 202, "accounting_export", export_id, export.version)

        return self._run_command(context, "create_accounting_export", idempotency_key, body, correlation_id, mutate)

    def list_accounting_exports(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {FINANCE_OPERATOR, FINANCE_CHECKER})
        self._validate_finance_list_filters(state_filter, _EXPORT_STATES, period_start, period_end, limit)
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    export for (tenant, _), export in state.accounting_exports.items()
                    if tenant == context.tenant_id
                    and export.created_at <= snapshot_at
                    and (state_filter is None or export.status == state_filter)
                    and self._period_overlaps(export.period_start, export.period_end, period_start, period_end)
                    and (cursor is None or export.id > cursor)
                ),
                key=lambda export: export.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_accounting_exports", "accounting_export", None, correlation_id, "success", "internal")
            return ServiceResponse(
                {
                    "items": [export.to_api(self._export_actions(export, context)) for export in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def list_period_closes(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        """Return a read-only close-readiness projection; it never closes a period."""
        self._authorize(context, {FINANCE_OPERATOR, FINANCE_CHECKER})
        self._validate_finance_list_filters(state_filter, _PERIOD_CLOSE_STATES, period_start, period_end, limit)
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            runs = [
                run for (tenant, _), run in state.revenue_runs.items()
                if tenant == context.tenant_id and run.created_at <= snapshot_at
            ]
            exports = [
                export for (tenant, _), export in state.accounting_exports.items()
                if tenant == context.tenant_id and export.created_at <= snapshot_at
            ]
            unresolved_reconciliation_count = sum(
                1
                for (tenant, _), item in state.reconciliation_items.items()
                if tenant == context.tenant_id and item.created_at <= snapshot_at and item.status != "resolved"
            )
            periods = sorted({(run.period_start, run.period_end) for run in runs} | {(export.period_start, export.period_end) for export in exports})
            rows: list[dict[str, Any]] = []
            for item_start, item_end in periods:
                if not self._period_overlaps(item_start, item_end, period_start, period_end):
                    continue
                period_runs = [run for run in runs if (run.period_start, run.period_end) == (item_start, item_end)]
                period_exports = [export for export in exports if (export.period_start, export.period_end) == (item_start, item_end)]
                if unresolved_reconciliation_count:
                    close_state = "blocked"
                elif any(export.status == "failed" for export in period_exports) or any(run.status == "failed" for run in period_runs):
                    close_state = "blocked"
                elif period_exports and all(export.status == "acknowledged" for export in period_exports):
                    close_state = "closed"
                elif period_exports:
                    close_state = "export_pending"
                elif period_runs and all(run.status == "posted" for run in period_runs):
                    close_state = "ready_for_export"
                else:
                    close_state = "revenue_pending"
                close_key = f"{wire_date(item_start)}:{wire_date(item_end)}"
                if (state_filter is None or close_state == state_filter) and (cursor is None or close_key > cursor):
                    rows.append(
                        {
                            "close_key": close_key,
                            "period_start": wire_date(item_start),
                            "period_end": wire_date(item_end),
                            "state": close_state,
                            "revenue_run_ids": [run.id for run in sorted(period_runs, key=lambda run: run.id)],
                            "accounting_export_ids": [export.id for export in sorted(period_exports, key=lambda export: export.id)],
                            "unresolved_reconciliation_count": unresolved_reconciliation_count,
                        }
                    )
            page = rows[:limit]
            self._audit(state, context, "list_period_closes", "period_close", None, correlation_id, "success", "internal")
            return ServiceResponse(
                {
                    "items": page,
                    "next_cursor": page[-1]["close_key"] if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_accounting_export(self, context: ActorContext, export_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        export_id = require_uuid(export_id, "export_id")
        self._authorize(context, {FINANCE_OPERATOR, FINANCE_CHECKER})
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            export = self._stored_export(state, context.tenant_id, export_id)
            self._audit(state, context, "get_accounting_export", "accounting_export", export.id, correlation_id, "success", "internal")
            return ServiceResponse(export.to_api(self._export_actions(export, context)), etag=self._etag(export.version))

        return self.repository.atomic(query)

    def acknowledge_accounting_export(self, context: ActorContext, export_id: str, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        export_id = require_uuid(export_id, "export_id")
        self._authorize(context, {ACCOUNTING_ADAPTER}, require_service=True)
        self._body(body, {"event_id", "external_batch_ref", "export_sha256", "status", "accepted_journal_ids", "rejected_journal_ids", "rejection_evidence_asset_id"}, {"event_id", "external_batch_ref", "export_sha256", "status", "accepted_journal_ids", "rejected_journal_ids", "rejection_evidence_asset_id"})
        event_id = require_uuid(body["event_id"], "event_id")
        export_sha256 = str(body["export_sha256"])
        if body["status"] not in _EXPORT_ACK_STATUS:
            raise ValidationError("status is not supported", field_name="status")
        accepted_ids = [require_uuid(item, "accepted_journal_ids") for item in body["accepted_journal_ids"]]
        rejected_ids = [require_uuid(item, "rejected_journal_ids") for item in body["rejected_journal_ids"]]
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            export = self._stored_export(state, context.tenant_id, export_id)
            if export.export_sha256 != export_sha256:
                raise ConflictError("export_sha256 does not match the prepared export", code="export_hash_mismatch")
            if event_id in export.applied_ack_event_ids:
                return _Outcome(export.to_api(self._export_actions(export, context)), 202, "accounting_export", export.id, export.version)
            export.version += 1
            export.updated_at = now
            export.applied_ack_event_ids.add(event_id)
            export.accepted_journal_ids |= set(accepted_ids)
            export.rejected_journal_ids |= set(rejected_ids)
            export.acknowledgement_ref = str(body["external_batch_ref"])[:160]
            if body["status"] == "rejected":
                export.status = "failed"
            elif set(export.journal_ids) <= export.accepted_journal_ids and not export.rejected_journal_ids:
                export.status = "acknowledged"
            else:
                export.status = "partially_acknowledged"
            self._emit(
                state,
                event_type="finance.accounting_export_acknowledged",
                tenant_id=context.tenant_id,
                aggregate_type="accounting_export",
                aggregate_id=export.id,
                aggregate_version=export.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:export-ack",
                classification="internal",
                payload={"export_id": export.id, "status": body["status"], "acknowledgement_id": new_id(), "export_sha256": export.export_sha256},
                now=now,
            )
            return _Outcome(export.to_api(self._export_actions(export, context)), 202, "accounting_export", export.id, export.version)

        return self._run_command(context, "acknowledge_accounting_export", idempotency_key, {"export_id": export_id, **body}, correlation_id, mutate)

    # ======================================================================
    # UC-M14-D: audit, evidence, legal hold and control exceptions
    # ======================================================================

    def list_audit_events(self, context: ActorContext, *, cursor: str | None = None, limit: int = 50, as_of: datetime | None = None, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {INTERNAL_AUDITOR, COMPLIANCE_REVIEWER})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()
        can_see_restricted = not context.roles.isdisjoint({COMPLIANCE_REVIEWER, COMPLIANCE_SUPERVISOR})

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for item in state.audit_events
                    if item.tenant_id == context.tenant_id
                    and item.occurred_at <= snapshot_at
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[: limit]
            items = []
            for item in page:
                if item.classification == "aml_restricted" and not can_see_restricted:
                    # mask the specific target too, but the wire aggregate_id stays a non-null
                    # uuid (self-reference) rather than null, matching the AuditEvent schema.
                    items.append({**item.to_api(), "action": "restricted", "aggregate_id": item.id, "evidence_asset_ids": []})
                else:
                    items.append(item.to_api())
            self._audit(state, context, "list_audit_events", "audit_event", None, correlation_id, "success", "internal")
            return ServiceResponse({"items": items, "next_cursor": page[-1].id if len(rows) > limit else None, "as_of": wire_time(snapshot_at), "total_estimate": len(rows)})

        return self.repository.atomic(query)

    def create_evidence_export(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {INTERNAL_AUDITOR})
        self._body(body, {"scope", "subject_id", "purpose", "include_restricted_aml", "approval_record_id", "expires_at"}, {"scope", "subject_id", "purpose", "include_restricted_aml", "approval_record_id", "expires_at"})
        scope = body["scope"]
        if scope not in {"customer", "application", "contract", "publication", "commission_run", "refund"}:
            raise ValidationError("scope is not supported", field_name="scope")
        subject_id = require_uuid(body["subject_id"], "subject_id")
        if body["purpose"] not in {"internal_audit", "regulatory_exam", "complaint_investigation", "litigation"}:
            raise ValidationError("purpose is not supported", field_name="purpose")
        include_restricted = body["include_restricted_aml"]
        if not isinstance(include_restricted, bool):
            raise ValidationError("include_restricted_aml must be a boolean", field_name="include_restricted_aml")
        if include_restricted and context.roles.isdisjoint({COMPLIANCE_REVIEWER, COMPLIANCE_SUPERVISOR}):
            raise ForbiddenError("restricted evidence requires compliance clearance", code="restricted_evidence_forbidden")
        approval_record_id = require_uuid(body["approval_record_id"], "approval_record_id")
        if not self.evidence.exists(context.tenant_id, approval_record_id):
            raise ValidationError("approval_record_id does not exist", code="evidence_not_found", field_name="approval_record_id")
        expires_at = parse_time(body["expires_at"], "expires_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            manifest = self._collect_evidence_refs(state, context.tenant_id, scope, subject_id)
            manifest_sha256 = canonical_hash([(e.ref_type, e.ref_id, e.sha256) for e in manifest])
            export_id = new_id()
            export = EvidenceExport(
                id=export_id,
                tenant_id=context.tenant_id,
                scope=scope,
                subject_id=subject_id,
                purpose=body["purpose"],
                include_restricted_aml=include_restricted,
                approval_record_id=approval_record_id,
                expires_at=expires_at,
                requester_id=context.actor_id,
                manifest=manifest,
                manifest_asset_id=new_id(),
                export_asset_id=new_id(),
                manifest_sha256=manifest_sha256,
                created_at=now,
                updated_at=now,
            )
            state.evidence_exports[(context.tenant_id, export_id)] = export
            return _Outcome(export.to_api([]), 202, "evidence_export", export_id, export.version)

        return self._run_command(context, "create_evidence_export", idempotency_key, body, correlation_id, mutate)

    def get_evidence_export(self, context: ActorContext, export_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        export_id = require_uuid(export_id, "export_id")
        self._authorize(context, {INTERNAL_AUDITOR})
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            export = state.evidence_exports.get((context.tenant_id, export_id))
            if export is None:
                raise NotFoundError("evidence export was not found")
            if export.include_restricted_aml and context.roles.isdisjoint({COMPLIANCE_REVIEWER, COMPLIANCE_SUPERVISOR}):
                raise ForbiddenError("restricted evidence requires compliance clearance", code="restricted_evidence_forbidden")
            self._audit(state, context, "get_evidence_export", "evidence_export", export.id, correlation_id, "success", "aml_restricted" if export.include_restricted_aml else "internal")
            return ServiceResponse(export.to_api([]), etag=self._etag(export.version))

        try:
            return self.repository.atomic(query)
        except ForbiddenError as exc:
            self._audit_failure(context, "get_evidence_export", correlation_id, exc.code)
            raise

    def create_legal_hold(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {COMPLIANCE_SUPERVISOR})
        self._body(body, {"scope", "subject_ids", "reason_code", "authority_evidence_asset_id", "review_due_at"}, {"scope", "subject_ids", "reason_code", "authority_evidence_asset_id", "review_due_at"})
        if body["scope"] not in {"customer", "contract", "publication", "commission_run", "refund", "record_set"}:
            raise ValidationError("scope is not supported", field_name="scope")
        raw_ids = body["subject_ids"]
        if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 1000:
            raise ValidationError("subject_ids must contain 1 to 1000 items", field_name="subject_ids")
        subject_ids = [require_uuid(item, "subject_ids") for item in raw_ids]
        authority_evidence_asset_id = require_uuid(body["authority_evidence_asset_id"], "authority_evidence_asset_id")
        if not self.evidence.exists(context.tenant_id, authority_evidence_asset_id):
            raise ValidationError("authority_evidence_asset_id does not exist", code="evidence_not_found", field_name="authority_evidence_asset_id")
        review_due_at = parse_time(body["review_due_at"], "review_due_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            hold_id = new_id()
            hold = LegalHold(
                id=hold_id,
                tenant_id=context.tenant_id,
                scope=body["scope"],
                subject_ids=subject_ids,
                reason_code=str(body["reason_code"])[:80],
                authority_evidence_asset_id=authority_evidence_asset_id,
                created_by=context.actor_id,
                review_due_at=review_due_at,
                created_at=now,
                updated_at=now,
            )
            state.legal_holds[(context.tenant_id, hold_id)] = hold
            self._emit(
                state,
                event_type="audit.legal_hold_changed",
                tenant_id=context.tenant_id,
                aggregate_type="legal_hold",
                aggregate_id=hold_id,
                aggregate_version=1,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:legal-hold-created",
                classification="internal",
                payload={"legal_hold_id": hold_id, "state": "active", "scope": hold.scope},
                now=now,
            )
            return _Outcome(hold.to_api(self._legal_hold_actions(hold, context)), 201, "legal_hold", hold_id, hold.version)

        return self._run_command(context, "create_legal_hold", idempotency_key, body, correlation_id, mutate)

    def release_legal_hold(self, context: ActorContext, hold_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        hold_id = require_uuid(hold_id, "hold_id")
        self._authorize(context, {COMPLIANCE_SUPERVISOR})
        self._body(body, {"reason_code", "release_authority_evidence_id", "independent_approver_id", "effective_at"}, {"reason_code", "release_authority_evidence_id", "independent_approver_id", "effective_at"})
        release_authority_evidence_id = require_uuid(body["release_authority_evidence_id"], "release_authority_evidence_id")
        if not self.evidence.exists(context.tenant_id, release_authority_evidence_id):
            raise ValidationError("release_authority_evidence_id does not exist", code="evidence_not_found", field_name="release_authority_evidence_id")
        independent_approver_id = require_uuid(body["independent_approver_id"], "independent_approver_id")
        effective_at = parse_time(body["effective_at"], "effective_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            hold = state.legal_holds.get((context.tenant_id, hold_id))
            if hold is None:
                raise NotFoundError("legal hold was not found")
            self._cas(hold.version, expected_version)
            if context.actor_id == hold.created_by:
                raise ForbiddenError("release requires an actor independent of the hold creator", code="legal_hold_release_requires_independent_actor")
            if hold.status != "active":
                raise ConflictError("legal hold is not active", code="legal_hold_not_active")
            hold.version += 1
            hold.updated_at = now
            hold.status = "released"
            hold.released_by = context.actor_id
            hold.released_reason_code = str(body["reason_code"])[:80]
            hold.released_at = effective_at
            self._emit(
                state,
                event_type="audit.legal_hold_changed",
                tenant_id=context.tenant_id,
                aggregate_type="legal_hold",
                aggregate_id=hold_id,
                aggregate_version=hold.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:legal-hold-released",
                classification="internal",
                payload={"legal_hold_id": hold_id, "state": "released", "scope": hold.scope},
                now=now,
            )
            return _Outcome(hold.to_api(self._legal_hold_actions(hold, context)), 200, "legal_hold", hold_id, hold.version)

        return self._run_command(context, "release_legal_hold", idempotency_key, {"hold_id": hold_id, **body}, correlation_id, mutate)

    def record_control_exception(self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {INTERNAL_AUDITOR})
        self._body(body, {"control_code", "severity", "description", "evidence_asset_ids", "remediation_owner_id", "due_at"}, {"control_code", "severity", "description", "evidence_asset_ids", "remediation_owner_id", "due_at"})
        if body["severity"] not in {"low", "medium", "high", "critical"}:
            raise ValidationError("severity is not supported", field_name="severity")
        raw_ids = body["evidence_asset_ids"]
        if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 50:
            raise ValidationError("evidence_asset_ids must contain 1 to 50 items", field_name="evidence_asset_ids")
        evidence_asset_ids = [require_uuid(item, "evidence_asset_ids") for item in raw_ids]
        for asset_id in evidence_asset_ids:
            if not self.evidence.exists(context.tenant_id, asset_id):
                raise ValidationError("evidence_asset_ids contains an unknown asset", code="evidence_not_found", field_name="evidence_asset_ids")
        remediation_owner_id = require_uuid(body["remediation_owner_id"], "remediation_owner_id")
        due_at = parse_time(body["due_at"], "due_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            exception_id = new_id()
            exception = ControlException(
                id=exception_id,
                tenant_id=context.tenant_id,
                control_code=str(body["control_code"])[:80],
                severity=body["severity"],
                description=str(body["description"])[:4000],
                evidence_asset_ids=evidence_asset_ids,
                remediation_owner_id=remediation_owner_id,
                due_at=due_at,
                created_by=context.actor_id,
                created_at=now,
                updated_at=now,
            )
            state.control_exceptions[(context.tenant_id, exception_id)] = exception
            return _Outcome(exception.to_api(self._control_exception_actions(exception, context)), 201, "control_exception", exception_id, exception.version, classification="internal")

        return self._run_command(context, "record_control_exception", idempotency_key, body, correlation_id, mutate)

    def act_on_control_exception(self, context: ActorContext, exception_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        exception_id = require_uuid(exception_id, "exception_id")
        self._authorize(context, {CONTROL_REMEDIATION_OWNER, INTERNAL_AUDITOR})
        self._body(body, {"action", "reason_code", "evidence_asset_ids", "note"}, {"action", "reason_code", "evidence_asset_ids"})
        if body["action"] not in _CONTROL_ACTIONS:
            raise ValidationError("action is not supported", field_name="action")
        raw_ids = body["evidence_asset_ids"]
        if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 50:
            raise ValidationError("evidence_asset_ids must contain 1 to 50 items", field_name="evidence_asset_ids")
        evidence_asset_ids = [require_uuid(item, "evidence_asset_ids") for item in raw_ids]
        for asset_id in evidence_asset_ids:
            if not self.evidence.exists(context.tenant_id, asset_id):
                raise ValidationError("evidence_asset_ids contains an unknown asset", code="evidence_not_found", field_name="evidence_asset_ids")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            exception = state.control_exceptions.get((context.tenant_id, exception_id))
            if exception is None:
                raise NotFoundError("control exception was not found")
            self._cas(exception.version, expected_version)
            action = body["action"]
            exception.version += 1
            exception.updated_at = now
            if action == "add_remediation_evidence":
                if context.actor_id != exception.remediation_owner_id:
                    raise ForbiddenError("only the remediation owner may add remediation evidence", code="not_remediation_owner")
                if exception.status not in {"open", "remediating", "reopened"}:
                    raise ConflictError("control exception is not open for remediation", code="control_exception_not_open")
                exception.evidence_asset_ids = exception.evidence_asset_ids + evidence_asset_ids
                exception.status = "remediating"
            elif action == "request_closure":
                if context.actor_id != exception.remediation_owner_id:
                    raise ForbiddenError("only the remediation owner may request closure", code="not_remediation_owner")
                if exception.status not in {"open", "remediating"}:
                    raise ConflictError("control exception is not ready to request closure", code="control_exception_not_remediating")
                exception.status = "ready_for_review"
            elif action == "approve_closure":
                if INTERNAL_AUDITOR not in context.roles or context.actor_id == exception.remediation_owner_id:
                    raise ForbiddenError("closure requires an independent internal auditor", code="maker_checker_violation")
                if exception.status != "ready_for_review":
                    raise ConflictError("control exception is not awaiting review", code="control_exception_not_ready_for_review")
                exception.status = "closed"
                exception.closed_by = context.actor_id
                exception.closure_evidence_id = evidence_asset_ids[0]
            elif action == "reject_closure":
                if INTERNAL_AUDITOR not in context.roles:
                    raise ForbiddenError("only an internal auditor may reject closure", code="forbidden")
                if exception.status != "ready_for_review":
                    raise ConflictError("control exception is not awaiting review", code="control_exception_not_ready_for_review")
                exception.status = "remediating"
            else:  # reopen
                if INTERNAL_AUDITOR not in context.roles:
                    raise ForbiddenError("only an internal auditor may reopen a control exception", code="forbidden")
                if exception.status != "closed":
                    raise ConflictError("only a closed control exception can be reopened", code="control_exception_not_closed")
                exception.status = "reopened"
                exception.closed_by = None
                exception.closure_evidence_id = None
            return _Outcome(exception.to_api(self._control_exception_actions(exception, context)), 200, "control_exception", exception.id, exception.version, classification="internal")

        return self._run_command(context, "act_on_control_exception", idempotency_key, {"exception_id": exception_id, **body}, correlation_id, mutate)

    # ======================================================================
    # Legal-hold aware purge evaluation (used by the scheduled purge worker)
    # ======================================================================

    def evaluate_purge_candidate(self, context: ActorContext, scope: str, subject_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> dict[str, Any]:
            active_hold = next(
                (
                    hold for (tenant, _), hold in state.legal_holds.items()
                    if tenant == context.tenant_id and hold.status == "active" and hold.scope == scope and subject_id in hold.subject_ids
                ),
                None,
            )
            if active_hold:
                self._audit(state, context, "purge_skip", "legal_hold", active_hold.id, correlation_id, "denied", "internal")
                return {"action": "skip", "reason_code": "legal_hold_active", "hold_id": active_hold.id}
            self._audit(state, context, "purge_eligible", scope, subject_id, correlation_id, "success", "internal")
            return {"action": "eligible_for_delete", "reason_code": "no_active_hold"}

        return self.repository.atomic(query)

    # ======================================================================
    # Consumer-side event application (idempotent inbox)
    # ======================================================================

    def apply_application_company_decided(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "application.company_decided", self._project_company_decided)

    def apply_contract_effective(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "contract.effective", self._project_contract_effective)

    def apply_contract_terminated(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "contract.terminated", self._project_contract_terminated)

    def apply_contract_service_ended(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "contract.service_ended", self._project_contract_service_ended)

    def apply_commission_run_approved(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "commission.run_approved", self._project_commission_run_approved)

    def apply_commission_adjustment_recorded(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "commission.adjustment_recorded", self._project_commission_adjustment_recorded)

    def apply_accounting_export_acknowledged(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "finance.accounting_export_acknowledged", lambda state, tenant, payload, now: None)

    def apply_legal_hold_changed(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._apply_event(envelope, "audit.legal_hold_changed", self._project_legal_hold_changed)

    def _apply_event(self, envelope: dict[str, Any], expected_type: str, project: Callable[[StoreState, str, dict[str, Any], datetime], None]) -> dict[str, Any]:
        if envelope.get("event_type") != expected_type:
            raise ValidationError(f"envelope event_type must be {expected_type}", field_name="event_type")
        for field_name in ("event_id", "tenant_id", "payload", "aggregate_id", "sequence_no", "producer"):
            if field_name not in envelope:
                raise ValidationError(f"envelope is missing {field_name}", field_name=field_name)
        tenant_id = require_uuid(envelope["tenant_id"], "tenant_id")
        event_id = require_uuid(envelope["event_id"], "event_id")
        producer = str(envelope["producer"])
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            raise ValidationError("payload must be an object", field_name="payload")
        payload_sha256 = canonical_hash(payload)
        now = self._clock()

        aggregate_id = str(envelope["aggregate_id"])
        sequence_no = int(envelope["sequence_no"])

        def mutate(state: StoreState) -> dict[str, Any]:
            key = (tenant_id, "M14", producer, event_id)
            previous = state.inbox.get(key)
            if previous is not None:
                if previous.payload_sha256 != payload_sha256:
                    raise ConflictError("inbox payload conflict for a previously applied event", code="inbox_payload_conflict")
                if previous.status != "retry":
                    return {"status": "duplicate"}

            last_sequence = max(
                (
                    receipt.sequence_no for receipt in state.inbox.values()
                    if receipt.tenant_id == tenant_id and receipt.producer == producer and receipt.aggregate_id == aggregate_id and receipt.status == "applied"
                ),
                default=0,
            )
            if sequence_no > last_sequence + 1:
                state.inbox[key] = InboxReceipt(tenant_id, "M14", producer, event_id, expected_type, payload_sha256, aggregate_id, sequence_no, "retry", "sequence_gap", None)
                return {"status": "retry", "result_code": "sequence_gap"}
            if sequence_no <= last_sequence:
                state.inbox[key] = InboxReceipt(tenant_id, "M14", producer, event_id, expected_type, payload_sha256, aggregate_id, sequence_no, "applied", "stale_sequence_ignored", now)
                return {"status": "applied", "result_code": "stale_sequence_ignored"}

            project(state, tenant_id, payload, now)
            state.inbox[key] = InboxReceipt(tenant_id, "M14", producer, event_id, expected_type, payload_sha256, aggregate_id, sequence_no, "applied", "applied", now)
            return {"status": "applied", "result_code": "applied"}

        return self.repository.atomic(mutate)

    @staticmethod
    def _project_company_decided(state: StoreState, tenant_id: str, payload: dict[str, Any], now: datetime) -> None:
        if payload.get("decision") == "accept" and payload.get("contract_id"):
            contract_id = require_uuid(payload["contract_id"], "contract_id")
            snapshot = state.contracts.setdefault((tenant_id, contract_id), ContractSnapshot(contract_id=contract_id))
            snapshot.accepted = True

    @staticmethod
    def _project_contract_effective(state: StoreState, tenant_id: str, payload: dict[str, Any], now: datetime) -> None:
        contract_id = require_uuid(payload["contract_id"], "contract_id")
        snapshot = state.contracts.setdefault((tenant_id, contract_id), ContractSnapshot(contract_id=contract_id))
        snapshot.customer_id = require_uuid(payload["customer_id"], "customer_id")
        snapshot.effective_at = parse_time(payload["effective_at"], "effective_at")
        snapshot.service_starts_at = parse_time(payload["service_starts_at"], "service_starts_at")
        snapshot.service_ends_at = parse_time(payload["service_ends_at"], "service_ends_at")

    @staticmethod
    def _project_contract_terminated(state: StoreState, tenant_id: str, payload: dict[str, Any], now: datetime) -> None:
        contract_id = require_uuid(payload["contract_id"], "contract_id")
        snapshot = state.contracts.setdefault((tenant_id, contract_id), ContractSnapshot(contract_id=contract_id))
        snapshot.terminated_at = parse_time(payload["terminated_at"], "terminated_at")
        snapshot.refund_evaluation_required = bool(payload.get("refund_evaluation_required", False))

    @staticmethod
    def _project_contract_service_ended(state: StoreState, tenant_id: str, payload: dict[str, Any], now: datetime) -> None:
        contract_id = require_uuid(payload["contract_id"], "contract_id")
        snapshot = state.contracts.setdefault((tenant_id, contract_id), ContractSnapshot(contract_id=contract_id))
        snapshot.ended_services[str(payload["contract_service_id"])] = parse_time(payload["ended_at"], "ended_at")

    @staticmethod
    def _project_commission_run_approved(state: StoreState, tenant_id: str, payload: dict[str, Any], now: datetime) -> None:
        state.approved_commission_runs[(tenant_id, str(payload["run_id"]))] = payload

    @staticmethod
    def _project_commission_adjustment_recorded(state: StoreState, tenant_id: str, payload: dict[str, Any], now: datetime) -> None:
        state.commission_adjustment_receipts[(tenant_id, str(payload["adjustment_id"]))] = payload

    @staticmethod
    def _project_legal_hold_changed(state: StoreState, tenant_id: str, payload: dict[str, Any], now: datetime) -> None:
        # M14 both produces and consumes this event (self-loop) so a redelivered or
        # out-of-process replay stays idempotent; the originating create/release
        # command already committed the authoritative row in the same transaction.
        hold_id = require_uuid(payload["legal_hold_id"], "legal_hold_id")
        hold = state.legal_holds.get((tenant_id, hold_id))
        if hold is not None:
            hold.status = payload["state"]

    # ======================================================================
    # Internal helpers
    # ======================================================================

    def _run_command(self, context: ActorContext, operation: str, key: str, request: dict[str, Any], correlation_id: str, mutate: Callable[[StoreState], _Outcome]) -> ServiceResponse:
        if not _KEY_RE.fullmatch(key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, operation, key)

        def transaction(state: StoreState) -> tuple[_Outcome, bool]:
            receipt = state.idempotency.get(receipt_key)
            if receipt:
                if receipt.request_sha256 != fingerprint:
                    raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
                return _Outcome(receipt.response_value, receipt.response_status, "replay", None, None, error=receipt.error), True
            outcome = mutate(state)
            self._audit(state, context, operation, outcome.aggregate_type, outcome.aggregate_id, correlation_id, "failed" if outcome.error else "success", outcome.classification, outcome.error.code if outcome.error else None)
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                operation=operation,
                key=key,
                request_sha256=fingerprint,
                response_status=outcome.status,
                response_value=outcome.value,
                error=outcome.error,
                completed_at=self._clock(),
            )
            return outcome, False

        try:
            outcome, replayed = self.repository.atomic(transaction)
        except DomainError as exc:
            self._audit_failure(context, operation, correlation_id, exc.code)
            raise
        if outcome.error:
            raise outcome.error
        etag = self._etag(outcome.aggregate_version) if outcome.aggregate_version else None
        return ServiceResponse(outcome.value, outcome.status, etag, replayed)

    def _emit(
        self,
        state: StoreState,
        *,
        event_type: str,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        correlation_id: str,
        idempotency_key: str,
        classification: str,
        payload: dict[str, Any],
        now: datetime,
        causation_id: str | None = None,
    ) -> None:
        sequence = 1 + max(
            (event.sequence_no for (tenant, _), event in state.outbox.items() if tenant == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(),
            event_type=event_type,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            sequence_no=sequence,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            classification=classification,
            payload=payload,
            occurred_at=now,
            emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    def _post_journal(
        self,
        state: StoreState,
        *,
        source_type: str,
        source_id: str,
        lines: list[JournalLine],
        prepared_by: str,
        approved_by: str,
        accounting_date: date,
        currency: str,
        now: datetime,
        reverses_id: str | None = None,
    ) -> Journal:
        debit_total = sum((line.debit for line in lines), Decimal("0"))
        credit_total = sum((line.credit for line in lines), Decimal("0"))
        if debit_total != credit_total:
            raise ConflictError("journal debit and credit totals must balance", code="journal_unbalanced")
        if approved_by == prepared_by:
            raise ConflictError("journal approval requires a different actor than preparation", code="maker_checker_violation")
        tenant_id = self._tenant_for_source(state, source_type, source_id)
        if not tenant_id:
            raise ValidationError("could not resolve tenant for journal posting")
        journal_id = new_id()
        journal_number = f"JNL-{journal_id[:10].upper()}"
        journal = Journal(
            id=journal_id,
            tenant_id=tenant_id,
            journal_number=journal_number,
            source_type=source_type,
            source_id=source_id,
            currency=currency,
            accounting_date=accounting_date,
            prepared_by=prepared_by,
            lines=lines,
            approved_by=approved_by,
            posted_at=now,
            reverses_id=reverses_id,
            created_at=now,
            updated_at=now,
        )
        state.journals[(tenant_id, journal_id)] = journal
        state.journal_number_index[(tenant_id, journal_number)] = journal_id
        state.journal_source_index[(tenant_id, source_type, source_id)] = journal_id
        return journal

    @staticmethod
    def _tenant_for_source(state: StoreState, source_type: str, source_id: str) -> str | None:
        if source_type == "revenue":
            run = next((r for (_, rid), r in state.revenue_runs.items() if rid == source_id), None)
            return run.tenant_id if run else None
        if source_type == "refund":
            refund = next((r for (_, rid), r in state.refunds.items() if rid == source_id), None)
            return refund.tenant_id if refund else None
        return None

    def _invoice_recognized_total(self, state: StoreState, tenant_id: str, invoice_id: str) -> Decimal:
        total = Decimal("0")
        for (tenant, _), recognition in state.revenue_recognitions.items():
            if tenant != tenant_id:
                continue
            schedule = state.service_schedules.get((tenant_id, recognition.schedule_id))
            if schedule and schedule.invoice_id == invoice_id:
                total += recognition.untaxed_amount
        return total

    def _collect_evidence_refs(self, state: StoreState, tenant_id: str, scope: str, subject_id: str) -> list[EvidenceExportManifestEntry]:
        entries: list[EvidenceExportManifestEntry] = []
        for event in state.audit_events:
            if event.tenant_id == tenant_id and event.aggregate_id == subject_id:
                entries.append(EvidenceExportManifestEntry("audit_event", event.id, canonical_hash({"action": event.action, "outcome": event.outcome, "occurred_at": wire_time(event.occurred_at)}), event.occurred_at))
        if scope == "refund":
            refund = state.refunds.get((tenant_id, subject_id))
            if refund:
                entries.append(EvidenceExportManifestEntry("refund", refund.id, canonical_hash({"status": refund.status, "amount": str(refund.requested_amount)}), refund.updated_at))
        elif scope == "customer":
            for (tenant, _), payment in state.payments.items():
                if tenant == tenant_id and payment.customer_id == subject_id:
                    entries.append(EvidenceExportManifestEntry("payment", payment.id, canonical_hash({"status": payment.status, "amount": str(payment.amount)}), payment.updated_at))
            for (tenant, _), refund in state.refunds.items():
                if tenant == tenant_id and refund.customer_id == subject_id:
                    entries.append(EvidenceExportManifestEntry("refund", refund.id, canonical_hash({"status": refund.status}), refund.updated_at))
        entries.sort(key=lambda entry: (entry.ref_type, entry.ref_id))
        return entries

    # ---- authorization and validation --------------------------------------

    def _authorize(self, context: ActorContext, roles: set[str], *, require_service: bool = False) -> None:
        if require_service and context.principal_type != "service":
            raise ForbiddenError("this operation requires a service identity", code="service_identity_required")
        if context.roles.isdisjoint(roles):
            raise ForbiddenError("actor role is not permitted for this operation")

    @staticmethod
    def _validate_finance_list_filters(
        state_filter: str | None,
        allowed_states: set[str],
        period_start: date | None,
        period_end: date | None,
        limit: int,
    ) -> None:
        if state_filter is not None and state_filter not in allowed_states:
            raise ValidationError("state is not supported for this resource", field_name="state")
        if period_start is not None and period_end is not None and period_end <= period_start:
            raise ValidationError("period_end must be after period_start", field_name="period_end")
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")

    @staticmethod
    def _period_overlaps(
        item_start: date,
        item_end: date,
        period_start: date | None,
        period_end: date | None,
    ) -> bool:
        return (period_start is None or item_end > period_start) and (period_end is None or item_start < period_end)

    @staticmethod
    def _body(body: Any, allowed: set[str], required: set[str], min_properties: int = 0) -> None:
        if not isinstance(body, dict):
            raise ValidationError("request body must be an object")
        extra = set(body) - allowed
        missing = required - set(body)
        if extra:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(extra))}", field_name=sorted(extra)[0])
        if missing:
            raise ValidationError(f"missing fields: {', '.join(sorted(missing))}", field_name=sorted(missing)[0])
        if len(body) < min_properties:
            raise ValidationError("request body has too few properties")

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _etag(version: int) -> str:
        return f'"{version}"'

    @staticmethod
    def _placeholder_customer_id(tenant_id: str, application_id: str) -> str:
        from uuid import NAMESPACE_URL, uuid5

        return str(uuid5(NAMESPACE_URL, f"longhao:{tenant_id}:application-customer:{application_id}"))

    @staticmethod
    def _placeholder_application_id(payment_id: str) -> str:
        from uuid import NAMESPACE_URL, uuid5

        return str(uuid5(NAMESPACE_URL, f"longhao:unlinked-application:{payment_id}"))

    @staticmethod
    def _callback_receipt_api(receipt: dict[str, Any]) -> dict[str, Any]:
        return {
            "receipt_id": receipt["receipt_id"],
            "event_id": receipt["event_id"],
            "status": receipt["status"],
            "received_at": wire_time(receipt["received_at"]),
            "correlation_id": receipt["correlation_id"],
        }

    def _invoice_outcome(self, invoice: Invoice, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(invoice.to_api(self._invoice_actions(invoice, context)), status, "invoice", invoice.id, invoice.version)

    @staticmethod
    def _invoice_actions(invoice: Invoice, context: ActorContext) -> list[str]:
        if invoice.status == "issued" and FINANCE_OPERATOR in context.roles:
            return ["create_payment_intent"]
        return []

    @staticmethod
    def _reconciliation_actions(item: ReconciliationItem, context: ActorContext) -> list[str]:
        if item.status in {"open", "investigating"} and FINANCE_CHECKER in context.roles:
            return ["resolve_reconciliation_item"]
        return []

    @staticmethod
    def _revenue_run_actions(run: RevenueRun, context: ActorContext) -> list[str]:
        if not run.dry_run and run.status in {"calculated", "review_pending"} and FINANCE_CHECKER in context.roles and context.actor_id != run.prepared_by:
            return ["approve_revenue_run"]
        return []

    @staticmethod
    def _refund_actions(refund: Refund, context: ActorContext) -> list[str]:
        actions: list[str] = []
        if refund.status == "approval_pending" and FINANCE_CHECKER in context.roles and context.actor_id != refund.prepared_by:
            actions.append("approve_refund")
        if refund.status in {"approved", "submitted", "reconciliation_required"} and REFUND_SERVICE in context.roles and context.principal_type == "service":
            actions.append("execute_refund")
        return actions

    @staticmethod
    def _export_actions(export: AccountingExport, context: ActorContext) -> list[str]:
        return []

    @staticmethod
    def _legal_hold_actions(hold: LegalHold, context: ActorContext) -> list[str]:
        if hold.status == "active" and COMPLIANCE_SUPERVISOR in context.roles and context.actor_id != hold.created_by:
            return ["release_legal_hold"]
        return []

    @staticmethod
    def _control_exception_actions(exception: ControlException, context: ActorContext) -> list[str]:
        actions: list[str] = []
        if exception.status in {"open", "remediating", "reopened"} and context.actor_id == exception.remediation_owner_id:
            actions.append("add_remediation_evidence")
        if exception.status in {"open", "remediating"} and context.actor_id == exception.remediation_owner_id:
            actions.append("request_closure")
        if exception.status == "ready_for_review" and INTERNAL_AUDITOR in context.roles and context.actor_id != exception.remediation_owner_id:
            actions.extend(["approve_closure", "reject_closure"])
        if exception.status == "closed" and INTERNAL_AUDITOR in context.roles:
            actions.append("reopen")
        return actions

    def _audit(self, state: StoreState, context: ActorContext, action: str, aggregate_type: str, aggregate_id: str | None, correlation_id: str, outcome: str, classification: str, reason_code: str | None = None) -> None:
        event_id = new_id()
        state.audit_events.append(
            ModuleAuditEvent(
                id=event_id,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                actor_type=context.principal_type,
                action=f"m14.{action}",
                aggregate_type=aggregate_type,
                # the wire AuditEvent.aggregate_id is a required UUID; list-level and
                # security-denial audits have no single target aggregate, so they
                # self-reference the audit event's own id rather than emitting null.
                aggregate_id=aggregate_id if aggregate_id is not None else event_id,
                occurred_at=self._clock(),
                correlation_id=correlation_id,
                outcome=outcome,
                classification=classification,
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security", None, correlation, "denied", "internal", reason_code)

        self.repository.atomic(record)

    @staticmethod
    def _stored_reconciliation(state: StoreState, tenant_id: str, item_id: str) -> ReconciliationItem:
        item = state.reconciliation_items.get((tenant_id, item_id))
        if item is None:
            raise NotFoundError("reconciliation item was not found")
        return item

    @staticmethod
    def _stored_revenue_run(state: StoreState, tenant_id: str, run_id: str) -> RevenueRun:
        run = state.revenue_runs.get((tenant_id, run_id))
        if run is None:
            raise NotFoundError("revenue run was not found")
        return run

    @staticmethod
    def _stored_refund(state: StoreState, tenant_id: str, refund_id: str) -> Refund:
        refund = state.refunds.get((tenant_id, refund_id))
        if refund is None:
            raise NotFoundError("refund was not found")
        return refund

    @staticmethod
    def _stored_export(state: StoreState, tenant_id: str, export_id: str) -> AccountingExport:
        export = state.accounting_exports.get((tenant_id, export_id))
        if export is None:
            raise NotFoundError("accounting export was not found")
        return export
