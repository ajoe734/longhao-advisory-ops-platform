from __future__ import annotations

import pytest

from modules.contracts_entitlements.domain import (
    ActorContext,
    ForbiddenError,
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

    m02["service"].evaluate_entitlements(entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=2, idempotency_key="eval-post-amend")
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
