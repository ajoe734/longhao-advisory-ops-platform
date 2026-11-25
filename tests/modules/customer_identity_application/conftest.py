from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.customer_identity_application.domain import ActorContext
from modules.customer_identity_application.ports import (
    FakeCustomerSources,
    FakeEvidenceStore,
    FakeIdentityProvider,
    VerifiedIdentity,
)
from modules.customer_identity_application.repository import InMemoryRepository
from modules.customer_identity_application.service import CustomerIdentityApplicationService


TENANT = "10000000-0000-0000-0000-000000000001"
OPERATOR = "20000000-0000-0000-0000-000000000001"
SUPERVISOR = "20000000-0000-0000-0000-000000000002"
CUSTOMER_ACTOR_A = "20000000-0000-0000-0000-000000000003"
CUSTOMER_ACTOR_B = "20000000-0000-0000-0000-000000000004"
PROOF_A = "30000000-0000-0000-0000-000000000001"
PROOF_B = "30000000-0000-0000-0000-000000000002"
PROOF_C = "30000000-0000-0000-0000-000000000003"
PROOF_FORGED = "30000000-0000-0000-0000-000000000004"
PROOF_TIMEOUT = "30000000-0000-0000-0000-000000000005"
CONSENT_A = "40000000-0000-0000-0000-000000000001"
CONSENT_B = "40000000-0000-0000-0000-000000000002"
MERGE_EVIDENCE = "40000000-0000-0000-0000-000000000003"
REVIEW_EVIDENCE = "40000000-0000-0000-0000-000000000004"
PACKAGE = "50000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m01():
    repository = InMemoryRepository()
    provider = FakeIdentityProvider({"https://legendflow.example.invalid"}, "longhao-ops")
    expires = NOW + timedelta(hours=1)
    provider.register(PROOF_A, VerifiedIdentity("legendflow", "subject-a", "https://legendflow.example.invalid", "longhao-ops", NOW, expires, "aal2", "合成客戶甲", "same@example.invalid", "+886900000001"))
    provider.register(PROOF_B, VerifiedIdentity("legendflow", "subject-b", "https://legendflow.example.invalid", "longhao-ops", NOW, expires, "aal2", "合成客戶乙", "same@example.invalid", "+886900000002"))
    provider.register(PROOF_C, VerifiedIdentity("legendflow", "subject-c", "https://legendflow.example.invalid", "longhao-ops", NOW, expires, "aal2", "合成客戶丙", "third@example.invalid", "+886900000003"))
    provider.fail(PROOF_FORGED, "forged")
    provider.fail(PROOF_TIMEOUT, "timeout")
    evidence = FakeEvidenceStore()
    for asset in (PROOF_A, PROOF_B, PROOF_C, MERGE_EVIDENCE, REVIEW_EVIDENCE):
        evidence.add(TENANT, asset)
    sources = FakeCustomerSources()
    clock = FixedClock()
    service = CustomerIdentityApplicationService(
        repository,
        provider,
        evidence,
        sources,
        hmac_key=b"m01-synthetic-hmac-key-32-bytes-minimum-only",
        clock=clock,
    )
    return {
        "service": service,
        "repository": repository,
        "provider": provider,
        "evidence": evidence,
        "sources": sources,
        "clock": clock,
    }


@pytest.fixture
def operator() -> ActorContext:
    return ActorContext(TENANT, OPERATOR, frozenset({"cms_operator"}), principal_type="staff")


@pytest.fixture
def supervisor() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"cms_supervisor"}), principal_type="staff")


def customer_context(customer_id: str, actor_id: str = CUSTOMER_ACTOR_A) -> ActorContext:
    return ActorContext(TENANT, actor_id, frozenset(), principal_type="customer", customer_id=customer_id, purpose="self_service")


def create_customer(service, context, proof_ref: str, key: str, source: str = "campaign:first"):
    return service.create_customer(
        context,
        {"external_system": "legendflow", "verified_identity_proof_ref": proof_ref, "marketing_source_ref": source},
        idempotency_key=key,
    )


def application_body(customer_id: str, touch: str = "touch:first"):
    return {
        "customer_id": customer_id,
        "package_version_id": PACKAGE,
        "service_selections": [{
            "service_code": "annual_advisory",
            "offer_version": "synthetic-v1",
            "quantity": 1,
            "billing_interval": "year",
            "quoted_price": {"currency": "TWD", "amount": "1000.000000"},
            "tax_mapping_version": "synthetic-tax-v1",
        }],
        "acquisition_touch_ref": touch,
        "intent": "new",
    }
