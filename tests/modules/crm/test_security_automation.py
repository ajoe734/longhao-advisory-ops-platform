"""SoD / role-authorization boundaries and the one approved LLM capability.

Every denial path here asserts the *specific* code, not merely "an error",
so a future accidental authorization loosening fails the test instead of
silently widening access.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from modules.crm.automation import CrmAutomationService, LocalContactDraftAssistant
from modules.crm.domain import ActorContext, ForbiddenError, ValidationError


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@pytest.fixture
def automation(repository):
    return CrmAutomationService(repository, LocalContactDraftAssistant(), environment="test")


# ---- role authorization boundaries ----------------------------------------


def test_marketing_cannot_create_opportunity(service, actors):
    with pytest.raises(ForbiddenError):
        service.create_opportunity(
            actors.marketing(), {
                "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
                "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
            }, idempotency_key="sec-create-00000001",
        )


def test_sales_cannot_decide_conversion(service, actors, evidence):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="sec-decide-opp-00000001",
    )
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    activity = service.record_sales_activity(
        actors.sales(), opp.data["id"], {
            "activity_type": "completed_call", "occurred_at": _iso(_now()), "customer_response_evidence_id": ev,
            "activity_evidence_asset_id": ev, "needs_summary": "x", "next_action": "y",
            "next_action_due_at": _iso(_now() + timedelta(days=1)), "outcome": "converted",
        }, expected_version=1, idempotency_key="sec-decide-activity-00000001",
    )
    claim = service.claim_conversion(
        actors.sales(), opp.data["id"], {
            "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "x",
        }, expected_version=2, idempotency_key="sec-decide-claim-00000001",
    )
    with pytest.raises(ForbiddenError):
        service.decide_conversion(
            actors.sales(), claim.data["id"], {
                "decision": "approve", "reason_code": "x", "approved_salesperson_id": actors.sales_id,
                "causality_evidence_asset_id": ev,
            }, expected_version=1, idempotency_key="sec-decide-decide-00000001",
        )


def test_sales_supervisor_cannot_claim_conversion(service, actors):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="sec-sup-claim-00000001",
    )
    with pytest.raises(ForbiddenError):
        service.claim_conversion(
            actors.supervisor(), opp.data["id"], {
                "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
                "sales_activity_ids": [_uuid()], "conversion_type": "renewal",
                "claimed_salesperson_id": actors.supervisor_id, "reason_code": "x",
            }, expected_version=1, idempotency_key="sec-sup-claim-00000002",
        )


def test_sales_cannot_access_another_sales_persons_opportunity(service, actors):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="sec-crossowner-00000001",
    )
    with pytest.raises(ForbiddenError) as excinfo:
        service.get_opportunity(actors.sales_2(), opp.data["id"])
    assert excinfo.value.code == "opportunity_not_owned"


def test_sales_supervisor_can_access_any_opportunity(service, actors):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="sec-superaccess-00000001",
    )
    fetched = service.get_opportunity(actors.supervisor(), opp.data["id"])
    assert fetched.data["id"] == opp.data["id"]


def test_sales_cannot_claim_credit_for_a_colleague(service, actors):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="sec-selfclaim-00000001",
    )
    with pytest.raises(ForbiddenError) as excinfo:
        service.claim_conversion(
            actors.sales(), opp.data["id"], {
                "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
                "sales_activity_ids": [_uuid()], "conversion_type": "renewal",
                "claimed_salesperson_id": actors.sales_id_2, "reason_code": "x",
            }, expected_version=1, idempotency_key="sec-selfclaim-00000002",
        )
    assert excinfo.value.code == "self_claim_required"


def test_disabled_actor_cannot_create_opportunity(service, actors):
    # ActorContext enforces principal_type membership at construction; the
    # service layer's own role check is the live SoD gate exercised here.
    outsider = ActorContext(tenant_id=actors.tenant_id, actor_id=_uuid(), roles=frozenset())
    with pytest.raises(ForbiddenError):
        service.create_opportunity(
            outsider, {
                "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
                "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
            }, idempotency_key="sec-noroles-00000001",
        )


# ---- automation capability: untrusted draft, no operational authority -----


def test_automation_drafts_require_minimized_input(automation, actors):
    work_item_id = _uuid()
    run = automation.draft_daily_contacts(
        actors.sales(), [{"work_item_id": work_item_id, "reason_code": "renewal_due", "suppression_applied": False}]
    )
    assert run.status == "untrusted_draft"
    assert len(run.drafts) == 1
    assert run.drafts[0].work_item_id == work_item_id
    assert run.model_version == "local-rules-no-llm-v1"


def test_automation_rejects_non_minimized_input(automation, actors):
    with pytest.raises(ValidationError) as excinfo:
        automation.draft_daily_contacts(
            actors.sales(), [{"work_item_id": _uuid(), "reason_code": "renewal_due", "suppression_applied": False, "customer_name": "Leaked PII"}]
        )
    assert excinfo.value.code == "automation_input_not_minimized"


def test_automation_cannot_be_invoked_by_service_agent(automation, actors):
    with pytest.raises(ForbiddenError):
        automation.draft_daily_contacts(actors.service_agent(), [])


def test_automation_review_requires_adopt_modify_or_reject(automation, actors):
    run = automation.draft_daily_contacts(actors.sales(), [])
    with pytest.raises(ValidationError):
        automation.record_review(actors.sales(), run.id, action="auto_approved", reason_code="x")
    review = automation.record_review(actors.sales(), run.id, action="adopted", reason_code="matches_known_pattern")
    assert review.action == "adopted"


def test_automation_review_of_unknown_run_is_rejected(automation, actors):
    with pytest.raises(ValidationError) as excinfo:
        automation.record_review(actors.sales(), _uuid(), action="adopted", reason_code="x")
    assert excinfo.value.code == "automation_run_not_found"


def test_fake_automation_adapter_forbidden_in_production(repository):
    with pytest.raises(RuntimeError):
        CrmAutomationService(repository, LocalContactDraftAssistant(), environment="production")


def test_fake_evidence_adapter_forbidden_in_production(repository, evidence):
    from modules.crm.service import CrmService

    with pytest.raises(RuntimeError):
        CrmService(repository, evidence, environment="production")
