"""Coherent synthetic demo data for local/development, created through the real API.

Every business record is created by the persona whose role the module checks,
through the FastAPI app (authentication, RBAC, idempotency, If-Match, workers
and the event relay all apply). Only what the modules model as external fake
ports is stubbed, through the synthetic world: identity-provider proofs,
provider callbacks (signature, screening, payment), uploaded files and finance
configuration such as approved tax mappings.

    longhao-api --seed demo        # seed, then serve (development only)
    python -m longhao_platform.synthetic_seed   # seed a throwaway runtime and print a summary
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from longhao_platform.synthetic_identities import PERSONAS_BY_KEY
from longhao_platform.synthetic_world import SyntheticWorld, existing_world, latest_event

SERVICE_CODE = "RESEARCH_ACCESS"
TAX_CODE = "TW-VAT-5"
TAX_MAPPING_VERSION = "tax-2026"


class SeedError(RuntimeError):
    pass


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class Result:
    status: int
    body: Any
    etag: str


@dataclass
class Seeder:
    client: Any
    world: SyntheticWorld
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    log: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- helpers
    def call(self, persona: str, method: str, path: str, body: Any = None, *, etag: str | None = None,
             expect: tuple[int, ...] = (200, 201, 202), headers: dict[str, str] | None = None) -> Result:
        sent = {"Authorization": PERSONAS_BY_KEY[persona].authorization}
        if method != "GET":
            sent["Idempotency-Key"] = f"seed:{uuid4()}"
        if etag:
            sent["If-Match"] = etag
        sent.update(headers or {})
        response = self.client.request(method, f"/api/v1{path}", json=body if method != "GET" else None, headers=sent)
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        line = f"{persona:>22} {method:<5} {path} -> {response.status_code}"
        self.log.append(line)
        if response.status_code not in expect:
            raise SeedError(f"{line}\n{json.dumps(payload, ensure_ascii=False, indent=1)[:1500]}")
        return Result(response.status_code, payload, response.headers.get("ETag", ""))

    def asset(self, label: str, **kwargs: Any) -> Any:
        return self.world.register_asset(label, **kwargs)

    def actor(self, persona: str) -> str:
        return str(PERSONAS_BY_KEY[persona].id)

    def webhook_headers(self, key_id: str, *, epoch: bool = False, signature: str | None = None) -> dict[str, str]:
        headers = {
            "X-Webhook-Timestamp": str(int(time.time())) if epoch else iso(datetime.now(timezone.utc)),
            "X-Webhook-Nonce": uuid4().hex,
            "X-Integration-Key-Id": key_id,
        }
        if signature is not None:
            headers["X-Webhook-Signature"] = signature
        return headers

    def service(self, module_id: str, name: str) -> Any:
        found = self.world.service(module_id, name)
        if found is None:
            raise SeedError(f"{module_id} does not expose {name} to the synthetic world")
        return found


# ------------------------------------------------------------- configuration
def seed_configuration(s: Seeder) -> dict[str, Any]:
    """Approved finance configuration and an approved contract package."""
    s.service("M14", "tax_mappings").approve(s.world.tenant_id, TAX_CODE, TAX_MAPPING_VERSION)
    s.service("M02", "webhook").register_key("m02-signature-provider", s.world.tenant_id)
    document = s.asset("contract package ADVISORY-STD 2026.11", content="synthetic advisory contract terms 2026.11")
    package = s.call("contract-operator", "POST", "/contract-packages", {
        "package_code": "ADVISORY-STD", "version_label": "2026.11", "service_codes": [SERVICE_CODE],
        "effective_from": iso(s.now - timedelta(days=1)), "acceptance_terms_version": "terms-2026.11",
        "documents": [{"asset_id": document.asset_id, "sha256": document.sha256, "media_type": "application/pdf",
                       "classification": "internal", "scan_status": "clean"}],
    })
    approved = s.call("compliance-reviewer", "POST", f"/contract-packages/{package.body['id']}/approval",
                      {"decision": "approve", "reason_code": "terms_reviewed"}, etag=package.etag)
    from modules.contracts_entitlements.domain import canonical_hash

    return {"package_id": package.body["id"], "package_sha256": canonical_hash(approved.body["documents"])}


# ---------------------------------------------------------------- onboarding
def _consent(s: Seeder, customer: str, purpose: str, label: str) -> str:
    current = s.call("cms-operator", "GET", f"/customers/{customer}")
    document = s.asset(f"{label} {customer}", content=f"synthetic {label}", customer_id=customer)
    recorded = s.call("cms-operator", "POST", f"/customers/{customer}/consents", {
        "purpose": purpose, "document_version": f"{purpose}-2026.11", "document_sha256": document.sha256,
        "granted": True, "evidence_asset_id": document.asset_id,
    }, etag=current.etag)
    return recorded.body["id"]


def onboard_customer(s: Seeder, config: dict[str, Any], member: str, display_name: str, phone: str,
                     *, annual_fee: str = "36000.00", stop_after: str | None = None) -> dict[str, Any]:
    """LegendFlow sign-up through an effective contract with an active entitlement.

    ``stop_after`` leaves the application at an intermediate stage so the work
    queues have something to show: "signed", "kyc", "screening" or "accepted".
    """
    from modules.customer_identity_application.ports import VerifiedIdentity

    out: dict[str, Any] = {"member": member, "display_name": display_name, "stage": "created"}
    proof = s.asset(f"{member} legendflow identity proof")
    s.service("M01", "identity_provider").register(proof.asset_id, VerifiedIdentity(
        "legendflow", f"synthetic:{member}", "https://legendflow.example.invalid", "longhao-ops",
        s.now, s.now + timedelta(days=30), "aal2", display_name, f"{member}@example.invalid", phone,
    ))
    customer = s.call("cms-operator", "POST", "/customers", {
        "external_system": "legendflow", "verified_identity_proof_ref": proof.asset_id, "display_name": display_name,
        "marketing_source_ref": "campaign:synthetic-2026q4",
    }).body["id"]
    out["customer_id"] = customer
    s.world.link_customer(s.actor(member), customer)

    application = s.call("cms-operator", "POST", "/applications", {
        "customer_id": customer, "package_version_id": config["package_id"], "intent": "new",
        "service_selections": [{"service_code": SERVICE_CODE, "offer_version": "offer-2026.11", "quantity": 1,
                                "billing_interval": "year", "quoted_price": {"amount": annual_fee, "currency": "TWD"},
                                "tax_mapping_version": TAX_MAPPING_VERSION}],
    }).body["id"]
    out["application_id"] = application

    # customer signs the contract package (M02 signing session + provider callback)
    esign = _consent(s, customer, "electronic_signing", "electronic signing consent")
    current = s.call("cms-operator", "GET", f"/applications/{application}")
    session = s.call("contract-operator", "POST", f"/applications/{application}/customer-signature", {
        "package_version_id": config["package_id"], "package_sha256": config["package_sha256"],
        "electronic_signing_consent_id": esign, "return_route": "application_progress",
    }, etag=current.etag).body
    signed = s.asset(f"signed contract {application}", content=f"synthetic signed container {application}", customer_id=customer)
    verification = s.asset(f"signature verification {application}")
    s.call("signature-adapter", "POST", "/webhooks/customer-signatures", {
        "event_id": str(uuid4()), "provider_session_ref": session["provider_session_ref"], "application_id": application,
        "package_version_id": config["package_id"], "document_sha256": config["package_sha256"], "result": "signed",
        "signed_asset_id": signed.asset_id, "signed_file_sha256": signed.sha256, "provider_signed_at": iso(s.now),
        "verification_evidence_asset_id": verification.asset_id,
    }, headers=s.webhook_headers("m02-signature-provider", signature="valid"))
    signature_id = latest_event(s.world, "application.customer_signed", application_id=application)["payload"]["customer_signature_id"]
    out.update(signature_id=signature_id, stage="signed")
    if stop_after == "signed":
        return out

    # KYC by the customer, decided by the KYC reviewer; suitability by the reviewer
    current = s.call("cms-operator", "GET", f"/applications/{application}")
    submission = s.call(member, "POST", f"/applications/{application}/kyc-submissions",
                        {"form_version": "m03-kyc-form-v1", "customer_signature_id": signature_id}, etag=current.etag)
    kyc_id = submission.body["id"]
    identity = s.asset(f"{member} identity document", content=f"synthetic identity document {member}",
                       customer_id=customer, classification="customer_confidential")
    upload = s.call(member, "POST", f"/kyc-submissions/{kyc_id}/document-uploads", {
        "document_type": "identity_front", "filename_extension": "png", "media_type": "image/png",
        "size_bytes": 20480, "sha256": identity.sha256,
    }, etag=submission.etag)
    document_id = upload.body["asset_id"]
    s.world.tick()  # the document scan worker marks the upload clean
    declaration = _consent(s, customer, "suitability_declaration", "kyc declaration")
    submitted = s.call(member, "POST", f"/kyc-submissions/{kyc_id}/submit", {
        "form_version": "m03-kyc-form-v1", "document_ids": [document_id], "declaration_consent_id": declaration,
        "answers": [{"question_id": "occupation", "answer_type": "text", "text_value": "工程師"},
                    {"question_id": "identity_basis", "answer_type": "choice", "choice_codes": ["national_id"]}],
    }, etag=upload.etag)
    out.update(kyc_submission_id=kyc_id, stage="kyc_submitted")
    if stop_after == "kyc":
        return out
    s.call("kyc-reviewer", "POST", f"/kyc-submissions/{kyc_id}/decision", {
        "decision": "approve", "identity_verification_method": "manual_document_review",
        "verification_evidence_ids": [document_id], "reason_code": "evidence_confirmed",
        "next_review_due_at": iso(s.now + timedelta(days=365)),
    }, etag=submitted.etag)
    current = s.call("cms-operator", "GET", f"/applications/{application}")
    assessment = s.call("suitability-reviewer", "POST", f"/applications/{application}/suitability-assessments", {
        "kyc_submission_id": kyc_id, "questionnaire_version": "m03-suitability-questionnaire-v1",
        "rule_version": "m03-suitability-rule-v1", "service_codes": [SERVICE_CODE], "decision": "suitable",
        "answers": [{"question_id": "risk_tolerance", "answer_type": "choice", "choice_codes": ["medium"]},
                    {"question_id": "investment_experience_years", "answer_type": "number", "number_value": "5"}],
        "conditions": [], "reason_code": "rule_pass", "next_review_due_at": iso(s.now + timedelta(days=180)),
    }, etag=current.etag).body["id"]
    out["suitability_assessment_id"] = assessment

    # AML: customer declaration, officer screening, provider callback
    aml_consent = _consent(s, customer, "aml_declaration", "aml declaration")
    answers = s.asset(f"{member} aml answers", content=f"synthetic aml answers {member}", customer_id=customer,
                      classification="aml_restricted")
    s.call(member, "POST", f"/applications/{application}/aml-declarations", {
        "form_version": "aml-decl-v1", "declaration_consent_id": aml_consent, "restricted_answer_asset_id": answers.asset_id,
        "restricted_document_ids": [identity.asset_id], "customer_signature_id": signature_id,
    })
    screening = s.call("aml-officer", "POST", "/aml/screenings", {
        "application_id": application, "customer_id": customer, "kyc_submission_id": kyc_id,
        "screening_policy_version": "policy-v1", "scope": ["sanctions", "pep"],
        "restricted_identity_asset_id": identity.asset_id, "reason_code": "application_intake",
    }).body["id"]
    out.update(screening_id=screening, stage="screening")
    s.world.tick()  # the dispatch worker hands the request to the screening provider
    if stop_after == "screening":
        return out
    result = s.asset(f"screening result {screening}", content=f"synthetic clear result {screening}", classification="aml_restricted")
    s.call("aml-adapter", "POST", "/webhooks/aml-screenings", {
        "event_id": str(uuid4()), "screening_id": screening, "provider_ref": "synthetic-watchlist:sanctions",
        "list_version": "list-2026.11", "screened_at": iso(s.now), "result": "clear",
        "restricted_result_asset_id": result.asset_id, "result_sha256": result.sha256,
    }, headers=s.webhook_headers("integration-adapter-001", epoch=True))
    clearance = latest_event(s.world, "business_clearance.changed", application_id=application)["payload"]["clearance_id"]

    # company acceptance creates the contract
    current = s.call("cms-operator", "GET", f"/applications/{application}")
    accepted = s.call("company-acceptor", "POST", f"/applications/{application}/company-acceptance", {
        "decision": "accept", "package_version_id": config["package_id"], "customer_signature_id": signature_id,
        "reason_code": "all_gates_passed", "kyc_submission_id": kyc_id, "suitability_assessment_id": assessment,
        "business_clearance_id": clearance, "intended_effective_at": iso(s.now),
        "payment_condition_version": "payment-terms-2026.11",
    }, etag=current.etag)
    contract_id = accepted.body["contract_id"]
    out.update(contract_id=contract_id, business_clearance_id=clearance, stage="accepted")
    if stop_after == "accepted":
        return out

    # invoice and payment; the provider confirms and M02 activates the entitlement
    # API gap: no endpoint exposes contract_service_id, which an M14 invoice line requires.
    service_row = s.world.modules["M02"].repository.read(
        lambda state: [row for row in state.contract_services.values() if row.contract_id == contract_id])[0]
    untaxed = f"{float(annual_fee) / 1.05:.2f}"
    tax = f"{float(annual_fee) - float(untaxed):.2f}"
    invoice = s.call("finance-operator", "POST", "/finance/invoices", {
        "customer_id": customer, "contract_id": contract_id, "invoice_date": s.now.date().isoformat(),
        "due_date": (s.now + timedelta(days=14)).date().isoformat(), "invoice_type": "service",
        "lines": [{"contract_service_id": service_row.id, "service_code": service_row.service_code,
                   "service_period_start": service_row.starts_at.date().isoformat(),
                   "service_period_end": service_row.ends_at.date().isoformat(), "quantity": 1,
                   "untaxed_service_amount": {"amount": untaxed, "currency": "TWD"},
                   "tax_amount": {"amount": tax, "currency": "TWD"}, "tax_code": TAX_CODE,
                   "tax_mapping_version": TAX_MAPPING_VERSION}],
    }).body["id"]
    intent = s.call("finance-operator", "POST", "/finance/payment-intents", {
        "application_id": application, "invoice_id": invoice, "amount": {"amount": annual_fee, "currency": "TWD"},
        "method": "card", "return_route": "application_progress",
    }).body["id"]
    receipt = s.asset(f"payment receipt {intent}", content=f"synthetic card payment receipt {intent}")
    s.call("payment-adapter", "POST", "/webhooks/payments", {
        "event_id": str(uuid4()), "provider_transaction_ref": f"ptx-{intent[:8]}", "payment_intent_id": intent,
        "status": "paid", "amount": {"amount": annual_fee, "currency": "TWD"}, "provider_occurred_at": iso(s.now),
        "evidence_asset_id": receipt.asset_id,
    }, headers=s.webhook_headers("payment-provider-01"))
    s.world.tick()  # entitlement recheck activates the entitlement and the contract becomes effective
    out.update(invoice_id=invoice, payment_intent_id=intent, contract_service_id=service_row.id, stage="effective")
    return out


# ----------------------------------------------------------------------- run
def run(client: Any, world: SyntheticWorld) -> dict[str, Any]:
    s = Seeder(client, world)
    config = seed_configuration(s)
    customers = [
        onboard_customer(s, config, "member-a", "合成客戶甲", "+886900000101"),
        onboard_customer(s, config, "member-b", "合成客戶乙", "+886900000102", stop_after="kyc"),
    ]
    world.tick()
    return {"configuration": config, "customers": customers, "requests": len(s.log),
            "relayed_events": world.status()["relayed_events"], "failures": list(world.failures)}


def seed_app(app: Any, runtime: Any) -> dict[str, Any]:
    world = existing_world(runtime)
    if world is None:
        raise SeedError("synthetic seed requires a development runtime with synthetic providers")
    from fastapi.testclient import TestClient

    return run(TestClient(app), world)


def main() -> int:
    from longhao_platform.app import PlatformRuntime
    from longhao_platform.http import create_app

    runtime = PlatformRuntime.bootstrap()
    summary = seed_app(create_app(runtime), runtime)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if not summary["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
