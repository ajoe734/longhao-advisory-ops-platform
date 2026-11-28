"""Coherent synthetic demo data for local/development, created through the real API.

Every business record is created by the persona whose role the module checks,
through the FastAPI app (authentication, RBAC, idempotency, If-Match, workers
and the event relay all apply). Only what the modules model as external fake
ports is stubbed, through the synthetic world: identity-provider proofs,
provider callbacks (signature, screening, payment), uploaded files and
configuration that has no published API (approved tax mappings, the company
pool policy, the service knowledge base and customer channel bindings).

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

from longhao_platform.synthetic_identities import PERSONAS_BY_KEY, SYNTHETIC_TENANT_ID
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
    def call(self, persona: str | None, method: str, path: str, body: Any = None, *, etag: str | None = None,
             expect: tuple[int, ...] = (200, 201, 202), headers: dict[str, str] | None = None,
             raw: bytes | None = None) -> Result:
        sent: dict[str, str] = {}
        if persona is not None:
            sent["Authorization"] = PERSONAS_BY_KEY[persona].authorization
        if method != "GET":
            sent["Idempotency-Key"] = f"seed:{uuid4()}"
        if etag:
            sent["If-Match"] = etag
        sent.update(headers or {})
        if raw is not None:
            sent["Content-Type"] = "application/json"
            response = self.client.request(method, f"/api/v1{path}", content=raw, headers=sent)
        else:
            response = self.client.request(method, f"/api/v1{path}", json=body if method != "GET" else None, headers=sent)
        persona = persona or "signed-webhook"
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
                     *, annual_fee: str = "36000.00", stop_after: str | None = None,
                     salesperson: str | None = None) -> dict[str, Any]:
    """LegendFlow sign-up through an effective contract with an active entitlement.

    ``stop_after`` leaves the application at an intermediate stage so the work
    queues have something to show: "signed", "kyc", "screening" or "accepted".
    ``salesperson`` follows the customer up while the application is open (an
    opportunity and a two-way meeting recorded before the payment) and, once
    the contract is effective, claims the conversion for the sales supervisor
    to decide.
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
    if salesperson:
        out.update(_follow_up(s, customer, salesperson))

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
    payment = latest_event(s.world, "finance.payment_confirmed", application_id=application)["payload"]["payment_id"]
    out.update(invoice_id=invoice, payment_intent_id=intent, payment_id=payment, contract_service_id=service_row.id,
               stage="effective")
    if salesperson:
        out.update(_claim_conversion(s, out, salesperson))
    return out


# ----------------------------------------------------------------- sales (M10)
def _follow_up(s: Seeder, customer: str, salesperson: str) -> dict[str, Any]:
    """The salesperson tracks the open application and meets the customer before it closes."""
    opportunity = s.call(salesperson, "POST", "/crm/opportunities", {
        "customer_id": customer, "type": "new_subscription", "service_code": SERVICE_CODE,
        "reason_code": "signup_followup", "owner_id": s.actor(salesperson), "due_at": iso(s.now + timedelta(days=5)),
    })
    notes = s.asset(f"meeting notes {customer}", content=f"synthetic meeting notes {customer}", customer_id=customer)
    reply = s.asset(f"customer reply {customer}", content=f"synthetic customer reply {customer}", customer_id=customer)
    activity = s.call(salesperson, "POST", f"/crm/opportunities/{opportunity.body['id']}/activities", {
        "activity_type": "meeting", "occurred_at": iso(s.now - timedelta(hours=2)),
        "customer_response_evidence_id": reply.asset_id, "activity_evidence_asset_id": notes.asset_id,
        "needs_summary": "客戶想訂閱年度研究服務，詢問報告頻率與通知方式", "next_action": "協助完成申辦",
        "next_action_due_at": iso(s.now + timedelta(days=1)), "outcome": "proposal_requested",
    }, etag=opportunity.etag)
    return {"opportunity_id": opportunity.body["id"], "sales_activity_id": activity.body["id"]}


def _claim_conversion(s: Seeder, onboarded: dict[str, Any], salesperson: str) -> dict[str, Any]:
    opportunity = s.call(salesperson, "GET", f"/crm/opportunities/{onboarded['opportunity_id']}")
    claim = s.call(salesperson, "POST", f"/crm/opportunities/{onboarded['opportunity_id']}/conversion-claims", {
        "application_id": onboarded["application_id"], "contract_id": onboarded["contract_service_id"],
        "finance_transaction_ref": onboarded["payment_id"], "sales_activity_ids": [onboarded["sales_activity_id"]],
        "conversion_type": "new_subscription", "claimed_salesperson_id": s.actor(salesperson),
        "reason_code": "closed_new_subscription",
    }, etag=opportunity.etag)
    causality = s.asset(f"conversion review {claim.body['id']}", content=f"synthetic causality review {claim.body['id']}")
    decided = s.call("sales-supervisor", "POST", f"/crm/conversions/{claim.body['id']}/decision", {
        "decision": "approve", "reason_code": "evidence_sufficient", "approved_salesperson_id": s.actor(salesperson),
        "qualifying_activity_ids": [onboarded["sales_activity_id"]], "causality_evidence_asset_id": causality.asset_id,
    }, etag=claim.etag)
    return {"conversion_id": claim.body["id"], "conversion_state": decided.body["state"]}


# ------------------------------------------------- research -> publication
def _pantheon_ref(external_id: str, kind: str, version: str = "1", checksum: str | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"system": "pantheon", "external_id": external_id, "kind": kind, "version": version}
    if checksum:
        ref["checksum_sha256"] = checksum
    return ref


def publish_research(s: Seeder, *, instrument: str = "2330", title: str = "台積電（2330）研究報告") -> dict[str, Any]:
    """Pantheon candidate -> review case -> report version, reviews, freeze, signature -> publication."""
    import hmac

    from modules.pantheon_intake.plugin import SYNTHETIC_PANTHEON_KEY_ID, SYNTHETIC_PANTHEON_SECRET
    from modules.report_signature.domain import canonical_hash as m07_hash
    from modules.report_signature.ports import QualifiedCredential

    out: dict[str, Any] = {"instrument": instrument}
    artifact = s.asset(f"pantheon artifact {instrument}", content=f"synthetic Pantheon research artifact for {instrument}")
    evidence = s.asset(f"pantheon evidence {instrument}", content=f"synthetic evidence bundle for {instrument}")
    provenance = s.asset(f"pantheon provenance {instrument}")
    candidate_body = {
        "external_event_id": str(uuid4()), "origin": "persona_autonomous",
        "ticket_ref": _pantheon_ref(f"ticket-{instrument}", "ticket"),
        "artifact_ref": _pantheon_ref(f"artifact-{instrument}", "report"),
        "artifact_content_sha256": artifact.sha256,
        "evidence_bundle_refs": [_pantheon_ref(f"evidence-{instrument}", "evidence", checksum=evidence.sha256)],
        # the source data was available before the research that used it was produced
        "source_available_at": iso(s.now - timedelta(hours=3)), "source_created_at": iso(s.now - timedelta(hours=2)),
        "data_scope": "research_only", "provenance_snapshot_asset_id": provenance.asset_id,
        "license_scope_ref": "customer_distribution_licensed",
    }
    raw = json.dumps(candidate_body, ensure_ascii=False).encode("utf-8")
    timestamp, nonce, path = str(int(time.time())), uuid4().hex, "/api/v1/research-candidates"
    material = f"{timestamp}\n{nonce}\nPOST\n{path}\n{hashlib.sha256(raw).hexdigest()}".encode("utf-8")
    signature = hmac.new(SYNTHETIC_PANTHEON_SECRET, material, hashlib.sha256).hexdigest()
    candidate = s.call(None, "POST", "/research-candidates", raw=raw, headers={
        "X-Integration-Key-Id": SYNTHETIC_PANTHEON_KEY_ID, "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Nonce": nonce, "X-Webhook-Signature": signature,
    })
    candidate_id = candidate.body["id"]
    snapshot = s.asset(f"pantheon source snapshot {instrument}")
    s.call("research-intake-service", "POST", f"/research-candidates/{candidate_id}/validation", {
        "observed_content_sha256": artifact.sha256, "evidence_access_confirmed": True,
        "license_scope_confirmed": True, "source_snapshot_asset_id": snapshot.asset_id,
    }, etag=candidate.etag)
    validated = latest_event(s.world, "research.candidate_validated", candidate_id=candidate_id)
    review_case_id = validated["payload"]["review_case_id"]
    out.update(candidate_id=candidate_id, review_case_id=review_case_id, stage="review_case")

    # the analyst drafts the report and its first version (M07)
    instrument_ref = {"market": "TWSE", "instrument_code": instrument, "instrument_type": "equity"}
    report = s.call("analyst", "POST", "/reports", {
        "review_case_id": review_case_id, "title": title, "instrument_refs": [instrument_ref], "report_type": "initial",
    })
    body_text = f"{title}：研究結論與風險揭露（合成資料，僅供開發環境）"
    content = s.asset(f"report content {candidate_id}", content=body_text)
    report_evidence = s.asset(f"report evidence {candidate_id}")
    # Contract gap: the M07 version must cite M08 recommendation versions and the M08 version must cite this
    # report version; both ids are server-generated, so the first M07 version can only cite a reserved id.
    reserved_recommendation_id = str(uuid4())
    channel_body = f"【{instrument}】進場建議已由分析師簽章確認，詳見研究報告。"
    audience_base = {"policy_id": str(uuid4()), "policy_version": "audience-2026.11", "service_codes": [SERVICE_CODE],
                     "required_entitlement_state": "active", "restriction_policy_version": "restriction-2026.11"}
    version = s.call("analyst", "POST", f"/reports/{report.body['id']}/versions", {
        "source_candidate_id": candidate_id,
        "source_artifact_ref": _pantheon_ref(f"artifact-{instrument}", "research_candidate"),
        "report_asset_id": content.asset_id, "report_content_sha256": content.sha256,
        "evidence_snapshot_asset_ids": [report_evidence.asset_id],
        "recommendation_version_ids": [reserved_recommendation_id],
        "channel_texts": [{"channel": "legendflow", "locale": "zh-TW", "body": channel_body,
                           "body_sha256": sha256_text(channel_body), "template_version": "template-2026.11",
                           "recommendation_version_ids": [reserved_recommendation_id]}],
        "audience_policy": {**audience_base, "audience_policy_sha256": m07_hash(audience_base)},
        "valid_from": iso(s.now), "valid_until": iso(s.now + timedelta(days=30)),
    }, etag=report.etag)
    report_version = version.body
    out.update(report_id=report.body["id"], report_version_id=report_version["id"], stage="draft")

    # the recommendation the analyst publishes with this report (M08)
    series = s.call("analyst", "POST", "/recommendation-series", {
        "instrument_ref": instrument_ref, "strategy_scope_ref": "momentum-core-v1",
        "research_origin_ref": _pantheon_ref(f"artifact-{instrument}", "report"),
    })
    recommendation = s.call("analyst", "POST", f"/recommendation-series/{series.body['id']}/versions", {
        "action": "entry", "rationale": f"依據已簽章研究報告 {report_version['id']}",
        "supporting_report_version_ids": [report_version["id"]], "valid_from": iso(s.now),
        "valid_until": iso(s.now + timedelta(days=30)), "source_candidate_id": candidate_id,
    }, etag=series.etag)
    out.update(series_id=series.body["id"], recommendation_version_id=recommendation.body["id"])

    # professional and compliance reviews, freeze
    checklist = s.asset(f"review checklist {report_version['id']}")
    reviews = []
    etag = version.etag
    for persona, review_type in (("analyst-reviewer", "professional"), ("compliance-reviewer", "compliance")):
        review = s.call(persona, "POST", f"/report-versions/{report_version['id']}/reviews", {
            "review_type": review_type, "decision": "approve",
            "report_content_sha256": report_version["report_content_sha256"],
            "bundle_manifest_sha256": report_version["bundle_manifest_sha256"],
            "review_policy_version": "m07-review-policy-v1", "checklist_result_asset_id": checklist.asset_id,
            "reason_code": "review_passed",
        }, etag=etag)
        reviews.append(review.body["id"])
        etag = s.call("analyst", "GET", f"/report-versions/{report_version['id']}").etag
    frozen = s.call("analyst", "POST", f"/report-versions/{report_version['id']}/freeze", {
        "report_content_sha256": report_version["report_content_sha256"],
        "bundle_manifest_sha256": report_version["bundle_manifest_sha256"],
        "required_review_ids": reviews, "canonicalization": "RFC8785",
    }, etag=etag)
    out["stage"] = "frozen"

    # the qualified analyst signs; the signature provider confirms; publication follows automatically
    signer = s.actor("analyst-signer")
    credential_id = str(uuid4())
    s.service("M07", "qualifications").register(s.world.tenant_id, signer, credential_id, QualifiedCredential(
        credential_id, signer, "analyst_report", s.now + timedelta(days=365), f"subject:{signer}"))
    session = s.call("analyst-signer", "POST", f"/report-versions/{report_version['id']}/signature", {
        "report_content_sha256": report_version["report_content_sha256"],
        "bundle_manifest_sha256": report_version["bundle_manifest_sha256"],
        "credential_id": credential_id, "required_review_ids": reviews, "signing_intent_confirmed": True,
    }, etag=frozen.etag).body
    signed = s.asset(f"signed report {report_version['id']}", content=f"synthetic signed report {report_version['id']}")
    verification = s.asset(f"report signature verification {report_version['id']}")
    s.call("signature-adapter", "POST", "/webhooks/analyst-signatures", {
        "event_id": str(uuid4()), "signature_request_id": session["id"], "provider_session_ref": session["provider_session_ref"],
        "report_version_id": report_version["id"], "report_content_sha256": report_version["report_content_sha256"],
        "bundle_manifest_sha256": report_version["bundle_manifest_sha256"], "verification_result": "verified",
        "signed_asset_id": signed.asset_id, "signed_file_sha256": signed.sha256, "signer_subject_ref": f"subject:{signer}",
        "signed_at": iso(datetime.now(timezone.utc)), "verification_evidence_asset_id": verification.asset_id,
    }, headers=s.webhook_headers("m07-signature-provider", signature="valid"))
    s.world.tick()
    s.world.tick()
    published = latest_event(s.world, "publication.created", report_version_id=report_version["id"])
    out.update(stage="published" if published else "signed",
               publication_id=published["payload"]["publication_id"] if published else None)
    return out


# ------------------------------------------- business configuration (M11-M13)
KNOWLEDGE_VERSION = "service-kb-2026.11"


def _staff_context(module: str, persona: str) -> Any:
    domain = __import__(f"modules.{module}.domain", fromlist=["ActorContext"])
    person = PERSONAS_BY_KEY[persona]
    return domain.ActorContext(str(SYNTHETIC_TENANT_ID), str(person.id), frozenset(person.roles), principal_type="staff")


def seed_business_configuration(s: Seeder) -> dict[str, Any]:
    """Pool policy (M11), commission policy (M12) and the service knowledge base (M13)."""
    # M11 has no published command for its pool policy; two sales supervisors propose and approve it.
    pool = s.service("M11", "service")
    proposed = pool.propose_policy_version(_staff_context("ownership_pool", "sales-supervisor"), lapse_days=7)
    pool.approve_policy_version(_staff_context("ownership_pool", "sales-supervisor-2"), proposed.data["id"], expected_version=1)

    year_start = s.now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    policy = s.call("commission-operator", "POST", "/commissions/policies", {
        "policy_code": f"standard-{s.now.year}", "effective_from": iso(year_start), "base_pool_rate": "0.10",
        "marketing_year_1": "1.0", "marketing_year_2": "0.5", "marketing_year_3_auto": "0.25",
        "assisted_marketing_share": "0.3", "assisted_sales_share": "0.7",
        "allocation_interpretation": "marketing_decay_only", "age_anchor": "first_paid_effective_start",
        "service_lineage_rule": "sticky_assisted_share", "year3_assisted_marketing_rule": "zero_unless_continuous_auto",
        "pool_policy_version": f"pool-v{proposed.data['policy_version']}",
        "recognition_policy_version": "service_period_straight_line_v1",
        "rounding_mode": "ROUND_HALF_UP", "rounding_scale": 2,
    })
    decision = s.asset(f"commission parameter decision {policy.body['id']}", content="synthetic decision: base pool 10%")
    s.call("commission-approver", "POST", f"/commissions/policies/{policy.body['id']}/approval", {
        "decision": "approve", "policy_sha256": policy.body["policy_sha256"], "approved_base_pool_rate": "0.10",
        "parameter_decision_evidence_asset_id": decision.asset_id, "effective_from": policy.body["effective_from"],
        "reason_code": "finance_approved",
    }, etag=policy.etag)

    knowledge = s.asset("service knowledge: billing and renewal", content="synthetic approved billing FAQ")
    s.service("M13", "knowledge_base").approve(s.world.tenant_id, KNOWLEDGE_VERSION, knowledge.asset_id)
    return {"pool_policy_version": proposed.data["policy_version"], "commission_policy_id": policy.body["id"],
            "knowledge_source_ref": knowledge.asset_id}


# ------------------------------------------------ ownership and the month close
def _month(now: datetime) -> tuple[str, str]:
    start = now.date().replace(day=1)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


FIRST_ASSIGNMENT_ETAG = '"1"'  # M11 treats "no assignment yet" as version 1


def assign_owner(s: Seeder, customer: str, salesperson: str) -> str:
    """The sales supervisor makes the converting salesperson the maintenance owner (M11)."""
    assigned = s.call("sales-supervisor", "POST", f"/customers/{customer}/maintenance-assignments", {
        "salesperson_id": s.actor(salesperson), "effective_from": iso(datetime.now(timezone.utc)),
        "reason_code": "converted_by_salesperson",
    }, etag=FIRST_ASSIGNMENT_ETAG)
    return assigned.body["id"]


def close_month(s: Seeder, business: dict[str, Any]) -> dict[str, Any]:
    """Revenue recognition (M14), the commission run built on it (M12) and its payout batch."""
    period_start, period_end = _month(s.now)
    revenue = s.call("finance-operator", "POST", "/finance/revenue-runs", {
        "period_start": period_start, "period_end": period_end,
        "recognition_policy_version": "service_period_straight_line_v1",
        "cutoff_at": iso(datetime.now(timezone.utc)), "dry_run": False,
    })
    review = s.asset(f"revenue run review {revenue.body['id']}", content=f"synthetic revenue review {revenue.body['id']}")
    s.call("finance-checker", "POST", f"/finance/revenue-runs/{revenue.body['id']}/approval", {
        "decision": "approve", "calculation_manifest_sha256": revenue.body["calculation_manifest_sha256"],
        "reason_code": "schedule_reconciled", "review_evidence_id": review.asset_id,
    }, etag=revenue.etag)
    s.world.tick()

    run = s.call("commission-operator", "POST", "/commissions/runs", {
        "policy_id": business["commission_policy_id"], "period_start": period_start, "period_end": period_end,
        "revenue_cutoff_at": iso(datetime.now(timezone.utc)),
    })
    s.world.tick()  # the commission job calculates the run
    calculated = s.call("commission-approver", "GET", f"/commissions/runs/{run.body['id']}")
    s.call("commission-approver", "POST", f"/commissions/runs/{run.body['id']}/approval",
           {"decision": "approve", "reason_code": "reviewed"}, etag=calculated.etag)
    batch = s.call("payroll-operator", "POST", "/commissions/payout-batches", {
        "approved_run_ids": [run.body["id"]], "payout_period": period_start[:7], "payroll_mapping_version": "payroll-v1",
    })
    s.world.tick()  # the batch job prepares the control totals
    # The batch is left awaiting the commission approver, so UI-24 has a decision to make.
    posted = s.call("finance-checker", "GET", f"/finance/revenue-runs/{revenue.body['id']}")
    return {"revenue_run_id": revenue.body["id"], "recognized": revenue.body["recognized_untaxed_total"],
            "journal_ids": posted.body["journal_ids"], "period": [period_start, period_end],
            "commission_run_id": run.body["id"], "commission_state": calculated.body.get("state"),
            "payout_batch_id": batch.body["id"]}


# ------------------------------------------------- finance operations (M14)
def finance_operations(s: Seeder, onboarded: dict[str, Any], month: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Settlement import with one unmatched line, a refund request awaiting the checker, an accounting export."""
    from decimal import Decimal

    from modules.finance_control.ports import SettlementLine

    paid = Decimal("36000.00")
    fee = Decimal("720.00")
    stray = Decimal("1200.00")
    lines = [
        SettlementLine(f"ptx-{onboarded['payment_intent_id'][:8]}", paid, fee, paid - fee, "TWD", "bank-line-0001"),
        # a provider transaction no payment in M14 knows about: lands in the reconciliation queue
        SettlementLine("ptx-unknown01", stray, Decimal("24.00"), stray - Decimal("24.00"), "TWD", "bank-line-0002"),
    ]
    statement = s.asset(f"settlement statement {s.now.date()}", content=f"synthetic settlement statement {s.now.date()}")
    s.service("M14", "statements").register(s.world.tenant_id, statement.asset_id, statement.sha256, lines)
    gross, fees = sum((line.gross_amount for line in lines), Decimal("0")), sum((line.fee_amount for line in lines), Decimal("0"))
    settlement = s.call("finance-operator", "POST", "/finance/settlement-imports", {
        "provider": "synthetic_payment_provider", "batch_ref": f"settle-{s.now:%Y%m%d}", "settlement_date": s.now.date().isoformat(),
        "statement_asset_id": statement.asset_id, "statement_sha256": statement.sha256,
        "declared_gross": {"amount": f"{gross:.2f}", "currency": "TWD"}, "declared_fee": {"amount": f"{fees:.2f}", "currency": "TWD"},
        "declared_net": {"amount": f"{gross - fees:.2f}", "currency": "TWD"}, "declared_count": len(lines),
        "format_version": "settlement-csv-v1",
    })

    # The member asked about the service period on the case; the agent requests a partial refund of unearned fees.
    # Contract gap: invoice lines expose no id, so the allocation cites the line's contract_service_id.
    request = s.asset(f"refund request {case['case_id']}", content=f"synthetic customer refund request {case['case_id']}",
                      customer_id=onboarded["customer_id"])
    refund = s.call("service-agent", "POST", "/finance/refunds", {
        "customer_id": onboarded["customer_id"], "contract_id": onboarded["contract_id"],
        "original_payment_id": onboarded["payment_id"], "case_id": case["case_id"], "reason_code": "service_start_dispute",
        "requested_amount": {"amount": "3000.00", "currency": "TWD"},
        "allocations": [{"invoice_line_ref": onboarded["contract_service_id"], "recognition_id": None,
                         "unearned_amount": {"amount": "3000.00", "currency": "TWD"},
                         "earned_reversal_amount": {"amount": "0.00", "currency": "TWD"},
                         "tax_adjustment_amount": {"amount": "0.00", "currency": "TWD"}}],
        "calculation_policy_version": "refund-policy-2026.11", "customer_request_evidence_id": request.asset_id,
    })

    export = s.call("finance-operator", "POST", "/finance/accounting-exports", {
        "period_start": month["period"][0], "period_end": month["period"][1], "journal_ids": month["journal_ids"],
        "account_mapping_version": "coa-2026", "tax_mapping_version": TAX_MAPPING_VERSION, "export_format": "csv_v1",
    })
    s.world.tick()
    return {"settlement_import_id": settlement.body.get("id"), "refund_id": refund.body["id"],
            "refund_state": refund.body.get("state"), "accounting_export_id": export.body["id"],
            "accounting_export_state": export.body.get("state")}


# --------------------------------------------------------- service cases (M13)
def service_case(s: Seeder, onboarded: dict[str, Any], business: dict[str, Any], *, answer: bool) -> dict[str, Any]:
    """A member asks about the invoice; with ``answer`` the case is assigned, drafted, approved and sent."""
    member, customer = onboarded["member"], onboarded["customer_id"]
    case = s.call(member, "POST", "/service-cases", {
        "customer_id": customer, "intake_channel": "web", "category": "billing", "subject": "帳單與服務期間",
        "description": "請問年費的服務期間從哪一天開始計算？", "priority": "normal",
    })
    out: dict[str, Any] = {"case_id": case.body["id"], "stage": "opened"}
    if not answer:
        return out
    s.call("service-supervisor", "POST", f"/service-cases/{case.body['id']}/assignment",
           {"assignee_id": s.actor("service-agent"), "reason_code": "billing_queue"}, etag=case.etag)
    current = s.call("service-agent", "GET", f"/service-cases/{case.body['id']}")
    draft = s.call("service-agent", "POST", f"/service-cases/{case.body['id']}/response-drafts", {
        "knowledge_version": KNOWLEDGE_VERSION, "approved_source_refs": [business["knowledge_source_ref"]],
        "intent": "billing_explanation", "requested_tone": "professional", "model_policy_version": "policy-v1",
    }, etag=current.etag)
    text = s.call("service-agent", "GET", f"/service-response-drafts/{draft.body['id']}")
    digest = sha256_text(text.body["text"])
    valid_until = iso(s.now + timedelta(days=7))
    approval = s.call("service-agent-2", "POST", f"/service-response-drafts/{draft.body['id']}/approval", {
        "draft_content_sha256": digest, "decision": "approve", "approval_mode": "human_reviewed",
        "template_version": "manual", "policy_version": "policy-v1", "reason_code": "facts_checked",
        "publication_reference_ids": [], "valid_until": valid_until,
    }, etag=text.etag)
    binding = str(uuid4())
    s.service("M13", "channel_bindings").add(s.world.tenant_id, binding, customer, "email")
    current = s.call("service-agent", "GET", f"/service-cases/{case.body['id']}")
    sent = s.call("service-agent", "POST", f"/service-cases/{case.body['id']}/responses", {
        "draft_id": draft.body["id"], "draft_content_sha256": digest, "channel_binding_id": binding, "channel": "email",
        "approval_mode": "human_reviewed", "approval_id": approval.body["id"], "template_version": "manual",
        "publication_reference_ids": [], "valid_until": valid_until,
    }, etag=current.etag)
    s.world.tick()  # the dispatch worker hands the response to the messaging provider
    out.update(draft_id=draft.body["id"], response_id=sent.body["id"], stage="responded")
    return out


# ----------------------------------------------------------------------- run
def run(client: Any, world: SyntheticWorld) -> dict[str, Any]:
    s = Seeder(client, world)
    config = seed_configuration(s)
    business = seed_business_configuration(s)
    customers = [
        onboard_customer(s, config, "member-a", "合成客戶甲", "+886900000101", salesperson="sales"),
        onboard_customer(s, config, "member-b", "合成客戶乙", "+886900000102", stop_after="kyc"),
    ]
    customers[0]["maintenance_assignment_id"] = assign_owner(s, customers[0]["customer_id"], "sales")
    research = [publish_research(s)]
    month = close_month(s, business)
    cases = [service_case(s, customers[0], business, answer=True), service_case(s, customers[1], business, answer=False)]
    finance = finance_operations(s, customers[0], month, cases[0])
    world.tick()
    return {"configuration": config, "business": business, "customers": customers, "research": research,
            "month_close": month, "service_cases": cases, "finance": finance, "requests": len(s.log),
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
