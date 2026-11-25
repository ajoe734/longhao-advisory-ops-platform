from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.aml_cft.domain import ActorContext
from modules.aml_cft.ports import FakeApplicationAccess, FakeEvidenceStore, FakeScreeningProvider
from modules.aml_cft.repository import InMemoryRepository
from modules.aml_cft.service import AmlCftService


TENANT = "10000000-0000-0000-0000-000000000001"
OTHER_TENANT = "10000000-0000-0000-0000-000000000099"
OFFICER = "20000000-0000-0000-0000-000000000001"
OFFICER_B = "20000000-0000-0000-0000-000000000002"
SUPERVISOR = "20000000-0000-0000-0000-000000000003"
COMPANY_ACCEPTOR = "20000000-0000-0000-0000-000000000004"
AUDITOR = "20000000-0000-0000-0000-000000000005"
ADAPTER_ACTOR = "20000000-0000-0000-0000-000000000006"
AI_SUMMARY_ACTOR = "20000000-0000-0000-0000-000000000007"
CUSTOMER_ACTOR = "20000000-0000-0000-0000-000000000008"
SALES_ACTOR = "20000000-0000-0000-0000-000000000009"
APPLICATION_A = "30000000-0000-0000-0000-000000000001"
APPLICATION_B = "30000000-0000-0000-0000-000000000002"
CUSTOMER_A = "40000000-0000-0000-0000-000000000001"
CUSTOMER_B = "40000000-0000-0000-0000-000000000002"
SUBMISSION_A = "50000000-0000-0000-0000-000000000001"
EVIDENCE_1 = "60000000-0000-0000-0000-000000000001"
EVIDENCE_2 = "60000000-0000-0000-0000-000000000002"
EVIDENCE_3 = "60000000-0000-0000-0000-000000000003"
NOW = datetime(2026, 1, 15, 1, 2, 3, tzinfo=timezone.utc)  # safely in the past of any real wall-clock test run


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def m04():
    repository = InMemoryRepository()
    provider = FakeScreeningProvider()
    evidence = FakeEvidenceStore()
    for asset in (EVIDENCE_1, EVIDENCE_2, EVIDENCE_3):
        evidence.add(TENANT, asset)
    application_access = FakeApplicationAccess()
    application_access.register(TENANT, APPLICATION_A, CUSTOMER_A)
    application_access.register(TENANT, APPLICATION_B, CUSTOMER_B)
    clock = FixedClock()
    service = AmlCftService(repository, provider, evidence, application_access, clock=clock)
    return {
        "service": service,
        "repository": repository,
        "provider": provider,
        "evidence": evidence,
        "application_access": application_access,
        "clock": clock,
    }


@pytest.fixture
def officer() -> ActorContext:
    return ActorContext(TENANT, OFFICER, frozenset({"aml_officer"}), principal_type="staff")


@pytest.fixture
def officer_b() -> ActorContext:
    return ActorContext(TENANT, OFFICER_B, frozenset({"aml_officer"}), principal_type="staff")


@pytest.fixture
def supervisor() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"aml_supervisor"}), principal_type="staff")


@pytest.fixture
def company_acceptor() -> ActorContext:
    return ActorContext(TENANT, COMPANY_ACCEPTOR, frozenset({"company_acceptor"}), principal_type="staff")


@pytest.fixture
def adapter() -> ActorContext:
    return ActorContext(TENANT, ADAPTER_ACTOR, frozenset({"aml_adapter"}), principal_type="service")


@pytest.fixture
def sales() -> ActorContext:
    return ActorContext(TENANT, SALES_ACTOR, frozenset({"sales"}), principal_type="staff")


def customer_context(customer_id: str) -> ActorContext:
    return ActorContext(TENANT, CUSTOMER_ACTOR, frozenset(), principal_type="customer", customer_id=customer_id, purpose="self_service")


def screening_request(application_id: str = APPLICATION_A, customer_id: str = CUSTOMER_A, scope: list | None = None) -> dict:
    return {
        "application_id": application_id,
        "customer_id": customer_id,
        "kyc_submission_id": SUBMISSION_A,
        "screening_policy_version": "policy-v1",
        "scope": scope or ["sanctions"],
        "restricted_identity_asset_id": EVIDENCE_1,
        "reason_code": "application_intake",
    }


def webhook_headers(*, moment: datetime = NOW, nonce: str = "a" * 32, integration_key_id: str = "integration-adapter-001") -> dict:
    return {
        "webhook_timestamp": str(int(moment.timestamp())),
        "webhook_nonce": nonce,
        "integration_key_id": integration_key_id,
    }


def screening_callback(screening_id: str, result: str, *, event_id: str, list_version: str = "list-v1") -> dict:
    return {
        "event_id": event_id,
        "screening_id": screening_id,
        "provider_ref": "synthetic-watchlist:sanctions",
        "list_version": list_version,
        "screened_at": "2026-11-26T01:02:03.000000Z",
        "result": result,
        "restricted_result_asset_id": EVIDENCE_2,
        "result_sha256": "a" * 64,
    }


def declaration_body(document_ids: list | None = None) -> dict:
    return {
        "form_version": "aml-decl-v1",
        "declaration_consent_id": EVIDENCE_1,
        "restricted_answer_asset_id": EVIDENCE_1,
        "restricted_document_ids": document_ids if document_ids is not None else [EVIDENCE_2],
        "customer_signature_id": EVIDENCE_3,
    }
