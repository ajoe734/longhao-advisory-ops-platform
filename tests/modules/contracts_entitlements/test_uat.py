from __future__ import annotations

from datetime import timedelta

import pytest

from modules.contracts_entitlements.domain import (
    ActorContext,
    ClearanceStatus,
    ConflictError,
    ForbiddenError,
    KycStatus,
    NotFoundError,
    StaleVersionError,
    ValidationError,
    canonical_hash,
    new_id,
    wire_time,
)

from conftest import (
    APPLICATION_A,
    APPLICATION_B,
    CUSTOMER_ACTOR,
    NOW,
    REVIEWER_A,
    TENANT,
    accept_application,
    acceptor_ctx,
    create_approved_package,
    customer_ctx,
    document_ref,
    entitlement_service_ctx,
    operator_ctx,
    register_application,
    reviewer_ctx,
    service_selection,
    set_gates,
    sign_application,
    supervisor_ctx,
)


def test_uat_m02_001_customer_can_sign_before_kyc(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, receive_resp, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256, ctx=customer_ctx(CUSTOMER_ACTOR))

    assert receive_resp.data["status"] == "accepted"
    signature = m02["repository"].snapshot().signatures[(TENANT, signature_id)]
    assert signature.application_id == APPLICATION_A
    sessions = [row for row in m02["repository"].snapshot().sessions.values() if row.application_id == APPLICATION_A]
    assert sessions[0].status == "verified"
    # No company acceptance happened yet: entitlements are not opened at all.
    assert m02["repository"].snapshot().contracts == {}
    outbox_types = {event.event_type for event in m02["repository"].snapshot().outbox.values()}
    assert "application.customer_signed" in outbox_types


def test_uat_m02_002_cannot_accept_without_completed_review(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)

    with pytest.raises(ValidationError) as excinfo:
        accept_application(
            m02, APPLICATION_A, package, signature_id,
            kyc_id=new_id(), suitability_id=new_id(), clearance_id=new_id(),
        )
    assert excinfo.value.code == "gate_not_satisfied"
    assert m02["repository"].snapshot().contracts == {}


def test_uat_m02_003_single_activation_under_concurrent_evaluation(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    accept_resp = accept_application(m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    contract_id = accept_resp.data["contract_id"]
    m02["payments"].set_condition(TENANT, APPLICATION_A, "payment-condition-ref-001")

    first = m02["service"].evaluate_entitlements(
        entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "policy-v1"},
        expected_version=1, idempotency_key="eval-worker-a",
    )
    assert first.data["decision"] == "grant"

    with pytest.raises(StaleVersionError):
        m02["service"].evaluate_entitlements(
            entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "policy-v1"},
            expected_version=1, idempotency_key="eval-worker-b",
        )

    effective_events = [e for e in m02["repository"].snapshot().outbox.values() if e.event_type == "contract.effective"]
    assert len(effective_events) == 1
    contract = m02["repository"].snapshot().contracts[(TENANT, contract_id)]
    assert contract.status == "effective"
    assert wire_time(contract.effective_at) == wire_time(NOW)


def test_uat_m02_004_rejecting_an_already_paid_application_queues_refund_not_payout(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "payment-condition-ref-002")

    resp = m02["service"].decide_company_acceptance(
        acceptor_ctx(), APPLICATION_A,
        {"decision": "reject", "package_version_id": package["id"], "customer_signature_id": signature_id, "reason_code": "insufficient_evidence"},
        expected_version=1, idempotency_key="reject-0001",
    )
    assert resp.data["contract_id"] is None
    assert m02["repository"].snapshot().contracts == {}
    jobs = [job for job in m02["repository"].snapshot().jobs.values() if job.kind == "refund_review"]
    assert len(jobs) == 1
    assert jobs[0].payload["application_id"] == APPLICATION_A


def test_uat_m02_005_parallel_contracts_keep_independent_entitlements(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"], selections=[service_selection(billing_interval="month")])
    register_application(m02, APPLICATION_B, CUSTOMER_ACTOR, package["id"], selections=[service_selection(billing_interval="year")])

    _, _, sig_a = sign_application(m02, APPLICATION_A, package, package_sha256)
    _, _, sig_b = sign_application(m02, APPLICATION_B, package, package_sha256)
    kyc_a, suit_a, clear_a = new_id(), new_id(), new_id()
    kyc_b, suit_b, clear_b = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_a, suitability_id=suit_a, clearance_id=clear_a)
    set_gates(m02, APPLICATION_B, kyc_id=kyc_b, suitability_id=suit_b, clearance_id=clear_b)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-a")
    m02["payments"].set_condition(TENANT, APPLICATION_B, "pay-b")

    contract_a = accept_application(m02, APPLICATION_A, package, sig_a, kyc_id=kyc_a, suitability_id=suit_a, clearance_id=clear_a, idempotency_key="accept-a").data["contract_id"]
    contract_b = accept_application(m02, APPLICATION_B, package, sig_b, kyc_id=kyc_b, suitability_id=suit_b, clearance_id=clear_b, idempotency_key="accept-b").data["contract_id"]
    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_a, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=1, idempotency_key="eval-a-1")
    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_b, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=1, idempotency_key="eval-b-1")

    m02["clock"].advance(days=31)
    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_a, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=2, idempotency_key="eval-a-2")

    entitlements = m02["service"].list_customer_entitlements(operator_ctx(), CUSTOMER_ACTOR).data["items"]
    by_contract = {row["contract_id"]: row["state"] for row in entitlements}
    assert by_contract[contract_a] == "expired"
    assert by_contract[contract_b] == "active"


def test_uat_m02_006_new_package_version_rejects_old_signature_hash(m02):
    package_v1, hash_v1 = create_approved_package(m02, package_code="advisory-upgrade", idempotency_key="pkg-v1-create")
    package_v2, hash_v2 = create_approved_package(m02, package_code="advisory-upgrade", documents=[document_ref("50000000-0000-0000-0000-000000000099")], idempotency_key="pkg-v2-create")
    assert hash_v1 != hash_v2
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package_v1["id"])
    sign_application(m02, APPLICATION_A, package_v1, hash_v1)

    consent_id = "60000000-0000-0000-0000-000000000099"
    m02["evidence"].add(TENANT, consent_id)
    with pytest.raises(ValidationError) as excinfo:
        m02["service"].create_customer_signature(
            operator_ctx(), APPLICATION_A,
            {"package_version_id": package_v2["id"], "package_sha256": hash_v1, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
            expected_version=1, idempotency_key="sign-mismatch",
        )
    assert excinfo.value.code == "package_hash_mismatch"

    v1_signature_id = m02["repository"].snapshot().signatures_by_application[(TENANT, APPLICATION_A)]
    assert m02["repository"].snapshot().signatures[(TENANT, v1_signature_id)].package_version_id == package_v1["id"]

    v2_session = m02["service"].create_customer_signature(
        operator_ctx(), APPLICATION_A,
        {"package_version_id": package_v2["id"], "package_sha256": hash_v2, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
        expected_version=1, idempotency_key="sign-v2-create",
    )
    assert v2_session.data["status"] == "pending"


def test_uat_m02_007_stale_kyc_reference_is_rejected(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    suit_id, clear_id = new_id(), new_id()
    stale_kyc_id = new_id()
    current_kyc_id = new_id()
    set_gates(m02, APPLICATION_A, kyc_id=current_kyc_id, suitability_id=suit_id, clearance_id=clear_id)

    with pytest.raises(StaleVersionError) as excinfo:
        accept_application(m02, APPLICATION_A, package, signature_id, kyc_id=stale_kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    assert excinfo.value.code == "stale_assessment"
    assert m02["repository"].snapshot().contracts == {}


def test_uat_m02_008_callback_result_failed_is_not_verification(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    consent_id = "60000000-0000-0000-0000-000000000005"
    m02["evidence"].add(TENANT, consent_id)
    session_resp = m02["service"].create_customer_signature(
        operator_ctx(), APPLICATION_A,
        {"package_version_id": package["id"], "package_sha256": package_sha256, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
        expected_version=1, idempotency_key="sign-fail",
    )
    callback = {
        "event_id": new_id(), "provider_session_ref": session_resp.data["provider_session_ref"], "application_id": APPLICATION_A,
        "package_version_id": package["id"], "document_sha256": package_sha256, "result": "failed",
    }
    receipt = m02["service"].receive_customer_signature(
        ActorContext(TENANT, "90000000-0000-0000-0000-000000000001", frozenset({"signature_adapter"}), principal_type="service"),
        callback, idempotency_key="receive-fail",
    )
    assert receipt.data["status"] == "accepted"
    assert m02["repository"].snapshot().signatures_by_application.get((TENANT, APPLICATION_A)) is None
    session = next(row for row in m02["repository"].snapshot().sessions.values() if row.application_id == APPLICATION_A)
    assert session.status == "failed"
    outbox_types = {event.event_type for event in m02["repository"].snapshot().outbox.values()}
    assert "application.customer_signed" not in outbox_types


def test_uat_m02_009_amendment_does_not_grant_early_access(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"], selections=[service_selection(billing_interval="month")])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-renewal")
    contract_id = accept_application(m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id).data["contract_id"]
    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=1, idempotency_key="eval-pre-amend")

    amendment = m02["service"].create_contract_amendment(
        operator_ctx(), contract_id,
        {"amendment_type": "renewal", "new_package_version_id": package["id"], "service_selections": [service_selection(billing_interval="month")], "effective_at": wire_time(NOW)},
        expected_version=2, idempotency_key="amend-0001",
    )
    assert amendment.data["state"] == "draft"

    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=3, idempotency_key="eval-post-amend")
    items = m02["service"].list_customer_entitlements(operator_ctx(), CUSTOMER_ACTOR).data["items"]
    states = sorted(row["state"] for row in items)
    assert states == ["active", "eligible"]


def test_uat_m02_010_segregation_of_duties_blocks_self_acceptance(m02):
    dual_role_actor = "95000000-0000-0000-0000-000000000001"
    package_resp = m02["service"].create_contract_package(
        ActorContext(TENANT, dual_role_actor, frozenset({"contract_operator"}), principal_type="staff"),
        {"package_code": "advisory-sod", "version_label": "v1", "documents": [document_ref()], "service_codes": ["annual_advisory"],
         "effective_from": wire_time(NOW), "acceptance_terms_version": "terms-v1"},
        idempotency_key="pkg-sod-create",
    )
    package = package_resp.data
    package_sha256 = canonical_hash([document_ref()])
    m02["service"].approve_contract_package(reviewer_ctx(REVIEWER_A), package["id"], {"decision": "approve", "reason_code": "ok"}, expected_version=package["version"], idempotency_key="pkg-sod-approve")
    package = m02["service"].get_contract_package(operator_ctx(), package["id"]).data

    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)

    with pytest.raises(ForbiddenError) as excinfo:
        accept_application(
            m02, APPLICATION_A, package, signature_id, acceptor=dual_role_actor,
            kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id,
        )
    assert excinfo.value.code == "segregation_conflict"
    assert m02["repository"].snapshot().contracts == {}


def test_uat_m02_011_suspended_entitlement_stays_denied_on_reevaluation(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-suspend")
    contract_id = accept_application(m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id).data["contract_id"]
    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=1, idempotency_key="eval-before-suspend")

    entitlement = next(row for row in m02["repository"].snapshot().entitlements.values() if row.contract_id == contract_id)
    evidence_ref = "60000000-0000-0000-0000-000000000077"
    m02["evidence"].add(TENANT, evidence_ref)
    suspend_resp = m02["service"].suspend_entitlement(
        supervisor_ctx(), entitlement.id, {"reason_code": "legal_hold", "evidence_ref": evidence_ref},
        expected_version=entitlement.version, idempotency_key="suspend-0001",
    )
    assert suspend_resp.data["state"] == "suspended"
    recorded_decision_id = m02["repository"].snapshot().entitlements[(TENANT, entitlement.id)].last_decision_id

    reeval = m02["service"].evaluate_entitlements(
        entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"},
        expected_version=2, idempotency_key="eval-after-suspend",
    )
    assert reeval.data["decision"] == "deny"
    refreshed = m02["repository"].snapshot().entitlements[(TENANT, entitlement.id)]
    assert refreshed.status == "suspended"
    assert refreshed.last_decision_id == reeval.data["id"]
    assert recorded_decision_id is not None


def test_uat_m02_012_termination_preserves_contract_and_signature_evidence(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-terminate")
    contract_id = accept_application(m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id).data["contract_id"]
    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=1, idempotency_key="eval-before-terminate")

    evidence_id = "60000000-0000-0000-0000-000000000088"
    m02["evidence"].add(TENANT, evidence_id)
    terminate_resp = m02["service"].terminate_contract(
        operator_ctx(), contract_id,
        {"requested_at": wire_time(NOW), "effective_at": wire_time(NOW), "reason_code": "customer_withdrawal",
         "customer_request_evidence_id": evidence_id, "refund_evaluation_required": True},
        expected_version=2, idempotency_key="terminate-0001",
    )
    assert terminate_resp.data["state"] == "terminated"

    entitlement = next(row for row in m02["repository"].snapshot().entitlements.values() if row.contract_id == contract_id)
    assert entitlement.status == "revoked"

    fetched = m02["service"].get_contract(operator_ctx(), contract_id).data
    assert fetched["state"] == "terminated"
    signature_still_present = m02["repository"].snapshot().signatures[(TENANT, signature_id)]
    assert signature_still_present.application_id == APPLICATION_A
    refund_jobs = [job for job in m02["repository"].snapshot().jobs.values() if job.kind == "refund_review"]
    assert len(refund_jobs) == 1


# ---- regression: candidate 409bd4111339 reopen findings F2-F6 -----------------


def test_regression_f2_receive_customer_signature_is_isolated_by_tenant(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f2-tenant", idempotency_key="pkg-f2-tenant")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    consent_id = "60000000-0000-0000-0000-000000000031"
    m02["evidence"].add(TENANT, consent_id)
    session_resp = m02["service"].create_customer_signature(
        operator_ctx(), APPLICATION_A,
        {"package_version_id": package["id"], "package_sha256": package_sha256, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
        expected_version=1, idempotency_key="sign-f2-tenant",
    )
    provider_ref = session_resp.data["provider_session_ref"]
    other_tenant_ctx = ActorContext(
        "10000000-0000-0000-0000-000000000099", "90000000-0000-0000-0000-000000000002",
        frozenset({"signature_adapter"}), principal_type="service",
    )
    callback = {
        "event_id": new_id(), "provider_session_ref": provider_ref, "application_id": APPLICATION_A,
        "package_version_id": package["id"], "document_sha256": package_sha256, "result": "failed",
    }
    with pytest.raises(NotFoundError):
        m02["service"].receive_customer_signature(other_tenant_ctx, callback, idempotency_key="receive-f2-tenant")
    session = next(row for row in m02["repository"].snapshot().sessions.values() if row.application_id == APPLICATION_A)
    assert session.status == "pending"


def test_regression_f2_receive_customer_signature_requires_signature_adapter_role(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f2-role", idempotency_key="pkg-f2-role")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    consent_id = "60000000-0000-0000-0000-000000000032"
    m02["evidence"].add(TENANT, consent_id)
    session_resp = m02["service"].create_customer_signature(
        operator_ctx(), APPLICATION_A,
        {"package_version_id": package["id"], "package_sha256": package_sha256, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
        expected_version=1, idempotency_key="sign-f2-role",
    )
    callback = {
        "event_id": new_id(), "provider_session_ref": session_resp.data["provider_session_ref"], "application_id": APPLICATION_A,
        "package_version_id": package["id"], "document_sha256": package_sha256, "result": "failed",
    }
    with pytest.raises(ForbiddenError):
        m02["service"].receive_customer_signature(operator_ctx(), callback, idempotency_key="receive-f2-role")


def test_regression_f2_package_hash_mismatch_persists_session_failure(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f2-hash", idempotency_key="pkg-f2-hash")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    consent_id = "60000000-0000-0000-0000-000000000033"
    m02["evidence"].add(TENANT, consent_id)
    session_resp = m02["service"].create_customer_signature(
        operator_ctx(), APPLICATION_A,
        {"package_version_id": package["id"], "package_sha256": package_sha256, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
        expected_version=1, idempotency_key="sign-f2-hash",
    )
    webhook_ctx = ActorContext(TENANT, "90000000-0000-0000-0000-000000000001", frozenset({"signature_adapter"}), principal_type="service")
    callback = {
        "event_id": new_id(), "provider_session_ref": session_resp.data["provider_session_ref"], "application_id": APPLICATION_A,
        "package_version_id": package["id"], "document_sha256": "f" * 64,
        "signed_asset_id": new_id(), "signed_file_sha256": "c" * 64, "provider_signed_at": wire_time(NOW),
        "verification_evidence_asset_id": new_id(), "result": "signed",
    }
    with pytest.raises(ValidationError) as excinfo:
        m02["service"].receive_customer_signature(webhook_ctx, callback, idempotency_key="receive-f2-hash")
    assert excinfo.value.code == "package_hash_mismatch"
    # The session-failed mutation must survive even though the command itself raises;
    # the old code raised *inside* the atomic callback, rolling the status change back.
    session = next(row for row in m02["repository"].snapshot().sessions.values() if row.application_id == APPLICATION_A)
    assert session.status == "failed"


def test_regression_f3_decide_company_acceptance_rejects_mismatched_package_version(m02):
    package_v1, hash_v1 = create_approved_package(m02, package_code="advisory-f3-version", idempotency_key="pkg-f3-v1")
    package_v2, _ = create_approved_package(
        m02, package_code="advisory-f3-version", documents=[document_ref("50000000-0000-0000-0000-000000000098")], idempotency_key="pkg-f3-v2",
    )
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package_v1["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package_v1, hash_v1)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)

    with pytest.raises(ValidationError) as excinfo:
        accept_application(m02, APPLICATION_A, package_v2, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    assert excinfo.value.code == "package_version_mismatch"
    assert m02["repository"].snapshot().contracts == {}


def test_regression_f3_decide_company_acceptance_rejects_unapproved_package(m02):
    draft_resp = m02["service"].create_contract_package(
        operator_ctx(),
        {"package_code": "advisory-f3-draft", "version_label": "v1", "documents": [document_ref("50000000-0000-0000-0000-000000000097")],
         "service_codes": ["annual_advisory"], "effective_from": wire_time(NOW), "acceptance_terms_version": "terms-v1"},
        idempotency_key="pkg-f3-draft",
    )
    draft_package = draft_resp.data
    approved_package, package_sha256 = create_approved_package(m02, package_code="advisory-f3-live", idempotency_key="pkg-f3-live")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, approved_package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, approved_package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)

    with pytest.raises(ValidationError) as excinfo:
        accept_application(m02, APPLICATION_A, draft_package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    assert excinfo.value.code == "package_not_approved"


def test_regression_f3_decide_company_acceptance_rejects_duplicate_accept(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f3-dup", idempotency_key="pkg-f3-dup")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)

    first = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f3-first",
    )
    assert first.data["contract_id"] is not None

    with pytest.raises(ConflictError) as excinfo:
        accept_application(
            m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f3-second",
        )
    assert excinfo.value.code == "contract_already_exists"
    contracts = [c for c in m02["repository"].snapshot().contracts.values() if c.application_id == APPLICATION_A]
    assert len(contracts) == 1


def test_regression_f4_evaluate_entitlements_suspends_when_live_gates_fail(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f4-gates", idempotency_key="pkg-f4-gates")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-f4-gates")
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f4-gates",
    ).data["contract_id"]

    # Compliance later reverses its decision; evaluate_entitlements must re-read the
    # authoritative port live, not just the payment condition captured at acceptance.
    m02["gates"].set_kyc(TENANT, APPLICATION_A, KycStatus(kyc_id, "rejected", 2))
    m02["gates"].set_clearance(TENANT, APPLICATION_A, ClearanceStatus(clear_id, "denied", 2, None))

    result = m02["service"].evaluate_entitlements(
        entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"},
        expected_version=1, idempotency_key="eval-f4-gates",
    )
    assert result.data["decision"] != "grant"
    entitlement = next(row for row in m02["repository"].snapshot().entitlements.values() if row.contract_id == contract_id)
    assert entitlement.status == "suspended"


def test_regression_f4_evaluate_entitlements_rejects_evaluation_before_acceptance(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f4-backdate", idempotency_key="pkg-f4-backdate")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-f4-backdate")
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f4-backdate",
    ).data["contract_id"]

    backdated = wire_time(NOW - timedelta(days=1))
    with pytest.raises(ValidationError) as excinfo:
        m02["service"].evaluate_entitlements(
            entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1", "evaluation_at": backdated},
            expected_version=1, idempotency_key="eval-f4-backdate",
        )
    assert excinfo.value.code == "effective_before_acceptance"


def test_regression_f4_evaluate_entitlements_emits_the_actual_decision_id(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f4-decid", idempotency_key="pkg-f4-decid")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-f4-decid")
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f4-decid",
    ).data["contract_id"]

    cause_event_id = new_id()
    result = m02["service"].evaluate_entitlements(
        entitlement_service_ctx(), contract_id, {"cause_event_id": cause_event_id, "policy_version": "v1"},
        expected_version=1, idempotency_key="eval-f4-decid",
    )
    decision_id = result.data["id"]
    assert decision_id != cause_event_id
    changed_events = [e for e in m02["repository"].snapshot().outbox.values() if e.event_type == "entitlement.changed"]
    assert changed_events
    for event in changed_events:
        assert event.payload["decision_id"] == decision_id
        assert event.causation_id == cause_event_id


def test_regression_f4_terminate_contract_records_entitlement_decision(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f4-term", idempotency_key="pkg-f4-term")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-f4-term")
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f4-term",
    ).data["contract_id"]
    m02["service"].evaluate_entitlements(
        entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=1, idempotency_key="eval-f4-term-pre",
    )

    evidence_id = "60000000-0000-0000-0000-000000000091"
    m02["evidence"].add(TENANT, evidence_id)
    m02["service"].terminate_contract(
        operator_ctx(), contract_id,
        {"requested_at": wire_time(NOW), "effective_at": wire_time(NOW), "reason_code": "customer_withdrawal",
         "customer_request_evidence_id": evidence_id, "refund_evaluation_required": False},
        expected_version=2, idempotency_key="terminate-f4-term",
    )
    entitlement = next(row for row in m02["repository"].snapshot().entitlements.values() if row.contract_id == contract_id)
    assert entitlement.status == "revoked"
    assert entitlement.last_decision_id is not None
    decision = m02["repository"].snapshot().entitlement_decisions.get((TENANT, entitlement.last_decision_id))
    assert decision is not None
    assert decision.decision == "revoke"
    assert entitlement.id in decision.entitlement_ids
    changed_events = [
        e for e in m02["repository"].snapshot().outbox.values()
        if e.event_type == "entitlement.changed" and e.aggregate_id == entitlement.id
    ]
    assert any(e.payload["decision_id"] == decision.id for e in changed_events)


def test_regression_f6_create_customer_signature_replay_does_not_duplicate_provider_session(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f6-replay", idempotency_key="pkg-f6-replay")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    consent_id = "60000000-0000-0000-0000-000000000041"
    m02["evidence"].add(TENANT, consent_id)
    body = {
        "package_version_id": package["id"], "package_sha256": package_sha256,
        "electronic_signing_consent_id": consent_id, "return_route": "application_progress",
    }

    first = m02["service"].create_customer_signature(operator_ctx(), APPLICATION_A, body, expected_version=1, idempotency_key="sign-f6-replay")
    second = m02["service"].create_customer_signature(operator_ctx(), APPLICATION_A, body, expected_version=1, idempotency_key="sign-f6-replay")

    assert second.idempotency_replayed is True
    assert first.data == second.data
    assert len(m02["signer"].sessions) == 1


def test_regression_f6_create_contract_amendment_rejects_unapproved_or_missing_package(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f6-amend", idempotency_key="pkg-f6-amend")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"], selections=[service_selection(billing_interval="month")])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f6-amend",
    ).data["contract_id"]

    draft_resp = m02["service"].create_contract_package(
        operator_ctx(),
        {"package_code": "advisory-f6-amend-v2", "version_label": "v1", "documents": [document_ref("50000000-0000-0000-0000-000000000096")],
         "service_codes": ["annual_advisory"], "effective_from": wire_time(NOW), "acceptance_terms_version": "terms-v1"},
        idempotency_key="pkg-f6-amend-draft",
    )
    draft_package = draft_resp.data

    with pytest.raises(ValidationError) as excinfo:
        m02["service"].create_contract_amendment(
            operator_ctx(), contract_id,
            {"amendment_type": "renewal", "new_package_version_id": draft_package["id"], "service_selections": [service_selection(billing_interval="month")], "effective_at": wire_time(NOW)},
            expected_version=1, idempotency_key="amend-f6-draft",
        )
    assert excinfo.value.code == "package_not_approved"

    with pytest.raises(NotFoundError):
        m02["service"].create_contract_amendment(
            operator_ctx(), contract_id,
            {"amendment_type": "renewal", "new_package_version_id": new_id(), "service_selections": [service_selection(billing_interval="month")], "effective_at": wire_time(NOW)},
            expected_version=1, idempotency_key="amend-f6-missing",
        )


def test_regression_f6_create_contract_amendment_processes_all_selections_and_bumps_contract_version(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f6-multi", idempotency_key="pkg-f6-multi")
    selections = [
        service_selection(service_code="svc-a", billing_interval="month"),
        service_selection(service_code="svc-b", billing_interval="month"),
    ]
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"], selections=selections)
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f6-multi",
    ).data["contract_id"]
    contract_before = m02["repository"].snapshot().contracts[(TENANT, contract_id)]
    services_before = {
        row.id for (tenant, _), row in m02["repository"].snapshot().contract_services.items()
        if tenant == TENANT and row.contract_id == contract_id
    }

    m02["service"].create_contract_amendment(
        operator_ctx(), contract_id,
        {"amendment_type": "renewal", "new_package_version_id": package["id"], "service_selections": selections, "effective_at": wire_time(NOW)},
        expected_version=contract_before.version, idempotency_key="amend-f6-multi",
    )

    contract_after = m02["repository"].snapshot().contracts[(TENANT, contract_id)]
    assert contract_after.version == contract_before.version + 1
    services_after = {
        row.id for (tenant, _), row in m02["repository"].snapshot().contract_services.items()
        if tenant == TENANT and row.contract_id == contract_id
    }
    assert len(services_after - services_before) == 2
