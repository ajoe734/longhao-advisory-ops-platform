from __future__ import annotations

import pytest

from modules.commissions.automation import CommissionExplainerService, LocalRuleExplainer
from modules.commissions.domain import ActorContext, ForbiddenError, NotFoundError, ValidationError

from conftest import (
    EVIDENCE_PARAMETER,
    MARKETING_ATTRIBUTION,
    SALES_A,
    SALES_B,
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
)


OTHER_TENANT = "10000000-0000-0000-0000-000000000099"
OTHER_ACTOR = "20000000-0000-0000-0000-000000000099"


def test_cross_tenant_staff_cannot_read_run_payout_batch_or_policy(m12, operator, approver, payroll_operator):
    service = m12["service"]
    draft_policy = create_policy(service, operator, "tenant-isolation-draft-policy")
    policy = approve_policy(service, approver, create_policy(service, operator, "tenant-isolation-policy"), "tenant-isolation-approve")
    recognition_id = "50000000-0000-0000-0000-000000000201"
    customer_id = "70000000-0000-0000-0000-000000000201"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000201", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="tenant-isolation-run").data
    run_jobs(m12)
    run = service.approve_commission_run(
        approver, run["id"], {"decision": "approve", "reason_code": "reviewed"}, expected_version=run["version"], idempotency_key="tenant-isolation-run-approve",
    ).data
    batch = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [run["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="tenant-isolation-batch",
    ).data

    m12["evidence"].add(OTHER_TENANT, EVIDENCE_PARAMETER)
    other_approver = ActorContext(OTHER_TENANT, OTHER_ACTOR, frozenset({"commission_approver"}))
    other_payroll = ActorContext(OTHER_TENANT, OTHER_ACTOR, frozenset({"payroll_operator", "commission_approver"}))
    with pytest.raises(NotFoundError):
        service.get_commission_run(other_approver, run["id"])
    with pytest.raises(NotFoundError):
        service.get_commission_payout_batch(other_payroll, batch["id"])
    with pytest.raises(NotFoundError):
        service.approve_commission_policy(
            other_approver, draft_policy["id"],
            {"decision": "approve", "policy_sha256": draft_policy["policy_sha256"], "approved_base_pool_rate": "0.10",
             "parameter_decision_evidence_asset_id": EVIDENCE_PARAMETER, "effective_from": draft_policy["effective_from"],
             "reason_code": "cross_tenant_attempt"},
            expected_version=draft_policy["version"], idempotency_key="tenant-isolation-cross-approve",
        )
    # The owning tenant's own view is unaffected by the cross-tenant lookups above.
    assert service.get_commission_run(approver, run["id"]).data["id"] == run["id"]


def test_unauthorized_roles_are_forbidden_from_every_sensitive_operation(m12, operator, approver, payroll_operator, payroll_adapter):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "rbac-policy"), "rbac-approve")
    recognition_id = "50000000-0000-0000-0000-000000000202"
    customer_id = "70000000-0000-0000-0000-000000000202"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000202", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="rbac-run").data
    run_jobs(m12)
    run = service.approve_commission_run(
        approver, run["id"], {"decision": "approve", "reason_code": "reviewed"}, expected_version=run["version"], idempotency_key="rbac-run-approve",
    ).data
    batch = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [run["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="rbac-batch",
    ).data

    unaffiliated = ActorContext(TENANT, "20000000-0000-0000-0000-000000000098", frozenset({"service_agent"}))
    with pytest.raises(ForbiddenError):
        service.create_commission_policy(unaffiliated, {}, idempotency_key="rbac-deny-create-policy")
    with pytest.raises(ForbiddenError):
        service.approve_commission_policy(unaffiliated, policy["id"], {}, expected_version=policy["version"], idempotency_key="rbac-deny-approve-policy")
    with pytest.raises(ForbiddenError):
        service.create_commission_run(unaffiliated, run_request(policy["id"]), idempotency_key="rbac-deny-create-run")
    with pytest.raises(ForbiddenError):
        service.approve_commission_run(unaffiliated, run["id"], {}, expected_version=run["version"], idempotency_key="rbac-deny-approve-run")
    with pytest.raises(ForbiddenError):
        service.create_commission_adjustment(unaffiliated, {}, idempotency_key="rbac-deny-adjustment")
    with pytest.raises(ForbiddenError):
        service.create_commission_payout_batch(unaffiliated, {}, idempotency_key="rbac-deny-batch")
    with pytest.raises(ForbiddenError):
        service.approve_commission_payout_batch(unaffiliated, batch["id"], {}, expected_version=batch["version"], idempotency_key="rbac-deny-batch-approve")
    with pytest.raises(ForbiddenError):
        service.acknowledge_commission_payout_batch(unaffiliated, batch["id"], {}, idempotency_key="rbac-deny-batch-ack")
    with pytest.raises(ForbiddenError):
        service.list_commission_entries(unaffiliated)


def test_allowed_actions_hide_approval_from_maker_and_non_approver(m12, operator, approver, payroll_operator):
    """Regression for F3: allowed_actions used to expose approve_* to makers and non-approvers.

    ``get_commission_run``/``get_commission_payout_batch`` recompute ``allowed_actions``
    fresh for whichever context calls them, so they are a clean read-only probe
    (policy has no standalone GET in the OpenAPI contract to probe the same way).
    """
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "allowed-actions-policy"), "allowed-actions-approve")
    recognition_id = "50000000-0000-0000-0000-000000000203"
    customer_id = "70000000-0000-0000-0000-000000000203"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000203", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="allowed-actions-run").data
    run_jobs(m12)

    maker_as_approver = ActorContext(TENANT, operator.actor_id, frozenset({"commission_operator", "commission_approver"}))
    seen_by_maker = service.get_commission_run(maker_as_approver, run["id"]).data
    assert seen_by_maker["allowed_actions"] == []  # maker cannot approve their own run, even holding the approver role

    non_approver = ActorContext(TENANT, "20000000-0000-0000-0000-000000000097", frozenset({"commission_operator"}))
    seen_by_non_approver = service.get_commission_run(non_approver, run["id"]).data
    assert seen_by_non_approver["allowed_actions"] == []  # lacks commission_approver entirely

    seen_by_distinct_approver = service.get_commission_run(approver, run["id"]).data
    assert seen_by_distinct_approver["allowed_actions"] == ["approve_commission_run"]

    approved_run = service.approve_commission_run(
        approver, run["id"], {"decision": "approve", "reason_code": "reviewed"},
        expected_version=run["version"], idempotency_key="allowed-actions-run-approve",
    ).data
    batch = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [approved_run["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="allowed-actions-batch",
    ).data
    run_jobs(m12)

    maker_payroll_as_approver = ActorContext(TENANT, payroll_operator.actor_id, frozenset({"payroll_operator", "commission_approver"}))
    assert service.get_commission_payout_batch(maker_payroll_as_approver, batch["id"]).data["allowed_actions"] == []
    assert service.get_commission_payout_batch(approver, batch["id"]).data["allowed_actions"] == ["approve_commission_payout_batch"]


def test_sales_role_only_sees_its_own_entries_in_list_commission_entries(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(
        service, approver,
        create_policy(service, operator, "sales-scoping-policy", allocation_interpretation="marketing_decay_only"),
        "sales-scoping-approve",
    )
    recognition_a = "50000000-0000-0000-0000-000000000204"
    customer_a = "70000000-0000-0000-0000-000000000204"
    seed_recognition(m12, recognition_id=recognition_a, contract_id="60000000-0000-0000-0000-000000000204", customer_id=customer_a, amount="1000")
    seed_cohort(m12, customer_id=customer_a, customer_year=2, marketing_attribution_id=MARKETING_ATTRIBUTION)
    seed_conversion(m12, recognition_id=recognition_a, conversion_id="80000000-0000-0000-0000-000000000011", salesperson_id=SALES_A, approved=True)

    recognition_b = "50000000-0000-0000-0000-000000000205"
    customer_b = "70000000-0000-0000-0000-000000000205"
    seed_recognition(m12, recognition_id=recognition_b, contract_id="60000000-0000-0000-0000-000000000205", customer_id=customer_b, amount="1000")
    seed_cohort(m12, customer_id=customer_b, customer_year=2, marketing_attribution_id=MARKETING_ATTRIBUTION)
    seed_conversion(m12, recognition_id=recognition_b, conversion_id="80000000-0000-0000-0000-000000000012", salesperson_id=SALES_B, approved=True)

    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_a, recognition_b]), idempotency_key="sales-scoping-run").data
    run_jobs(m12)

    all_entries = entries_for_run(m12, run["id"])
    assert {entry["recipient_id"] for entry in all_entries if entry["recipient_type"] == "sales"} == {SALES_A, SALES_B}

    sales_a_view = service.list_commission_entries(sales_context(SALES_A), limit=200).data
    assert sales_a_view["items"]
    assert all(item["recipient_id"] == SALES_A for item in sales_a_view["items"])
    assert not any(item["recipient_id"] == SALES_B for item in sales_a_view["items"])

    sales_b_view = service.list_commission_entries(sales_context(SALES_B), limit=200).data
    assert all(item["recipient_id"] == SALES_B for item in sales_b_view["items"])

    # commission_operator/commission_approver are not row-scoped; they see both.
    full_view = service.list_commission_entries(approver, limit=200).data
    assert {SALES_A, SALES_B} <= {item["recipient_id"] for item in full_view["items"] if item["recipient_type"] == "sales"}


def test_commission_explainer_produces_untrusted_draft_without_command_authority(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(
        service, approver,
        create_policy(service, operator, "explainer-policy", allocation_interpretation="marketing_decay_only"),
        "explainer-approve",
    )
    recognition_id = "50000000-0000-0000-0000-000000000206"
    customer_id = "70000000-0000-0000-0000-000000000206"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000206", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=2)
    seed_conversion(m12, recognition_id=recognition_id, conversion_id="80000000-0000-0000-0000-000000000013", salesperson_id=SALES_A, approved=True)
    preview = service.preview_commissions(operator, run_request(policy["id"], [recognition_id]), idempotency_key="explainer-preview").data

    explainer = CommissionExplainerService(LocalRuleExplainer())
    run = explainer.explain_lines(operator, preview["lines"])
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert len(run.notes) == 1
    assert run.notes[0].recognition_id == recognition_id
    # Explaining never mutates domain state: no new run/policy/entry was created by this read-only capability.
    snapshot_before = m12["repository"].snapshot()
    explainer.explain_lines(approver, preview["lines"])
    snapshot_after = m12["repository"].snapshot()
    assert len(snapshot_after.runs) == len(snapshot_before.runs)
    assert len(snapshot_after.policies) == len(snapshot_before.policies)

    sales_only = sales_context(SALES_A)
    with pytest.raises(ForbiddenError):
        explainer.explain_lines(sales_only, preview["lines"])
    with pytest.raises(ValidationError):
        explainer.explain_lines(operator, [])
    with pytest.raises(ValidationError):
        explainer.explain_lines(operator, [{"not_recognition_id": "x"}])


def test_production_automation_refuses_local_fake_explainer():
    with pytest.raises(RuntimeError):
        CommissionExplainerService(LocalRuleExplainer(), environment="production")
