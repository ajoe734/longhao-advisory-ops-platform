from __future__ import annotations

from datetime import timedelta

import pytest

from modules.customer_identity_application.domain import (
    ActorContext,
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    NotFoundError,
    StaleVersionError,
    UnauthorizedError,
    ValidationError,
)
from modules.customer_identity_application.http import M01Controller
from modules.customer_identity_application.ports import SourceSnapshot

from conftest import (
    CONSENT_A,
    CONSENT_B,
    CUSTOMER_ACTOR_B,
    MERGE_EVIDENCE,
    NOW,
    OPERATOR,
    PACKAGE,
    PROOF_A,
    PROOF_B,
    PROOF_C,
    PROOF_FORGED,
    PROOF_TIMEOUT,
    REVIEW_EVIDENCE,
    SUPERVISOR,
    TENANT,
    application_body,
    create_customer,
    customer_context,
)


def test_uat_m01_001_same_external_subject_is_single_customer(m01, operator):
    service = m01["service"]
    first = create_customer(service, operator, PROOF_A, "customer-create-001")
    second = create_customer(service, operator, PROOF_A, "customer-create-002", "campaign:later")
    snapshot = m01["repository"].snapshot()
    assert first.data["id"] == second.data["id"]
    assert len(snapshot.customers) == 1
    assert len(snapshot.identities) == 1
    assert len(snapshot.outbox) == 2
    assert snapshot.customers[(TENANT, first.data["id"])].original_marketing_source_ref == "campaign:first"


def test_uat_m01_002_equal_email_different_subject_requires_human_review(m01, operator):
    first = create_customer(m01["service"], operator, PROOF_A, "customer-create-011")
    second = create_customer(m01["service"], operator, PROOF_B, "customer-create-012")
    snapshot = m01["repository"].snapshot()
    assert first.data["id"] != second.data["id"]
    assert len(snapshot.customers) == 2
    candidates = [case for case in snapshot.merge_cases.values() if case.reason_code == "verified_contact_match_requires_review"]
    assert len(candidates) == 1
    assert candidates[0].status == "proposed"


def test_uat_m01_003_draft_resume_preserves_id_and_withdraws_without_entitlement(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "customer-create-021").data
    context = customer_context(customer["id"])
    first = m01["service"].create_application(context, application_body(customer["id"]), idempotency_key="application-create-001")
    second = m01["service"].create_application(context, application_body(customer["id"], "touch:later"), idempotency_key="application-create-002")
    assert first.data["id"] == second.data["id"]
    stored = m01["repository"].snapshot().applications[(TENANT, first.data["id"])]
    assert stored.original_acquisition_touch_ref == "touch:first"
    assert stored.acquisition_touches == ["touch:first", "touch:later"]
    read = m01["service"].get_application(context, first.data["id"])
    withdrawn = m01["service"].withdraw_application(
        context,
        first.data["id"],
        {"reason_code": "customer_changed_mind"},
        expected_version=1,
        idempotency_key="application-withdraw-001",
    )
    assert read.data["state"] == "draft"
    assert withdrawn.data["state"] == "withdrawn"
    assert withdrawn.data["allowed_actions"] == []
    assert withdrawn.data["contract_id"] is None


def test_uat_m01_004_cross_customer_reads_hide_existence(m01, operator):
    a = create_customer(m01["service"], operator, PROOF_A, "customer-create-031").data
    b = create_customer(m01["service"], operator, PROOF_C, "customer-create-032").data
    b_context = customer_context(b["id"], CUSTOMER_ACTOR_B)
    app = m01["service"].create_application(b_context, application_body(b["id"]), idempotency_key="application-create-031").data
    controller = M01Controller(m01["service"])
    customer_response = controller.handle("get_customer", customer_context(a["id"]), path={"customer_id": b["id"]})
    app_response = controller.handle("get_application", customer_context(a["id"]), path={"application_id": app["id"]})
    assert customer_response.status == 404
    assert app_response.status == 404
    assert b["display_name"] not in str(customer_response.body)
    denied_audits = [event for event in m01["repository"].snapshot().audit_events if event.result == "rejected"]
    assert {event.action for event in denied_audits} >= {"m01.get_customer", "m01.get_application"}
    assert all(b["display_name"] not in str(event) for event in denied_audits)


def test_uat_m01_005_later_touch_does_not_replace_first_touch(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "customer-create-041", "campaign:first").data
    context = customer_context(customer["id"])
    m01["service"].create_application(context, application_body(customer["id"], "campaign:second"), idempotency_key="application-create-041")
    m01["service"].create_application(context, application_body(customer["id"], "campaign:third"), idempotency_key="application-create-042")
    stored_customer = m01["repository"].snapshot().customers[(TENANT, customer["id"])]
    assert stored_customer.original_marketing_source_ref == "campaign:first"
    assert stored_customer.acquisition_touches[-2:] == ["campaign:second", "campaign:third"]


def test_uat_m01_006_consent_withdrawal_is_scoped_and_append_only(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "customer-create-051").data
    context = customer_context(customer["id"])
    m01["evidence"].add(TENANT, CONSENT_A, customer["id"])
    m01["evidence"].add(TENANT, CONSENT_B, customer["id"])
    granted = m01["service"].record_consent(context, customer["id"], {
        "purpose": "marketing", "document_version": "notice-v1", "document_sha256": "a" * 64,
        "granted": True, "evidence_asset_id": CONSENT_A,
    }, expected_version=1, idempotency_key="consent-record-001")
    withdrawn = m01["service"].record_consent(context, customer["id"], {
        "purpose": "marketing", "document_version": "notice-v1", "document_sha256": "a" * 64,
        "granted": False, "evidence_asset_id": CONSENT_B,
    }, expected_version=2, idempotency_key="consent-record-002")
    snapshot = m01["repository"].snapshot()
    assert granted.data["granted"] is True and withdrawn.data["granted"] is False
    assert len(snapshot.consents) == 2
    assert all(consent.purpose == "marketing" for consent in snapshot.consents.values())
    assert not any(consent.purpose == "service_notification" and not consent.granted for consent in snapshot.consents.values())


def test_uat_m01_007_forged_external_token_creates_nothing(m01, operator):
    before = m01["repository"].snapshot()
    with pytest.raises(UnauthorizedError):
        create_customer(m01["service"], operator, PROOF_FORGED, "customer-create-061")
    after = m01["repository"].snapshot()
    assert len(after.customers) == len(before.customers) == 0
    assert len(after.identities) == 0
    assert after.audit_events[-1].reason_code == "identity_proof_invalid"


def test_uat_m01_008_approved_merge_keeps_alias_and_checker_evidence(m01, operator, supervisor):
    source = create_customer(m01["service"], operator, PROOF_A, "customer-create-071").data
    target = create_customer(m01["service"], operator, PROOF_C, "customer-create-072").data
    proposed = m01["service"].propose_customer_merge(operator, {
        "source_customer_id": source["id"], "target_customer_id": target["id"],
        "evidence_asset_ids": [MERGE_EVIDENCE], "reason_code": "verified_duplicate",
    }, idempotency_key="merge-propose-001")
    decided = m01["service"].decide_customer_merge(supervisor, proposed.data["id"], {
        "decision": "approve", "reason_code": "evidence_confirmed", "evidence_asset_ids": [REVIEW_EVIDENCE],
    }, expected_version=1, idempotency_key="merge-decide-001")
    snapshot = m01["repository"].snapshot()
    assert decided.data["status"] == "applied"
    assert snapshot.aliases[(TENANT, source["id"])] == target["id"]
    assert snapshot.customers[(TENANT, source["id"])].status == "merged"
    case = snapshot.merge_cases[(TENANT, proposed.data["id"])]
    assert case.reviewer_id == SUPERVISOR and case.reviewer_evidence_asset_ids == [REVIEW_EVIDENCE]
    assert m01["service"].get_customer(operator, source["id"]).data["id"] == target["id"]


def test_uat_m01_009_stale_update_is_rejected_and_first_write_remains(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "customer-create-081").data
    first = m01["service"].update_customer(operator, customer["id"], {"display_name": "第一位更新者"}, expected_version=1, idempotency_key="customer-update-001")
    with pytest.raises(StaleVersionError) as stale:
        m01["service"].update_customer(operator, customer["id"], {"display_name": "過期覆寫"}, expected_version=1, idempotency_key="customer-update-002")
    assert stale.value.status == 412 and stale.value.current_version == 2
    assert first.data["display_name"] == m01["service"].get_customer(operator, customer["id"]).data["display_name"]


def test_uat_m01_010_provider_timeout_does_not_create_identity(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "customer-create-091").data
    before = m01["repository"].snapshot()
    with pytest.raises(DependencyUnavailableError) as unavailable:
        m01["service"].link_external_identity(operator, customer["id"], {
            "provider": "legendflow", "verified_proof_ref": PROOF_TIMEOUT,
        }, expected_version=1, idempotency_key="identity-link-091")
    after = m01["repository"].snapshot()
    assert unavailable.value.retryable is True
    assert len(after.identities) == len(before.identities)
    assert after.customers[(TENANT, customer["id"])].version == 1


def test_uat_m01_011_assisted_draft_cannot_impersonate_consent_or_acceptance(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "customer-create-101").data
    invalid = application_body(customer["id"])
    invalid["company_effective"] = True
    with pytest.raises(ValidationError):
        m01["service"].create_application(operator, invalid, idempotency_key="application-create-101")
    m01["evidence"].add(TENANT, CONSENT_A)
    with pytest.raises(ForbiddenError):
        m01["service"].record_consent(operator, customer["id"], {
            "purpose": "privacy", "document_version": "notice-v1", "document_sha256": "b" * 64,
            "granted": True, "evidence_asset_id": CONSENT_A,
        }, expected_version=1, idempotency_key="consent-record-101")
    snapshot = m01["repository"].snapshot()
    assert len(snapshot.applications) == 0 and len(snapshot.consents) == 0
    assert any(event.reason_code == "customer_consent_evidence_required" for event in snapshot.audit_events)


def test_uat_m01_012_degraded_source_is_not_rendered_as_empty(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "customer-create-111").data
    m01["sources"].set(TENANT, customer["id"], [
        SourceSnapshot("contracts", "degraded", NOW - timedelta(minutes=5), None, "m02_unavailable"),
        SourceSnapshot("payments", "available", NOW, [], None),
    ])
    summary = m01["service"].get_customer_360(operator, customer["id"])
    contracts, payments = summary["sources"]
    assert contracts["source_status"] == "degraded" and contracts["items"] is None and contracts["last_success_at"]
    assert payments["source_status"] == "available" and payments["items"] == []


def test_idempotency_key_reuse_with_different_payload_is_409(m01, operator):
    create_customer(m01["service"], operator, PROOF_A, "same-key-0001")
    with pytest.raises(ConflictError) as conflict:
        create_customer(m01["service"], operator, PROOF_C, "same-key-0001")
    assert conflict.value.code == "idempotency_conflict"


def test_merge_maker_cannot_check_own_case(m01, operator):
    source = create_customer(m01["service"], operator, PROOF_A, "customer-create-121").data
    target = create_customer(m01["service"], operator, PROOF_C, "customer-create-122").data
    proposed = m01["service"].propose_customer_merge(operator, {
        "source_customer_id": source["id"], "target_customer_id": target["id"],
        "evidence_asset_ids": [MERGE_EVIDENCE], "reason_code": "duplicate",
    }, idempotency_key="merge-propose-121")
    maker_with_supervisor = ActorContext(TENANT, OPERATOR, frozenset({"cms_operator", "cms_supervisor"}), principal_type="staff")
    with pytest.raises(ForbiddenError) as denied:
        m01["service"].decide_customer_merge(maker_with_supervisor, proposed.data["id"], {
            "decision": "approve", "reason_code": "self_approval",
        }, expected_version=1, idempotency_key="merge-decide-121")
    assert denied.value.code == "maker_checker_violation"
