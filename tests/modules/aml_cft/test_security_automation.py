from __future__ import annotations

import pytest

from modules.aml_cft.automation import CaseSummaryAutomationService, LocalCaseSummaryAssistant
from modules.aml_cft.domain import ActorContext, ForbiddenError, NotFoundError, ValidationError

from conftest import (
    APPLICATION_A,
    EVIDENCE_1,
    OTHER_TENANT,
    TENANT,
    screening_callback,
    screening_request,
)


def test_cross_tenant_staff_cannot_read_or_decide_case(m04, officer):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="tenant-screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000101"), idempotency_key="tenant-callback-001")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]

    m04["evidence"].add(OTHER_TENANT, EVIDENCE_1)  # isolate this test to case-lookup tenancy, not evidence existence
    other_tenant_supervisor = ActorContext(OTHER_TENANT, "20000000-0000-0000-0000-000000000099", frozenset({"aml_supervisor"}), principal_type="staff")
    with pytest.raises(NotFoundError):
        service.get_aml_case(other_tenant_supervisor, case_id)
    with pytest.raises(NotFoundError):
        service.decide_aml_case(
            other_tenant_supervisor, case_id,
            {"decision": "approve", "risk_level": "low", "reason_code": "cross_tenant_attempt", "restricted_evidence_ids": [EVIDENCE_1]},
            expected_version=1, idempotency_key="tenant-decide-001",
        )
    assert service.get_aml_case(officer, case_id).data["id"] == case_id


def test_customer_cannot_submit_declaration_for_someone_elses_application(m04):
    service = m04["service"]
    impostor = ActorContext(TENANT, "20000000-0000-0000-0000-000000000008", frozenset(), principal_type="customer", customer_id="40000000-0000-0000-0000-000000000099")
    with pytest.raises(NotFoundError):
        service.submit_aml_declaration(
            impostor, APPLICATION_A,
            {
                "form_version": "v1", "declaration_consent_id": EVIDENCE_1, "restricted_answer_asset_id": EVIDENCE_1,
                "restricted_document_ids": [], "customer_signature_id": EVIDENCE_1,
            },
            expected_version=None, idempotency_key="impostor-decl-001",
        )
    snapshot = m04["repository"].snapshot()
    assert len(snapshot.declarations) == 0


def test_webhook_adapter_role_is_required_for_screening_callback(m04, officer, sales):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="adapter-screen-001").data
    with pytest.raises(ForbiddenError):
        service.receive_aml_screening(
            ActorContext(TENANT, sales.actor_id, frozenset({"sales"}), principal_type="staff"),
            screening_callback(screening["id"], "clear", event_id="80000000-0000-0000-0000-000000000201"),
            idempotency_key="adapter-callback-001",
        )
    with pytest.raises(ForbiddenError):
        # even the right role is rejected when presented as a human staff principal, not a service integration.
        service.receive_aml_screening(
            ActorContext(TENANT, officer.actor_id, frozenset({"aml_adapter"}), principal_type="staff"),
            screening_callback(screening["id"], "clear", event_id="80000000-0000-0000-0000-000000000202"),
            idempotency_key="adapter-callback-002",
        )


def test_minimized_case_summary_automation_cannot_mutate_case_state(m04, officer, supervisor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="automation-screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000301"), idempotency_key="automation-callback-001")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    before = service.get_aml_case(officer, case_id).data

    automation = CaseSummaryAutomationService(m04["repository"], LocalCaseSummaryAssistant())
    run = automation.summarize(officer, case_id)
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert run.draft.restricted_evidence_count >= 1
    assert not hasattr(automation, "decide_aml_case") and not hasattr(automation, "clear_case")

    after = service.get_aml_case(officer, case_id).data
    assert after == before

    sales_principal = ActorContext(TENANT, "20000000-0000-0000-0000-000000000009", frozenset({"sales"}), principal_type="staff")
    with pytest.raises(ForbiddenError):
        automation.summarize(sales_principal, case_id)


def test_production_automation_refuses_local_fake(m04):
    with pytest.raises(RuntimeError):
        CaseSummaryAutomationService(m04["repository"], LocalCaseSummaryAssistant(), environment="production")


def test_add_case_action_rejects_evidence_that_does_not_exist(m04, officer):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="evidence-screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id="80000000-0000-0000-0000-000000000401"), idempotency_key="evidence-callback-001")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    case = service.get_aml_case(officer, case_id).data
    with pytest.raises(ValidationError) as missing_evidence:
        service.add_aml_case_action(
            officer, case_id,
            {"action": "record_investigation", "reason_code": "x", "restricted_note_asset_id": "60000000-0000-0000-0000-000000009999"},
            expected_version=case["version"], idempotency_key="evidence-action-001",
        )
    assert missing_evidence.value.code == "evidence_not_found"
