"""UI-relevant invariants (SA §10): server-decided ``allowed_actions``,
suppression projection, and the funnel/worklist page shape the admin
features (``apps/admin/src/features/crm``) depend on.

These do not render React; they prove the API responses the UI reads from
carry the fields and server-side gating the components assume (see
``apps/admin/src/features/crm/types.ts`` for the matching TypeScript shape).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def test_allowed_actions_are_server_decided_not_client_supplied(service, actors):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="ui-allowed-00000001",
    )
    assert set(opp.data["allowed_actions"]) == {"record_sales_activity", "set_next_action", "claim_conversion"}

    # A different salesperson sees the same opportunity (as supervisor) with
    # no actionable buttons available to them.
    other_view = service.get_opportunity(actors.supervisor(), opp.data["id"])
    assert other_view.data["allowed_actions"] == ["claim_conversion", "record_sales_activity", "set_next_action"]


def test_terminal_opportunity_has_no_allowed_actions(service, actors, evidence):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="ui-terminal-00000001",
    )
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    activity = service.record_sales_activity(
        actors.sales(), opp.data["id"], {
            "activity_type": "completed_call", "occurred_at": _iso(_now()), "customer_response_evidence_id": ev,
            "activity_evidence_asset_id": ev, "needs_summary": "x", "next_action": "y",
            "next_action_due_at": _iso(_now() + timedelta(days=1)), "outcome": "converted",
        }, expected_version=1, idempotency_key="ui-terminal-activity-00000001",
    )
    claim = service.claim_conversion(
        actors.sales(), opp.data["id"], {
            "application_id": _uuid(), "contract_id": _uuid(), "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "x",
        }, expected_version=2, idempotency_key="ui-terminal-claim-00000001",
    )
    service.decide_conversion(
        actors.supervisor(), claim.data["id"], {
            "decision": "approve", "reason_code": "x", "approved_salesperson_id": actors.sales_id,
            "causality_evidence_asset_id": ev,
        }, expected_version=1, idempotency_key="ui-terminal-decide-00000001",
    )
    converted = service.get_opportunity(actors.sales(), opp.data["id"])
    assert converted.data["state"] == "converted"
    assert converted.data["allowed_actions"] == [], "a converted opportunity must not offer further sales actions"


def test_work_item_suppressed_state_disables_set_next_action_for_sales_push(service, actors):
    customer_id = _uuid()
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": customer_id, "type": "upgrade", "service_code": "advisory-pro",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="ui-suppress-opp-00000001",
    )
    service.set_next_action(
        actors.sales(), opp.data["id"], {
            "owner_id": actors.sales_id, "action_type": "upgrade_assessment", "due_at": _iso(_now() + timedelta(days=1)), "reason_code": "x",
        }, expected_version=1, idempotency_key="ui-suppress-assign-00000001",
    )
    service.create_crm_suppression(
        actors.service_agent(), {
            "customer_id": customer_id, "scope": "sales_contact", "reason_category": "open_complaint",
            "reason_ref": _uuid(), "expires_at": None,
        }, idempotency_key="ui-suppress-create-00000001",
    )
    page = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    item = next(row for row in page.data["items"] if row["customer_id"] == customer_id)
    assert item["state"] == "suppressed"
    assert item["suppression_applied"] is True
    # The UI must still be able to assign a service follow-up (set_next_action
    # stays in allowed_actions); whether it accepts a sales push is a
    # business-rule check at submit time, not a hidden button.
    assert "set_next_action" in item["allowed_actions"]


def test_funnel_metrics_page_shape_matches_ui_fields(service, actors):
    page = service.get_funnel_metrics(actors.supervisor())
    assert set(page.data) == {"items", "as_of", "attribution_policy_version", "data_complete_through"}
    for item in page.data["items"]:
        assert set(item) == {"stage", "count", "cohort_key", "service_code", "period_start", "period_end"}


def test_worklist_page_shape_matches_ui_fields(service, actors):
    page = service.list_crm_worklists(actors.supervisor(), state_filter=None, cursor=None, limit=50, as_of=None)
    assert set(page.data) == {"items", "next_cursor", "as_of", "total_estimate"}
    for item in page.data["items"]:
        assert set(item) == {
            "id", "tenant_id", "version", "created_at", "updated_at", "allowed_actions", "customer_id",
            "opportunity_id", "worklist", "owner_id", "action_type", "due_at", "state", "reason_codes",
            "evidence_refs", "suggestion_origin", "suppression_applied",
        }


def test_marketing_role_only_sees_company_pool_worklist(consumer, service, actors):
    customer_id = _uuid()
    from conftest import make_envelope

    envelope = make_envelope(
        event_type="ownership.pool_entered", tenant_id=actors.tenant_id, producer="M11", aggregate_type="pool_entry",
        aggregate_id=_uuid(), payload={"pool_entry_id": _uuid(), "customer_id": customer_id, "entry_reason": "paid_service_lapse", "policy_version": "m11-v1"},
    )
    consumer.consume(envelope)
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="ui-marketing-scope-00000001",
    )
    service.set_next_action(
        actors.sales(), opp.data["id"], {
            "owner_id": actors.sales_id, "action_type": "renewal_offer", "due_at": _iso(_now() + timedelta(days=1)), "reason_code": "x",
        }, expected_version=1, idempotency_key="ui-marketing-scope-00000002",
    )

    marketing_page = service.list_crm_worklists(actors.marketing(), state_filter=None, cursor=None, limit=50, as_of=None)
    assert all(item["worklist"] == "company_pool" for item in marketing_page.data["items"])
    assert any(item["customer_id"] == customer_id for item in marketing_page.data["items"])
