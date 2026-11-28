"""The composed development world: modules see each other through events and projections."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from longhao_platform.app import PlatformRuntime
from longhao_platform.contracts import PlatformConfig
from longhao_platform.http import create_app
from longhao_platform.synthetic_identities import PERSONAS_BY_KEY
from longhao_platform.synthetic_seed import seed_app
from longhao_platform.synthetic_world import existing_world, world_for


@pytest.fixture(scope="module")
def seeded():
    runtime = PlatformRuntime.bootstrap()
    app = create_app(runtime)
    summary = seed_app(app, runtime)
    return runtime, app, summary


def auth(persona: str) -> dict[str, str]:
    return {"Authorization": PERSONAS_BY_KEY[persona].authorization}


def test_world_binds_every_module_with_its_consumer(seeded):
    runtime, _app, _summary = seeded
    status = existing_world(runtime).status()
    expected = [f"M{n:02d}" for n in range(1, 15)]
    assert status["bound_modules"] == expected
    assert status["consumers"] == expected


def test_seed_runs_without_failures_and_no_event_is_left_waiting(seeded):
    runtime, _app, summary = seeded
    assert summary["failures"] == []
    status = existing_world(runtime).status()
    assert status["pending_retries"] == []
    failed = [(d.event_type, d.target, d.code) for d in existing_world(runtime).deliveries if d.status in {"failed", "skipped"}]
    assert failed == []


def test_onboarded_customer_has_an_effective_contract_and_active_entitlement(seeded):
    _runtime, app, summary = seeded
    member_a = summary["customers"][0]
    assert member_a["stage"] == "effective"
    client = TestClient(app)
    contract = client.get(f"/api/v1/contracts/{member_a['contract_id']}", headers=auth("contract-operator"))
    assert contract.status_code == 200 and contract.json()["state"] == "effective"
    entitlements = client.get(f"/api/v1/customers/{member_a['customer_id']}/entitlements", headers=auth("contract-operator"))
    assert [row["state"] for row in entitlements.json()["items"]] == ["active"]


def test_cross_module_projections_reached_downstream_modules(seeded):
    runtime, _app, _summary = seeded
    # the full delivery log, not the status endpoint's most recent 50
    delivered = {(d.event_type, d.target) for d in existing_world(runtime).deliveries if d.status == "applied"}
    for pair in [
        ("application.customer_signed", "M03"),   # M02 -> M03 signed application for KYC
        ("kyc.decision_recorded", "M02"),         # M03 -> M02 acceptance gate
        ("business_clearance.changed", "M02"),    # M04 -> M02 acceptance gate
        ("finance.payment_confirmed", "M02"),     # M14 -> M02 payment condition
        ("entitlement.changed", "M09"),           # M02 -> M09 delivery audience
        ("contract.effective", "M11"),            # M02 -> M11 ownership window
        ("customer.identity_linked", "M09"),      # partial subscriber, sequence gap accepted
    ]:
        assert pair in delivered, pair


def test_member_identity_acts_as_its_linked_customer(seeded):
    runtime, app, summary = seeded
    world = existing_world(runtime)
    member_a = summary["customers"][0]
    assert world.customer_for(str(PERSONAS_BY_KEY["member-a"].id)) == member_a["customer_id"]
    feed = TestClient(app).get("/api/v1/member/feed", headers=auth("member-a"))
    assert feed.status_code == 200


def test_world_status_is_operator_only(seeded):
    _runtime, app, _summary = seeded
    client = TestClient(app)
    assert client.get("/internal/platform/world").status_code == 401
    assert client.get("/internal/platform/world", headers=auth("sales")).status_code == 403
    assert client.get("/internal/platform/world", headers=auth("platform-admin")).status_code == 200


def test_no_world_without_synthetic_providers():
    class Runtime:
        config = PlatformConfig(environment="test", synthetic_providers=False, allow_missing_modules=True)

    assert world_for(Runtime()) is None


def test_signed_research_is_published_automatically_and_delivered_to_entitled_members(seeded):
    runtime, app, summary = seeded
    research = summary["research"][0]
    assert research["stage"] == "published"
    client = TestClient(app)
    publications = client.get("/api/v1/publications", headers=auth("analyst")).json()["items"]
    assert [(row["state"], row["recipient_count"]) for row in publications] == [("published", 1)]
    deliveries = existing_world(runtime).modules["M09"].repository.read(
        lambda state: [(row.status, row.channel) for row in state.deliveries.values()])
    assert deliveries == [("accepted", "legendflow")]
    entitled = client.get("/api/v1/member/feed", headers=auth("member-a")).json()["items"]
    assert [row["action"] for row in entitled] == ["entry"]
    not_entitled = client.get("/api/v1/member/feed", headers=auth("member-b")).json()["items"]
    assert not_entitled == []


def test_synthetic_evidence_upload_needs_an_identity_and_reaches_every_module(seeded):
    runtime, app, _summary = seeded
    client = TestClient(app)
    assert client.post("/internal/platform/world/assets", json={"label": "x"}).status_code == 401
    bad = client.post("/internal/platform/world/assets", json={"label": "x", "classification": "public"}, headers=auth("kyc-reviewer"))
    assert bad.status_code == 422
    created = client.post("/internal/platform/world/assets", json={"label": "kyc evidence"}, headers=auth("kyc-reviewer"))
    assert created.status_code == 201
    asset_id = created.json()["asset_id"]
    world = existing_world(runtime)
    assert world.service("M02", "applications") is not None
    assert asset_id in world.assets


def test_converting_salesperson_gets_the_sales_share_of_recognized_revenue(seeded):
    _runtime, app, summary = seeded
    client = TestClient(app)
    member_a = summary["customers"][0]
    assert member_a["conversion_state"] == "approved"
    month = summary["month_close"]
    assert month["recognized"]["amount"] != "0"
    run = client.get(f"/api/v1/commissions/runs/{month['commission_run_id']}", headers=auth("commission-approver")).json()
    assert run["state"] == "approved"
    entries = client.get("/api/v1/commissions/entries", headers=auth("commission-approver")).json()["items"]
    entries = [row for row in entries if row["run_id"] == month["commission_run_id"]]
    by_type = {row["recipient_type"]: row for row in entries}
    assert by_type["sales"]["recipient_id"] == str(PERSONAS_BY_KEY["sales"].id)
    total = sum(float(row["amount"]["amount"]) for row in entries)
    # the base pool (10% of the recognized untaxed revenue) is fully allocated
    assert round(total, 2) == round(float(month["recognized"]["amount"]) * 0.10, 2)
    batch = client.get(f"/api/v1/commissions/payout-batches/{month['payout_batch_id']}", headers=auth("payroll-operator")).json()
    assert batch["state"] == "approval_pending"


def test_work_items_close_when_their_condition_is_resolved(seeded):
    _runtime, app, summary = seeded
    client = TestClient(app)
    member_a, member_b = summary["customers"]
    items = client.get("/api/v1/crm/worklists", headers=auth("sales-supervisor")).json()["items"]
    states = {(row["customer_id"], row["worklist"]): row["state"] for row in items}
    assert {key[1]: value for key, value in states.items() if key[0] == member_a["customer_id"]} == {
        "pending_review": "completed", "incomplete_application": "completed", "company_pool": "completed"}
    assert states[(member_b["customer_id"], "pending_review")] == "open"


def test_accepted_customer_is_active_and_owned(seeded):
    _runtime, app, summary = seeded
    client = TestClient(app)
    member_a, member_b = summary["customers"]
    assert client.get(f"/api/v1/customers/{member_a['customer_id']}", headers=auth("cms-operator")).json()["status"] == "active"
    assert client.get(f"/api/v1/customers/{member_b['customer_id']}", headers=auth("cms-operator")).json()["status"] == "applicant"
    history = client.get(f"/api/v1/customers/{member_a['customer_id']}/ownership-history", headers=auth("sales-supervisor")).json()
    assert [row["state"] for row in history["items"]] == ["active"]


def test_service_case_answer_is_approved_by_a_second_agent_and_sent(seeded):
    _runtime, app, summary = seeded
    client = TestClient(app)
    answered, waiting = summary["service_cases"]
    response = client.get(f"/api/v1/service-responses/{answered['response_id']}", headers=auth("service-agent")).json()
    assert response["state"] == "accepted"
    cases = {row["id"]: row for row in client.get("/api/v1/service-cases", headers=auth("service-supervisor")).json()["items"]}
    assert cases[waiting["case_id"]]["state"] == "new"
    # the case queue is staff-only
    assert client.get("/api/v1/service-cases", headers=auth("member-b")).status_code == 403
