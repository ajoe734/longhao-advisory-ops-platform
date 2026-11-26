from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.contracts_entitlements.domain import (
    ActorContext,
    ApplicationSnapshot,
    ClearanceStatus,
    KycStatus,
    SuitabilityStatus,
    canonical_hash,
    wire_time,
)
from modules.contracts_entitlements.http import M02Controller
from modules.contracts_entitlements.ports import (
    FakeApplicationSource,
    FakeEvidenceStore,
    FakeGateDirectory,
    FakePaymentDirectory,
    FakeSignatureProvider,
    FakeWebhookVerifier,
)
from modules.contracts_entitlements.repository import InMemoryRepository
from modules.contracts_entitlements.service import ContractsEntitlementsService


TENANT = "10000000-0000-0000-0000-000000000001"
OPERATOR = "20000000-0000-0000-0000-000000000001"
REVIEWER_A = "20000000-0000-0000-0000-000000000002"
REVIEWER_B = "20000000-0000-0000-0000-000000000003"
ACCEPTOR_A = "20000000-0000-0000-0000-000000000004"
SUPERVISOR = "20000000-0000-0000-0000-000000000005"
ENTITLEMENT_SERVICE_ACTOR = "20000000-0000-0000-0000-000000000006"
CUSTOMER_ACTOR = "30000000-0000-0000-0000-000000000001"
APPLICATION_A = "40000000-0000-0000-0000-000000000001"
APPLICATION_B = "40000000-0000-0000-0000-000000000002"
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def m02():
    repository = InMemoryRepository()
    applications = FakeApplicationSource()
    gates = FakeGateDirectory()
    payments = FakePaymentDirectory()
    signer = FakeSignatureProvider()
    webhook = FakeWebhookVerifier()
    webhook.register_key("key-0000001", TENANT)
    evidence = FakeEvidenceStore()
    clock = FixedClock()
    service = ContractsEntitlementsService(
        repository, applications, gates.kyc_port(), gates.suitability_port(), gates.clearance_port(),
        payments, signer, evidence, clock=clock,
    )
    controller = M02Controller(service, webhook)
    return {
        "repository": repository, "applications": applications, "gates": gates, "payments": payments,
        "signer": signer, "webhook": webhook, "evidence": evidence, "clock": clock, "service": service,
        "controller": controller,
    }


def operator_ctx() -> ActorContext:
    return ActorContext(TENANT, OPERATOR, frozenset({"contract_operator"}), principal_type="staff")


def reviewer_ctx(actor_id: str = REVIEWER_A) -> ActorContext:
    return ActorContext(TENANT, actor_id, frozenset({"compliance_reviewer"}), principal_type="staff")


def acceptor_ctx(actor_id: str = ACCEPTOR_A) -> ActorContext:
    return ActorContext(TENANT, actor_id, frozenset({"company_acceptor"}), principal_type="staff")


def supervisor_ctx() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"contract_supervisor"}), principal_type="staff")


def entitlement_service_ctx() -> ActorContext:
    return ActorContext(TENANT, ENTITLEMENT_SERVICE_ACTOR, frozenset({"entitlement_service"}), principal_type="service")


def customer_ctx(customer_id: str, actor_id: str = CUSTOMER_ACTOR) -> ActorContext:
    return ActorContext(TENANT, actor_id, frozenset(), principal_type="customer", customer_id=customer_id)


def document_ref(asset_id: str = "50000000-0000-0000-0000-000000000001") -> dict:
    return {
        "asset_id": asset_id, "sha256": "a" * 64, "media_type": "application/pdf",
        "classification": "customer_confidential", "scan_status": "clean",
    }


def service_selection(service_code: str = "annual_advisory", amount: str = "12000.000000", billing_interval: str = "year") -> dict:
    return {
        "service_code": service_code, "offer_version": "synthetic-v1", "quantity": 1, "billing_interval": billing_interval,
        "quoted_price": {"currency": "TWD", "amount": amount}, "tax_mapping_version": "synthetic-tax-v1",
    }


def create_approved_package(m02, *, reviewer: str = REVIEWER_A, package_code: str = "advisory-standard", documents=None, idempotency_key: str = "pkg-create-0001"):
    documents = documents or [document_ref()]
    resp = m02["service"].create_contract_package(
        operator_ctx(),
        {"package_code": package_code, "version_label": "v1", "documents": documents, "service_codes": ["annual_advisory"],
         "effective_from": wire_time(NOW), "acceptance_terms_version": "terms-v1"},
        idempotency_key=idempotency_key,
    )
    package = resp.data
    approved = m02["service"].approve_contract_package(
        reviewer_ctx(reviewer), package["id"], {"decision": "approve", "reason_code": "complete"},
        expected_version=package["version"], idempotency_key=f"{idempotency_key}-approve",
    )
    package_sha256 = canonical_hash(documents)
    return approved.data, package_sha256


def register_application(m02, application_id: str, customer_id: str, package_version_id: str, *, selections=None, state="draft", version=1, preceding_contract_id=None):
    m02["applications"].register(ApplicationSnapshot(
        application_id, TENANT, version, customer_id, package_version_id, tuple(selections or [service_selection()]),
        "new", preceding_contract_id, state,
    ))


def sign_application(m02, application_id: str, package: dict, package_sha256: str, *, ctx=None):
    ctx = ctx or operator_ctx()
    consent_id = "60000000-0000-0000-0000-000000000001"
    m02["evidence"].add(TENANT, consent_id)
    create_resp = m02["service"].create_customer_signature(
        ctx, application_id,
        {"package_version_id": package["id"], "package_sha256": package_sha256, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
        expected_version=1, idempotency_key=f"sign-{application_id}",
    )
    session = create_resp.data
    callback = {
        "event_id": f"70000000-0000-0000-0000-{application_id[-12:]}", "provider_session_ref": session["provider_session_ref"],
        "application_id": application_id, "package_version_id": package["id"], "document_sha256": package_sha256,
        "signed_asset_id": "80000000-0000-0000-0000-000000000001", "signed_file_sha256": "b" * 64,
        "provider_signed_at": wire_time(NOW), "verification_evidence_asset_id": "80000000-0000-0000-0000-000000000002",
        "result": "signed",
    }
    receive_resp = m02["service"].receive_customer_signature(
        ActorContext(TENANT, "90000000-0000-0000-0000-000000000001", frozenset({"signature_adapter"}), principal_type="service"),
        callback, idempotency_key=f"receive-{application_id}",
    )
    signature_id = m02["repository"].snapshot().signatures_by_application[(TENANT, application_id)]
    return session, receive_resp, signature_id


def set_gates(m02, application_id: str, *, kyc_id: str, suitability_id: str, clearance_id: str, expires_at=None):
    m02["gates"].set_kyc(TENANT, application_id, KycStatus(kyc_id, "approved", 1))
    m02["gates"].set_suitability(TENANT, application_id, SuitabilityStatus(suitability_id, "suitable", 1))
    m02["gates"].set_clearance(TENANT, application_id, ClearanceStatus(clearance_id, "approved", 1, expires_at))


def accept_application(m02, application_id: str, package: dict, signature_id: str, *, acceptor: str = ACCEPTOR_A,
                        kyc_id: str, suitability_id: str, clearance_id: str, intended_effective_at=None, idempotency_key="accept-0001"):
    body = {
        "decision": "accept", "package_version_id": package["id"], "customer_signature_id": signature_id,
        "reason_code": "all_clear", "kyc_submission_id": kyc_id, "suitability_assessment_id": suitability_id,
        "business_clearance_id": clearance_id, "intended_effective_at": wire_time(intended_effective_at or NOW),
        "payment_condition_version": "payment-policy-v1",
    }
    return m02["service"].decide_company_acceptance(acceptor_ctx(acceptor), application_id, body, expected_version=1, idempotency_key=idempotency_key)
