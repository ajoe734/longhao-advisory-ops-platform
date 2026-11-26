from __future__ import annotations

from datetime import timedelta

import pytest

from modules.ownership_pool.domain import StaleVersionError, ValidationError

from conftest import (
    HANDOFF_EVIDENCE,
    HR_EVIDENCE,
    NOW,
    SALES_A,
    SALES_B,
    TENANT,
    current_version,
    seed_customer_with_contract,
    seed_policy,
)


CUSTOMER_A = "40000000-0000-0000-0000-000000000001"
CUSTOMER_B = "40000000-0000-0000-0000-000000000002"
CUSTOMER_C = "40000000-0000-0000-0000-000000000003"
CONTRACT_A = "50000000-0000-0000-0000-000000000001"
CONTRACT_B = "50000000-0000-0000-0000-000000000002"


def test_uat_m11_001_self_service_customer_without_owner_stays_in_company_pool(m11, supervisor, sales_a):
    seed_policy(m11["service"], supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW, ends_at=NOW + timedelta(days=365))

    pool = m11["service"].list_company_pool(sales_a)
    entries = [item for item in pool.data["items"] if item["customer_id"] == CUSTOMER_A]
    assert len(entries) == 1
    assert entries[0]["entry_reason"] == "unassigned_web"
    assert entries[0]["state"] == "available"
    assert entries[0]["claimed_by"] is None

    history = m11["service"].get_ownership_history(sales_a, CUSTOMER_A)
    assert history.data["items"] == []  # no sales assignment ever created; renewal needed no owner


def test_uat_m11_002_departure_returns_to_pool_immediately(m11, supervisor, hr_operator, sales_a):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    pool_version = current_version(m11, CUSTOMER_A)
    assigned = service.assign_maintenance_owner(
        supervisor, CUSTOMER_A,
        {"salesperson_id": SALES_A, "effective_from": _t(NOW), "reason_code": "assignment"},
        expected_version=pool_version, idempotency_key="assign-001",
    )
    assert assigned.data["state"] == "active"

    departure = service.record_employee_departure(
        hr_operator,
        {"employee_id": SALES_A, "effective_at": _t(NOW), "hr_evidence_asset_id": HR_EVIDENCE, "reason_code": "resignation"},
        idempotency_key="departure-001",
    )
    assert departure.status == 202
    assert departure.data["assignments_closed"] == 1
    assert departure.data["pool_entries_created"] == 1

    history = service.get_ownership_history(sales_a, CUSTOMER_A)
    assert history.data["items"][0]["state"] == "departure"
    snapshot = m11["repository"].snapshot()
    current_id = snapshot.current_assignment[(TENANT, CUSTOMER_A)]
    assert snapshot.assignments[(TENANT, current_id)].owner_type == "company_pool"
    outbox = [e for e in snapshot.outbox.values() if e.event_id == departure.data["access_revocation_event_id"]]
    assert outbox and outbox[0].event_type == "ownership.employee_departed"


def test_uat_m11_003_direct_assignment_grants_no_commission(m11, supervisor, sales_a):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    pool_version = current_version(m11, CUSTOMER_A)
    assigned = service.assign_maintenance_owner(
        supervisor, CUSTOMER_A,
        {"salesperson_id": SALES_A, "effective_from": _t(NOW), "reason_code": "assignment"},
        expected_version=pool_version, idempotency_key="assign-002",
    )
    assert assigned.status == 201
    assert assigned.data["commission_entitlement_granted"] is False
    assert assigned.data["reason_code"] == "assignment"


def test_uat_m11_004_redevelopment_claim_grants_no_commission_either(m11, supervisor, sales_a):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    entry_id = _only_pool_entry(m11, CUSTOMER_A)["id"]

    claimed = service.claim_pool_customer(
        sales_a, entry_id,
        {"salesperson_id": SALES_A, "first_contact_due_at": _t(NOW + timedelta(days=2)), "reason_code": "redevelopment"},
        expected_version=1, idempotency_key="claim-001",
    )
    assert claimed.status == 201
    assert claimed.data["commission_entitlement_granted"] is False
    assert claimed.data["source_pool_entry_id"] == entry_id

    pool_after = service.list_company_pool(sales_a)
    entry_after = next(item for item in pool_after.data["items"] if item["id"] == entry_id)
    assert entry_after["state"] == "claimed"
    assert entry_after["claimed_by"] == SALES_A


def test_uat_m11_005_seven_day_rule_is_not_seven_days_without_contact(m11, supervisor, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW, ends_at=NOW + timedelta(days=365))

    result = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(NOW + timedelta(days=8)), "policy_version": wire_code, "dry_run": True, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-005",
    )
    assert result.data["eligible_count"] == 0
    assert result.data["evaluated_count"] == 1


def test_uat_m11_006_only_full_168_hour_interruption_is_a_candidate(m11, supervisor, sales_a, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor)
    ends_at = NOW
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW - timedelta(days=30), ends_at=ends_at)
    pool_version = current_version(m11, CUSTOMER_A)
    service.assign_maintenance_owner(
        supervisor, CUSTOMER_A,
        {"salesperson_id": SALES_A, "effective_from": _t(NOW - timedelta(days=30)), "reason_code": "assignment"},
        expected_version=pool_version, idempotency_key="assign-006",
    )

    before = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(ends_at + timedelta(hours=167)), "policy_version": wire_code, "dry_run": True, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-006a",
    )
    assert before.data["eligible_count"] == 0

    at_threshold = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(ends_at + timedelta(hours=168)), "policy_version": wire_code, "dry_run": True, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-006b",
    )
    assert at_threshold.data["eligible_count"] == 1

    live = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(ends_at + timedelta(hours=168)), "policy_version": wire_code, "dry_run": False, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-006c",
    )
    assert live.data["rule_status"] == "approved"
    assert live.data["eligible_count"] == 1
    snapshot = m11["repository"].snapshot()
    current_id = snapshot.current_assignment[(TENANT, CUSTOMER_A)]
    assert snapshot.assignments[(TENANT, current_id)].owner_type == "company_pool"


def test_uat_m11_007_one_lapsed_monthly_service_does_not_offset_an_active_annual_service(m11, supervisor, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW - timedelta(days=400), ends_at=NOW + timedelta(days=100))
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_B, starts_at=NOW - timedelta(days=40), ends_at=NOW - timedelta(days=10))

    result = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(NOW), "policy_version": wire_code, "dry_run": True, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-007",
    )
    assert result.data["eligible_count"] == 0
    evaluation = next(iter(m11["repository"].snapshot().evaluations.values()))
    row = next(item for item in evaluation.evaluated if item["customer_id"] == CUSTOMER_A)
    assert row.get("all_paid_services_lapsed") is False


def test_uat_m11_008_unapproved_policy_blocks_live_recovery_but_allows_preview(m11, supervisor, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor, approve=False)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW - timedelta(days=30), ends_at=NOW - timedelta(days=10))

    preview = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(NOW), "policy_version": wire_code, "dry_run": True, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-008a",
    )
    assert preview.data["rule_status"] == "proposed"
    assert preview.data["eligible_count"] == 1

    with pytest.raises(ValidationError) as excinfo:
        service.evaluate_company_pool(
            ownership_service,
            {"as_of": _t(NOW), "policy_version": wire_code, "dry_run": False, "customer_ids": [CUSTOMER_A]},
            idempotency_key="evaluate-008b",
        )
    assert excinfo.value.code == "policy_not_approved"
    snapshot = m11["repository"].snapshot()
    current_id = snapshot.current_assignment[(TENANT, CUSTOMER_A)]
    assert snapshot.assignments[(TENANT, current_id)].owner_type == "company_pool"  # unchanged from baseline


def test_uat_m11_009_compliance_restricted_customer_is_never_a_sales_worklist_candidate(m11, supervisor, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW - timedelta(days=30), ends_at=NOW - timedelta(days=10))
    m11["restriction"].restrict(TENANT, CUSTOMER_A)

    result = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(NOW), "policy_version": wire_code, "dry_run": True, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-009",
    )
    assert result.data["eligible_count"] == 0
    assert result.data["blocked_count"] == 1
    evaluation = next(iter(m11["repository"].snapshot().evaluations.values()))
    row = next(item for item in evaluation.evaluated if item["customer_id"] == CUSTOMER_A)
    assert row["outcome"] == "blocked"
    assert row["reason"] == "compliance_hold"
    assert "aml" not in str(row).lower()


def test_uat_m11_010_only_one_concurrent_claim_succeeds(m11, supervisor, sales_a, sales_b):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    entry = _only_pool_entry(m11, CUSTOMER_A)

    winner = service.claim_pool_customer(
        sales_a, entry["id"],
        {"salesperson_id": SALES_A, "first_contact_due_at": _t(NOW + timedelta(days=1)), "reason_code": "redevelopment"},
        expected_version=entry["version"], idempotency_key="claim-010-a",
    )
    assert winner.status == 201

    with pytest.raises(StaleVersionError) as excinfo:
        service.claim_pool_customer(
            sales_b, entry["id"],
            {"salesperson_id": SALES_B, "first_contact_due_at": _t(NOW + timedelta(days=1)), "reason_code": "redevelopment"},
            expected_version=entry["version"], idempotency_key="claim-010-b",
        )
    assert excinfo.value.current_version == entry["version"] + 1

    pool_after = service.list_company_pool(sales_a)
    assert all(item["id"] != entry["id"] or item["claimed_by"] == SALES_A for item in pool_after.data["items"])


def test_uat_m11_011_handoff_never_rewrites_history(m11, supervisor, sales_a):
    service = m11["service"]
    seed_policy(service, supervisor, supervisor)
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    pool_version = current_version(m11, CUSTOMER_A)
    first = service.assign_maintenance_owner(
        supervisor, CUSTOMER_A,
        {"salesperson_id": SALES_A, "effective_from": _t(NOW), "reason_code": "assignment"},
        expected_version=pool_version, idempotency_key="assign-011-a",
    )

    second = service.assign_maintenance_owner(
        supervisor, CUSTOMER_A,
        {
            "salesperson_id": SALES_B, "effective_from": _t(NOW + timedelta(days=1)), "reason_code": "assignment",
            "previous_assignment_id": first.data["id"], "handoff_evidence_asset_id": HANDOFF_EVIDENCE,
        },
        expected_version=1, idempotency_key="assign-011-b",
    )
    assert second.status == 201

    history = service.get_ownership_history(sales_a, CUSTOMER_A)
    first_row = next(item for item in history.data["items"] if item["id"] == first.data["id"])
    assert first_row["salesperson_id"] == SALES_A
    assert first_row["state"] == "superseded"
    assert first_row["valid_from"] == first.data["valid_from"]  # original interval is untouched


def test_uat_m11_012_service_recovery_resets_the_interruption_clock(m11, supervisor, ownership_service):
    service = m11["service"]
    wire_code = seed_policy(service, supervisor, supervisor)
    ends_at = NOW
    seed_customer_with_contract(m11["consumer"], CUSTOMER_A, CONTRACT_A, starts_at=NOW - timedelta(days=30), ends_at=ends_at)

    # Day 6: a new contract becomes effective, covering the evaluation point.
    seed_customer_with_contract(
        m11["consumer"], CUSTOMER_A, CONTRACT_B,
        starts_at=ends_at + timedelta(days=6), ends_at=ends_at + timedelta(days=400),
    )

    result = service.evaluate_company_pool(
        ownership_service,
        {"as_of": _t(ends_at + timedelta(hours=169)), "policy_version": wire_code, "dry_run": True, "customer_ids": [CUSTOMER_A]},
        idempotency_key="evaluate-012",
    )
    assert result.data["eligible_count"] == 0
    evaluation = next(iter(m11["repository"].snapshot().evaluations.values()))
    row = next(item for item in evaluation.evaluated if item["customer_id"] == CUSTOMER_A)
    assert row["reason"] == "active_paid_service_present"


def _t(value) -> str:
    from modules.ownership_pool.domain import wire_time
    return wire_time(value)


def _only_pool_entry(m11, customer_id: str) -> dict:
    snapshot = m11["repository"].snapshot()
    entries = [entry for entry in snapshot.pool_entries.values() if entry.customer_id == customer_id]
    assert len(entries) == 1
    return {"id": entries[0].id, "version": entries[0].version}
