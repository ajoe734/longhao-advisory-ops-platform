from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import yaml

from modules.ownership_pool.domain import ActorContext, wire_time
from modules.ownership_pool.http import M11Controller, ROUTES

from conftest import NOW, SALES_A, SUPERVISOR_B, TENANT, current_version, seed_customer_with_contract, seed_policy


CUSTOMER = "d0000000-0000-0000-0000-000000000001"
CONTRACT = "e0000000-0000-0000-0000-000000000001"
ROOT = Path(__file__).resolve().parents[3]


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


def test_company_pool_policy_lifecycle_is_published_audited_and_current(m11, supervisor, supervisor_b):
    controller = M11Controller(m11["service"])
    create_headers = {"Idempotency-Key": "pool-policy-create-001"}
    create_body = {"lapse_days": 7, "departure_immediate": True}

    created = controller.handle("create_company_pool_policy", supervisor, body=create_body, headers=create_headers)
    assert created.status == 201
    assert created.headers["ETag"] == '"1"'
    assert created.headers["Idempotency-Replayed"] == "false"
    assert created.body["policy_code"] == "company_pool_lapse"
    assert created.body["approval_status"] == "draft"
    assert created.body["allowed_actions"] == []  # maker cannot review their own version

    replay = controller.handle("create_company_pool_policy", supervisor, body=create_body, headers=create_headers)
    assert replay.status == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.body["id"] == created.body["id"]

    denied_self_review = controller.handle(
        "approve_company_pool_policy",
        supervisor,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-self-approve-001", "If-Match": '"1"'},
    )
    assert denied_self_review.status == 403
    assert denied_self_review.body["code"] == "approval_requires_separate_actor"

    approved = controller.handle(
        "approve_company_pool_policy",
        supervisor_b,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-approve-001", "If-Match": '"1"'},
    )
    assert approved.status == 200
    assert approved.headers["ETag"] == '"2"'
    assert approved.body["approval_status"] == "approved"
    assert approved.body["approved_by"] == SUPERVISOR_B

    # A later draft is not yet effective and must not replace the approved
    # version returned by the current-policy read.
    controller.handle(
        "create_company_pool_policy",
        supervisor,
        body={"lapse_days": 14, "departure_immediate": True},
        headers={"Idempotency-Key": "pool-policy-create-002"},
    )
    current = controller.handle("get_current_company_pool_policy", supervisor_b)
    assert current.status == 200
    assert current.body["id"] == created.body["id"]
    assert current.body["policy_version"] == "company_pool_lapse:1"

    actions = [event.action for event in m11["repository"].snapshot().audit_events]
    assert "m11.create_company_pool_policy" in actions
    assert "m11.approve_company_pool_policy" in actions
    assert "m11.get_current_company_pool_policy" in actions


def test_company_pool_policy_controller_rejects_missing_identity_and_platform_admin(m11, supervisor):
    controller = M11Controller(m11["service"])
    unauthenticated = controller.handle(
        "create_company_pool_policy",
        None,
        body={"lapse_days": 7, "departure_immediate": True},
        headers={"Idempotency-Key": "pool-policy-authn-001"},
    )
    assert unauthenticated.status == 401
    assert unauthenticated.body["code"] == "authentication_required"

    platform_admin = ActorContext(
        TENANT,
        "20000000-0000-0000-0000-000000000099",
        frozenset({"platform_admin"}),
        principal_type="staff",
    )
    policy_create = controller.handle(
        "create_company_pool_policy",
        platform_admin,
        body={"lapse_days": 7, "departure_immediate": True},
        headers={"Idempotency-Key": "pool-policy-platform-admin-001"},
    )
    assert policy_create.status == 403
    assert policy_create.body["code"] == "forbidden"

    # Existing pool reads remain restricted too; the new current-policy read
    # must not become an indirect platform-admin bypass.
    assert controller.handle("list_company_pool", platform_admin).status == 403
    assert controller.handle("get_current_company_pool_policy", platform_admin).status == 403


def test_company_pool_policy_controller_is_tenant_scoped(m11, supervisor, supervisor_b, other_tenant_supervisor):
    controller = M11Controller(m11["service"])
    created = controller.handle(
        "create_company_pool_policy",
        supervisor,
        body={"lapse_days": 7, "departure_immediate": True},
        headers={"Idempotency-Key": "pool-policy-tenant-create-001"},
    )

    cross_tenant_approval = controller.handle(
        "approve_company_pool_policy",
        other_tenant_supervisor,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-tenant-approve-001", "If-Match": '"1"'},
    )
    assert cross_tenant_approval.status == 404

    approved = controller.handle(
        "approve_company_pool_policy",
        supervisor_b,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-tenant-approve-002", "If-Match": '"1"'},
    )
    assert approved.status == 200

    other_tenant_current = controller.handle("get_current_company_pool_policy", other_tenant_supervisor)
    assert other_tenant_current.status == 404
    assert other_tenant_current.body["code"] == "current_policy_not_found"


def test_company_pool_policy_commands_reject_server_controlled_fields_and_stale_versions(m11, supervisor, supervisor_b):
    controller = M11Controller(m11["service"])

    # Request bodies cannot forge a policy's tenant, policy code, maker, or
    # approval state. Those values are all derived server-side from the
    # authenticated ActorContext and the existing policy transition.
    forged = controller.handle(
        "create_company_pool_policy",
        supervisor,
        body={
            "lapse_days": 7,
            "departure_immediate": True,
            "tenant_id": "10000000-0000-0000-0000-000000000002",
        },
        headers={"Idempotency-Key": "pool-policy-forged-field-001"},
    )
    assert forged.status == 422
    assert forged.body["code"] == "validation_failed"

    created = controller.handle(
        "create_company_pool_policy",
        supervisor,
        body={"lapse_days": 7, "departure_immediate": True},
        headers={"Idempotency-Key": "pool-policy-cas-create-001"},
    )
    assert created.status == 201

    missing_version = controller.handle(
        "approve_company_pool_policy",
        supervisor_b,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-cas-missing-001"},
    )
    assert missing_version.status == 428
    assert missing_version.body["code"] == "precondition_required"

    stale_version = controller.handle(
        "approve_company_pool_policy",
        supervisor_b,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-cas-stale-001", "If-Match": '"2"'},
    )
    assert stale_version.status == 412
    assert stale_version.body["code"] == "stale_version"

    approved = controller.handle(
        "approve_company_pool_policy",
        supervisor_b,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-cas-approve-001", "If-Match": '"1"'},
    )
    assert approved.status == 200
    assert approved.body["approved_by"] == SUPERVISOR_B


def test_company_pool_policy_operations_and_live_response_match_openapi(m11, supervisor, supervisor_b):
    spec = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text())
    expected = {
        "create_company_pool_policy": ("/ownership/company-pool-policies", "post"),
        "approve_company_pool_policy": ("/ownership/company-pool-policies/{policy_id}/approval", "post"),
        "get_current_company_pool_policy": ("/ownership/company-pool-policies/current", "get"),
    }
    route_operations = {operation_id for _, _, operation_id in ROUTES}
    for operation_id, (path, method) in expected.items():
        operation = spec["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert operation["x-allowed-roles"] == ["sales_supervisor"]
        assert operation["x-audit-action"] == f"m11.{operation_id}"
        assert operation_id in route_operations

    controller = M11Controller(m11["service"])
    created = controller.handle(
        "create_company_pool_policy",
        supervisor,
        body={"lapse_days": 7, "departure_immediate": True},
        headers={"Idempotency-Key": "pool-policy-contract-001"},
    )
    approved = controller.handle(
        "approve_company_pool_policy",
        supervisor_b,
        body={"decision": "approve"},
        path={"policy_id": created.body["id"]},
        headers={"Idempotency-Key": "pool-policy-contract-002", "If-Match": '"1"'},
    )
    validator = Draft202012Validator(
        {"$ref": "#/components/schemas/CompanyPoolPolicyVersion", "components": spec["components"]},
        format_checker=FormatChecker(),
    )
    assert list(validator.iter_errors(approved.body)) == []
