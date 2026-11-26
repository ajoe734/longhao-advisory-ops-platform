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


def test_first_ever_assignment_for_a_customer_with_no_prior_row_is_reachable_via_http(m11, supervisor):
    # A customer M11 has never seen at all (no contract.effective consumed,
    # so no company-pool baseline row exists yet): the shared If-Match
    # parameter (api/openapi.yaml #/components/parameters/IfMatch) requires a
    # positive integer and forbids "0", so this must be reachable without the
    # client ever being able to assert the (unrepresentable) absence of a
    # prior version.
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    controller = M11Controller(service)
    never_seen_customer = "d0000000-0000-0000-0000-00000000ffff"
    body = {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"}

    rejected_zero = controller.handle(
        "assign_maintenance_owner", supervisor, body=body, path={"customer_id": never_seen_customer},
        headers={"Idempotency-Key": "first-assign-000", "If-Match": '"0"'},
    )
    assert rejected_zero.status == 422  # "0" never satisfies the wire pattern; not the bug under test

    response = controller.handle(
        "assign_maintenance_owner", supervisor, body=body, path={"customer_id": never_seen_customer},
        headers={"Idempotency-Key": "first-assign-001", "If-Match": '"1"'},
    )
    assert response.status == 201
    assert response.body["state"] == "active"
    assert response.body["salesperson_id"] == SALES_A


def test_idempotency_replay_keeps_the_original_etag(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    pool_version = current_version(m11, CUSTOMER)
    controller = M11Controller(service)
    body = {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"}
    headers = {"Idempotency-Key": "replay-etag-001", "If-Match": f'"{pool_version}"'}

    first = controller.handle("assign_maintenance_owner", supervisor, body=body, path={"customer_id": CUSTOMER}, headers=headers)
    replay = controller.handle("assign_maintenance_owner", supervisor, body=body, path={"customer_id": CUSTOMER}, headers=headers)
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.headers["ETag"] == first.headers["ETag"] == '"1"'
