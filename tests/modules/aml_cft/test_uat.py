from __future__ import annotations

from datetime import timedelta

import pytest

from modules.aml_cft.automation import CaseSummaryAutomationService, LocalCaseSummaryAssistant
from modules.aml_cft.domain import ActorContext, ForbiddenError

from conftest import (
    AI_SUMMARY_ACTOR,
    APPLICATION_A,
    EVIDENCE_1,
    EVIDENCE_2,
    TENANT,
    declaration_body,
    screening_callback,
    screening_request,
)


def test_uat_m04_001_possible_match_creates_restricted_case_and_blocks_clearance(m04, officer, company_acceptor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    callback = service.receive_aml_screening(
        adapter,
        screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000001"),
        idempotency_key="callback-001",
    )
    assert callback.data["status"] == "accepted"
    cases = service.list_aml_cases(officer).data["items"]
    assert len(cases) == 1 and cases[0]["state"] == "open"
    clearance = service.get_business_clearance(company_acceptor, APPLICATION_A).data
    assert clearance["decision"] == "pending"


def test_uat_m04_002_ai_summary_principal_cannot_decide(m04, officer, supervisor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-002").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000002"), idempotency_key="callback-002")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    before = service.get_aml_case(officer, case_id).data

    ai_principal = ActorContext(TENANT, AI_SUMMARY_ACTOR, frozenset(), scopes=frozenset({"m04:case_summary"}), principal_type="service")
    automation = CaseSummaryAutomationService(m04["repository"], LocalCaseSummaryAssistant())
    run = automation.summarize(ai_principal, case_id)
    assert run.status == "untrusted_draft"

    with pytest.raises(ForbiddenError) as denied:
        service.decide_aml_case(
            ai_principal, case_id,
            {"decision": "approve", "risk_level": "low", "reason_code": "ai_suggested", "restricted_evidence_ids": [EVIDENCE_1]},
            expected_version=before["version"], idempotency_key="decide-ai-002",
        )
    assert denied.value.code == "forbidden"
    after = service.get_aml_case(officer, case_id).data
    assert after == before
    denied_audits = [event for event in m04["repository"].snapshot().audit_events if event.result == "rejected"]
    assert any(event.actor_id == AI_SUMMARY_ACTOR for event in denied_audits)


def test_uat_m04_003_general_ops_cannot_read_case_or_filing_detail(m04, officer, sales, company_acceptor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-003").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000003"), idempotency_key="callback-003")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]

    with pytest.raises(ForbiddenError):
        service.get_aml_case(sales, case_id)
    with pytest.raises(ForbiddenError):
        service.list_aml_cases(sales)

    clearance = service.get_business_clearance(company_acceptor, APPLICATION_A).data
    assert set(clearance) == {
        "id", "tenant_id", "version", "created_at", "updated_at", "allowed_actions",
        "application_id", "customer_id", "decision", "decided_at", "expires_at", "policy_version",
    }
    assert clearance["decision"] == "pending"


def test_uat_m04_004_provider_timeout_is_not_treated_as_clear(m04, officer, company_acceptor):
    from workers.tasks.aml_cft.tasks import ScreeningDispatchWorker

    service = m04["service"]
    m04["provider"].failure_mode = "timeout"
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-004").data
    worker = ScreeningDispatchWorker(m04["repository"], m04["provider"])
    result = worker.run_once("m04-drill-worker")
    assert result.manual_required == 1 and result.dispatched == 0
    stored = m04["repository"].snapshot().screenings[(TENANT, screening["id"])]
    assert stored.state == "error" and stored.manual_required is True
    clearance = service.get_business_clearance(company_acceptor, APPLICATION_A).data
    assert clearance["decision"] != "approved"
    failures = m04["repository"].snapshot()
    assert len(failures.jobs) == 1
    assert next(iter(failures.jobs.values())).status == "dead_letter"


def test_uat_m04_005_false_positive_release_requires_independent_checker(m04, officer, supervisor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-005").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000005"), idempotency_key="callback-005")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    case = service.get_aml_case(officer, case_id).data
    action = service.add_aml_case_action(
        officer, case_id,
        {"action": "record_investigation", "reason_code": "false_positive_name_match", "restricted_note_asset_id": EVIDENCE_1},
        expected_version=case["version"], idempotency_key="action-005",
    ).data

    maker_as_supervisor = ActorContext(TENANT, officer.actor_id, frozenset({"aml_supervisor"}), principal_type="staff")
    with pytest.raises(ForbiddenError) as self_review:
        service.decide_aml_case(
            maker_as_supervisor, case_id,
            {"decision": "approve", "risk_level": "low", "reason_code": "self_cleared", "restricted_evidence_ids": [EVIDENCE_1]},
            expected_version=action["version"], idempotency_key="decide-self-005",
        )
    assert self_review.value.code == "maker_checker_violation"

    decided = service.decide_aml_case(
        supervisor, case_id,
        {"decision": "approve", "risk_level": "low", "reason_code": "independent_review_confirmed", "restricted_evidence_ids": [EVIDENCE_1]},
        expected_version=action["version"], idempotency_key="decide-005",
    ).data
    assert decided["decision"] == "approved"
    snapshot = m04["repository"].snapshot()
    assert len([a for (_, _), a in snapshot.case_actions.items() if a.case_id == case_id]) == 1
    assert len(snapshot.clearance_by_application[(TENANT, APPLICATION_A)]) == 2


def test_uat_m04_006_list_version_refresh_blocks_and_keeps_v1_screening(m04, officer):
    service = m04["service"]
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    first = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-006a").data
    service.receive_aml_screening(adapter, screening_callback(first["id"], "clear", event_id="80000000-0000-0000-0000-000000000061", list_version="list-v1"), idempotency_key="callback-006a")
    v1_snapshot = m04["repository"].snapshot().screenings[(TENANT, first["id"])]
    assert v1_snapshot.state == "clear" and v1_snapshot.list_version == "list-v1"

    second = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-006b").data
    service.receive_aml_screening(adapter, screening_callback(second["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000062", list_version="list-v2"), idempotency_key="callback-006b")

    after = m04["repository"].snapshot()
    assert after.screenings[(TENANT, first["id"])].state == "clear"  # v1 record is never overwritten
    assert after.screenings[(TENANT, first["id"])].list_version == "list-v1"
    latest_clearance_id = after.clearance_by_application[(TENANT, APPLICATION_A)][-1]
    assert after.clearances[(TENANT, latest_clearance_id)].decision == "pending"


def test_uat_m04_007_declaration_signature_cannot_substitute_screening(m04, officer):
    service = m04["service"]
    service.submit_aml_declaration(officer, APPLICATION_A, declaration_body(), expected_version=None, idempotency_key="decl-007")
    clearance = service.get_business_clearance(
        ActorContext(TENANT, "20000000-0000-0000-0000-000000000004", frozenset({"company_acceptor"}), principal_type="staff"),
        APPLICATION_A,
    ).data
    assert clearance["decision"] == "pending"
    assert clearance["decided_at"] is None


def test_uat_m04_008_restricted_event_only_carries_safe_summary(m04, officer):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-008").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000008"), idempotency_key="callback-008")
    snapshot = m04["repository"].snapshot()
    clearance_events = [event for event in snapshot.outbox.values() if event.event_type == "business_clearance.changed"]
    assert clearance_events
    for event in clearance_events:
        assert set(event.payload) == {"clearance_id", "application_id", "decision", "expires_at"}
        assert "screening" not in str(event.payload).lower()
        assert EVIDENCE_1 not in str(event.payload) and EVIDENCE_2 not in str(event.payload)
    case_events = [event for event in snapshot.outbox.values() if event.event_type == "aml.case_changed"]
    for event in case_events:
        assert set(event.payload) == {"case_id", "screening_id", "restricted_record_version"}


def test_uat_m04_009_edd_request_is_neutral_outside_the_workbench(m04, officer, company_acceptor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-009").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000009"), idempotency_key="callback-009")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    case = service.get_aml_case(officer, case_id).data
    due = m04["clock"].value + timedelta(days=3)
    updated = service.add_aml_case_action(
        officer, case_id,
        {"action": "request_edd", "reason_code": "beneficial_owner_document_required", "restricted_note_asset_id": EVIDENCE_1, "due_at": due.isoformat().replace("+00:00", "Z")},
        expected_version=case["version"], idempotency_key="action-009",
    ).data
    assert updated["state"] == "under_review"
    clearance = service.get_business_clearance(company_acceptor, APPLICATION_A).data
    assert clearance["decision"] == "pending"
    assert "beneficial_owner_document_required" not in str(clearance)


def test_uat_m04_010_auditor_needs_scoped_time_boxed_grant(m04, officer, supervisor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-010").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000010"), idempotency_key="callback-010")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    auditor = ActorContext(TENANT, "20000000-0000-0000-0000-000000000005", frozenset({"internal_auditor"}), principal_type="staff")

    with pytest.raises(ForbiddenError):
        service.get_aml_case(auditor, case_id)

    service.grant_case_read(
        supervisor, case_id, auditor.actor_id, reason_code="scheduled_internal_audit",
        expires_at=m04["clock"].value + timedelta(hours=1),
    )
    read = service.get_aml_case(auditor, case_id)
    assert read.data["id"] == case_id
    snapshot = m04["repository"].snapshot()
    assert any(event.actor_id == auditor.actor_id and event.result == "succeeded" for event in snapshot.audit_events)
    with pytest.raises(ForbiddenError):
        service.decide_aml_case(
            auditor, case_id,
            {"decision": "approve", "risk_level": "low", "reason_code": "auditor_cannot_decide", "restricted_evidence_ids": [EVIDENCE_1]},
            expected_version=read.data["version"], idempotency_key="decide-auditor-010",
        )

    m04["clock"].advance(hours=2)
    with pytest.raises(ForbiddenError):
        service.get_aml_case(auditor, case_id)


def test_uat_m04_011_open_case_under_legal_hold_is_never_cleaned_up(m04, officer, supervisor):
    from workers.tasks.aml_cft.tasks import RetentionSweeper

    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-011").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000011"), idempotency_key="callback-011")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    service.apply_legal_hold(supervisor, case_id, reason_code="regulatory_inquiry_pending")

    sweep = RetentionSweeper(m04["repository"]).sweep(TENANT)
    assert sweep.skipped_hold == 1
    case = service.get_aml_case(officer, case_id).data
    assert case["id"] == case_id  # still present and readable, not purged


def test_uat_m04_012_expired_clearance_cannot_be_reused(m04, officer, supervisor, company_acceptor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="screen-012").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000012"), idempotency_key="callback-012")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    case = service.get_aml_case(officer, case_id).data
    decided = service.decide_aml_case(
        supervisor, case_id,
        {
            "decision": "approve", "risk_level": "low", "reason_code": "cleared_after_review",
            "restricted_evidence_ids": [EVIDENCE_1], "expires_at": (m04["clock"].value + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
        expected_version=case["version"], idempotency_key="decide-012",
    ).data
    assert decided["decision"] == "approved"

    m04["clock"].advance(days=2)
    clearance = service.get_business_clearance(company_acceptor, APPLICATION_A).data
    assert clearance["decision"] == "expired"
    assert "screening" not in str(clearance).lower()
