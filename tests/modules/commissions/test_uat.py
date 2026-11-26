from __future__ import annotations

from decimal import Decimal

import pytest

from modules.commissions.domain import ActorContext, ForbiddenError, ValidationError

from conftest import (
    APPROVER,
    EVIDENCE_ACK,
    EVIDENCE_ADJUSTMENT,
    MARKETING_ATTRIBUTION,
    NOW,
    OPERATOR,
    PAYROLL_ADAPTER,
    PAYROLL_OPERATOR,
    SALES_A,
    TENANT,
    approve_policy,
    create_policy,
    entries_for_run,
    run_jobs,
    run_request,
    sales_context,
    seed_cohort,
    seed_conversion,
    seed_recognition,
    seed_refund,
)


def test_uat_m12_001_unapproved_rate_blocks_live_but_allows_simulation(m12, operator):
    service = m12["service"]
    policy = create_policy(service, operator, "m12-001-policy", base_pool_rate=None)
    seed_recognition(m12, recognition_id="50000000-0000-0000-0000-000000000001", contract_id="60000000-0000-0000-0000-000000000001", customer_id="70000000-0000-0000-0000-000000000001", amount="10000")
    seed_cohort(m12, customer_id="70000000-0000-0000-0000-000000000001", customer_year=1)

    preview = service.preview_commissions(operator, run_request(policy["id"]), idempotency_key="m12-001-preview").data
    assert preview["live_payable"] is False
    assert "base_pool_rate:unresolved_simulated_as_zero" in preview["unapproved_assumptions"]

    with pytest.raises(ValidationError) as excinfo:
        service.create_commission_run(operator, run_request(policy["id"]), idempotency_key="m12-001-run")
    assert excinfo.value.code == "policy_not_approved"


def test_uat_m12_002_self_renewal_has_no_sales_share_and_remainder_goes_to_company(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-002-policy"), "m12-002-approve")
    customer_id = "70000000-0000-0000-0000-000000000002"
    recognition_id = "50000000-0000-0000-0000-000000000002"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000002", customer_id=customer_id, amount="10000")
    seed_cohort(m12, customer_id=customer_id, customer_year=2, current_owner_id=SALES_A)

    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-002-run").data
    run_jobs(m12)
    entries = entries_for_run(m12, run["id"])
    by_type = {entry["recipient_type"]: entry for entry in entries}

    assert by_type["sales"]["amount"]["amount"] == "0"
    assert by_type["sales"]["recipient_id"] is None
    assert by_type["marketing"]["amount"]["amount"] == "500"
    assert by_type["company_retained"]["amount"]["amount"] == "500"
    # Remainder goes to the company, never to the customer's current maintenance owner.
    assert by_type["company_retained"]["recipient_id"] is None
    assert by_type["company_retained"]["recipient_id"] != SALES_A


def test_uat_m12_003_year1_and_year2_coefficients_follow_customer_cohort(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-003-policy"), "m12-003-approve")
    rec_year1 = "50000000-0000-0000-0000-000000000003"
    rec_year2 = "50000000-0000-0000-0000-000000000004"
    seed_recognition(m12, recognition_id=rec_year1, contract_id="60000000-0000-0000-0000-000000000003", customer_id="70000000-0000-0000-0000-000000000003", amount="10000")
    seed_recognition(m12, recognition_id=rec_year2, contract_id="60000000-0000-0000-0000-000000000004", customer_id="70000000-0000-0000-0000-000000000004", amount="10000")
    seed_cohort(m12, customer_id="70000000-0000-0000-0000-000000000003", customer_year=1)
    seed_cohort(m12, customer_id="70000000-0000-0000-0000-000000000004", customer_year=2)

    preview = service.preview_commissions(operator, run_request(policy["id"], [rec_year1, rec_year2]), idempotency_key="m12-003-preview").data
    lines = {line["recognition_id"]: line for line in preview["lines"]}
    assert lines[rec_year1]["marketing_age_coefficient"] == "1"
    assert lines[rec_year2]["marketing_age_coefficient"] == "0.5"
    assert lines[rec_year1]["marketing_award"]["amount"] == "1000"
    assert lines[rec_year2]["marketing_award"]["amount"] == "500"


def test_uat_m12_004_year3_tail_requires_continuous_auto_renew(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-004-policy"), "m12-004-approve")
    rec_auto = "50000000-0000-0000-0000-000000000005"
    rec_manual = "50000000-0000-0000-0000-000000000006"
    seed_recognition(m12, recognition_id=rec_auto, contract_id="60000000-0000-0000-0000-000000000005", customer_id="70000000-0000-0000-0000-000000000005", amount="10000")
    seed_recognition(m12, recognition_id=rec_manual, contract_id="60000000-0000-0000-0000-000000000006", customer_id="70000000-0000-0000-0000-000000000006", amount="10000")
    seed_cohort(m12, customer_id="70000000-0000-0000-0000-000000000005", customer_year=3, continuous_auto_renew=True)
    seed_cohort(m12, customer_id="70000000-0000-0000-0000-000000000006", customer_year=3, continuous_auto_renew=False)

    preview = service.preview_commissions(operator, run_request(policy["id"], [rec_auto, rec_manual]), idempotency_key="m12-004-preview").data
    lines = {line["recognition_id"]: line for line in preview["lines"]}
    assert lines[rec_auto]["marketing_age_coefficient"] == "0.25"
    assert lines[rec_auto]["marketing_award"]["amount"] == "250"
    assert lines[rec_manual]["marketing_age_coefficient"] == "0"
    assert lines[rec_manual]["marketing_award"]["amount"] == "0"
    # Manually restarted auto-renewal does not retroactively restore tail eligibility.
    assert "age:year3_manual_restart_zero" in lines[rec_manual]["rule_trace_codes"]
    assert "age:year3_continuous_auto" in lines[rec_auto]["rule_trace_codes"]


def test_uat_m12_005_assisted_renewal_splits_pool_before_decay_with_approved_conversion(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(
        service, approver,
        create_policy(service, operator, "m12-005-policy", allocation_interpretation="marketing_decay_only"),
        "m12-005-approve",
    )
    recognition_id = "50000000-0000-0000-0000-000000000007"
    customer_id = "70000000-0000-0000-0000-000000000007"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000007", customer_id=customer_id, amount="10000")
    seed_cohort(m12, customer_id=customer_id, customer_year=2)
    seed_conversion(m12, recognition_id=recognition_id, conversion_id="80000000-0000-0000-0000-000000000001", salesperson_id=SALES_A, approved=True)

    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-005-run").data
    run_jobs(m12)
    entries = entries_for_run(m12, run["id"])
    by_type = {entry["recipient_type"]: entry for entry in entries}

    # base_pool = 10000 * 0.10 = 1000; 30/70 split first, then marketing-only decay at year2 (0.5).
    assert by_type["marketing"]["amount"]["amount"] == "150"  # 1000*0.3*0.5
    assert by_type["sales"]["amount"]["amount"] == "700"      # 1000*0.7, undecayed
    assert by_type["company_retained"]["amount"]["amount"] == "150"
    assert by_type["sales"]["recipient_id"] == SALES_A
    trace = by_type["marketing"]["calculation"]["rule_trace_codes"]
    assert "split:assisted_30_70" in trace and "decay:marketing_decay_only" in trace
    total = sum(Decimal(by_type[key]["amount"]["amount"]) for key in ("marketing", "sales", "company_retained"))
    assert total == Decimal("1000")


def test_uat_m12_006_the_two_decay_algorithms_cannot_be_mixed_in_one_policy(m12, operator, approver):
    service = m12["service"]
    recognition_id = "50000000-0000-0000-0000-000000000008"
    customer_id = "70000000-0000-0000-0000-000000000008"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000008", customer_id=customer_id, amount="10000")
    seed_cohort(m12, customer_id=customer_id, customer_year=2)
    seed_conversion(m12, recognition_id=recognition_id, conversion_id="80000000-0000-0000-0000-000000000002", salesperson_id=SALES_A, approved=True)

    policy_a = create_policy(service, operator, "m12-006-a", policy_code="decay-a", allocation_interpretation="marketing_decay_only")
    policy_b = create_policy(service, operator, "m12-006-b", policy_code="decay-b", allocation_interpretation="whole_pool_decay")
    assert policy_a["policy_sha256"] != policy_b["policy_sha256"]

    preview_a = service.preview_commissions(operator, run_request(policy_a["id"], [recognition_id]), idempotency_key="m12-006-preview-a").data
    preview_b = service.preview_commissions(operator, run_request(policy_b["id"], [recognition_id]), idempotency_key="m12-006-preview-b").data
    sales_a = preview_a["lines"][0]["sales_award"]["amount"]
    sales_b = preview_b["lines"][0]["sales_award"]["amount"]
    assert sales_a == "700"  # marketing_decay_only: sales keeps the full assisted share
    assert sales_b == "350"  # whole_pool_decay: sales is also decayed by the year-2 coefficient
    assert sales_a != sales_b

    # A live run is always bound to exactly one approved, single-strategy policy.
    approved_a = approve_policy(service, approver, policy_a, "m12-006-approve-a")
    live_run = service.create_commission_run(operator, run_request(approved_a["id"], [recognition_id]), idempotency_key="m12-006-run").data
    assert live_run["policy_id"] == approved_a["id"]


def test_uat_m12_007_refund_reverses_original_recipient_and_policy_not_the_new_owner(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-007-policy"), "m12-007-approve")
    recognition_id = "50000000-0000-0000-0000-000000000009"
    customer_id = "70000000-0000-0000-0000-000000000009"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000009", customer_id=customer_id, amount="10000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1, marketing_attribution_id=MARKETING_ATTRIBUTION)

    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-007-run").data
    run_jobs(m12)
    marketing_entry = next(e for e in entries_for_run(m12, run["id"]) if e["recipient_type"] == "marketing")
    assert marketing_entry["recipient_id"] == MARKETING_ATTRIBUTION

    # Maintenance ownership changes after the fact; the reversal must still bind to the original marketer.
    seed_cohort(m12, customer_id=customer_id, customer_year=1, current_owner_id=SALES_A, marketing_attribution_id=MARKETING_ATTRIBUTION)
    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000001", original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-200")

    adjustment = service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": marketing_entry["id"],
            "refund_id": "90000000-0000-0000-0000-000000000001",
            "attributable_recognition_id": recognition_id,
            "adjustment_amount": {"amount": "-200", "currency": "TWD"},
            "reason_code": "refund_partial",
            "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="m12-007-adjustment",
    ).data
    assert adjustment["original_entry_id"] == marketing_entry["id"]
    assert adjustment["original_recipient_id"] == MARKETING_ATTRIBUTION
    assert adjustment["original_recipient_id"] != SALES_A
    assert adjustment["amount"]["amount"] == "-200"


def test_uat_m12_008_adjustment_amount_beyond_the_original_award_is_flagged_not_clipped(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-008-policy"), "m12-008-approve")
    recognition_id = "50000000-0000-0000-0000-000000000010"
    customer_id = "70000000-0000-0000-0000-000000000010"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000010", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-008-run").data
    run_jobs(m12)
    marketing_entry = next(e for e in entries_for_run(m12, run["id"]) if e["recipient_type"] == "marketing")
    assert marketing_entry["amount"]["amount"] == "100"  # 1000 * 0.10 pool * 1.0 year1

    # The within-basis portion matches the award exactly: no clipping or silent drop.
    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000002", original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-50")
    within_basis = service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": marketing_entry["id"],
            "refund_id": "90000000-0000-0000-0000-000000000002",
            "attributable_recognition_id": recognition_id,
            "adjustment_amount": {"amount": "-50", "currency": "TWD"},
            "reason_code": "refund_earned_portion",
            "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="m12-008-adjustment-within",
    ).data
    assert within_basis["payroll_offset_allowed"] is True

    # A deferred/unearned refund portion must not silently reduce already-earned revenue past the award.
    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000003", original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-500")
    beyond_award = service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": marketing_entry["id"],
            "refund_id": "90000000-0000-0000-0000-000000000003",
            "attributable_recognition_id": recognition_id,
            "adjustment_amount": {"amount": "-500", "currency": "TWD"},
            "reason_code": "refund_includes_deferred_portion",
            "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="m12-008-adjustment-beyond",
    ).data
    assert beyond_award["amount"]["amount"] == "-500"  # recorded in full, not clipped to -100
    # Exceeding the original award is never auto-deducted from payroll, even though the entry has not been paid yet.
    assert beyond_award["payroll_offset_allowed"] is False


def test_uat_m12_009_resending_the_same_refund_only_creates_one_linked_adjustment(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-009-policy"), "m12-009-approve")
    recognition_id = "50000000-0000-0000-0000-000000000011"
    customer_id = "70000000-0000-0000-0000-000000000011"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000011", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-009-run").data
    run_jobs(m12)
    marketing_entry = next(e for e in entries_for_run(m12, run["id"]) if e["recipient_type"] == "marketing")
    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000004", original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-30")

    request_body = {
        "original_entry_id": marketing_entry["id"],
        "refund_id": "90000000-0000-0000-0000-000000000004",
        "attributable_recognition_id": recognition_id,
        "adjustment_amount": {"amount": "-30", "currency": "TWD"},
        "reason_code": "refund_webhook_retry",
        "evidence_asset_id": EVIDENCE_ADJUSTMENT,
    }
    first = service.create_commission_adjustment(operator, request_body, idempotency_key="m12-009-first-delivery").data
    second = service.create_commission_adjustment(operator, request_body, idempotency_key="m12-009-second-delivery-different-key").data
    assert first["id"] == second["id"]
    snapshot = m12["repository"].snapshot()
    linked = [adj for adj in snapshot.adjustments.values() if adj.original_entry_id == marketing_entry["id"]]
    assert len(linked) == 1


def test_uat_m12_010_marketing_sales_company_always_conserve_the_base_pool(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(
        service, approver,
        create_policy(service, operator, "m12-010-policy", base_pool_rate="0.10", allocation_interpretation="whole_pool_decay"),
        "m12-010-approve",
    )
    recognition_id = "50000000-0000-0000-0000-000000000012"
    customer_id = "70000000-0000-0000-0000-000000000012"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000012", customer_id=customer_id, amount="333.33")
    seed_cohort(m12, customer_id=customer_id, customer_year=2)
    seed_conversion(m12, recognition_id=recognition_id, conversion_id="80000000-0000-0000-0000-000000000003", salesperson_id=SALES_A, approved=True)

    preview = service.preview_commissions(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-010-preview").data
    line = preview["lines"][0]
    total = Decimal(line["marketing_award"]["amount"]) + Decimal(line["sales_award"]["amount"]) + Decimal(line["company_retained"]["amount"])
    assert total == Decimal(line["base_pool"]["amount"])
    # The control total exposed on the preview matches the sum of base pools too.
    assert Decimal(preview["control_total"]["amount"]) == Decimal(line["base_pool"]["amount"])


def test_uat_m12_011_maker_cannot_self_approve_and_payout_requires_a_deduped_receipt(m12, operator, approver, payroll_operator, payroll_adapter):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-011-policy"), "m12-011-approve")
    recognition_id = "50000000-0000-0000-0000-000000000013"
    customer_id = "70000000-0000-0000-0000-000000000013"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000013", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)

    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-011-run").data
    run_jobs(m12)

    maker_as_approver = ActorContext(TENANT, OPERATOR, frozenset({"commission_operator", "commission_approver"}))
    with pytest.raises(ForbiddenError) as excinfo:
        service.approve_commission_run(
            maker_as_approver, run["id"], {"decision": "approve", "reason_code": "self_review"},
            expected_version=run["version"], idempotency_key="m12-011-self-approve",
        )
    assert excinfo.value.code == "segregation_conflict"

    approved_run = service.approve_commission_run(
        approver, run["id"], {"decision": "approve", "reason_code": "reviewed"},
        expected_version=run["version"], idempotency_key="m12-011-approve",
    ).data
    assert approved_run["state"] == "approved"

    batch = service.create_commission_payout_batch(
        payroll_operator,
        {"approved_run_ids": [run["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="m12-011-batch",
    ).data
    run_jobs(m12)
    refreshed_batch = service.get_commission_payout_batch(payroll_operator, batch["id"]).data
    assert refreshed_batch["state"] == "approval_pending"

    maker_as_batch_approver = ActorContext(TENANT, PAYROLL_OPERATOR, frozenset({"payroll_operator", "commission_approver"}))
    with pytest.raises(ForbiddenError) as excinfo:
        service.approve_commission_payout_batch(
            maker_as_batch_approver, batch["id"], {"decision": "approve", "reason_code": "self_review"},
            expected_version=refreshed_batch["version"], idempotency_key="m12-011-batch-self-approve",
        )
    assert excinfo.value.code == "segregation_conflict"

    approved_batch = service.approve_commission_payout_batch(
        approver, batch["id"], {"decision": "approve", "reason_code": "control_total_checked"},
        expected_version=refreshed_batch["version"], idempotency_key="m12-011-batch-approve",
    ).data
    assert approved_batch["state"] == "approved"

    marketing_entry = next(e for e in entries_for_run(m12, run["id"]) if e["recipient_type"] == "marketing")

    ack_body = {
        "event_id": "90000000-0000-0000-0000-000000000005",
        "external_batch_ref": "payroll-ref-001",
        "control_sha256": approved_batch["control_sha256"],
        "status": "accepted",
        "accepted_entry_ids": [marketing_entry["id"]],
        "rejected_entry_ids": [],
        "acknowledgement_evidence_id": EVIDENCE_ACK,
    }
    acked = service.acknowledge_commission_payout_batch(payroll_adapter, batch["id"], ack_body, idempotency_key="m12-011-ack").data
    assert acked["state"] == "acknowledged"

    # A duplicate webhook delivery (same provider event_id, different Idempotency-Key) does not reprocess or re-pay.
    replay = service.acknowledge_commission_payout_batch(payroll_adapter, batch["id"], ack_body, idempotency_key="m12-011-ack-retry").data
    assert replay["state"] == "acknowledged"
    assert replay["version"] == acked["version"]


def test_uat_m12_012_a_paid_entrys_reversal_is_flagged_for_manual_recovery_not_auto_deducted(m12, operator, approver, payroll_operator, payroll_adapter):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "m12-012-policy"), "m12-012-approve")
    recognition_id = "50000000-0000-0000-0000-000000000014"
    customer_id = "70000000-0000-0000-0000-000000000014"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000014", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="m12-012-run").data
    run_jobs(m12)
    marketing_entry = next(e for e in entries_for_run(m12, run["id"]) if e["recipient_type"] == "marketing")

    approved_run = service.approve_commission_run(
        approver, run["id"], {"decision": "approve", "reason_code": "reviewed"},
        expected_version=run["version"], idempotency_key="m12-012-run-approve",
    ).data
    batch = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [run["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="m12-012-batch",
    ).data
    run_jobs(m12)
    refreshed_batch = service.get_commission_payout_batch(payroll_operator, batch["id"]).data
    approved_batch = service.approve_commission_payout_batch(
        approver, batch["id"], {"decision": "approve", "reason_code": "checked"},
        expected_version=refreshed_batch["version"], idempotency_key="m12-012-batch-approve",
    ).data
    service.acknowledge_commission_payout_batch(
        payroll_adapter, batch["id"],
        {
            "event_id": "90000000-0000-0000-0000-000000000006",
            "external_batch_ref": "payroll-ref-002",
            "control_sha256": approved_batch["control_sha256"],
            "status": "accepted",
            "accepted_entry_ids": [marketing_entry["id"]],
            "rejected_entry_ids": [],
            "acknowledgement_evidence_id": EVIDENCE_ACK,
        },
        idempotency_key="m12-012-ack",
    )

    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000007", original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-100")
    adjustment = service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": marketing_entry["id"],
            "refund_id": "90000000-0000-0000-0000-000000000007",
            "attributable_recognition_id": recognition_id,
            "adjustment_amount": {"amount": "-100", "currency": "TWD"},
            "reason_code": "refund_after_payout",
            "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="m12-012-adjustment",
    ).data
    assert adjustment["payroll_offset_allowed"] is False
