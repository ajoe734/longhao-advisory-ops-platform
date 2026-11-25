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
            expected_version=_version(assessed), idempotency_key="uat006-override-denied",
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
        expected_version=_version(assessed), idempotency_key="uat006-override-authorized",
    )
    assert overridden.data["state"] == "suitable"
    overrides = [row for row in service.repository.snapshot().suitability_overrides.values() if row["assessment_id"] == overridden.data["id"]]
    assert len(overrides) == 1 and overrides[0]["computed_decision"] == "not_suitable"


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


def test_uat_m03_010_general_staff_without_role_cannot_assess_suitability(m03):
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
