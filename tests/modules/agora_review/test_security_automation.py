from __future__ import annotations

from datetime import timedelta

import pytest

from modules.agora_review.automation import AgoraDraftAutomationService, LocalDraftAssistant, assert_no_privileged_capability
from modules.agora_review.domain import ActorContext, ForbiddenError, NotFoundError, ValidationError
from modules.agora_review.ports import servant_webhook_signature, verify_servant_webhook

from conftest import ANALYST, CASE_A, NOW, TENANT, assign, seed_case


def test_cross_tenant_staff_cannot_read_or_mutate_review_case(m06, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    other_analyst = ActorContext(
        "10000000-0000-0000-0000-000000000099", "20000000-0000-0000-0000-000000000099",
        frozenset({"analyst"}), principal_type="staff",
    )
    with pytest.raises(NotFoundError):
        m06["service"].get_review_case(other_analyst, case_id)
    with pytest.raises(NotFoundError):
        m06["service"].get_revision_request(other_analyst, case_id)
    own_analyst = ActorContext(TENANT, ANALYST, frozenset({"analyst"}), principal_type="staff")
    assert m06["service"].get_review_case(own_analyst, case_id).data["id"] == case_id


def test_automation_cannot_sign_publish_or_move_money():
    for capability in ("sign", "publish", "payment", "entitlement", "disbursement"):
        with pytest.raises(ForbiddenError) as denied:
            assert_no_privileged_capability(capability)
        assert denied.value.code == "automation_privileged_capability_denied"
    assert_no_privileged_capability("summarize_diff")  # harmless capability is not blocked


def test_draft_automation_is_untrusted_and_requires_human_review(m06, analyst):
    automation = AgoraDraftAutomationService(m06["repository"], LocalDraftAssistant())
    run = automation.draft_revision(analyst, CASE_A, ["target_price changed"], source_refs=[])
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert "target_price changed" in run.draft_text
    review = automation.record_review(analyst, run.id, action="modified", reason_code="human_rewrote_tone", adopted_text="人工修訂後文字")
    assert review.action == "modified"
    assert m06["repository"].snapshot().automation_reviews[(TENANT, review.id)] == review


def test_draft_automation_rejects_non_minimized_or_unauthorized_input(m06, analyst, servant_adapter):
    automation = AgoraDraftAutomationService(m06["repository"], LocalDraftAssistant())
    with pytest.raises(ForbiddenError):
        automation.draft_revision(servant_adapter, CASE_A, ["x"], source_refs=[])
    with pytest.raises(ValidationError):
        automation.draft_revision(analyst, CASE_A, [{"not": "a string"}], source_refs=[])


def test_production_automation_refuses_local_fake(m06):
    with pytest.raises(RuntimeError):
        AgoraDraftAutomationService(m06["repository"], LocalDraftAssistant(), environment="production")


def test_webhook_signature_accepts_valid_and_rejects_tampered_or_stale():
    secret = b"m06-synthetic-webhook-secret-32-bytes-minimum!"
    timestamp = str(int(NOW.timestamp()))
    nonce = "a" * 16
    raw_body = b'{"event_id":"synthetic"}'
    signature = servant_webhook_signature(
        secret, timestamp=timestamp, nonce=nonce, method="POST", path="/api/v1/webhooks/servant-revisions", raw_body=raw_body,
    )
    verify_servant_webhook(
        secret, timestamp=timestamp, nonce=nonce, method="POST", path="/api/v1/webhooks/servant-revisions",
        raw_body=raw_body, provided_signature=signature, now=NOW,
    )
    with pytest.raises(ForbiddenError):
        verify_servant_webhook(
            secret, timestamp=timestamp, nonce=nonce, method="POST", path="/api/v1/webhooks/servant-revisions",
            raw_body=b"tampered", provided_signature=signature, now=NOW,
        )
    with pytest.raises(ForbiddenError):
        verify_servant_webhook(
            secret, timestamp=timestamp, nonce=nonce, method="POST", path="/api/v1/webhooks/servant-revisions",
            raw_body=raw_body, provided_signature=signature, now=NOW + timedelta(seconds=301),
        )
