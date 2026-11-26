from __future__ import annotations

import pytest

from modules.legendflow_delivery.automation import DeliveryExceptionAutomationService, LocalExceptionSummaryAssistant
from modules.legendflow_delivery.domain import ForbiddenError, ValidationError

from conftest import (
    CHANNEL_PROOF_LINE_OK,
    MEMBER_B,
    TENANT,
    dispatch,
    seed_publication,
    set_entitlement,
)


# ---- separation of duty / role boundaries ----------------------------------

def test_member_cannot_list_deliveries_or_reconcile(m09, member_a):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    with pytest.raises(ForbiddenError):
        env["service"].list_deliveries(member_a)
    with pytest.raises(ForbiddenError):
        env["service"].reconcile_delivery(member_a, message.id, {"reason_code": "x"}, expected_version=message.version, idempotency_key="sod-0001")


def test_staff_cannot_use_member_only_operations(m09, operator):
    env = m09
    with pytest.raises(ForbiddenError):
        env["service"].bind_member_channel(
            operator, {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True},
            idempotency_key="sod-0002",
        )
    with pytest.raises(ForbiddenError):
        env["service"].get_member_feed(operator)


def test_delivery_operator_cannot_redrive_dead_letters(m09, operator):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    with pytest.raises(ForbiddenError):
        env["service"].redrive_delivery_event(
            operator, job.id,
            {"reason_code": "x", "expected_event_id": "e0000000-0000-0000-0000-000000000099", "approved_by": operator.actor_id, "revalidate_entitlement": True, "revalidate_content_expiry": True},
            expected_version=job.version, idempotency_key="sod-0003",
        )


def test_member_cannot_read_or_bind_another_members_resources(m09, member_a):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    # member_a's feed never contains anything addressed to member_b, and vice
    # versa; resource ownership is server-resolved from the verified session,
    # not from any client-supplied customer_id in the request.
    other_publication = "50000000-0000-0000-0000-000000000004"
    other_entitlement = "40000000-0000-0000-0000-000000000004"
    other_report = "60000000-0000-0000-0000-000000000004"
    set_entitlement(env, entitlement_id=other_entitlement, customer_id=MEMBER_B, state="active")
    seed_publication(env, publication_id=other_publication, recipient_id="30000000-0000-0000-0000-000000000098", customer_id=MEMBER_B, entitlement_id=other_entitlement, report_version_id=other_report)
    dispatch(env, other_publication)

    feed = env["service"].get_member_feed(member_a)
    assert other_publication not in {item["publication_id"] for item in feed.data["items"]}


# ---- approved LLM capability: advisory-only, no send/redrive authority ----

def test_exception_summary_assistant_never_executes_only_drafts(m09, operator, supervisor):
    env = m09
    automation = DeliveryExceptionAutomationService(env["repository"], LocalExceptionSummaryAssistant())
    records = [{"delivery_id": "30000000-0000-0000-0000-000000000005", "error_code": "entitlement_revoked", "attempt_count": 1}]
    run = automation.summarize(operator, records)
    assert run.capability == "delivery_exception_summary"
    assert run.model_version == LocalExceptionSummaryAssistant.model_version
    assert run.status == "untrusted_draft"
    assert len(run.summaries) == 1
    assert "權限" in run.summaries[0].headline

    review = automation.record_review(supervisor, run.id, action="adopted", reason_code="核對權限後同意摘要內容")
    assert review.action == "adopted"
    assert review.reviewer_id == supervisor.actor_id


def test_exception_summary_rejects_non_minimized_input(m09, operator):
    env = m09
    automation = DeliveryExceptionAutomationService(env["repository"], LocalExceptionSummaryAssistant())
    with pytest.raises(ValidationError) as excinfo:
        automation.summarize(operator, [{"delivery_id": "30000000-0000-0000-0000-000000000005", "error_code": "x", "attempt_count": 1, "customer_display_name": "張三"}])
    assert excinfo.value.code == "automation_input_not_minimized"


def test_exception_summary_requires_authorized_role(m09, member_a):
    env = m09
    automation = DeliveryExceptionAutomationService(env["repository"], LocalExceptionSummaryAssistant())
    with pytest.raises(ForbiddenError):
        automation.summarize(member_a, [])


def test_fake_adapters_are_forbidden_in_production_composition(m09):
    env = m09
    from modules.legendflow_delivery.service import LegendflowDeliveryService

    with pytest.raises(RuntimeError):
        LegendflowDeliveryService(
            env["repository"], publications=env["publications"], recipients=env["recipients"], entitlements=env["entitlements"],
            report_access=env["report_access"], channel_proof=env["channel_proof"], channel_send=env["channel_send"],
            integration_keys=env["integration_keys"], hmac_key=b"0" * 32, environment="production",
        )


# ---- F6: member-role enforcement, replay-safe proof verification, and the
#      list_deliveries role grant matching the published OpenAPI contract ---

def test_require_member_rejects_a_customer_session_with_no_member_role(m09):
    env = m09
    from modules.legendflow_delivery.domain import ActorContext, ForbiddenError as _ForbiddenError

    impostor = ActorContext(TENANT, "20000000-0000-0000-0000-000000000009", frozenset(), principal_type="customer", customer_id="20000000-0000-0000-0000-000000000009")
    with pytest.raises(_ForbiddenError):
        env["service"].get_member_feed(impostor)


def test_bind_member_channel_idempotent_replay_succeeds_after_single_use_proof_is_consumed(m09, member_a):
    env = m09
    body = {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True}
    first = env["service"].bind_member_channel(member_a, body, idempotency_key="bind-single-use-0001")

    # Simulate the proof being single-use at the real provider: a second
    # verify() call for the same proof_ref now fails.
    env["channel_proof"].fail(CHANNEL_PROOF_LINE_OK, "forged")

    replay = env["service"].bind_member_channel(member_a, body, idempotency_key="bind-single-use-0001")
    assert replay.idempotency_replayed is True
    assert replay.data["id"] == first.data["id"]


def test_list_deliveries_grants_exactly_the_published_contract_roles(m09, supervisor):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)

    # api/openapi.yaml's x-allowed-roles for list_deliveries is
    # [service_agent, delivery_operator] only; delivery_supervisor must be
    # rejected rather than silently granted undocumented read access.
    with pytest.raises(ForbiddenError):
        env["service"].list_deliveries(supervisor)
