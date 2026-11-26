from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.commissions.domain import ActorContext
from modules.commissions.ports import (
    CohortFact,
    ConversionFact,
    FakeEvidenceStore,
    FakeOwnershipCohort,
    FakeRevenueBasis,
    FakeSalesConversion,
    RecognitionFact,
    RefundAllocationFact,
)
from modules.commissions.repository import InMemoryRepository
from modules.commissions.service import CommissionService
from workers.tasks.commissions.tasks import CommissionJobRunner


TENANT = "10000000-0000-0000-0000-000000000001"
OPERATOR = "20000000-0000-0000-0000-000000000001"
APPROVER = "20000000-0000-0000-0000-000000000002"
PAYROLL_OPERATOR = "20000000-0000-0000-0000-000000000003"
PAYROLL_ADAPTER = "20000000-0000-0000-0000-000000000004"
SALES_A = "20000000-0000-0000-0000-000000000005"
SALES_B = "20000000-0000-0000-0000-000000000006"
MARKETING_ATTRIBUTION = "30000000-0000-0000-0000-000000000001"
EVIDENCE_PARAMETER = "40000000-0000-0000-0000-000000000001"
EVIDENCE_ADJUSTMENT = "40000000-0000-0000-0000-000000000002"
EVIDENCE_ACK = "40000000-0000-0000-0000-000000000003"
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)
RECOGNITION_DEFAULT_OCCURRED_AT = datetime(2026, 1, 15, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m12():
    repository = InMemoryRepository()
    revenue = FakeRevenueBasis()
    conversions = FakeSalesConversion()
    ownership = FakeOwnershipCohort()
    evidence = FakeEvidenceStore()
    for asset in (EVIDENCE_PARAMETER, EVIDENCE_ADJUSTMENT, EVIDENCE_ACK):
        evidence.add(TENANT, asset)
    clock = FixedClock()
    service = CommissionService(repository, revenue, conversions, ownership, evidence, clock=clock)
    return {
        "service": service,
        "repository": repository,
        "revenue": revenue,
        "conversions": conversions,
        "ownership": ownership,
        "evidence": evidence,
        "clock": clock,
    }


@pytest.fixture
def operator() -> ActorContext:
    return ActorContext(TENANT, OPERATOR, frozenset({"commission_operator"}))


@pytest.fixture
def approver() -> ActorContext:
    return ActorContext(TENANT, APPROVER, frozenset({"commission_approver"}))


@pytest.fixture
def payroll_operator() -> ActorContext:
    return ActorContext(TENANT, PAYROLL_OPERATOR, frozenset({"payroll_operator"}))


@pytest.fixture
def payroll_adapter() -> ActorContext:
    return ActorContext(TENANT, PAYROLL_ADAPTER, frozenset({"payroll_adapter"}), principal_type="service", actor_type="service")


def sales_context(actor_id: str = SALES_A) -> ActorContext:
    return ActorContext(TENANT, actor_id, frozenset({"sales"}))


def policy_body(
    *,
    policy_code: str = "standard-2026",
    base_pool_rate: str | None = "0.10",
    allocation_interpretation: str = "marketing_decay_only",
    age_anchor: str = "first_paid_effective_start",
    service_lineage_rule: str = "sticky_assisted_share",
    year3_assisted_marketing_rule: str = "zero_unless_continuous_auto",
    effective_from: str = "2026-01-01T00:00:00.000000Z",
) -> dict:
    return {
        "policy_code": policy_code,
        "effective_from": effective_from,
        "base_pool_rate": base_pool_rate,
        "marketing_year_1": "1.0",
        "marketing_year_2": "0.5",
        "marketing_year_3_auto": "0.25",
        "assisted_marketing_share": "0.3",
        "assisted_sales_share": "0.7",
        "allocation_interpretation": allocation_interpretation,
        "age_anchor": age_anchor,
        "service_lineage_rule": service_lineage_rule,
        "year3_assisted_marketing_rule": year3_assisted_marketing_rule,
        "pool_policy_version": "pool-v1",
        "recognition_policy_version": "recognition-v1",
        "rounding_mode": "ROUND_HALF_UP",
        "rounding_scale": 2,
    }


def create_policy(service, context, key: str, **overrides) -> dict:
    return service.create_commission_policy(context, policy_body(**overrides), idempotency_key=key).data


def approve_policy(service, context, policy: dict, key: str, *, approved_base_pool_rate: str = "0.10", reason_code: str = "finance_approved") -> dict:
    return service.approve_commission_policy(
        context,
        policy["id"],
        {
            "decision": "approve",
            "policy_sha256": policy["policy_sha256"],
            "approved_base_pool_rate": approved_base_pool_rate,
            "parameter_decision_evidence_asset_id": EVIDENCE_PARAMETER,
            "effective_from": policy["effective_from"],
            "reason_code": reason_code,
        },
        expected_version=policy["version"],
        idempotency_key=key,
    ).data


def seed_recognition(
    fixtures,
    *,
    recognition_id: str,
    contract_id: str,
    customer_id: str,
    amount: str,
    occurred_at: datetime = RECOGNITION_DEFAULT_OCCURRED_AT,
    currency: str = "TWD",
) -> None:
    fixtures["revenue"].add_recognition(
        TENANT,
        RecognitionFact(recognition_id, contract_id, customer_id, occurred_at, Decimal(amount), currency),
    )


def seed_cohort(
    fixtures,
    *,
    customer_id: str,
    customer_year: int,
    continuous_auto_renew: bool = True,
    current_owner_id: str | None = None,
    marketing_attribution_id: str | None = MARKETING_ATTRIBUTION,
) -> None:
    fixtures["ownership"].set(
        TENANT,
        CohortFact(customer_id, customer_year, continuous_auto_renew, current_owner_id, marketing_attribution_id),
    )


def seed_conversion(fixtures, *, recognition_id: str, conversion_id: str, salesperson_id: str, approved: bool = True) -> None:
    fixtures["conversions"].set(TENANT, recognition_id, ConversionFact(conversion_id, salesperson_id, approved))


def seed_refund(fixtures, *, refund_id: str, original_entry_id: str, recognition_id: str, amount: str) -> None:
    fixtures["revenue"].add_refund(
        TENANT, RefundAllocationFact(refund_id, original_entry_id, recognition_id, Decimal(amount), "TWD")
    )


def run_jobs(fixtures, worker_id: str = "m12-test-worker") -> None:
    runner = CommissionJobRunner(fixtures["repository"], fixtures["service"])
    for _ in range(5):
        result = runner.run_once(worker_id)
        if result.processed == 0 and result.retried == 0:
            break
