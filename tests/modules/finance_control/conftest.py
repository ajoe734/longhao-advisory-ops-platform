from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.finance_control.domain import ActorContext, Invoice, InvoiceLine, Payment, RevenueRecognition, utc_now
from modules.finance_control.ports import (
    FakeEvidenceAssets,
    FakeRefundProvider,
    FakeSettlementStatements,
    FakeTaxMappings,
)
from modules.finance_control.repository import InMemoryRepository
from modules.finance_control.service import FinanceControlService


TENANT = "10000000-0000-0000-0000-000000000001"
OTHER_TENANT = "10000000-0000-0000-0000-000000000099"
OPERATOR = "20000000-0000-0000-0000-000000000001"
CHECKER = "20000000-0000-0000-0000-000000000002"
OTHER_CHECKER = "20000000-0000-0000-0000-000000000003"
EXECUTOR = "20000000-0000-0000-0000-000000000004"
PAYMENT_ADAPTER_ACTOR = "20000000-0000-0000-0000-000000000005"
ACCOUNTING_ADAPTER_ACTOR = "20000000-0000-0000-0000-000000000006"
AUDITOR = "20000000-0000-0000-0000-000000000007"
COMPLIANCE_SUPERVISOR_A = "20000000-0000-0000-0000-000000000008"
COMPLIANCE_SUPERVISOR_B = "20000000-0000-0000-0000-000000000009"
REMEDIATION_OWNER = "20000000-0000-0000-0000-000000000010"
SERVICE_AGENT_ACTOR = "20000000-0000-0000-0000-000000000011"

TAX_CODE = "TW_VAT_5"
TAX_MAPPING_VERSION = "synthetic-tax-v1"
RECOGNITION_POLICY_VERSION = "service_period_straight_line_v1"
NOW = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def m14(clock):
    repository = InMemoryRepository()
    refund_provider = FakeRefundProvider()
    statements = FakeSettlementStatements()
    evidence = FakeEvidenceAssets()
    tax_mappings = FakeTaxMappings()
    tax_mappings.approve(TENANT, TAX_CODE, TAX_MAPPING_VERSION)
    service = FinanceControlService(repository, refund_provider, statements, evidence, tax_mappings, clock=clock)
    return {
        "service": service,
        "repository": repository,
        "refund_provider": refund_provider,
        "statements": statements,
        "evidence": evidence,
        "tax_mappings": tax_mappings,
        "clock": clock,
    }


@pytest.fixture
def operator() -> ActorContext:
    return ActorContext(TENANT, OPERATOR, frozenset({"finance_operator"}))


@pytest.fixture
def checker() -> ActorContext:
    return ActorContext(TENANT, CHECKER, frozenset({"finance_checker"}))


@pytest.fixture
def other_checker() -> ActorContext:
    return ActorContext(TENANT, OTHER_CHECKER, frozenset({"finance_checker"}))


@pytest.fixture
def executor() -> ActorContext:
    return ActorContext(TENANT, EXECUTOR, frozenset({"refund_service"}), principal_type="service")


@pytest.fixture
def payment_adapter_ctx() -> ActorContext:
    return ActorContext(TENANT, PAYMENT_ADAPTER_ACTOR, frozenset({"payment_adapter"}), principal_type="service")


@pytest.fixture
def accounting_adapter_ctx() -> ActorContext:
    return ActorContext(TENANT, ACCOUNTING_ADAPTER_ACTOR, frozenset({"accounting_adapter"}), principal_type="service")


@pytest.fixture
def auditor() -> ActorContext:
    return ActorContext(TENANT, AUDITOR, frozenset({"internal_auditor"}))


@pytest.fixture
def compliance_supervisor_a() -> ActorContext:
    return ActorContext(TENANT, COMPLIANCE_SUPERVISOR_A, frozenset({"compliance_supervisor"}))


@pytest.fixture
def compliance_supervisor_b() -> ActorContext:
    return ActorContext(TENANT, COMPLIANCE_SUPERVISOR_B, frozenset({"compliance_supervisor"}))


@pytest.fixture
def remediation_owner() -> ActorContext:
    return ActorContext(TENANT, REMEDIATION_OWNER, frozenset({"control_remediation_owner"}))


@pytest.fixture
def service_agent() -> ActorContext:
    return ActorContext(TENANT, SERVICE_AGENT_ACTOR, frozenset({"service_agent"}))


def money(amount: str, currency: str = "TWD") -> dict:
    return {"amount": amount, "currency": currency}


def invoice_body(
    *,
    customer_id: str,
    contract_id: str,
    contract_service_id: str,
    untaxed: str = "1000",
    tax: str = "50",
    period_start: str = "2026-01-01",
    period_end: str = "2026-02-01",
    invoice_date: str = "2026-01-01",
    due_date: str = "2026-01-15",
) -> dict:
    return {
        "customer_id": customer_id,
        "contract_id": contract_id,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "invoice_type": "service",
        "lines": [
            {
                "contract_service_id": contract_service_id,
                "service_code": "advisory_basic",
                "service_period_start": period_start,
                "service_period_end": period_end,
                "quantity": 1,
                "untaxed_service_amount": money(untaxed),
                "tax_amount": money(tax),
                "tax_code": TAX_CODE,
                "tax_mapping_version": TAX_MAPPING_VERSION,
            }
        ],
    }


def seed_paid_payment(repository: InMemoryRepository, *, tenant: str, payment_id: str, customer_id: str, amount: Decimal, currency: str = "TWD", invoice_id: str | None = None, provider: str = "synthetic_payment_provider", provider_transaction_id: str = "ptx-seed", application_id: str | None = None) -> None:
    def seed(state):
        linked_invoice_id = invoice_id or payment_id
        if invoice_id is None:
            invoice_line = InvoiceLine(
                id=payment_id,
                line_no=1,
                contract_service_id=payment_id,
                service_code="synthetic_paid_service",
                service_period_start=utc_now().date(),
                service_period_end=(utc_now() + timedelta(days=30)).date(),
                quantity=1,
                untaxed_amount=amount,
                tax_amount=Decimal("0"),
                tax_code="synthetic",
                tax_mapping_version="synthetic-test-v1",
            )
            state.invoices[(tenant, linked_invoice_id)] = Invoice(
                id=linked_invoice_id,
                tenant_id=tenant,
                customer_id=customer_id,
                contract_id=payment_id,
                invoice_number=f"TEST-INV-{payment_id[:8]}",
                invoice_date=utc_now().date(),
                due_date=(utc_now() + timedelta(days=30)).date(),
                invoice_type="service",
                currency=currency,
                lines=[invoice_line],
            )
        state.payments[(tenant, payment_id)] = Payment(
            id=payment_id,
            tenant_id=tenant,
            customer_id=customer_id,
            application_id=application_id,
            invoice_id=linked_invoice_id,
            amount=amount,
            currency=currency,
            method="card",
            return_route="application_progress",
            provider=provider,
            provider_transaction_id=provider_transaction_id,
            checkout_url="https://payments.example.invalid/checkout/seed",
            expires_at=utc_now() + timedelta(minutes=30),
            status="paid",
        )
        state.payment_provider_index[(tenant, provider, provider_transaction_id)] = payment_id

    repository.atomic(seed)


def seed_recognition(repository: InMemoryRepository, *, tenant: str, recognition_id: str, untaxed_amount: Decimal, currency: str = "TWD", commission_eligible: bool = True) -> None:
    def seed(state):
        state.revenue_recognitions[(tenant, recognition_id)] = RevenueRecognition(
            id=recognition_id,
            tenant_id=tenant,
            run_id="00000000-0000-0000-0000-0000000000f1",
            schedule_id="00000000-0000-0000-0000-0000000000f2",
            recognition_key="00000000-0000-0000-0000-0000000000f2",
            recognized_at=utc_now(),
            untaxed_amount=untaxed_amount,
            currency=currency,
            confirmed_by="00000000-0000-0000-0000-0000000000f3",
            policy_version=RECOGNITION_POLICY_VERSION,
            commission_eligible=commission_eligible,
        )

    repository.atomic(seed)
