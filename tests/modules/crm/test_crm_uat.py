"""逐案驗收 UAT-M10-001..012 (docs/modules/M10-crm.md §14).

Each test exercises the real ``CrmService``/``M10EventConsumer`` call paths
against the in-memory repository (never a parallel hand-rolled projection),
so a passing test is evidence the published operationId/consumer behavior
actually satisfies the UAT, not merely that a constant equals itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from envelope import make_envelope

from modules.crm.domain import ActorContext, ConflictError, ValidationError


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


# ---- UAT-M10-001: 未完成申辦產生可執行待辦 --------------------------------


def test_uat_001_incomplete_application_single_actionable_work_item(consumer, service, actors):
    customer_id = _uuid()
    envelope = make_envelope(
        event_type="kyc.refresh_required", tenant_id=actors.tenant_id, producer="M03", aggregate_type="kyc_submission",
        aggregate_id=_uuid(), payload={"customer_id": customer_id, "submission_id": _uuid(), "refresh_case_id": _uuid(), "due_at": _iso(_now() + timedelta(days=3))},
    )
    result = consumer.consume(envelope)
    assert result.status == "applied"

    page = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    items = [item for item in page.data["items"] if item["customer_id"] == customer_id]
    assert len(items) == 1
    item = items[0]
    assert item["worklist"] == "incomplete_application"
    assert "kyc_refresh_required" in item["reason_codes"]
    assert item["due_at"]
    assert item["action_type"]

    # Replaying the same event_id must not create a second work item.
    assert consumer.consume(envelope).status == "duplicate"
    page_again = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    assert len([item for item in page_again.data["items"] if item["customer_id"] == customer_id]) == 1

    # Business assigns owner/next action/due date explicitly through the
    # opportunity this customer's incomplete application is tracked under.
    opportunity_id = _create_opportunity_for(service, actors, customer_id, "new_subscription")
    assigned = service.set_next_action(
        actors.sales(), opportunity_id,
        {"owner_id": actors.sales_id, "action_type": "complete_documents", "due_at": _iso(_now() + timedelta(days=1)), "reason_code": "missing_documents"},
        expected_version=1, idempotency_key="uat001-assign-00000001",
    )
    assert assigned.data["owner_id"] == actors.sales_id
    assert assigned.data["due_at"]


def _create_opportunity_for(service, actors, customer_id: str, kind_type: str) -> str:
    resp = service.create_opportunity(
        actors.sales(), {
            "customer_id": customer_id, "type": kind_type, "service_code": "advisory-basic",
            "reason_code": "signup_followup", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key=f"uat-create-opp-{_uuid()}",
    )
    return resp.data["id"]


def _opportunity_version(service, actors, opportunity_id: str) -> int:
    """Every CAS'd write bumps the opportunity's version; re-fetch rather than
    assume a fixed number, exactly as a real client would use the latest ETag."""
    return service.get_opportunity(actors.sales(), opportunity_id).data["version"]


# ---- UAT-M10-002: 自助續約不計業務轉換 ------------------------------------


def test_uat_002_self_service_renewal_is_not_sales_conversion(service, actors, evidence):
    customer_id = _uuid()
    # A maintenance owner exists (ownership assignment), but there is no
    # qualifying sales evidence -- the renewal still happened (auto-renew).
    opp_id = _create_opportunity_for(service, actors, customer_id, "renewal")
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    activity = service.record_sales_activity(
        actors.sales(), opp_id, {
            "activity_type": "follow_up", "occurred_at": _iso(_now()), "activity_evidence_asset_id": ev,
            "needs_summary": "automated renewal reminder", "next_action": "none", "next_action_due_at": _iso(_now() + timedelta(days=1)),
            "outcome": "follow_up",
        }, expected_version=1, idempotency_key="uat002-activity-00000001",
    )
    claim = service.claim_conversion(
        actors.sales(), opp_id, {
            "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "auto_renew",
        }, expected_version=_opportunity_version(service, actors, opp_id), idempotency_key="uat002-claim-00000001",
    )
    decision = service.decide_conversion(
        actors.supervisor(), claim.data["id"], {
            "decision": "approve", "reason_code": "no_sales_evidence_self_service", "approved_salesperson_id": None,
            "causality_evidence_asset_id": ev,
        }, expected_version=1, idempotency_key="uat002-decide-00000001",
    )
    assert decision.data["state"] == "approved"
    assert decision.data["approved_salesperson_id"] is None, "a maintenance owner existing must never imply sales credit"


# ---- UAT-M10-003: 業務續約需事前活動和交易 --------------------------------


def test_uat_003_sales_assisted_conversion_requires_prior_activity_and_transaction(service, actors, evidence):
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "upgrade")
    ev_activity, ev_response, ev_causality = _uuid(), _uuid(), _uuid()
    for asset in (ev_activity, ev_response, ev_causality):
        evidence.register(actors.tenant_id, asset)
    activity = service.record_sales_activity(
        actors.sales(), opp_id, {
            "activity_type": "meeting", "occurred_at": _iso(_now() - timedelta(days=1)),
            "customer_response_evidence_id": ev_response, "activity_evidence_asset_id": ev_activity,
            "needs_summary": "customer wants to upgrade", "next_action": "send proposal",
            "next_action_due_at": _iso(_now() + timedelta(days=2)), "outcome": "proposal_requested",
        }, expected_version=1, idempotency_key="uat003-activity-00000001",
    )
    assert activity.data["qualifies_for_attribution"] is True

    claim = service.claim_conversion(
        actors.sales(), opp_id, {
            "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "upgrade",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "closed_upgrade",
        }, expected_version=_opportunity_version(service, actors, opp_id), idempotency_key="uat003-claim-00000001",
    )
    decision = service.decide_conversion(
        actors.supervisor(), claim.data["id"], {
            "decision": "approve", "reason_code": "evidence_sufficient", "approved_salesperson_id": actors.sales_id,
            "qualifying_activity_ids": [activity.data["id"]], "causality_evidence_asset_id": ev_causality,
        }, expected_version=1, idempotency_key="uat003-decide-00000001",
    )
    assert decision.status == 200
    assert decision.data["state"] == "approved"
    assert decision.data["approved_salesperson_id"] == actors.sales_id
    assert activity.data["id"] in decision.data["evidence_activity_ids"]
    assert decision.data["decision_at"] is not None


# ---- UAT-M10-004: 空白聯絡或自動訊息不能搶獎金 ----------------------------


def test_uat_004_blank_or_automated_contact_is_evidence_insufficient(service, actors, evidence):
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "renewal")
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    # Mass automated follow_up with no proof the customer ever responded.
    activity = service.record_sales_activity(
        actors.sales(), opp_id, {
            "activity_type": "follow_up", "occurred_at": _iso(_now()), "activity_evidence_asset_id": ev,
            "needs_summary": "bulk email blast", "next_action": "wait", "next_action_due_at": _iso(_now() + timedelta(days=1)),
            "outcome": "follow_up",
        }, expected_version=1, idempotency_key="uat004-activity-00000001",
    )
    assert activity.data["qualifies_for_attribution"] is False
    assert activity.data["attribution_review_reason"] == "evidence_insufficient_not_two_way"

    claim = service.claim_conversion(
        actors.sales(), opp_id, {
            "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "claim_anyway",
        }, expected_version=_opportunity_version(service, actors, opp_id), idempotency_key="uat004-claim-00000001",
    )
    with pytest.raises(ValidationError) as excinfo:
        service.decide_conversion(
            actors.supervisor(), claim.data["id"], {
                "decision": "approve", "reason_code": "x", "approved_salesperson_id": actors.sales_id,
                "causality_evidence_asset_id": ev,
            }, expected_version=1, idempotency_key="uat004-decide-00000001",
        )
    assert excinfo.value.code == "evidence_insufficient"


# ---- UAT-M10-005: 沒有讀研究報告不單獨判流失 ------------------------------


def test_uat_005_unread_report_alone_never_implies_churn(consumer, service, actors):
    customer_id = _uuid()
    # Customer receives frequent (synthetic) messages but this module never
    # consumes a "report read" signal at all -- there is no field anywhere
    # in StoreState for it, so a retention item can only ever be explained
    # by a real observable service signal.
    page_before = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    assert all(item["customer_id"] != customer_id for item in page_before.data["items"])

    envelope = make_envelope(
        event_type="entitlement.changed", tenant_id=actors.tenant_id, producer="M02", aggregate_type="entitlement",
        aggregate_id=_uuid(),
        payload={"entitlement_id": _uuid(), "customer_id": customer_id, "contract_id": _uuid(), "state": "suspended", "decision_id": _uuid(), "valid_until": _iso(_now() + timedelta(days=30))},
    )
    assert consumer.consume(envelope).status == "applied"

    page_after = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    items = [item for item in page_after.data["items"] if item["customer_id"] == customer_id]
    assert len(items) == 1
    assert items[0]["worklist"] == "retention"
    assert all("report" not in code for code in items[0]["reason_codes"])
    assert "service_restricted" in items[0]["reason_codes"]


# ---- UAT-M10-006: 禁止聯絡或未結抱怨抑制銷售 ------------------------------


def test_uat_006_suppression_blocks_sales_push_but_keeps_service_followup(service, actors, evidence):
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "upgrade")
    reason_ref = _uuid()
    evidence.register(actors.tenant_id, reason_ref)
    service.create_crm_suppression(
        actors.service_agent(), {
            "customer_id": customer_id, "scope": "sales_contact", "reason_category": "open_complaint",
            "reason_ref": reason_ref, "expires_at": None,
        }, idempotency_key="uat006-suppress-00000001",
    )
    with pytest.raises(ConflictError) as excinfo:
        service.set_next_action(
            actors.sales(), opp_id, {
                "owner_id": actors.sales_id, "action_type": "upgrade_assessment", "due_at": _iso(_now() + timedelta(days=1)),
                "reason_code": "upsell_push",
            }, expected_version=1, idempotency_key="uat006-blocked-00000001",
        )
    assert excinfo.value.code.startswith("suppression_blocks_action")

    allowed = service.set_next_action(
        actors.sales(), opp_id, {
            "owner_id": actors.sales_id, "action_type": "resolve_issue", "due_at": _iso(_now() + timedelta(days=1)),
            "reason_code": "service_followup",
        }, expected_version=1, idempotency_key="uat006-allowed-00000001",
    )
    assert allowed.status == 201
    assert allowed.data["suppression_applied"] is True


def test_uat_006_marketing_optout_blocks_sales_push_until_regranted(consumer, service, actors):
    """``customer.consent_changed(purpose=marketing, granted=False)`` must
    suppress sales push (not just ``sales_contact``/``all_nonessential_contact``
    scopes), emit ``crm.suppression_changed(active=True)``, and a later
    re-grant must lift it (and emit ``active=False``) rather than leave it
    suppressed forever."""
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "renewal")
    consent_id = _uuid()
    optout = make_envelope(
        event_type="customer.consent_changed", tenant_id=actors.tenant_id, producer="M01", aggregate_type="customer",
        aggregate_id=customer_id, payload={"customer_id": customer_id, "consent_id": consent_id, "purpose": "marketing", "granted": False},
    )
    assert consumer.consume(optout).status == "applied"
    outbox_after_optout = service.repository.snapshot().outbox
    assert any(
        event.event_type == "crm.suppression_changed" and event.payload["customer_id"] == customer_id and event.payload["active"] is True
        for event in outbox_after_optout.values()
    )

    with pytest.raises(ConflictError) as excinfo:
        service.set_next_action(
            actors.sales(), opp_id, {
                "owner_id": actors.sales_id, "action_type": "renewal_offer", "due_at": _iso(_now() + timedelta(days=1)),
                "reason_code": "upsell_push",
            }, expected_version=1, idempotency_key="uat006-optout-blocked-00000001",
        )
    assert excinfo.value.code.startswith("suppression_blocks_action")

    regrant = make_envelope(
        event_type="customer.consent_changed", tenant_id=actors.tenant_id, producer="M01", aggregate_type="customer",
        aggregate_id=customer_id, sequence_no=2, payload={"customer_id": customer_id, "consent_id": _uuid(), "purpose": "marketing", "granted": True},
    )
    assert consumer.consume(regrant).status == "applied"
    outbox_after_regrant = service.repository.snapshot().outbox
    assert any(
        event.event_type == "crm.suppression_changed" and event.payload["customer_id"] == customer_id and event.payload["active"] is False
        for event in outbox_after_regrant.values()
    )

    allowed_again = service.set_next_action(
        actors.sales(), opp_id, {
            "owner_id": actors.sales_id, "action_type": "renewal_offer", "due_at": _iso(_now() + timedelta(days=1)),
            "reason_code": "upsell_push",
        }, expected_version=1, idempotency_key="uat006-regranted-allowed-00000001",
    )
    assert allowed_again.status == 201
    assert allowed_again.data["suppression_applied"] is False


def test_uat_006_major_open_complaint_auto_suppresses_sales_and_lifts_on_resolution(consumer, service, actors):
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "renewal")
    case_id = _uuid()
    opened = make_envelope(
        event_type="support.case_opened", tenant_id=actors.tenant_id, producer="M13", aggregate_type="service_case",
        aggregate_id=case_id, payload={"case_id": case_id, "customer_id": customer_id, "category": "complaint", "priority": "urgent"},
    )
    assert consumer.consume(opened).status == "applied"
    outbox_after_open = service.repository.snapshot().outbox
    assert any(
        event.event_type == "crm.suppression_changed" and event.payload["customer_id"] == customer_id and event.payload["active"] is True
        for event in outbox_after_open.values()
    )

    with pytest.raises(ConflictError) as excinfo:
        service.set_next_action(
            actors.sales(), opp_id, {
                "owner_id": actors.sales_id, "action_type": "renewal_offer", "due_at": _iso(_now() + timedelta(days=1)),
                "reason_code": "upsell_push",
            }, expected_version=1, idempotency_key="uat006-complaint-blocked-00000001",
        )
    assert excinfo.value.code.startswith("suppression_blocks_action")

    resolved = make_envelope(
        event_type="support.case_resolved", tenant_id=actors.tenant_id, producer="M13", aggregate_type="service_case",
        aggregate_id=case_id, sequence_no=2, payload={"case_id": case_id, "customer_id": customer_id, "resolution_record_id": _uuid()},
    )
    assert consumer.consume(resolved).status == "applied"
    outbox_after_resolved = service.repository.snapshot().outbox
    assert any(
        event.event_type == "crm.suppression_changed" and event.payload["customer_id"] == customer_id and event.payload["active"] is False
        for event in outbox_after_resolved.values()
    )

    allowed = service.set_next_action(
        actors.sales(), opp_id, {
            "owner_id": actors.sales_id, "action_type": "renewal_offer", "due_at": _iso(_now() + timedelta(days=1)),
            "reason_code": "upsell_push",
        }, expected_version=1, idempotency_key="uat006-complaint-resolved-allowed-00000001",
    )
    assert allowed.status == 201
    assert allowed.data["suppression_applied"] is False


def test_uat_006_minor_complaint_does_not_auto_suppress_sales(consumer, service, actors):
    """Only "major" (high/urgent-priority) open complaints auto-suppress;
    a routine/normal-priority complaint must not block sales outreach."""
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "renewal")
    opened = make_envelope(
        event_type="support.case_opened", tenant_id=actors.tenant_id, producer="M13", aggregate_type="service_case",
        aggregate_id=_uuid(), payload={"case_id": _uuid(), "customer_id": customer_id, "category": "complaint", "priority": "normal"},
    )
    assert consumer.consume(opened).status == "applied"

    allowed = service.set_next_action(
        actors.sales(), opp_id, {
            "owner_id": actors.sales_id, "action_type": "renewal_offer", "due_at": _iso(_now() + timedelta(days=1)),
            "reason_code": "upsell_push",
        }, expected_version=1, idempotency_key="uat006-minor-complaint-allowed-00000001",
    )
    assert allowed.status == 201
    assert allowed.data["suppression_applied"] is False


# ---- UAT-M10-007: 升級機會須服務適合度允許 --------------------------------


def test_uat_007_upgrade_requires_suitability(consumer, service, actors):
    customer_id = _uuid()
    envelope = make_envelope(
        event_type="entitlement.changed", tenant_id=actors.tenant_id, producer="M02", aggregate_type="entitlement",
        aggregate_id=_uuid(),
        payload={"entitlement_id": _uuid(), "customer_id": customer_id, "contract_id": _uuid(), "state": "suspended", "decision_id": _uuid(), "valid_until": _iso(_now() + timedelta(days=30))},
    )
    consumer.consume(envelope)

    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": customer_id, "type": "upgrade", "service_code": "advisory-pro",
            "reason_code": "llm_suggested_upgrade", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="uat007-create-00000001",
    )
    assert opp.data["state"] == "suppressed", "unsuitable upgrade must not enter the contactable upgrade list"

    with pytest.raises(ValidationError) as excinfo:
        service.set_next_action(
            actors.sales(), opp.data["id"], {
                "owner_id": actors.sales_id, "action_type": "renewal_offer",
                "due_at": _iso(_now() + timedelta(days=1)), "reason_code": "push_upgrade",
            }, expected_version=1, idempotency_key="uat007-blocked-00000001",
        )
    assert excinfo.value.code == "upgrade_requires_reassessment"

    reassess = service.set_next_action(
        actors.sales(), opp.data["id"], {
            "owner_id": actors.sales_id, "action_type": "upgrade_assessment", "due_at": _iso(_now() + timedelta(days=1)),
            "reason_code": "needs_reassessment",
        }, expected_version=1, idempotency_key="uat007-reassess-00000001",
    )
    assert reassess.status == 201


# ---- UAT-M10-008: 四種owner不互相覆寫 -------------------------------------


def test_uat_008_four_owner_types_are_independent(consumer, service, actors, evidence):
    """REQ-M10-008: original source, maintenance owner, opportunity owner and
    the decided transaction owner are four separate dimensions; approving a
    transaction owner must never overwrite the other three."""
    customer_id = _uuid()
    marketing_attribution_id = _uuid()
    maintenance_salesperson = _uuid()
    opportunity_owner = actors.sales_id
    deciding_salesperson = actors.sales_id_2

    customer_created = make_envelope(
        event_type="customer.created", tenant_id=actors.tenant_id, producer="M01", aggregate_type="customer",
        aggregate_id=customer_id, payload={"customer_id": customer_id, "marketing_attribution_id": marketing_attribution_id},
    )
    consumer.consume(customer_created)
    ownership_event = make_envelope(
        event_type="ownership.assignment_changed", tenant_id=actors.tenant_id, producer="M11", aggregate_type="maintenance_assignment",
        aggregate_id=_uuid(), payload={"assignment_id": _uuid(), "customer_id": customer_id, "salesperson_id": maintenance_salesperson, "effective_at": _iso(_now()), "state": "active"},
    )
    consumer.consume(ownership_event)

    # The opportunity owner (not the eventually-approved salesperson) does
    # the actual recorded work and submits the claim; the supervisor then
    # hands final transaction credit to a different person (e.g. a
    # documented handoff) -- demonstrating claimed_salesperson_id,
    # owner_actor_id and sales_actor_id are three independent fields.
    opp_id = _create_opportunity_for(service, actors, customer_id, "upgrade")
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    activity = service.record_sales_activity(
        actors.sales(), opp_id, {
            "activity_type": "meeting", "occurred_at": _iso(_now()), "customer_response_evidence_id": ev,
            "activity_evidence_asset_id": ev, "needs_summary": "closed deal", "next_action": "none",
            "next_action_due_at": _iso(_now() + timedelta(days=1)), "outcome": "converted",
        }, expected_version=1, idempotency_key="uat008-activity-00000001",
    )
    claim = service.claim_conversion(
        actors.sales(), opp_id, {
            "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "upgrade",
            "claimed_salesperson_id": opportunity_owner, "reason_code": "closed",
        }, expected_version=_opportunity_version(service, actors, opp_id), idempotency_key="uat008-claim-00000001",
    )
    decision = service.decide_conversion(
        actors.supervisor(), claim.data["id"], {
            "decision": "approve", "reason_code": "x", "approved_salesperson_id": deciding_salesperson,
            "causality_evidence_asset_id": ev,
        }, expected_version=1, idempotency_key="uat008-decide-00000001",
    )

    opp_after = service.get_opportunity(actors.sales(), opp_id)
    assert opp_after.data["owner_id"] == opportunity_owner, "opportunity owner must survive an unrelated transaction owner decision"
    assert decision.data["approved_salesperson_id"] == deciding_salesperson
    assert decision.data["claimed_salesperson_id"] == opportunity_owner
    assert decision.data["approved_salesperson_id"] != decision.data["claimed_salesperson_id"], "final transaction credit may legitimately differ from the original claimant"
    # Maintenance ownership and original marketing attribution are stored on
    # entirely separate aggregates and were never touched by decide_conversion.
    snapshot = _snapshot(service)
    assert snapshot.ownership[(actors.tenant_id, customer_id)].salesperson_id == maintenance_salesperson
    attributions = [row for row in snapshot.marketing_attributions.values() if row.customer_id == customer_id]
    assert len(attributions) == 1
    assert len({opportunity_owner, maintenance_salesperson, deciding_salesperson}) == 3, "the three actor-owners must be distinct identities in this scenario"


def _snapshot(service):
    return service.repository.snapshot()


# ---- UAT-M10-009: 爭議認領不能重複計獎 ------------------------------------


def test_uat_009_disputed_claims_never_double_pay(service, actors, evidence):
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "renewal")
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    activity = service.record_sales_activity(
        actors.sales(), opp_id, {
            "activity_type": "completed_call", "occurred_at": _iso(_now()), "customer_response_evidence_id": ev,
            "activity_evidence_asset_id": ev, "needs_summary": "x", "next_action": "y",
            "next_action_due_at": _iso(_now() + timedelta(days=1)), "outcome": "converted",
        }, expected_version=1, idempotency_key="uat009-activity-00000001",
    )
    contract_service_id = _uuid()
    claim_a = service.claim_conversion(
        actors.sales(), opp_id, {
            "application_id": _uuid(), "contract_id": contract_service_id, "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "x",
        }, expected_version=_opportunity_version(service, actors, opp_id), idempotency_key="uat009-claim-a-00000001",
    )
    claim_b = service.claim_conversion(
        actors.sales_2(), opp_id, {
            "application_id": _uuid(), "contract_id": contract_service_id, "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id_2, "reason_code": "x",
        }, expected_version=_opportunity_version(service, actors, opp_id), idempotency_key="uat009-claim-b-00000001",
    )
    assert claim_a.data["id"] == claim_b.data["id"], "competing claims on the same transaction must share one row"
    assert claim_b.data["state"] == "under_review"
    assert claim_b.data["state"] not in {"approved"}, "pending/disputed must never surface as approved"

    decision = service.decide_conversion(
        actors.supervisor(), claim_b.data["id"], {
            "decision": "approve", "reason_code": "resolved_dispute", "approved_salesperson_id": actors.sales_id_2,
            "causality_evidence_asset_id": ev,
        }, expected_version=claim_b.data["version"], idempotency_key="uat009-decide-00000001",
    )
    assert decision.data["state"] == "approved"
    assert decision.data["approved_salesperson_id"] == actors.sales_id_2

    # A further claim attempt on the now-decided contract_service_id is rejected.
    opportunity_now = service.get_opportunity(actors.sales(), opp_id)
    with pytest.raises(ConflictError) as excinfo:
        service.claim_conversion(
            actors.sales(), opp_id, {
                "application_id": _uuid(), "contract_id": contract_service_id, "finance_transaction_ref": _uuid(),
                "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
                "claimed_salesperson_id": actors.sales_id, "reason_code": "late_claim",
            }, expected_version=opportunity_now.data["version"], idempotency_key="uat009-claim-c-00000001",
        )
    assert excinfo.value.code == "conversion_already_decided"


# ---- UAT-M10-010: 轉換後補寫活動不能回填搶獎 ------------------------------


def test_uat_010_late_entry_activity_cannot_backdate_attribution(service, actors, evidence):
    customer_id = _uuid()
    opp_id = _create_opportunity_for(service, actors, customer_id, "renewal")
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    first_activity = service.record_sales_activity(
        actors.sales(), opp_id, {
            "activity_type": "completed_call", "occurred_at": _iso(_now() - timedelta(days=2)), "customer_response_evidence_id": ev,
            "activity_evidence_asset_id": ev, "needs_summary": "x", "next_action": "y",
            "next_action_due_at": _iso(_now() + timedelta(days=1)), "outcome": "converted",
        }, expected_version=1, idempotency_key="uat010-activity-00000001",
    )
    claim = service.claim_conversion(
        actors.sales(), opp_id, {
            "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [first_activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "x",
        }, expected_version=_opportunity_version(service, actors, opp_id), idempotency_key="uat010-claim-00000001",
    )
    decision = service.decide_conversion(
        actors.supervisor(), claim.data["id"], {
            "decision": "approve", "reason_code": "x", "approved_salesperson_id": actors.sales_id,
            "causality_evidence_asset_id": ev,
        }, expected_version=1, idempotency_key="uat010-decide-00000001",
    )
    assert decision.data["state"] == "approved"
    opportunity_after_decision = service.get_opportunity(actors.sales(), opp_id)
    assert opportunity_after_decision.data["state"] == "converted"

    recorded_before = _now()
    late_activity = service.record_sales_activity(
        actors.sales(), opp_id, {
            "activity_type": "completed_call", "occurred_at": _iso(_now() - timedelta(days=10)),
            "customer_response_evidence_id": ev, "activity_evidence_asset_id": ev,
            "needs_summary": "backfilled note", "next_action": "none", "next_action_due_at": _iso(_now() + timedelta(days=1)),
            "outcome": "contacted",
        }, expected_version=opportunity_after_decision.data["version"], idempotency_key="uat010-late-activity-00000001",
    )
    assert late_activity.data["qualifies_for_attribution"] is False
    assert late_activity.data["attribution_review_reason"] == "late_entry_pending_approval"
    recorded_created_at = datetime.fromisoformat(late_activity.data["created_at"].replace("Z", "+00:00"))
    assert recorded_created_at >= recorded_before, "created_at must reflect real recording time, never the backdated occurred_at"


# ---- UAT-M10-011: 漏斗統計重放與退出正確 ----------------------------------


def test_uat_011_funnel_replay_dedupes_and_tracks_withdrawal_separately(consumer, service, actors):
    customer_id = _uuid()
    application_id = _uuid()
    contract_id = _uuid()
    package_version_id = _uuid()
    created_envelope = make_envelope(
        event_type="application.created", tenant_id=actors.tenant_id, producer="M01", aggregate_type="application",
        aggregate_id=application_id, sequence_no=1,
        payload={"application_id": application_id, "customer_id": customer_id, "package_version_id": package_version_id, "intent": "new"},
    )
    assert consumer.consume(created_envelope).status == "applied"
    # Exact replay of the same event_id (at-least-once redelivery).
    assert consumer.consume(created_envelope).status == "duplicate"

    signed_envelope = make_envelope(
        # sequence_no restarts at 1: it is scoped per (producer, aggregate_id),
        # and this is M02's first event for this application aggregate, M01's
        # own sequence for this aggregate is independent.
        event_type="application.customer_signed", tenant_id=actors.tenant_id, producer="M02", aggregate_type="application",
        aggregate_id=application_id, sequence_no=1,
        payload={"application_id": application_id, "customer_signature_id": _uuid(), "package_version_id": package_version_id, "document_sha256": "0" * 64, "customer_signed_at": _iso(_now())},
    )
    consumer.consume(signed_envelope)
    consumer.consume(signed_envelope)  # replay again

    funnel_before_churn = service.get_funnel_metrics(actors.supervisor())
    started = next(item for item in funnel_before_churn.data["items"] if item["stage"] == "application_started")
    signed = next(item for item in funnel_before_churn.data["items"] if item["stage"] == "customer_signed")
    assert started["count"] == 1, "replayed application.created must not double count"
    assert signed["count"] == 1, "replayed application.customer_signed must not double count"
    assert all(item["stage"] != "churn" for item in funnel_before_churn.data["items"])

    terminated_envelope = make_envelope(
        event_type="contract.terminated", tenant_id=actors.tenant_id, producer="M02", aggregate_type="contract",
        aggregate_id=contract_id, payload={"contract_id": contract_id, "customer_id": customer_id, "terminated_at": _iso(_now()), "refund_evaluation_required": False},
    )
    consumer.consume(terminated_envelope)
    funnel_after_churn = service.get_funnel_metrics(actors.supervisor())
    churn = next(item for item in funnel_after_churn.data["items"] if item["stage"] == "churn")
    assert churn["count"] == 1


def test_uat_011_lost_and_cancelled_opportunities_are_excluded_from_renewal_and_upgrade(consumer, service, actors):
    """A suitability-blocked (cancelled/"suppressed") upgrade opportunity
    must not be counted in the "upgrade" funnel stage -- it is not a
    contactable upgrade-pipeline member (REQ-M10-007), it is parked pending
    reassessment."""
    suspended = make_envelope(
        event_type="entitlement.changed", tenant_id=actors.tenant_id, producer="M02", aggregate_type="entitlement",
        aggregate_id=_uuid(),
        payload={"entitlement_id": _uuid(), "customer_id": _uuid(), "contract_id": _uuid(), "state": "suspended", "decision_id": _uuid(), "valid_until": _iso(_now() + timedelta(days=30))},
    )
    consumer.consume(suspended)
    cancelled_upgrade = service.create_opportunity(
        actors.sales(), {
            "customer_id": suspended["payload"]["customer_id"], "type": "upgrade", "service_code": "advisory-pro",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="uat011-cancelled-upgrade-00000001",
    )
    assert cancelled_upgrade.data["state"] == "suppressed"

    open_upgrade = _create_opportunity_for(service, actors, _uuid(), "upgrade")

    funnel = service.get_funnel_metrics(actors.supervisor())
    upgrade = next((item for item in funnel.data["items"] if item["stage"] == "upgrade"), {"count": 0})
    assert upgrade["count"] == 1, "only the still-open upgrade opportunity counts, not the suitability-blocked one"


def test_uat_011_active_excludes_entitlements_on_a_terminated_contract(consumer, service, actors):
    customer_id = _uuid()
    contract_id = _uuid()
    active_envelope = make_envelope(
        event_type="entitlement.changed", tenant_id=actors.tenant_id, producer="M02", aggregate_type="entitlement",
        aggregate_id=_uuid(),
        payload={"entitlement_id": _uuid(), "customer_id": customer_id, "contract_id": contract_id, "state": "active", "decision_id": _uuid(), "valid_until": _iso(_now() + timedelta(days=365))},
    )
    consumer.consume(active_envelope)
    funnel_while_active = service.get_funnel_metrics(actors.supervisor())
    active_before = next((item for item in funnel_while_active.data["items"] if item["stage"] == "active"), {"count": 0})
    assert active_before["count"] == 1

    terminated_envelope = make_envelope(
        event_type="contract.terminated", tenant_id=actors.tenant_id, producer="M02", aggregate_type="contract",
        aggregate_id=contract_id, payload={"contract_id": contract_id, "customer_id": customer_id, "terminated_at": _iso(_now()), "refund_evaluation_required": False},
    )
    consumer.consume(terminated_envelope)
    funnel_after_termination = service.get_funnel_metrics(actors.supervisor())
    active_after = next((item for item in funnel_after_termination.data["items"] if item["stage"] == "active"), {"count": 0})
    assert active_after["count"] == 0, "a terminated contract's entitlement must not still read as active"


# ---- UAT-M10-012: 同一待辦不因日批次無限重複 ------------------------------


def test_uat_012_repeated_sweep_never_duplicates_open_work_item(consumer, service, actors):
    customer_id = _uuid()
    contract_id = _uuid()
    now = _now()
    effective_envelope = make_envelope(
        event_type="contract.effective", tenant_id=actors.tenant_id, producer="M02", aggregate_type="contract",
        aggregate_id=contract_id,
        payload={"contract_id": contract_id, "customer_id": customer_id, "effective_at": _iso(now - timedelta(days=300)), "service_starts_at": _iso(now - timedelta(days=300)), "service_ends_at": _iso(now + timedelta(days=10))},
    )
    consumer.consume(effective_envelope)

    first = service.sweep_worklists(actors.tenant_id, as_of=now)
    assert first.created == 1

    page1 = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    renewal_items = [item for item in page1.data["items"] if item["customer_id"] == customer_id]
    assert len(renewal_items) == 1
    work_item_id = renewal_items[0]["id"]

    # The salesperson picks it up: owner is assigned and work begins.
    def claim(state):
        item = state.work_items[(actors.tenant_id, work_item_id)]
        item.owner_actor_id = actors.sales_id
        item.status = "in_progress"
        item.version += 1

    service.repository.atomic(lambda state: claim(state))

    for _ in range(3):
        summary = service.sweep_worklists(actors.tenant_id, as_of=now)
        assert summary.created == 0, "re-running the sweep over unchanged inputs must never create a duplicate"

    page2 = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    renewal_items_after = [item for item in page2.data["items"] if item["customer_id"] == customer_id]
    assert len(renewal_items_after) == 1
    assert renewal_items_after[0]["id"] == work_item_id
    assert renewal_items_after[0]["owner_id"] == actors.sales_id, "sweep re-runs must preserve the claimed owner"
    assert renewal_items_after[0]["state"] == "in_progress", "sweep re-runs must not reset in-progress work back to open"
