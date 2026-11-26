from __future__ import annotations

from datetime import timedelta

from modules.ownership_pool.domain import wire_time
from modules.ownership_pool.http import M11Controller

from conftest import NOW, SALES_A, current_version, seed_customer_with_contract, seed_policy


CUSTOMER = "d0000000-0000-0000-0000-000000000001"
CONTRACT = "e0000000-0000-0000-0000-000000000001"


def test_assign_via_controller_sets_etag_and_replay_header(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    pool_version = current_version(m11, CUSTOMER)
    controller = M11Controller(service)
    body = {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"}
    headers = {"Idempotency-Key": "controller-assign-001", "If-Match": f'"{pool_version}"'}

    response = controller.handle(
        "assign_maintenance_owner", supervisor, body=body, path={"customer_id": CUSTOMER}, headers=headers,
    )
    assert response.status == 201
    assert response.headers["ETag"] == '"1"'
    assert response.headers["Idempotency-Replayed"] == "false"

    replay = controller.handle(
        "assign_maintenance_owner", supervisor, body=body, path={"customer_id": CUSTOMER}, headers=headers,
    )
    assert replay.headers["Idempotency-Replayed"] == "true"


def test_missing_if_match_renders_problem_json(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    controller = M11Controller(service)
    body = {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"}

    response = controller.handle(
        "assign_maintenance_owner", supervisor, body=body, path={"customer_id": CUSTOMER},
        headers={"Idempotency-Key": "controller-no-etag-001"},
    )
    assert response.status == 428
    assert response.headers["Content-Type"] == "application/problem+json"
    assert response.body["code"] == "precondition_required"


def test_list_company_pool_via_controller(m11, supervisor, sales_a):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    controller = M11Controller(service)

    response = controller.handle("list_company_pool", sales_a, query={}, headers={})
    assert response.status == 200
    assert any(item["customer_id"] == CUSTOMER for item in response.body["items"])
