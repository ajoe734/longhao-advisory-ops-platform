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
    deliveries = existing_world(runtime).status()["recent_deliveries"]
    stuck = [(d["event_type"], d["target"], d["code"]) for d in deliveries if d["status"] not in {"applied", "duplicate"}]
    assert stuck == []


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
    delivered = {(d["event_type"], d["target"]) for d in existing_world(runtime).status()["recent_deliveries"] if d["status"] == "applied"}
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
