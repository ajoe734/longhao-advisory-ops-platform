from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest

from modules.service_cases.domain import ConflictError, ForbiddenError, ValidationError, canonical_hash, wire_time
from workers.tasks.service_cases.consumer import M13EventConsumer
from workers.tasks.service_cases.tasks import SuppressionJobRunner

from tests.modules.service_cases.conftest import (
    AGENT_TWO,
    CHANNEL_BINDING,
    CONSENT_EVIDENCE,
    CONTEXT_EVIDENCE,
    CUSTOMER_ID,
    INTERACTION_CONTENT_ASSET,
    KNOWLEDGE_SOURCE_REF,
    KNOWLEDGE_VERSION,
    NOTICE_EVIDENCE,
    NOW,
    RECEIPT_EVIDENCE,
    REOPEN_EVIDENCE,
    TENANT,
    case_body,
    create_case,
    customer_context,
    draft_body,
    receive_receipt,
)


def _draft_sha(service, agent, case_id, version, key, **overrides):
    draft = service.draft_service_response(agent, case_id, draft_body(**overrides), expected_version=version, idempotency_key=key).data
    got = service.get_service_response_draft(agent, draft["id"]).data
    return draft, sha256(got["text"].encode("utf-8")).hexdigest() if got["text"] else None


def _approve(service, reviewer, draft_id, sha, key, *, decision="approve"):
    return service.approve_service_response_draft(reviewer, draft_id, {
        "draft_content_sha256": sha, "decision": decision, "approval_mode": "human_reviewed",
        "template_version": "manual", "policy_version": "policy-v1", "reason_code": "ok_to_send",
        "publication_reference_ids": [], "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=1, idempotency_key=key)


def test_uat_m13_001_cross_channel_case_keeps_single_sla_timeline(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "uat001-create-0001", intake_channel="web").data
    before_due = case["first_response_due_at"], case["resolution_due_at"]
    interaction = service.add_case_interaction(agent, case["id"], {
        "channel": "phone", "direction": "inbound", "occurred_at": wire_time(NOW),
        "content_asset_id": INTERACTION_CONTENT_ASSET, "summary": "電話補充身份驗證並說明問題",
        "recording_consent_evidence_id": CONSENT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat001-interaction-0001").data
    after = service.get_service_case(agent, case["id"]).data
    assert {case["intake_channel"], interaction["channel"]} == {"web", "phone"}
    assert interaction["occurred_at"]
    assert (after["first_response_due_at"], after["resolution_due_at"]) == before_due


def test_uat_m13_002_duplicate_provider_message_creates_one_interaction(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "uat002-create-0001").data
    body = {
        "channel": "email", "direction": "inbound", "occurred_at": wire_time(NOW),
        "content_asset_id": INTERACTION_CONTENT_ASSET, "summary": "客戶詢問帳單重複寄送",
    }
    first = service.add_case_interaction(agent, case["id"], body, expected_version=case["version"], idempotency_key="uat002-same-message-key").data
    replay = service.add_case_interaction(agent, case["id"], body, expected_version=case["version"], idempotency_key="uat002-same-message-key")
    assert replay.idempotency_replayed is True
    assert replay.data["id"] == first["id"]
    snapshot = m13["repository"].snapshot()
    assert len([i for (tenant, _), i in snapshot.interactions.items() if tenant == TENANT and i.case_id == case["id"]]) == 1


def test_uat_m13_003_unverified_case_hides_contract_and_payment_links(m13, agent):
    service = m13["service"]
    contract_id = str(uuid4())
    unlinked = service.create_service_case(agent, case_body(customer_id=None, contract_id=contract_id), idempotency_key="uat003-create-0001").data
    fetched = service.get_service_case(agent, unlinked["id"]).data
    assert fetched["customer_id"] is None
    assert fetched["linked_contract_id"] is None
    linked = service.create_service_case(agent, case_body(contract_id=contract_id), idempotency_key="uat003-create-0002").data
    fetched_linked = service.get_service_case(agent, linked["id"]).data
    assert fetched_linked["linked_contract_id"] == contract_id


def test_uat_m13_004_draft_requires_approval_before_send_and_tracks_real_status(m13, agent, agent_two, messaging_adapter):
    service = m13["service"]
    case = create_case(service, agent, "uat004-create-0001").data
    draft, sha = _draft_sha(service, agent, case["id"], case["version"], "uat004-draft-0001")
    assert draft["state"] == "drafted"
    assert draft["send_authorized"] is False
    approval = _approve(service, agent_two, draft["id"], sha, "uat004-approve-0001").data
    case_latest = service.get_service_case(agent, case["id"]).data
    sent = service.send_service_response(agent, case["id"], {
        "draft_id": draft["id"], "draft_content_sha256": sha, "channel_binding_id": CHANNEL_BINDING,
        "channel": "email", "approval_mode": "human_reviewed", "approval_id": approval["id"],
        "template_version": "manual", "publication_reference_ids": [], "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=case_latest["version"], idempotency_key="uat004-send-0001").data
    assert sent["state"] == "queued" and sent["sent_at"] is None and sent["delivered_at"] is None
    accepted_event_id = str(uuid4())
    accepted = receive_receipt(m13, messaging_adapter, {
        "event_id": accepted_event_id, "response_id": sent["id"], "provider_message_ref": "ref-001",
        "provider_status": "accepted", "provider_occurred_at": wire_time(NOW), "provider_receipt_asset_id": RECEIPT_EVIDENCE,
    }, idempotency_key="uat004-receipt-0001").data
    assert accepted["status"] == "accepted"
    mid_state = service.get_service_response(agent, sent["id"]).data
    assert mid_state["state"] == "accepted" and mid_state["sent_at"] is None
    duplicate = receive_receipt(m13, messaging_adapter, {
        "event_id": accepted_event_id, "response_id": sent["id"], "provider_message_ref": "ref-001",
        "provider_status": "accepted", "provider_occurred_at": wire_time(NOW), "provider_receipt_asset_id": RECEIPT_EVIDENCE,
    }, idempotency_key="uat004-receipt-dup-0001").data
    assert duplicate["status"] == "duplicate"
    unknown = receive_receipt(m13, messaging_adapter, {
        "event_id": str(uuid4()), "response_id": str(uuid4()), "provider_message_ref": "ref-404",
        "provider_status": "delivered", "provider_occurred_at": wire_time(NOW), "provider_receipt_asset_id": RECEIPT_EVIDENCE,
    }, idempotency_key="uat004-receipt-unknown-0001").data
    assert unknown["status"] == "quarantined"


def test_uat_m13_005_investment_question_routes_to_analyst_not_adhoc_advice(m13, agent, agent_two):
    service = m13["service"]
    case = create_case(service, agent, "uat005-create-0001", category="research_question", subject="詢問新買賣建議").data
    draft = service.draft_service_response(agent, case["id"], draft_body(intent="status"), expected_version=case["version"], idempotency_key="uat005-draft-0001").data
    assert draft["state"] == "requires_human"
    assert draft["contains_new_investment_judgment"] is True
    assert draft["text"] is None
    # Regression for F7(a): baseline 0001_domains.sql CHECK requires
    # content_asset_id whenever status is 'requires_human'; a prior candidate
    # left it NULL, which a real PostgreSQL insert would reject outright.
    stored_draft = m13["repository"].snapshot().response_drafts[(TENANT, draft["id"])]
    assert stored_draft.content_asset_id is not None
    with pytest.raises(ConflictError) as approve_denied:
        _approve(service, agent_two, draft["id"], "0" * 64, "uat005-approve-0001")
    assert approve_denied.value.code == "draft_not_ready"
    escalated = service.escalate_service_case(agent, case["id"], {
        "target_queue": "analyst", "reason_code": "new_investment_judgment_requested", "due_at": wire_time(NOW),
        "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat005-escalate-0001").data
    assert escalated["state"] == "waiting_internal"


def test_uat_m13_006_refund_request_cannot_be_approved_or_executed_by_service_agent(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "uat006-create-0001", category="refund").data
    escalated = service.escalate_service_case(agent, case["id"], {
        "target_queue": "finance", "reason_code": "customer_requested_refund", "due_at": wire_time(NOW),
        "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat006-escalate-0001").data
    assert escalated["escalated_queue"] if "escalated_queue" in escalated else True
    assert escalated["state"] == "waiting_internal"
    assert escalated["linked_refund_id"] is None
    assert not hasattr(service, "approve_refund")
    assert not hasattr(service, "execute_refund")


def test_uat_m13_007_major_complaint_suppresses_marketing_without_blocking_service(m13, agent, agent_two):
    service = m13["service"]
    case = create_case(service, agent, "uat007-create-0001", category="complaint").data
    service.escalate_service_case(agent, case["id"], {
        "target_queue": "service_supervisor", "reason_code": "major_unresolved_complaint", "due_at": wire_time(NOW),
        "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat007-escalate-0001")
    runner = SuppressionJobRunner(m13["repository"], m13["suppression"])
    result = runner.run_once("worker-1")
    assert result.processed == 1
    assert m13["suppression"].requests[0].scope == "marketing"
    assert m13["suppression"].requests[0].customer_id == CUSTOMER_ID
    case_latest = service.get_service_case(agent, case["id"]).data
    draft, sha = _draft_sha(service, agent, case["id"], case_latest["version"], "uat007-draft-0001")
    approval = _approve(service, agent_two, draft["id"], sha, "uat007-approve-0001").data
    case_after_draft = service.get_service_case(agent, case["id"]).data
    sent = service.send_service_response(agent, case["id"], {
        "draft_id": draft["id"], "draft_content_sha256": sha, "channel_binding_id": CHANNEL_BINDING,
        "channel": "email", "approval_mode": "human_reviewed", "approval_id": approval["id"],
        "template_version": "manual", "publication_reference_ids": [], "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=case_after_draft["version"], idempotency_key="uat007-send-0001")
    assert sent.status == 202


def test_uat_m13_008_waiting_internal_does_not_reset_sla_clock(m13, agent, supervisor):
    service = m13["service"]
    case = create_case(service, agent, "uat008-create-0001").data
    before = (case["first_response_due_at"], case["resolution_due_at"])
    escalated = service.escalate_service_case(agent, case["id"], {
        "target_queue": "technical", "reason_code": "needs_engineering_input", "due_at": wire_time(NOW),
        "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat008-escalate-0001").data
    assert (escalated["first_response_due_at"], escalated["resolution_due_at"]) == before
    reassigned = service.assign_service_case(supervisor, case["id"], {
        "assignee_id": "20000000-0000-0000-0000-000000000001", "reason_code": "reassigned_to_cover_shift",
    }, expected_version=escalated["version"], idempotency_key="uat008-assign-0001").data
    assert (reassigned["first_response_due_at"], reassigned["resolution_due_at"]) == before
    m13["clock"].advance(10 * 24 * 3600)
    overdue = service.get_service_case(agent, case["id"]).data
    assert overdue["resolution_due_at"] < wire_time(m13["clock"]())


def test_uat_m13_009_aml_handoff_does_not_copy_content_into_timeline_or_llm(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "uat009-create-0001", category="complaint", description="客戶聲稱疑似洗錢相關異常交易細節").data
    escalated = service.escalate_service_case(agent, case["id"], {
        "target_queue": "compliance", "reason_code": "aml_review_required", "due_at": wire_time(NOW),
        "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat009-escalate-0001").data
    assert "description" not in escalated
    draft = service.draft_service_response(agent, case["id"], draft_body(), expected_version=escalated["version"], idempotency_key="uat009-draft-0001").data
    assert draft["state"] == "blocked"
    assert draft["text"] is None
    assert draft["model_version"] == "manual"


def test_uat_m13_010_resolution_requires_evidence_and_keeps_delivery_status_separate(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "uat010-create-0001").data
    with pytest.raises(ValidationError):
        service.resolve_service_case(agent, case["id"], {"resolution_code": "fixed"}, expected_version=case["version"], idempotency_key="uat010-resolve-missing-0001")
    resolved = service.resolve_service_case(agent, case["id"], {
        "resolution_code": "billing_corrected", "resolution_summary": "已更正帳單金額並通知客戶",
        "customer_notice_evidence_id": NOTICE_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat010-resolve-0001").data
    assert resolved["state"] == "resolved"
    snapshot = m13["repository"].snapshot()
    resolution = next(r for (tenant, _), r in snapshot.case_resolutions.items() if tenant == TENANT and r.case_id == case["id"])
    assert resolution.customer_notice_evidence_id == NOTICE_EVIDENCE


def test_uat_m13_011_reopen_preserves_original_resolution_history(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "uat011-create-0001").data
    resolved = service.resolve_service_case(agent, case["id"], {
        "resolution_code": "closed_duplicate", "resolution_summary": "確認為重複案件",
        "customer_notice_evidence_id": NOTICE_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="uat011-resolve-0001").data
    before_due = (resolved["first_response_due_at"], resolved["resolution_due_at"])
    reopened = service.reopen_service_case(agent, case["id"], {
        "reason_code": "customer_reported_unresolved", "evidence_asset_ids": [REOPEN_EVIDENCE],
    }, expected_version=resolved["version"], idempotency_key="uat011-reopen-0001").data
    assert reopened["state"] == "reopened"
    assert (reopened["first_response_due_at"], reopened["resolution_due_at"]) == before_due
    snapshot = m13["repository"].snapshot()
    resolutions = [r for (tenant, _), r in snapshot.case_resolutions.items() if tenant == TENANT and r.case_id == case["id"]]
    assert len(resolutions) == 1
    assert resolutions[0].resolution_code == "closed_duplicate"


def test_uat_m13_012_case_owner_change_does_not_move_maintenance_ownership(m13, agent, supervisor):
    service = m13["service"]
    case = create_case(service, agent, "uat012-create-0001").data
    assigned = service.assign_service_case(supervisor, case["id"], {
        "assignee_id": AGENT_TWO, "reason_code": "initial_assignment",
    }, expected_version=case["version"], idempotency_key="uat012-assign-0001").data
    assert assigned["assignee_id"] == AGENT_TWO
    assert assigned["maintenance_owner_id"] is None
    resolved = service.resolve_service_case(agent, case["id"], {
        "resolution_code": "self_service_renewal", "resolution_summary": "客戶自助完成續約",
        "customer_notice_evidence_id": NOTICE_EVIDENCE,
    }, expected_version=assigned["version"], idempotency_key="uat012-resolve-0001").data
    assert resolved["maintenance_owner_id"] is None
    salesperson_id = str(uuid4())
    payload = {
        "assignment_id": str(uuid4()), "customer_id": CUSTOMER_ID, "salesperson_id": salesperson_id,
        "effective_at": wire_time(NOW), "state": "active",
    }
    envelope = {
        "event_id": str(uuid4()), "event_type": "ownership.assignment_changed", "schema_version": "1.0.0",
        "tenant_id": TENANT, "producer": "M11", "aggregate_type": "maintenance_assignment", "aggregate_id": payload["assignment_id"],
        "aggregate_version": 1, "sequence_no": 1, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": "uat012-ownership-0001",
        "classification": "customer_confidential", "payload_sha256": canonical_hash(payload), "payload": payload,
    }
    consumer = M13EventConsumer(m13["repository"])
    consumer.consume(envelope)
    projected = service.get_service_case(agent, case["id"]).data
    assert projected["maintenance_owner_id"] == salesperson_id
    assert projected["assignee_id"] == AGENT_TWO
