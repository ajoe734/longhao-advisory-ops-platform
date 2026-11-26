from __future__ import annotations

from datetime import datetime, timezone

import pytest

from modules.service_cases.automation import DraftResult
from modules.service_cases.domain import ActorContext, ConflictError, ForbiddenError, NotFoundError, ValidationError

from conftest import (
    CHANNEL_BINDING,
    CONTEXT_EVIDENCE,
    TENANT,
    case_body,
    create_case,
    draft_body,
)


def test_cross_tenant_staff_cannot_read_or_mutate_case(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "tenant-create-001").data
    other = ActorContext(
        "10000000-0000-0000-0000-000000000099", "20000000-0000-0000-0000-000000000099",
        frozenset({"service_agent"}), principal_type="staff",
    )
    m13["assets"].add(other.tenant_id, CONTEXT_EVIDENCE)
    with pytest.raises(NotFoundError):
        service.get_service_case(other, case["id"])
    with pytest.raises(NotFoundError):
        service.escalate_service_case(other, case["id"], {
            "target_queue": "technical", "reason_code": "cross_tenant_probe", "due_at": "2027-01-01T00:00:00Z",
            "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
        }, expected_version=1, idempotency_key="tenant-escalate-001")
    assert service.get_service_case(agent, case["id"]).data["subject"] == case["subject"]


def test_unverified_customer_cannot_see_another_customers_case(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "self-service-001").data

    from conftest import customer_context
    stranger = customer_context(customer_id="30000000-0000-0000-0000-000000000099", actor_id="30000000-0000-0000-0000-000000000099")
    with pytest.raises(NotFoundError):
        service.add_case_interaction(stranger, case["id"], {
            "channel": "web", "direction": "inbound", "occurred_at": "2027-01-01T00:00:00Z",
            "content_asset_id": "40000000-0000-0000-0000-000000000002", "summary": "冒用他人案件嘗試",
        }, expected_version=case["version"], idempotency_key="stranger-interaction-001")


def test_customer_cannot_record_internal_or_outbound_interactions(m13, agent):
    service = m13["service"]
    from conftest import customer_context
    case = create_case(service, agent, "customer-direction-001").data
    customer = customer_context()
    with pytest.raises(ForbiddenError) as denied:
        service.add_case_interaction(customer, case["id"], {
            "channel": "web", "direction": "internal", "occurred_at": "2027-01-01T00:00:00Z",
            "content_asset_id": "40000000-0000-0000-0000-000000000002", "summary": "客戶嘗試寫內部備註",
        }, expected_version=case["version"], idempotency_key="customer-direction-001")
    assert denied.value.code == "customer_direction_forbidden"


def test_phone_and_onsite_interactions_require_recording_consent_evidence(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "consent-required-001").data
    with pytest.raises(ValidationError) as missing_consent:
        service.add_case_interaction(agent, case["id"], {
            "channel": "phone", "direction": "inbound", "occurred_at": "2027-01-01T00:00:00Z",
            "content_asset_id": "40000000-0000-0000-0000-000000000002", "summary": "電話記錄缺少同意證據",
        }, expected_version=case["version"], idempotency_key="consent-required-001")
    assert missing_consent.value.field_name == "recording_consent_evidence_id"


def test_drafting_assistant_only_receives_minimized_fields(m13, agent):
    service = m13["service"]
    captured: dict = {}

    class RecordingAssistant:
        is_fake = True
        model_version = "recording-v1"
        prompt_version = "recording-v1"

        def draft(self, minimized_input):
            captured.update(minimized_input)
            return DraftResult(text="安全回覆內容", contains_new_investment_judgment=False, requires_human=False)

    service.assistant = RecordingAssistant()
    case = create_case(service, agent, "automation-minimized-001", description="客戶的完整機敏敘述，不應流向LLM。").data
    service.draft_service_response(agent, case["id"], draft_body(), expected_version=case["version"], idempotency_key="automation-minimized-001")
    assert set(captured) == {"category", "intent", "requested_tone", "knowledge_version", "approved_source_refs"}
    assert "客戶的完整機敏敘述" not in str(captured.values())


def test_aml_escalated_case_never_calls_the_drafting_assistant(m13, agent):
    service = m13["service"]

    class ExplodingAssistant:
        is_fake = True
        model_version = "exploding-v1"
        prompt_version = "exploding-v1"

        def draft(self, minimized_input):
            raise AssertionError("the drafting assistant must never be called for an AML-escalated case")

    case = create_case(service, agent, "aml-no-call-001").data
    escalated = service.escalate_service_case(agent, case["id"], {
        "target_queue": "compliance", "reason_code": "aml_review_required", "due_at": "2027-01-01T00:00:00Z",
        "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="aml-no-call-escalate-001").data
    service.assistant = ExplodingAssistant()
    draft = service.draft_service_response(agent, case["id"], draft_body(), expected_version=escalated["version"], idempotency_key="aml-no-call-draft-001").data
    assert draft["state"] == "blocked"


def test_hard_block_refuses_approval_and_send_of_new_investment_judgment_even_if_drafted(m13, agent, agent_two):
    """Defense in depth: even if a draft somehow reaches `drafted` status while
    flagged with contains_new_investment_judgment, approval and send must both
    hard-refuse it. The local deterministic assistant never produces this
    combination itself, so the repository state is crafted directly here to
    exercise the guard regardless of assistant behaviour."""
    service = m13["service"]
    case = create_case(service, agent, "hard-block-001").data

    from modules.service_cases.domain import ResponseDraft

    def inject(state):
        draft = ResponseDraft(
            id="70000000-0000-0000-0000-000000000001", tenant_id=TENANT, case_id=case["id"], version=1,
            created_at=m13["clock"](), updated_at=m13["clock"](), status="drafted", created_by=agent.actor_id,
            knowledge_version="kb-v1", model_version="manual", prompt_version="manual", source_refs=[],
            text="已核准內容", content_asset_id="80000000-0000-0000-0000-000000000001",
            content_sha256="a" * 64, contains_new_investment_judgment=True,
        )
        state.response_drafts[(TENANT, draft.id)] = draft
        return draft.id

    draft_id = m13["repository"].atomic(inject)
    with pytest.raises(ForbiddenError) as approve_denied:
        service.approve_service_response_draft(agent_two, draft_id, {
            "draft_content_sha256": "a" * 64, "decision": "approve", "approval_mode": "human_reviewed",
            "template_version": "manual", "policy_version": "policy-v1", "reason_code": "override_attempt",
            "publication_reference_ids": [], "valid_until": "2027-01-01T00:00:00Z",
        }, expected_version=1, idempotency_key="hard-block-approve-001")
    assert approve_denied.value.code == "new_investment_judgment_blocked"


def test_refund_escalation_cannot_be_approved_or_executed_by_this_module(m13, agent):
    service = m13["service"]
    assert not hasattr(service, "approve_refund")
    assert not hasattr(service, "execute_refund")
    assert not hasattr(service, "request_refund")


def test_production_automation_refuses_local_fake(m13):
    from modules.service_cases.automation import LocalDraftingAssistant
    from modules.service_cases.ports import FakeChannelBindings, FakeKnowledgeBase, FakeObjectAssetStore, FakeSuppressionRequests
    from modules.service_cases.repository import InMemoryRepository
    from modules.service_cases.service import ServiceCaseService

    with pytest.raises(RuntimeError):
        ServiceCaseService(
            InMemoryRepository(), FakeObjectAssetStore(), FakeKnowledgeBase(), FakeChannelBindings(),
            FakeSuppressionRequests(), LocalDraftingAssistant(), environment="production",
        )
