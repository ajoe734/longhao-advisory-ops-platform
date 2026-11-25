from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.kyc_suitability.domain import ActorContext
from modules.kyc_suitability.ports import (
    FakeApplicationGateway,
    FakeCustomerGateway,
    FakeDocumentScanner,
    FakeExternalVerification,
)
from modules.kyc_suitability.repository import InMemoryRepository
from modules.kyc_suitability.service import KycSuitabilityService


TENANT = "10000000-0000-0000-0000-000000000001"
KYC_REVIEWER = "20000000-0000-0000-0000-000000000001"
SUITABILITY_REVIEWER = "20000000-0000-0000-0000-000000000002"
SUITABILITY_AUTHORIZER = "20000000-0000-0000-0000-000000000003"
SALES = "20000000-0000-0000-0000-000000000004"
CUSTOMER_ACTOR = "20000000-0000-0000-0000-000000000005"
APPLICATION_A = "30000000-0000-0000-0000-000000000001"
APPLICATION_B = "30000000-0000-0000-0000-000000000002"
CUSTOMER_A = "40000000-0000-0000-0000-000000000001"
CUSTOMER_B = "40000000-0000-0000-0000-000000000002"
SIGNATURE_A = "50000000-0000-0000-0000-000000000001"
SIGNATURE_B = "50000000-0000-0000-0000-000000000002"
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m03():
    repository = InMemoryRepository()
    gateway = FakeApplicationGateway()
    gateway.register(TENANT, APPLICATION_A, CUSTOMER_A, SIGNATURE_A, NOW)
    gateway.register(TENANT, APPLICATION_B, CUSTOMER_B, SIGNATURE_B, NOW)
    customers = FakeCustomerGateway()
    customers.register(TENANT, CUSTOMER_A)
    customers.register(TENANT, CUSTOMER_B)
    scanner = FakeDocumentScanner()
    verifier = FakeExternalVerification()
    clock = FixedClock()
    service = KycSuitabilityService(repository, gateway, customers, clock=clock)
    return {
        "service": service,
        "repository": repository,
        "gateway": gateway,
        "customers": customers,
        "scanner": scanner,
        "verifier": verifier,
        "clock": clock,
    }


@pytest.fixture
def kyc_reviewer() -> ActorContext:
    return ActorContext(TENANT, KYC_REVIEWER, frozenset({"kyc_reviewer"}), principal_type="staff")


@pytest.fixture
def suitability_reviewer() -> ActorContext:
    return ActorContext(TENANT, SUITABILITY_REVIEWER, frozenset({"suitability_reviewer"}), principal_type="staff")


@pytest.fixture
def suitability_authorizer() -> ActorContext:
    return ActorContext(
        TENANT, SUITABILITY_AUTHORIZER, frozenset({"suitability_reviewer", "suitability_authorizer"}), principal_type="staff"
    )


@pytest.fixture
def sales() -> ActorContext:
    return ActorContext(TENANT, SALES, frozenset({"sales"}), principal_type="staff")


def customer_context(customer_id: str, actor_id: str = CUSTOMER_ACTOR) -> ActorContext:
    return ActorContext(TENANT, actor_id, frozenset(), principal_type="customer", customer_id=customer_id, purpose="self_service")


def create_submission(service, context, application_id: str, key: str, signature_id: str = SIGNATURE_A, expected_version: int = 1):
    return service.create_kyc_submission(
        context, application_id, {"form_version": "m03-kyc-form-v1", "customer_signature_id": signature_id},
        expected_version=expected_version, idempotency_key=key,
    )


def complete_answers() -> list[dict]:
    return [
        {"question_id": "occupation", "answer_type": "text", "text_value": "財務分析師"},
        {"question_id": "identity_basis", "answer_type": "choice", "choice_codes": ["national_id"]},
    ]


def suitability_answers(risk_tolerance: str = "low", experience_years: str = "1") -> list[dict]:
    return [
        {"question_id": "risk_tolerance", "answer_type": "choice", "choice_codes": [risk_tolerance]},
        {"question_id": "investment_experience_years", "answer_type": "number", "number_value": experience_years},
    ]


def upload_document(service, context, submission_id: str, expected_version: int, key: str, document_type: str = "identity_front"):
    return service.request_kyc_upload(
        context, submission_id,
        {"document_type": document_type, "filename_extension": "pdf", "media_type": "application/pdf", "size_bytes": 1024, "sha256": "a" * 64},
        expected_version=expected_version, idempotency_key=key,
    )


def _version(response) -> int:
    return int(response.etag.strip('"'))


def approve_full_cycle(service, kyc_reviewer, application_id: str, signature_id: str, key_prefix: str):
    customer_id = service.application_gateway.get_signed_application(TENANT, application_id).customer_id
    customer = customer_context(customer_id)
    created = create_submission(service, customer, application_id, f"{key_prefix}-create", signature_id)
    submission_id = created.data["id"]
    uploaded = upload_document(service, customer, submission_id, _version(created), f"{key_prefix}-upload")
    asset_id = uploaded.data["asset_id"]
    submitted = service.submit_kyc(
        customer, submission_id,
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [asset_id], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key=f"{key_prefix}-submit",
    )
    decided = service.decide_kyc(
        kyc_reviewer, submitted.data["id"],
        {
            "decision": "approve", "identity_verification_method": "manual_document_review",
            "verification_evidence_ids": [asset_id], "reason_code": "evidence_confirmed",
            "next_review_due_at": "2027-11-23T00:00:00.000000Z",
        },
        expected_version=_version(submitted), idempotency_key=f"{key_prefix}-decide",
    )
    return decided
