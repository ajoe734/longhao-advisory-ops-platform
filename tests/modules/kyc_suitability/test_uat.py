from __future__ import annotations

import pytest

from modules.kyc_suitability.automation import LocalOcrAssistant, OcrAutomationService
from modules.kyc_suitability.domain import ActorContext, ConflictError, ForbiddenError, ValidationError
from modules.kyc_suitability.ports import ScanOutcome, VerificationOutcome
from modules.kyc_suitability.service import compute_suitability

from workers.tasks.kyc_suitability.consumer import M03EventConsumer
from workers.tasks.kyc_suitability.tasks import DocumentScanWorker, DueDiligenceWorker

from conftest import (
    APPLICATION_A,
    APPLICATION_B,
    CUSTOMER_A,
    CUSTOMER_B,
    SALES,
    SIGNATURE_A,
    SIGNATURE_B,
    TENANT,
    _version,
    approve_full_cycle,
    complete_answers,
    create_submission,
    customer_context,
    mark_document_clean,
    suitability_answers,
    upload_document,
)


def test_uat_m03_001_signed_application_can_start_online_kyc(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat001-create")
    assert created.status == 201 and created.data["state"] == "not_started"
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat001-upload")
    submitted = service.submit_kyc(
        customer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="uat001-submit",
    )
    assert submitted.data["state"] == "submitted"
    assert submitted.data["form_version"] == "m03-kyc-form-v1"
    assert submitted.data["answer_sha256"] is not None
    assert submitted.data["document_ids"] == [uploaded.data["asset_id"]]
    assert submitted.data["missing_field_codes"] == []
    # Submitting never opens a service by itself: there is no approval field here.
    assert submitted.data["state"] != "approved"


def test_uat_m03_002_missing_required_fields_blocks_approval(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat002-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat002-upload")
    incomplete_answers = [{"question_id": "occupation", "answer_type": "text", "text_value": "工程師"}]
    submitted = service.submit_kyc(
        customer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": incomplete_answers, "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="uat002-submit",
    )
    assert submitted.data["state"] == "incomplete"
    assert submitted.data["missing_field_codes"] == ["identity_basis"]
    snapshot = service.repository.snapshot()
    stored = snapshot.submissions[(TENANT, submitted.data["id"])]
    assert stored.answers["occupation"]["value"] == "工程師"  # filled content is preserved

    with pytest.raises(ConflictError) as exc:
        service.decide_kyc(
            kyc_reviewer, submitted.data["id"],
            {
                "decision": "approve", "identity_verification_method": "manual_document_review",
                "verification_evidence_ids": [uploaded.data["asset_id"]], "reason_code": "attempted_approval",
            },
            expected_version=_version(submitted), idempotency_key="uat002-decide",
        )
    assert exc.value.code == "submission_incomplete"
    assert snapshot.submissions[(TENANT, submitted.data["id"])].status != "approved"


def test_uat_m03_002b_customer_cannot_self_trigger_due_diligence_refresh(m03, kyc_reviewer):
    # F2 regression: the OpenAPI security requirement restricts
    # request_due_diligence_refresh to staffBearer + kyc_reviewer; a customer
    # must never be able to self-service trigger their own re-screening.
    service = m03["service"]
    approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat002b")
    customer = customer_context(CUSTOMER_A)
    with pytest.raises(ForbiddenError):
        service.request_due_diligence_refresh(
            customer, CUSTOMER_A,
            {"trigger": "scheduled", "reason_code": "self_requested", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
            expected_version=1, idempotency_key="uat002b-refresh-denied",
        )
    snapshot = service.repository.snapshot()
    assert len(snapshot.refresh_cases) == 0  # no refresh case was created by the denied attempt


def test_uat_m03_002c_decide_kyc_denies_the_same_staff_who_submitted_on_the_customers_behalf(m03, kyc_reviewer):
    # F3 regression: decide_kyc's maker-checker gate must trigger even when
    # the submitter is staff (self_or_authorized_staff submit_kyc), not only
    # when assess_suitability's override path is exercised.
    service = m03["service"]
    created = create_submission(service, kyc_reviewer, APPLICATION_A, "uat002c-create")
    uploaded = upload_document(service, kyc_reviewer, created.data["id"], _version(created), "uat002c-upload")
    mark_document_clean(service, uploaded.data["asset_id"])
    submitted = service.submit_kyc(
        kyc_reviewer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="uat002c-submit",
    )
    assert submitted.data["state"] == "submitted"

    with pytest.raises(ForbiddenError) as exc:
        service.decide_kyc(
            kyc_reviewer, submitted.data["id"],
            {
                "decision": "approve", "identity_verification_method": "manual_document_review",
                "verification_evidence_ids": [uploaded.data["asset_id"]], "reason_code": "self_approval_attempt",
                "next_review_due_at": "2027-11-23T00:00:00.000000Z",
            },
            expected_version=_version(submitted), idempotency_key="uat002c-decide",
        )
    assert exc.value.code == "maker_checker_violation"
    assert service.repository.snapshot().submissions[(TENANT, submitted.data["id"])].status == "submitted"


def test_uat_m03_003_ocr_assist_does_not_overwrite_customer_answer(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat003-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat003-upload")
    answers = complete_answers() + [{"question_id": "legal_name", "answer_type": "text", "text_value": "王小明"}]
    submitted = service.submit_kyc(
        customer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": answers, "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="uat003-submit",
    )
    assistant = LocalOcrAssistant()
    assistant.register(uploaded.data["asset_id"], {"legal_name": "WANG XIAO MING"})
    automation = OcrAutomationService(service.repository, assistant)
    with pytest.raises(ConflictError) as exc:
        automation.extract(kyc_reviewer, submitted.data["id"], uploaded.data["asset_id"])
    assert exc.value.code == "document_not_clean"  # OCR never runs on an unscanned/un-cleared document

    mark_document_clean(service, uploaded.data["asset_id"])
    run = automation.extract(kyc_reviewer, submitted.data["id"], uploaded.data["asset_id"])
    suggestion = next(item for item in run.suggestions if item.question_id == "legal_name")
    assert suggestion.ocr_value == "WANG XIAO MING"
    assert suggestion.conflicts_with_customer_answer is True

    stored = service.repository.snapshot().submissions[(TENANT, submitted.data["id"])]
    assert stored.answers["legal_name"]["value"] == "王小明"  # original customer answer is unchanged and still readable

    full_view = service.get_kyc_submission(kyc_reviewer, submitted.data["id"])
    assert full_view.data["state"] == "submitted"


def test_uat_m03_004_malicious_document_is_blocked_in_quarantine(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat004-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat004-upload")
    asset_id = uploaded.data["asset_id"]
    m03["scanner"].register(asset_id, ScanOutcome("rejected", "malware"))
    worker = DocumentScanWorker(service.repository, m03["scanner"])
    worker.run_once("uat004-drill")

    status = service.get_document_status(kyc_reviewer, asset_id)
    assert status.data["scan_status"] == "rejected"
    assert status.data["rejection_code"] == "malware"
    assert all("url" not in key for key in status.data)  # never leaks a readable/public URL

    with pytest.raises(ValidationError) as exc:
        service.submit_kyc(
            customer, created.data["id"],
            {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [asset_id], "declaration_consent_id": SIGNATURE_A},
            expected_version=_version(uploaded), idempotency_key="uat004-submit",
        )
    assert exc.value.code == "document_quarantined"


def test_uat_m03_004b_decide_kyc_cannot_approve_unscanned_or_quarantined_documents(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat004b-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat004b-upload")
    asset_id = uploaded.data["asset_id"]
    # submit_kyc only blocks already-rejected documents; a still-pending (not yet
    # scanned) document is accepted at submit time but must never reach approval.
    submitted = service.submit_kyc(
        customer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [asset_id], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="uat004b-submit",
    )
    decision_body = {
        "decision": "approve", "identity_verification_method": "manual_document_review",
        "verification_evidence_ids": [asset_id], "reason_code": "evidence_confirmed",
        "next_review_due_at": "2027-11-23T00:00:00.000000Z",
    }
    with pytest.raises(ConflictError) as exc:
        service.decide_kyc(kyc_reviewer, submitted.data["id"], decision_body, expected_version=_version(submitted), idempotency_key="uat004b-decide-pending")
    assert exc.value.code == "document_not_clean"
    assert service.repository.snapshot().submissions[(TENANT, submitted.data["id"])].status != "approved"

    m03["scanner"].register(asset_id, ScanOutcome("rejected", "malware"))
    DocumentScanWorker(service.repository, m03["scanner"]).run_once("uat004b-drill")
    with pytest.raises(ConflictError) as exc:
        service.decide_kyc(kyc_reviewer, submitted.data["id"], decision_body, expected_version=_version(submitted), idempotency_key="uat004b-decide-rejected")
    assert exc.value.code == "document_not_clean"

    with pytest.raises(ValidationError) as exc:
        service.decide_kyc(
            kyc_reviewer, submitted.data["id"],
            {**decision_body, "verification_evidence_ids": ["90000000-0000-0000-0000-000000000099"]},
            expected_version=_version(submitted), idempotency_key="uat004b-decide-foreign-evidence",
        )
    assert exc.value.code == "evidence_not_found"

    mark_document_clean(service, asset_id)
    approved = service.decide_kyc(kyc_reviewer, submitted.data["id"], decision_body, expected_version=_version(submitted), idempotency_key="uat004b-decide-approve")
    assert approved.data["state"] == "approved"


def test_uat_m03_005_suitability_is_computed_by_versioned_rules(m03, kyc_reviewer, suitability_reviewer):
    service = m03["service"]
    # Deterministic: same answers and service codes always yield the same decision.
    first = compute_suitability({"risk_tolerance": {"answer_type": "choice", "value": ["low"]}}, ["leveraged_fx"])
    second = compute_suitability({"risk_tolerance": {"answer_type": "choice", "value": ["low"]}}, ["leveraged_fx"])
    assert first == second == ("not_suitable", [], "risk_tolerance_mismatch")

    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat005")
    assessed = service.assess_suitability(
        suitability_reviewer, APPLICATION_A,
        {
            "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
            "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
            "service_codes": ["annual_advisory"], "decision": "suitable", "conditions": [],
            "reason_code": "rule_pass", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
        },
        expected_version=1, idempotency_key="uat005-assess",
    )
    assert assessed.data["state"] == "suitable"
    assert assessed.data["rule_version"] == "m03-suitability-rule-v1"


def test_uat_m03_006_not_suitable_cannot_be_overridden_without_authorization(m03, kyc_reviewer, suitability_reviewer, suitability_authorizer):
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_B, SIGNATURE_B, "uat006")
    assessed = service.assess_suitability(
        suitability_reviewer, APPLICATION_B,
        {
            "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
            "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
            "service_codes": ["leveraged_fx"], "decision": "not_suitable", "conditions": [],
            "reason_code": "rule_pass", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
        },
        expected_version=1, idempotency_key="uat006-assess",
    )
    assert assessed.data["state"] == "not_suitable"

    with pytest.raises(ForbiddenError):
        service.assess_suitability(
            suitability_reviewer, APPLICATION_B,
            {
                "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
                "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
                "service_codes": ["leveraged_fx"], "decision": "suitable", "conditions": [],
                "reason_code": "customer_paid_already", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
            },
            # CAS here is against the application's version (always 1 in the fake
            # gateway), not the assessment's own ETag -- see service.py:_cas(signed.version, ...).
            expected_version=1, idempotency_key="uat006-override-denied",
        )
    snapshot = service.repository.snapshot()
    assert snapshot.assessments[(TENANT, assessed.data["id"])].status == "not_suitable"  # rejection evidence retained

    overridden = service.assess_suitability(
        suitability_authorizer, APPLICATION_B,
        {
            "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
            "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
            "service_codes": ["leveraged_fx"], "decision": "suitable", "conditions": [],
            "reason_code": "authorized_exception_with_evidence", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
        },
        expected_version=1, idempotency_key="uat006-override-authorized",
    )
    assert overridden.data["state"] == "suitable"
    assert overridden.data["id"] != assessed.data["id"]  # a new immutable row, not an in-place rewrite
    overrides = [row for row in service.repository.snapshot().suitability_overrides.values() if row["assessment_id"] == overridden.data["id"]]
    assert len(overrides) == 1 and overrides[0]["computed_decision"] == "not_suitable"
    final_snapshot = service.repository.snapshot()
    assert final_snapshot.assessments[(TENANT, assessed.data["id"])].status == "not_suitable"  # history untouched by the override
    assert final_snapshot.assessments[(TENANT, overridden.data["id"])].status == "suitable"


def test_uat_m03_007_newer_submission_version_makes_the_old_one_stale(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat007-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat007-upload")
    v1 = service.submit_kyc(
        customer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="uat007-submit-v1",
    )
    v1_id = v1.data["id"]
    v1_answer_sha = v1.data["answer_sha256"]

    revised_answers = complete_answers() + [{"question_id": "note", "answer_type": "text", "text_value": "更新補充"}]
    v2 = service.submit_kyc(
        customer, v1_id,
        {"form_version": "m03-kyc-form-v1", "answers": revised_answers, "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(v1), idempotency_key="uat007-submit-v2",
    )
    assert v2.data["id"] != v1_id  # a new version row; v1 is not mutated

    with pytest.raises(ConflictError) as exc:
        service.decide_kyc(
            kyc_reviewer, v1_id,
            {
                "decision": "approve", "identity_verification_method": "manual_document_review",
                "verification_evidence_ids": [uploaded.data["asset_id"]], "reason_code": "stale_attempt",
            },
            expected_version=_version(v1), idempotency_key="uat007-decide-v1",
        )
    assert exc.value.code == "stale_submission"

    v1_stored = service.repository.snapshot().submissions[(TENANT, v1_id)]
    assert v1_stored.answer_sha256 == v1_answer_sha  # v1's original answers are untouched

    mark_document_clean(service, uploaded.data["asset_id"])
    decided_v2 = service.decide_kyc(
        kyc_reviewer, v2.data["id"],
        {
            "decision": "approve", "identity_verification_method": "manual_document_review",
            "verification_evidence_ids": [uploaded.data["asset_id"]], "reason_code": "evidence_confirmed",
            "next_review_due_at": "2027-11-23T00:00:00.000000Z",
        },
        expected_version=_version(v2), idempotency_key="uat007-decide-v2",
    )
    assert decided_v2.data["state"] == "approved"


def test_uat_m03_007b_resubmission_can_reuse_documents_from_earlier_submission_version(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat007b-create")
    s1_id = created.data["id"]
    d1 = upload_document(service, customer, s1_id, _version(created), "uat007b-upload-d1")
    incomplete_answers = [{"question_id": "occupation", "answer_type": "text", "text_value": "分析師"}]
    s1 = service.submit_kyc(
        customer, s1_id,
        {"form_version": "m03-kyc-form-v1", "answers": incomplete_answers, "document_ids": [d1.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(d1), idempotency_key="uat007b-submit-1",
    )
    assert s1.data["state"] == "incomplete" and s1.data["id"] == s1_id  # still the same version row

    d2 = upload_document(service, customer, s1_id, _version(s1), "uat007b-upload-d2", document_type="address_evidence")
    s2 = service.submit_kyc(
        customer, s1_id,
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [d1.data["asset_id"], d2.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(d2), idempotency_key="uat007b-submit-2",
    )
    s2_id = s2.data["id"]
    assert s2_id != s1_id and s2.data["state"] == "submitted"  # a new version row; d1/d2 still point at s1

    service.decide_kyc(
        kyc_reviewer, s2_id,
        {
            "decision": "request_information", "identity_verification_method": "manual_document_review",
            "verification_evidence_ids": [d1.data["asset_id"]], "reason_code": "need_more_evidence",
            "missing_field_codes": ["beneficial_owner"],
        },
        expected_version=_version(s2), idempotency_key="uat007b-decide-request-info",
    )
    reloaded = service.get_kyc_submission(kyc_reviewer, s2_id)
    d3 = upload_document(service, customer, s2_id, _version(reloaded), "uat007b-upload-d3", document_type="beneficial_owner_evidence")

    # d1 and d2 were uploaded against s1, not s2/s3; submitting against s2 must
    # still accept them because they belong to the same application/customer.
    s3 = service.submit_kyc(
        customer, s2_id,
        {
            "form_version": "m03-kyc-form-v1", "answers": complete_answers(),
            "document_ids": [d1.data["asset_id"], d2.data["asset_id"], d3.data["asset_id"]],
            "declaration_consent_id": SIGNATURE_A,
        },
        expected_version=_version(d3), idempotency_key="uat007b-submit-3",
    )
    assert s3.data["id"] not in {s1_id, s2_id} and s3.data["state"] == "submitted"
    assert set(s3.data["document_ids"]) == {d1.data["asset_id"], d2.data["asset_id"], d3.data["asset_id"]}


def test_uat_m03_008_business_role_sees_only_missing_items_not_evidence(m03, sales):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat008-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat008-upload")
    incomplete_answers = [{"question_id": "occupation", "answer_type": "text", "text_value": "業務"}]
    submitted = service.submit_kyc(
        customer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": incomplete_answers, "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="uat008-submit",
    )
    view = service.get_kyc_submission(sales, submitted.data["id"])
    assert view.data["missing_field_codes"] == ["identity_basis"]
    assert view.data["next_review_due_at"] is None
    assert view.data["document_ids"] == []
    assert view.data["answer_snapshot_asset_id"] is None
    assert view.data["answer_sha256"] is None


def test_uat_m03_009_risk_change_triggers_refresh_not_silent_suitable(m03, kyc_reviewer, suitability_reviewer):
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat009")
    service.assess_suitability(
        suitability_reviewer, APPLICATION_A,
        {
            "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
            "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
            "service_codes": ["annual_advisory"], "decision": "suitable", "conditions": [],
            "reason_code": "rule_pass", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
        },
        expected_version=1, idempotency_key="uat009-assess",
    )

    consumer = M03EventConsumer(service.repository)
    envelope = {
        "event_id": "60000000-0000-0000-0000-000000000001", "event_type": "customer.profile_updated", "schema_version": "1.0.0",
        "tenant_id": TENANT, "producer": "M01", "aggregate_type": "customer", "aggregate_id": CUSTOMER_A,
        "aggregate_version": 1, "sequence_no": 1, "occurred_at": "2026-11-23T00:00:00.000000Z", "emitted_at": "2026-11-23T00:00:00.000000Z",
        "correlation_id": "70000000-0000-0000-0000-000000000001", "causation_id": None, "idempotency_key": "customer.profile_updated:uat009",
        "classification": "customer_confidential",
        "payload": {"customer_id": CUSTOMER_A, "changed_field_codes": ["beneficial_owner"], "profile_evidence_ref": "80000000-0000-0000-0000-000000000001"},
    }
    from modules.kyc_suitability.domain import canonical_hash
    envelope["payload_sha256"] = canonical_hash(envelope["payload"])
    result = consumer.consume(envelope)
    assert result.status == "applied" and result.result_code == "risk_change_refresh_created"

    snapshot = service.repository.snapshot()
    submission = snapshot.submissions[(TENANT, decided.data["id"])]
    assert submission.status == "refresh_required"
    assessment = next(row for row in snapshot.assessments.values() if row.application_id == APPLICATION_A)
    assert assessment.status == "reassessment_due"  # the old "suitable" is not silently reused
    assert len(snapshot.refresh_cases) == 1


def test_uat_m03_010_general_staff_without_role_cannot_assess_suitability(m03, kyc_reviewer):
    service = m03["service"]
    general_service_agent = ActorContext(TENANT, "20000000-0000-0000-0000-000000000099", frozenset({"service_agent"}), principal_type="staff")
    with pytest.raises(ForbiddenError):
        service.assess_suitability(
            general_service_agent, APPLICATION_A,
            {
                "kyc_submission_id": "90000000-0000-0000-0000-000000000001", "questionnaire_version": "m03-suitability-questionnaire-v1",
                "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
                "service_codes": ["annual_advisory"], "decision": "suitable", "conditions": [],
                "reason_code": "attempted_without_role", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
            },
            expected_version=1, idempotency_key="uat010-denied",
        )

    # A conditional decision whose required condition is dropped (weakened) to
    # "suitable" is an override even though both calls name a valid decision;
    # it still needs suitability_authorizer, not just suitability_reviewer.
    decided_a = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat010a")
    reviewer_only = ActorContext(TENANT, "20000000-0000-0000-0000-000000000011", frozenset({"suitability_reviewer"}), principal_type="staff")
    base_request = {
        "kyc_submission_id": decided_a.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
        "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("medium", "1"),
        "service_codes": ["leveraged_fx"], "next_review_due_at": "2027-05-23T00:00:00.000000Z",
    }
    conditional = service.assess_suitability(
        reviewer_only, APPLICATION_A,
        {**base_request, "decision": "conditional", "conditions": ["enhanced_disclosure_required"], "reason_code": "rule_pass"},
        expected_version=1, idempotency_key="uat010-conditional",
    )
    assert conditional.data["state"] == "conditional"

    with pytest.raises(ForbiddenError):
        service.assess_suitability(
            reviewer_only, APPLICATION_A,
            {**base_request, "decision": "suitable", "conditions": [], "reason_code": "skip_disclosure"},
            expected_version=1, idempotency_key="uat010-conditional-to-suitable-denied",
        )

    different_authorizer = ActorContext(
        TENANT, "20000000-0000-0000-0000-000000000012", frozenset({"suitability_reviewer", "suitability_authorizer"}), principal_type="staff"
    )
    upgraded = service.assess_suitability(
        different_authorizer, APPLICATION_A,
        {**base_request, "decision": "suitable", "conditions": [], "reason_code": "authorized_waiver"},
        expected_version=1, idempotency_key="uat010-conditional-to-suitable-authorized",
    )
    assert upgraded.data["state"] == "suitable"
    assert upgraded.data["id"] != conditional.data["id"]  # a new immutable row

    # Maker/checker: an actor who holds BOTH roles still cannot authorize an
    # override of their own prior assessment -- role multiplicity never
    # substitutes for a distinct checker (docs/11-security-rbac.md §4).
    decided_b = approve_full_cycle(service, kyc_reviewer, APPLICATION_B, SIGNATURE_B, "uat010b")
    dual_role = ActorContext(TENANT, "20000000-0000-0000-0000-000000000013", frozenset({"suitability_reviewer", "suitability_authorizer"}), principal_type="staff")
    first = service.assess_suitability(
        dual_role, APPLICATION_B,
        {
            "kyc_submission_id": decided_b.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
            "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
            "service_codes": ["leveraged_fx"], "decision": "not_suitable", "conditions": [],
            "reason_code": "rule_pass", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
        },
        expected_version=1, idempotency_key="uat010-maker-checker-first",
    )
    assert first.data["state"] == "not_suitable"

    with pytest.raises(ForbiddenError) as exc:
        service.assess_suitability(
            dual_role, APPLICATION_B,
            {
                "kyc_submission_id": decided_b.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
                "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
                "service_codes": ["leveraged_fx"], "decision": "suitable", "conditions": [],
                "reason_code": "self_override_attempt", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
            },
            expected_version=1, idempotency_key="uat010-maker-checker-self-override",
        )
    assert exc.value.code == "maker_checker_violation"
    assert service.repository.snapshot().assessments[(TENANT, first.data["id"])].status == "not_suitable"


def test_uat_m03_010b_weakening_conditional_conditions_is_still_an_override(m03, kyc_reviewer):
    # F8 regression: computed_decision and resulting_decision can both be
    # "conditional" when an authorizer drops a required condition. The SQL
    # CHECK on kyc.suitability_override must accept this row (it previously
    # only allowed computed_decision <> resulting_decision), and the override
    # evidence must capture both condition sets.
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat010b-override")
    reviewer_only = ActorContext(TENANT, "20000000-0000-0000-0000-000000000021", frozenset({"suitability_reviewer"}), principal_type="staff")
    base_request = {
        "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
        "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("medium", "1"),
        "service_codes": ["leveraged_fx"], "next_review_due_at": "2027-05-23T00:00:00.000000Z",
    }
    conditional = service.assess_suitability(
        reviewer_only, APPLICATION_A,
        {**base_request, "decision": "conditional", "conditions": ["enhanced_disclosure_required"], "reason_code": "rule_pass"},
        expected_version=1, idempotency_key="uat010b-conditional",
    )
    assert conditional.data["state"] == "conditional"

    # Same decision label ("conditional"), but the required condition is
    # swapped for a lighter one -- still an override requiring
    # suitability_authorizer, not just suitability_reviewer. (A fully empty
    # conditions list is rejected independently of the override check, so the
    # weakened set must stay non-empty here.)
    with pytest.raises(ForbiddenError):
        service.assess_suitability(
            reviewer_only, APPLICATION_A,
            {**base_request, "decision": "conditional", "conditions": ["basic_disclosure_required"], "reason_code": "attempted_weaken"},
            expected_version=1, idempotency_key="uat010b-weaken-denied",
        )

    different_authorizer = ActorContext(
        TENANT, "20000000-0000-0000-0000-000000000022", frozenset({"suitability_reviewer", "suitability_authorizer"}), principal_type="staff"
    )
    weakened = service.assess_suitability(
        different_authorizer, APPLICATION_A,
        {**base_request, "decision": "conditional", "conditions": ["basic_disclosure_required"], "reason_code": "authorized_weaken"},
        expected_version=1, idempotency_key="uat010b-weaken-authorized",
    )
    assert weakened.data["state"] == "conditional"
    assert weakened.data["conditions"] == ["basic_disclosure_required"]

    override_rows = [
        row for row in service.repository.snapshot().suitability_overrides.values()
        if row["assessment_id"] == weakened.data["id"]
    ]
    assert len(override_rows) == 1
    override = override_rows[0]
    assert override["computed_decision"] == "conditional"
    assert override["resulting_decision"] == "conditional"
    assert override["computed_conditions"] == ["enhanced_disclosure_required"]
    assert override["resulting_conditions"] == ["basic_disclosure_required"]


def test_uat_m03_011_due_diligence_timeout_goes_to_manual_queue_never_passes(m03, kyc_reviewer):
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat011")
    refreshed = service.request_due_diligence_refresh(
        kyc_reviewer, CUSTOMER_A,
        {"trigger": "scheduled", "reason_code": "periodic_refresh", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
        expected_version=1, idempotency_key="uat011-refresh",
    )
    assert refreshed.data["state"] == "refresh_required"
    m03["verifier"].fail(TENANT, CUSTOMER_A, "timeout")
    worker = DueDiligenceWorker(service.repository, m03["verifier"], max_attempts=3)

    def make_due(state):
        for case in state.refresh_cases.values():
            case.next_attempt_at = None

    for _ in range(3):
        worker.run_once("uat011-drill")
        service.repository.atomic(make_due)

    snapshot = service.repository.snapshot()
    case = next(iter(snapshot.refresh_cases.values()))
    assert case.status == "manual_required"
    assert worker.alerts  # alerted, not silently resolved
    assert case.status != "completed"

    # A manual_required case is not a dead end: an authorized redrive with a
    # non-empty reason requeues it for another automated attempt.
    with pytest.raises(ValidationError):
        worker.redrive(TENANT, case.id, "")
    worker.redrive(TENANT, case.id, "customer_reached_by_phone")
    assert any(entry.startswith("m03_due_diligence_redriven:") for entry in worker.alerts)
    requeued = service.repository.snapshot().refresh_cases[(TENANT, case.id)]
    assert requeued.status == "pending" and requeued.attempt_count == 0


def test_uat_m03_011b_due_diligence_refresh_is_not_a_dead_end(m03, kyc_reviewer):
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat011b")
    refreshed = service.request_due_diligence_refresh(
        kyc_reviewer, CUSTOMER_A,
        {"trigger": "scheduled", "reason_code": "periodic_refresh", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
        expected_version=1, idempotency_key="uat011b-refresh",
    )
    assert refreshed.data["state"] == "refresh_required"
    # The metadata that tells API consumers what they can do next must not be
    # a dead end while the refresh is pending.
    assert "submit_kyc" in refreshed.data["allowed_actions"]
    assert "request_kyc_upload" in refreshed.data["allowed_actions"]

    worker = DueDiligenceWorker(service.repository, m03["verifier"])
    result = worker.run_once("uat011b-drill")
    assert result.processed == 1

    snapshot = service.repository.snapshot()
    case = next(iter(snapshot.refresh_cases.values()))
    assert case.status == "completed"
    submission = snapshot.submissions[(TENANT, decided.data["id"])]
    assert submission.status == "approved"  # the refresh loop closes back to approved, not left dangling


def test_uat_m03_011c_due_diligence_worker_rejects_stolen_lease(m03, kyc_reviewer):
    service = m03["service"]
    approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat011c")
    service.request_due_diligence_refresh(
        kyc_reviewer, CUSTOMER_A,
        {"trigger": "scheduled", "reason_code": "periodic_refresh", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
        expected_version=1, idempotency_key="uat011c-refresh",
    )
    case_id = next(iter(service.repository.snapshot().refresh_cases.values())).id
    worker = DueDiligenceWorker(service.repository, m03["verifier"])
    worker._lease("uat011c-worker-a", 50)  # another worker already owns the lease

    with pytest.raises(ConflictError) as exc:
        worker._complete(TENANT, case_id, "uat011c-worker-b")
    assert exc.value.code == "worker_lease_lost"


def test_uat_m03_011d_stale_resubmission_rejection_closes_the_refresh_case_without_resurrecting_s1(m03, kyc_reviewer):
    # F9 regression: s1 goes approved -> refresh_required, the customer
    # resubmits (creating s2), and the reviewer rejects s2. decide_kyc must
    # close s1's still-open refresh case on reject (not only on approve), so
    # a later worker scan never flips the superseded s1 back to approved.
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat011d")
    s1_id = decided.data["id"]
    asset_id = decided.data["document_ids"][0]

    refreshed = service.request_due_diligence_refresh(
        kyc_reviewer, CUSTOMER_A,
        {"trigger": "scheduled", "reason_code": "periodic_refresh", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
        expected_version=1, idempotency_key="uat011d-refresh",
    )
    assert refreshed.data["id"] == s1_id
    assert refreshed.data["state"] == "refresh_required"

    customer = customer_context(CUSTOMER_A)
    resubmitted = service.submit_kyc(
        customer, s1_id,
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [asset_id], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(refreshed), idempotency_key="uat011d-resubmit",
    )
    s2_id = resubmitted.data["id"]
    assert s2_id != s1_id
    assert resubmitted.data["state"] == "submitted"

    rejected = service.decide_kyc(
        kyc_reviewer, s2_id,
        {
            "decision": "reject", "identity_verification_method": "manual_document_review",
            "verification_evidence_ids": [asset_id], "reason_code": "evidence_insufficient",
        },
        expected_version=_version(resubmitted), idempotency_key="uat011d-decide2",
    )
    assert rejected.data["state"] == "rejected"

    snapshot = service.repository.snapshot()
    case = next(iter(snapshot.refresh_cases.values()))
    assert case.status == "completed"  # closed on reject too, not only on approve
    assert case.lease_owner is None

    worker = DueDiligenceWorker(service.repository, m03["verifier"])
    result = worker.run_once("uat011d-drill")
    assert result.processed == 0  # nothing left pending for the worker to touch

    snapshot = service.repository.snapshot()
    assert snapshot.submissions[(TENANT, s1_id)].status == "refresh_required"  # never resurrected to approved
    assert snapshot.submissions[(TENANT, s2_id)].status == "rejected"


def test_uat_m03_011e_worker_holding_a_stale_lease_cannot_resurrect_a_superseded_submission(m03, kyc_reviewer):
    # Reproduces the exact F9 race: a worker leases the refresh case while it
    # is still pending, then decide_kyc resolves a newer resubmission before
    # the worker's own completion call lands. The worker's completion must
    # lose the lease race instead of flipping the superseded s1 to approved.
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "uat011e")
    s1_id = decided.data["id"]
    asset_id = decided.data["document_ids"][0]

    refreshed = service.request_due_diligence_refresh(
        kyc_reviewer, CUSTOMER_A,
        {"trigger": "scheduled", "reason_code": "periodic_refresh", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
        expected_version=1, idempotency_key="uat011e-refresh",
    )
    case_id = next(iter(service.repository.snapshot().refresh_cases.values())).id

    customer = customer_context(CUSTOMER_A)
    resubmitted = service.submit_kyc(
        customer, s1_id,
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [asset_id], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(refreshed), idempotency_key="uat011e-resubmit",
    )
    s2_id = resubmitted.data["id"]

    worker = DueDiligenceWorker(service.repository, m03["verifier"])
    worker._lease("uat011e-worker", 50)  # worker is mid-flight, holding the case lease

    approved_s2 = service.decide_kyc(
        kyc_reviewer, s2_id,
        {
            "decision": "approve", "identity_verification_method": "manual_document_review",
            "verification_evidence_ids": [asset_id], "reason_code": "evidence_confirmed",
            "next_review_due_at": "2027-11-23T00:00:00.000000Z",
        },
        expected_version=_version(resubmitted), idempotency_key="uat011e-decide2",
    )
    assert approved_s2.data["state"] == "approved"

    with pytest.raises(ConflictError) as exc:
        worker._complete(TENANT, case_id, "uat011e-worker")
    assert exc.value.code == "worker_lease_lost"

    snapshot = service.repository.snapshot()
    assert snapshot.submissions[(TENANT, s1_id)].status == "refresh_required"  # the worker never got to touch it
    assert snapshot.submissions[(TENANT, s2_id)].status == "approved"


def test_uat_m03_012_replayed_submit_does_not_duplicate_the_submission(m03, kyc_reviewer):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "uat012-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "uat012-upload")
    body = {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A}
    first = service.submit_kyc(customer, created.data["id"], body, expected_version=_version(uploaded), idempotency_key="uat012-submit")
    second = service.submit_kyc(customer, created.data["id"], body, expected_version=_version(uploaded), idempotency_key="uat012-submit")
    assert first.data["id"] == second.data["id"]
    assert second.idempotency_replayed is True
    snapshot = service.repository.snapshot()
    assert len(snapshot.submissions) == 1
