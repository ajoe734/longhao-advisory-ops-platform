from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

import pytest

from modules.service_cases.automation import DraftResult
from modules.service_cases.domain import ActorContext, ConflictError, ForbiddenError, NotFoundError, ValidationError

from tests.modules.service_cases.conftest import (
    CHANNEL_BINDING,
    CONTEXT_EVIDENCE,
    SUPERVISOR,
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

    from tests.modules.service_cases.conftest import customer_context
    stranger = customer_context(customer_id="30000000-0000-0000-0000-000000000099", actor_id="30000000-0000-0000-0000-000000000099")
    with pytest.raises(NotFoundError):
        service.add_case_interaction(stranger, case["id"], {
            "channel": "web", "direction": "inbound", "occurred_at": "2027-01-01T00:00:00Z",
            "content_asset_id": "40000000-0000-0000-0000-000000000002", "summary": "冒用他人案件嘗試",
        }, expected_version=case["version"], idempotency_key="stranger-interaction-001")


def test_customer_cannot_record_internal_or_outbound_interactions(m13, agent):
    service = m13["service"]
    from tests.modules.service_cases.conftest import customer_context
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


def test_case_allowed_actions_offer_send_and_never_offer_resolve_twice(m13, agent):
    """Regression for F6: _case_actions() never advertised
    "send_service_response" at all (the admin UI's send button was gated on
    an action that could never appear), and it kept advertising
    "resolve_service_case" once the case was already resolved, which would
    411/409 on retry."""
    service = m13["service"]
    case = create_case(service, agent, "allowed-actions-create-001").data
    assert "send_service_response" in case["allowed_actions"]
    assert "resolve_service_case" in case["allowed_actions"]

    resolved = service.resolve_service_case(agent, case["id"], {
        "resolution_code": "fixed", "resolution_summary": "問題已處理完成",
        "customer_notice_evidence_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="allowed-actions-resolve-001").data
    assert "resolve_service_case" not in resolved["allowed_actions"]
    assert "send_service_response" not in resolved["allowed_actions"]


def test_draft_allowed_actions_never_offer_approve_to_the_drafter(m13, agent, agent_two):
    """Regression for F6: _draft_actions() offered "approve_service_response_draft"
    to the drafter themself even though human_reviewed maker-checker always
    rejects that combination, making the advertised action unusable."""
    service = m13["service"]
    case = create_case(service, agent, "draft-actions-create-001").data
    draft = service.draft_service_response(agent, case["id"], draft_body(), expected_version=case["version"], idempotency_key="draft-actions-draft-001").data
    assert "approve_service_response_draft" not in draft["allowed_actions"]
    as_drafter = service.get_service_response_draft(agent, draft["id"]).data
    assert "approve_service_response_draft" not in as_drafter["allowed_actions"]
    as_reviewer = service.get_service_response_draft(agent_two, draft["id"]).data
    assert "approve_service_response_draft" in as_reviewer["allowed_actions"]


def test_refund_escalation_cannot_be_approved_or_executed_by_this_module(m13, agent):
    service = m13["service"]
    assert not hasattr(service, "approve_refund")
    assert not hasattr(service, "execute_refund")
    assert not hasattr(service, "request_refund")


def test_production_automation_refuses_local_fake(m13):
    from modules.service_cases.automation import LocalDraftingAssistant
    from modules.service_cases.ports import FakeChannelBindings, FakeIntegrationKeys, FakeKnowledgeBase, FakeObjectAssetStore, FakeSuppressionRequests
    from modules.service_cases.repository import InMemoryRepository
    from modules.service_cases.service import ServiceCaseService

    with pytest.raises(RuntimeError):
        ServiceCaseService(
            InMemoryRepository(), FakeObjectAssetStore(), FakeKnowledgeBase(), FakeChannelBindings(),
            FakeSuppressionRequests(), LocalDraftingAssistant(), FakeIntegrationKeys(), environment="production",
        )


def test_approved_low_risk_template_requires_policy_service_and_blocks_sensitive_categories(m13, agent, response_service):
    """Regression for F2: a prior candidate let any service_agent/supervisor —
    including the drafter — self-approve by passing
    approval_mode="approved_low_risk_template" with an arbitrary
    template_version, bypassing maker-checker on any case including
    complaint/refund. Only the controlled policy service may use this mode,
    and never for complaint/refund/research_question/AML-escalated cases."""
    service = m13["service"]

    def draft_and_approve_attempt(approver, **case_overrides):
        case = create_case(service, agent, f"low-risk-create-{case_overrides.get('category', 'billing')}", **case_overrides).data
        draft = service.draft_service_response(agent, case["id"], draft_body(), expected_version=case["version"], idempotency_key=f"low-risk-draft-{case['id']}").data
        sha = sha256(draft["text"].encode("utf-8")).hexdigest() if draft["text"] else "0" * 64
        return service.approve_service_response_draft(approver, draft["id"], {
            "draft_content_sha256": sha, "decision": "approve",
            "approval_mode": "approved_low_risk_template", "template_version": "any-attacker-chosen-version",
            "policy_version": "policy-v1", "reason_code": "self_service_template", "publication_reference_ids": [],
            "valid_until": "2027-01-01T00:00:00Z",
        }, expected_version=1, idempotency_key=f"low-risk-approve-{case['id']}")

    # The drafter themself (a plain service_agent) must never bypass review this way.
    with pytest.raises(ForbiddenError) as drafter_denied:
        draft_and_approve_attempt(agent, category="billing")
    assert drafter_denied.value.code == "low_risk_template_requires_policy_service"

    # Even a different human staff role (supervisor) cannot use this mode.
    supervisor = ActorContext(TENANT, SUPERVISOR, frozenset({"service_supervisor"}), principal_type="staff")
    with pytest.raises(ForbiddenError) as supervisor_denied:
        draft_and_approve_attempt(supervisor, category="billing")
    assert supervisor_denied.value.code == "low_risk_template_requires_policy_service"

    # The controlled policy service may use it for a low-risk category...
    allowed = draft_and_approve_attempt(response_service, category="billing")
    assert allowed.status == 201

    # ...but never for complaint/refund/research_question, even as the policy service.
    for category in ("complaint", "refund", "research_question"):
        with pytest.raises((ForbiddenError, ConflictError)) as blocked:
            draft_and_approve_attempt(response_service, category=category)
        if isinstance(blocked.value, ForbiddenError):
            assert blocked.value.code in ("low_risk_template_category_blocked", "new_investment_judgment_blocked")
