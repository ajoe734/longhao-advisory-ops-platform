from __future__ import annotations

from datetime import timedelta

import pytest

from modules.ownership_pool.domain import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionRequiredError,
    StaleVersionError,
    ValidationError,
    wire_time,
)

from conftest import (
    HR_EVIDENCE,
    NOW,
    SALES_A,
    SALES_B,
    TENANT,
    current_version,
    seed_customer_with_contract,
    seed_policy,
)


CUSTOMER = "a0000000-0000-0000-0000-000000000001"
CONTRACT = "b0000000-0000-0000-0000-000000000001"


def test_sales_cannot_assign_maintenance_owner(m11, sales_a):
    with pytest.raises(ForbiddenError):
        m11["service"].assign_maintenance_owner(
            sales_a, CUSTOMER,
            {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"},
            expected_version=1, idempotency_key="assign-forbidden-001",
        )


def test_assign_without_if_match_is_precondition_required(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    with pytest.raises(PreconditionRequiredError):
        m11["service"].assign_maintenance_owner(
            supervisor, CUSTOMER,
            {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"},
            expected_version=None, idempotency_key="assign-no-etag-001",
        )


def test_assign_with_stale_version_is_rejected(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    with pytest.raises(StaleVersionError) as excinfo:
        m11["service"].assign_maintenance_owner(
            supervisor, CUSTOMER,
            {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"},
            expected_version=99, idempotency_key="assign-stale-001",
        )
    assert excinfo.value.current_version == 1


def test_idempotency_replay_returns_the_same_assignment(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    pool_version = current_version(m11, CUSTOMER)
    body = {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"}
    first = service.assign_maintenance_owner(supervisor, CUSTOMER, body, expected_version=pool_version, idempotency_key="assign-replay-001")
    second = service.assign_maintenance_owner(supervisor, CUSTOMER, body, expected_version=pool_version, idempotency_key="assign-replay-001")
    assert first.data["id"] == second.data["id"]
    assert second.idempotency_replayed is True


def test_idempotency_key_reused_with_different_payload_conflicts(m11, supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    pool_version = current_version(m11, CUSTOMER)
    service.assign_maintenance_owner(
        supervisor, CUSTOMER,
        {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"},
        expected_version=pool_version, idempotency_key="assign-conflict-001",
    )
    with pytest.raises(ConflictError) as excinfo:
        service.assign_maintenance_owner(
            supervisor, CUSTOMER,
            {"salesperson_id": SALES_B, "effective_from": wire_time(NOW), "reason_code": "assignment"},
            expected_version=pool_version, idempotency_key="assign-conflict-001",
        )
    assert excinfo.value.code == "idempotency_conflict"


def test_claim_requires_self_salesperson(m11, supervisor, sales_a):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    snapshot = m11["repository"].snapshot()
    entry = next(entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER)
    with pytest.raises(ForbiddenError) as excinfo:
        service.claim_pool_customer(
            sales_a, entry.id,
            {"salesperson_id": SALES_B, "first_contact_due_at": wire_time(NOW + timedelta(days=1)), "reason_code": "redevelopment"},
            expected_version=entry.version, idempotency_key="claim-forbidden-001",
        )
    assert excinfo.value.code == "claim_requires_self"


def test_departure_requires_existing_hr_evidence(m11, hr_operator):
    with pytest.raises(ValidationError) as excinfo:
        m11["service"].record_employee_departure(
            hr_operator,
            {"employee_id": SALES_A, "effective_at": wire_time(NOW), "hr_evidence_asset_id": "c0000000-0000-0000-0000-000000000099", "reason_code": "resignation"},
            idempotency_key="departure-bad-evidence-001",
        )
    assert excinfo.value.code == "evidence_not_found"


def test_departure_without_prior_assignment_closes_nothing(m11, hr_operator):
    receipt = m11["service"].record_employee_departure(
        hr_operator,
        {"employee_id": SALES_A, "effective_at": wire_time(NOW), "hr_evidence_asset_id": HR_EVIDENCE, "reason_code": "resignation"},
        idempotency_key="departure-none-001",
    )
    assert receipt.data["assignments_closed"] == 0
    assert receipt.data["pool_entries_created"] == 0


def test_release_requires_a_sales_owned_assignment(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    snapshot = m11["repository"].snapshot()
    pool_assignment_id = snapshot.current_assignment[(TENANT, CUSTOMER)]
    with pytest.raises(ValidationError) as excinfo:
        m11["service"].release_maintenance_owner(
            supervisor, pool_assignment_id, {"reason_code": "manual_release"},
            expected_version=1, idempotency_key="release-not-sales-001",
        )
    assert excinfo.value.code == "not_sales_owned"


def test_evaluate_without_known_policy_is_not_found(m11, ownership_service):
    with pytest.raises(NotFoundError):
        m11["service"].evaluate_company_pool(
            ownership_service,
            {"as_of": wire_time(NOW), "policy_version": "unknown_policy:1", "dry_run": True},
            idempotency_key="evaluate-unknown-001",
        )


def test_evaluate_requires_service_principal(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    with pytest.raises(ForbiddenError):
        m11["service"].evaluate_company_pool(
            supervisor,
            {"as_of": wire_time(NOW), "policy_version": "company_pool_lapse:1", "dry_run": True},
            idempotency_key="evaluate-wrong-principal-001",
        )


def test_departed_salesperson_cannot_claim_pool_customer(m11, supervisor, hr_operator, sales_a):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    service.record_employee_departure(
        hr_operator,
        {"employee_id": SALES_A, "effective_at": wire_time(NOW), "hr_evidence_asset_id": HR_EVIDENCE, "reason_code": "resignation"},
        idempotency_key="departure-claim-block-001",
    )
    snapshot = m11["repository"].snapshot()
    entry = next(entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER)
    with pytest.raises(ForbiddenError) as excinfo:
        service.claim_pool_customer(
            sales_a, entry.id,
            {"salesperson_id": SALES_A, "first_contact_due_at": wire_time(NOW + timedelta(days=1)), "reason_code": "redevelopment"},
            expected_version=entry.version, idempotency_key="claim-departed-001",
        )
    assert excinfo.value.code == "actor_departed"


def test_departed_salesperson_cannot_be_assigned_a_new_customer(m11, supervisor, hr_operator):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    service.record_employee_departure(
        hr_operator,
        {"employee_id": SALES_A, "effective_at": wire_time(NOW), "hr_evidence_asset_id": HR_EVIDENCE, "reason_code": "resignation"},
        idempotency_key="departure-assign-block-001",
    )
    pool_version = current_version(m11, CUSTOMER)
    with pytest.raises(ValidationError) as excinfo:
        service.assign_maintenance_owner(
            supervisor, CUSTOMER,
            {"salesperson_id": SALES_A, "effective_from": wire_time(NOW), "reason_code": "assignment"},
            expected_version=pool_version, idempotency_key="assign-departed-001",
        )
    assert excinfo.value.code == "salesperson_departed"


def test_forbidden_claim_still_writes_a_rejection_audit(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    snapshot = m11["repository"].snapshot()
    entry = next(entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER)
    before = len(m11["repository"].snapshot().audit_events)
    with pytest.raises(ForbiddenError):
        # supervisor holds no "sales" role, so claim_pool_customer's role
        # check rejects before ever reaching _run_command's transaction.
        m11["service"].claim_pool_customer(
            supervisor, entry.id,
            {"salesperson_id": SALES_A, "first_contact_due_at": wire_time(NOW), "reason_code": "redevelopment"},
            expected_version=entry.version, idempotency_key="claim-audit-001",
        )
    after = len(m11["repository"].snapshot().audit_events)
    assert after == before + 1


def test_evaluate_company_pool_writes_exactly_one_audit_per_call(m11, supervisor, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    before = len(m11["repository"].snapshot().audit_events)
    service.evaluate_company_pool(
        ownership_service,
        {"as_of": wire_time(NOW), "policy_version": wire_code, "dry_run": True},
        idempotency_key="evaluate-audit-001",
    )
    after = len(m11["repository"].snapshot().audit_events)
    assert after == before + 1


def test_other_tenant_cannot_see_or_claim_a_pool_entry(m11, supervisor, other_tenant_sales, other_tenant_supervisor):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    snapshot = m11["repository"].snapshot()
    entry = next(entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER)

    other_tenant_pool = service.list_company_pool(other_tenant_sales)
    assert all(item["customer_id"] != CUSTOMER for item in other_tenant_pool.data["items"])

    with pytest.raises(NotFoundError):
        service.claim_pool_customer(
            other_tenant_sales, entry.id,
            {"salesperson_id": other_tenant_sales.actor_id, "first_contact_due_at": wire_time(NOW + timedelta(days=1)), "reason_code": "redevelopment"},
            expected_version=entry.version, idempotency_key="cross-tenant-claim-001",
        )

    assert service.get_ownership_history(other_tenant_sales, CUSTOMER).data["items"] == []

    # A policy proposed in TENANT is also not visible to OTHER_TENANT's
    # supervisor: assigning against it must fail at policy lookup, not CAS.
    with pytest.raises(ValidationError) as excinfo:
        service.assign_maintenance_owner(
            other_tenant_supervisor, CUSTOMER,
            {"salesperson_id": other_tenant_sales.actor_id, "effective_from": wire_time(NOW), "reason_code": "assignment"},
            expected_version=1, idempotency_key="cross-tenant-assign-001",
        )
    assert excinfo.value.code == "pool_policy_not_configured"
