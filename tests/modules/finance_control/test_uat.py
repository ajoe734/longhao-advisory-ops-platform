"""Executable UAT-M14-001..012 cases from docs/modules/M14-finance_control.md §14.

Each test calls the real FinanceControlService command/query surface (no
shortcut assertions on constants); failures reproduce the documented
business defect and the implementation must make them pass.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from modules.finance_control.domain import ConflictError, ForbiddenError, Payment, utc_now
from modules.finance_control.ports import RefundProviderResult, SettlementLine

from conftest import (
    RECOGNITION_POLICY_VERSION,
    TAX_CODE,
    TENANT,
    invoice_body,
    money,
    seed_paid_payment,
    seed_recognition,
)


def _evidence(fixture, *assets: str) -> None:
    for asset in assets:
        fixture.add(TENANT, asset)


EV1 = "00000000-0000-0000-0000-000000000001"
EV2 = "00000000-0000-0000-0000-000000000002"
EV3 = "00000000-0000-0000-0000-000000000003"
EV4 = "00000000-0000-0000-0000-000000000004"


def test_uat_m14_001_duplicate_payment_webhook_applies_once(m14, operator, payment_adapter_ctx):
    service = m14["service"]
    _evidence(m14["evidence"], EV1)
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    invoice = service.create_invoice(operator, invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id), idempotency_key="idem-inv-001").data
    intent = service.create_payment_intent(
        operator,
        {"application_id": str(uuid4()), "invoice_id": invoice["id"], "amount": money("1050"), "method": "card", "return_route": "application_progress"},
        idempotency_key="idem-pi-001",
    ).data

    event_id = str(uuid4())
    callback = {
        "event_id": event_id,
        "provider_transaction_ref": "ptx-uat-001",
        "payment_intent_id": intent["id"],
        "status": "paid",
        "amount": money("1050"),
        "provider_occurred_at": "2026-01-02T00:00:00Z",
        "evidence_asset_id": EV1,
    }
    first = service.receive_payment_webhook(payment_adapter_ctx, callback, idempotency_key="idem-wh-001")
    second = service.receive_payment_webhook(payment_adapter_ctx, callback, idempotency_key="idem-wh-002")
    assert first.data["status"] == "accepted"
    assert second.data["status"] == "duplicate"

    state = m14["repository"].snapshot()
    payment = state.payments[(TENANT, intent["id"])]
    assert payment.status == "paid"
    assert payment.version == 2  # exactly one transition, not two
    outbox = [e for e in state.outbox.values() if e.aggregate_id == intent["id"] and e.event_type == "finance.payment_confirmed"]
    assert len(outbox) == 1
    refreshed_invoice = state.invoices[(TENANT, invoice["id"])]
    assert refreshed_invoice.paid_amount == Decimal("1050")  # not doubled


def test_uat_m14_002_prepayment_separated_from_recognized_revenue(m14, operator, checker, payment_adapter_ctx):
    service = m14["service"]
    _evidence(m14["evidence"], EV2)
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    invoice = service.create_invoice(operator, invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id), idempotency_key="idem-inv-002").data
    intent = service.create_payment_intent(
        operator,
        {"application_id": str(uuid4()), "invoice_id": invoice["id"], "amount": money("1050"), "method": "card", "return_route": "application_progress"},
        idempotency_key="idem-pi-002",
    ).data
    callback = {
        "event_id": str(uuid4()),
        "provider_transaction_ref": "ptx-uat-002",
        "payment_intent_id": intent["id"],
        "status": "paid",
        "amount": money("1050"),
        "provider_occurred_at": "2026-01-02T00:00:00Z",
        "evidence_asset_id": EV2,
    }
    service.receive_payment_webhook(payment_adapter_ctx, callback, idempotency_key="idem-wh-002")

    run = service.run_revenue_recognition(
        operator,
        {"period_start": "2026-03-01", "period_end": "2026-04-01", "recognition_policy_version": RECOGNITION_POLICY_VERSION, "cutoff_at": "2026-02-01T00:00:00Z", "dry_run": False},
        idempotency_key="idem-run-002",
    ).data
    # the invoice's service period (2026-01-01..02-01) is outside the queried period -> nothing eligible yet
    assert run["entry_count"] == 0
    assert run["recognized_untaxed_total"]["amount"] == "0"

    state = m14["repository"].snapshot()
    refreshed_invoice = state.invoices[(TENANT, invoice["id"])]
    assert refreshed_invoice.paid_amount == Decimal("1050")
    assert len(state.revenue_recognitions) == 0  # no commission-eligible revenue booked yet


def test_uat_m14_003_settlement_fee_and_revenue_kept_in_separate_columns(m14, operator):
    service = m14["service"]
    _evidence(m14["evidence"], EV3)
    payment_id = str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=str(uuid4()), amount=Decimal("1000"), provider="acme_pay", provider_transaction_id="ptx-uat-003")
    m14["statements"].register(TENANT, EV3, "a" * 64, [SettlementLine("ptx-uat-003", Decimal("1000"), Decimal("30"), Decimal("970"), "TWD", "bankline-003")])

    result = service.import_settlement(
        operator,
        {
            "provider": "acme_pay",
            "batch_ref": "batch-uat-003",
            "settlement_date": "2026-01-03",
            "statement_asset_id": EV3,
            "statement_sha256": "a" * 64,
            "declared_gross": money("1000"),
            "declared_fee": money("30"),
            "declared_net": money("970"),
            "declared_count": 1,
            "format_version": "v1",
        },
        idempotency_key="idem-settle-003",
    ).data
    assert result["gross"]["amount"] == "1000"
    assert result["fee"]["amount"] == "30"
    assert result["net"]["amount"] == "970"
    assert result["state"] == "matched"
    state = m14["repository"].snapshot()
    settlement = next(iter(state.settlements.values()))
    assert settlement.gross_amount == Decimal("1000") and settlement.fee_amount == Decimal("30") and settlement.net_amount == Decimal("970")


def test_uat_m14_004_unapproved_tax_mapping_blocks_invoice_then_succeeds_once_approved(m14, operator):
    service = m14["service"]
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    body = invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id)
    body["lines"][0]["tax_mapping_version"] = "not-yet-approved-v9"

    with pytest.raises(ConflictError) as excinfo:
        service.create_invoice(operator, body, idempotency_key="idem-inv-004a")
    assert excinfo.value.code == "tax_mapping_not_approved"

    m14["tax_mappings"].approve(TENANT, TAX_CODE, "not-yet-approved-v9")
    invoice = service.create_invoice(operator, body, idempotency_key="idem-inv-004b").data
    assert invoice["state"] == "issued"


def test_uat_m14_005_refund_maker_checker_executor_separation_of_duties(m14, operator, checker, executor):
    service = m14["service"]
    _evidence(m14["evidence"], EV4)
    customer_id, payment_id = str(uuid4()), str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("500"), provider_transaction_id="ptx-uat-005")
    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id,
            "contract_id": str(uuid4()),
            "original_payment_id": payment_id,
            "reason_code": "service_cancelled",
            "requested_amount": money("500"),
            "allocations": [{"invoice_line_ref": str(uuid4()), "recognition_id": None, "unearned_amount": money("500"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1",
            "customer_request_evidence_id": EV4,
        },
        idempotency_key="idem-refund-005",
    ).data

    # the preparer, even when also holding the checker role, cannot approve their own refund
    from modules.finance_control.domain import ActorContext

    maker_as_checker = ActorContext(operator.tenant_id, operator.actor_id, frozenset({"finance_checker"}))
    with pytest.raises(ForbiddenError) as excinfo:
        service.approve_refund(maker_as_checker, refund["id"], {"decision": "approve", "reason_code": "self"}, expected_version=1, idempotency_key="idem-approve-005-bad")
    assert excinfo.value.code == "maker_checker_violation"

    approved = service.approve_refund(checker, refund["id"], {"decision": "approve", "reason_code": "ok"}, expected_version=1, idempotency_key="idem-approve-005-ok").data
    assert approved["state"] == "approved"

    # a non-service identity cannot execute
    with pytest.raises(ForbiddenError):
        service.execute_refund(
            checker,
            refund["id"],
            {"approval_record_id": refund["id"], "original_payment_id": payment_id, "provider_idempotency_key": "prov-key-005", "last_provider_status": "not_submitted"},
            expected_version=2,
            idempotency_key="idem-exec-005-bad",
        )

    m14["refund_provider"].register("prov-key-005", RefundProviderResult("confirmed", "provref-005", None))
    executed = service.execute_refund(
        executor,
        refund["id"],
        {"approval_record_id": refund["id"], "original_payment_id": payment_id, "provider_idempotency_key": "prov-key-005", "last_provider_status": "not_submitted"},
        expected_version=2,
        idempotency_key="idem-exec-005-ok",
    ).data
    assert executed["state"] == "accounting_adjusted"


def test_uat_m14_006_refund_timeout_queries_before_resubmitting(m14, operator, checker, executor):
    service = m14["service"]
    _evidence(m14["evidence"], EV1)
    customer_id, payment_id = str(uuid4()), str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("250"), provider_transaction_id="ptx-uat-006")
    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id,
            "contract_id": str(uuid4()),
            "original_payment_id": payment_id,
            "reason_code": "service_cancelled",
            "requested_amount": money("250"),
            "allocations": [{"invoice_line_ref": str(uuid4()), "recognition_id": None, "unearned_amount": money("250"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1",
            "customer_request_evidence_id": EV1,
        },
        idempotency_key="idem-refund-006",
    ).data
    approved = service.approve_refund(checker, refund["id"], {"decision": "approve", "reason_code": "ok"}, expected_version=1, idempotency_key="idem-approve-006").data

    m14["refund_provider"].register("prov-key-006", "timeout")
    exec_body = {"approval_record_id": refund["id"], "original_payment_id": payment_id, "provider_idempotency_key": "prov-key-006", "last_provider_status": "not_submitted"}
    first = service.execute_refund(executor, refund["id"], exec_body, expected_version=approved["version"], idempotency_key="idem-exec-006-1").data
    assert first["state"] == "reconciliation_required"
    second = service.execute_refund(executor, refund["id"], exec_body, expected_version=first["version"], idempotency_key="idem-exec-006-2").data
    assert second["state"] == "reconciliation_required"
    assert m14["refund_provider"].submit_calls == ["prov-key-006"]  # never resubmitted

    m14["refund_provider"]._outcomes["prov-key-006"] = RefundProviderResult("confirmed", "provref-late-006", None)
    third = service.execute_refund(executor, refund["id"], exec_body, expected_version=second["version"], idempotency_key="idem-exec-006-3").data
    assert third["state"] == "accounting_adjusted"
    assert m14["refund_provider"].submit_calls == ["prov-key-006"]


def test_uat_m14_007_refund_splits_unearned_and_earned_reversal(m14, operator):
    service = m14["service"]
    _evidence(m14["evidence"], EV2)
    customer_id, payment_id, recognition_id = str(uuid4()), str(uuid4()), str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("1200"), provider_transaction_id="ptx-uat-007")
    seed_recognition(m14["repository"], tenant=TENANT, recognition_id=recognition_id, untaxed_amount=Decimal("600"))

    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id,
            "contract_id": str(uuid4()),
            "original_payment_id": payment_id,
            "reason_code": "partial_service_cancel",
            "requested_amount": money("900"),
            "allocations": [
                {"invoice_line_ref": str(uuid4()), "recognition_id": None, "unearned_amount": money("600"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")},
                {"invoice_line_ref": str(uuid4()), "recognition_id": recognition_id, "unearned_amount": money("0"), "earned_reversal_amount": money("300"), "tax_adjustment_amount": money("0")},
            ],
            "calculation_policy_version": "v1",
            "customer_request_evidence_id": EV2,
        },
        idempotency_key="idem-refund-007",
    ).data
    unearned_line, earned_line = refund["allocations"]
    assert unearned_line["unearned_amount"]["amount"] == "600" and unearned_line["earned_reversal_amount"]["amount"] == "0"
    assert earned_line["earned_reversal_amount"]["amount"] == "300" and earned_line["recognition_id"] == recognition_id

    # earned_reversal_amount without a recognition_id must be rejected
    with pytest.raises(Exception):
        service.request_refund(
            operator,
            {
                "customer_id": customer_id,
                "contract_id": str(uuid4()),
                "original_payment_id": payment_id,
                "reason_code": "partial_service_cancel",
                "requested_amount": money("50"),
                "allocations": [{"invoice_line_ref": str(uuid4()), "recognition_id": None, "unearned_amount": money("0"), "earned_reversal_amount": money("50"), "tax_adjustment_amount": money("0")}],
                "calculation_policy_version": "v1",
                "customer_request_evidence_id": EV2,
            },
            idempotency_key="idem-refund-007-bad",
        )


def test_uat_m14_008_unbalanced_journal_rejected_from_accounting_export(m14, operator):
    from modules.finance_control.domain import Journal, JournalLine

    service = m14["service"]
    balanced_id, unbalanced_id = str(uuid4()), str(uuid4())

    def seed(state):
        state.journals[(TENANT, balanced_id)] = Journal(
            id=balanced_id, tenant_id=TENANT, journal_number="JNL-UAT-008-OK", source_type="revenue", source_id=str(uuid4()), currency="TWD",
            accounting_date=utc_now().date(), prepared_by=str(uuid4()),
            lines=[JournalLine(1, "deferred_revenue_liability", Decimal("100"), Decimal("0")), JournalLine(2, "service_revenue", Decimal("0"), Decimal("100"))],
            status="posted", approved_by=str(uuid4()), posted_at=utc_now(),
        )
        state.journals[(TENANT, unbalanced_id)] = Journal(
            id=unbalanced_id, tenant_id=TENANT, journal_number="JNL-UAT-008-BAD", source_type="revenue", source_id=str(uuid4()), currency="TWD",
            accounting_date=utc_now().date(), prepared_by=str(uuid4()),
            lines=[JournalLine(1, "deferred_revenue_liability", Decimal("100.01"), Decimal("0")), JournalLine(2, "service_revenue", Decimal("0"), Decimal("100"))],
            status="posted", approved_by=str(uuid4()), posted_at=utc_now(),
        )

    m14["repository"].atomic(seed)
    with pytest.raises(ConflictError) as excinfo:
        service.create_accounting_export(
            operator,
            {"period_start": "2026-01-01", "period_end": "2026-02-01", "journal_ids": [unbalanced_id], "account_mapping_version": "v1", "tax_mapping_version": "v1", "export_format": "json_v1"},
            idempotency_key="idem-export-008-bad",
        )
    assert excinfo.value.code == "journal_unbalanced"

    ok = service.create_accounting_export(
        operator,
        {"period_start": "2026-01-01", "period_end": "2026-02-01", "journal_ids": [balanced_id], "account_mapping_version": "v1", "tax_mapping_version": "v1", "export_format": "json_v1"},
        idempotency_key="idem-export-008-ok",
    ).data
    assert ok["debit_total"]["amount"] == "100" and ok["credit_total"]["amount"] == "100"


def test_uat_m14_009_accounting_export_ack_resend_is_idempotent(m14, operator, accounting_adapter_ctx):
    from modules.finance_control.domain import Journal, JournalLine

    service = m14["service"]
    journal_id = str(uuid4())

    def seed(state):
        state.journals[(TENANT, journal_id)] = Journal(
            id=journal_id, tenant_id=TENANT, journal_number="JNL-UAT-009", source_type="revenue", source_id=str(uuid4()), currency="TWD",
            accounting_date=utc_now().date(), prepared_by=str(uuid4()),
            lines=[JournalLine(1, "deferred_revenue_liability", Decimal("500"), Decimal("0")), JournalLine(2, "service_revenue", Decimal("0"), Decimal("500"))],
            status="posted", approved_by=str(uuid4()), posted_at=utc_now(),
        )

    m14["repository"].atomic(seed)
    export = service.create_accounting_export(
        operator,
        {"period_start": "2026-01-01", "period_end": "2026-02-01", "journal_ids": [journal_id], "account_mapping_version": "v1", "tax_mapping_version": "v1", "export_format": "json_v1"},
        idempotency_key="idem-export-009",
    ).data

    event_id = str(uuid4())
    ack_body = {
        "event_id": event_id,
        "external_batch_ref": "erp-batch-009",
        "export_sha256": export["export_sha256"],
        "status": "accepted",
        "accepted_journal_ids": [journal_id],
        "rejected_journal_ids": [],
        "rejection_evidence_asset_id": None,
    }
    first = service.acknowledge_accounting_export(accounting_adapter_ctx, export["id"], ack_body, idempotency_key="idem-ack-009-1").data
    second = service.acknowledge_accounting_export(accounting_adapter_ctx, export["id"], dict(ack_body), idempotency_key="idem-ack-009-2").data
    assert first["state"] == "acknowledged" and second["state"] == "acknowledged"
    assert first["version"] == second["version"]  # the resend never re-applied

    state = m14["repository"].snapshot()
    outbox = [e for e in state.outbox.values() if e.event_type == "finance.accounting_export_acknowledged" and e.aggregate_id == export["id"]]
    assert len(outbox) == 1  # ack applied exactly once despite the resend


def test_uat_m14_010_evidence_export_manifest_traces_audit_chain(m14, operator, checker, auditor):
    service = m14["service"]
    _evidence(m14["evidence"], EV3, EV4)
    customer_id, payment_id = str(uuid4()), str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("300"), provider_transaction_id="ptx-uat-010")
    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id,
            "contract_id": str(uuid4()),
            "original_payment_id": payment_id,
            "reason_code": "service_cancelled",
            "requested_amount": money("300"),
            "allocations": [{"invoice_line_ref": str(uuid4()), "recognition_id": None, "unearned_amount": money("300"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1",
            "customer_request_evidence_id": EV3,
        },
        idempotency_key="idem-refund-010",
    ).data

    export = service.create_evidence_export(
        auditor,
        {"scope": "refund", "subject_id": refund["id"], "purpose": "internal_audit", "include_restricted_aml": False, "approval_record_id": EV4, "expires_at": "2027-01-01T00:00:00Z"},
        idempotency_key="idem-ee-010",
    ).data
    assert export["state"] == "ready"
    assert export["manifest_sha256"] is not None

    fetched = service.get_evidence_export(auditor, export["id"]).data
    assert fetched["manifest_sha256"] == export["manifest_sha256"]

    audit_page = service.list_audit_events(auditor).data
    actions = {item["action"] for item in audit_page["items"]}
    assert "m14.request_refund" in actions
    assert "m14.create_evidence_export" in actions


def test_uat_m14_011_legal_hold_blocks_purge_until_independent_release(m14, compliance_supervisor_a, compliance_supervisor_b):
    service = m14["service"]
    _evidence(m14["evidence"], EV1)
    subject_id = str(uuid4())
    hold = service.create_legal_hold(
        compliance_supervisor_a,
        {"scope": "customer", "subject_ids": [subject_id], "reason_code": "litigation_hold", "authority_evidence_asset_id": EV1, "review_due_at": "2027-01-01T00:00:00Z"},
        idempotency_key="idem-hold-011",
    ).data

    skip = service.evaluate_purge_candidate(compliance_supervisor_a, "customer", subject_id)
    assert skip == {"action": "skip", "reason_code": "legal_hold_active", "hold_id": hold["id"]}

    with pytest.raises(ForbiddenError):
        service.release_legal_hold(
            compliance_supervisor_a,
            hold["id"],
            {"reason_code": "resolved", "release_authority_evidence_id": EV1, "independent_approver_id": str(uuid4()), "effective_at": "2026-03-01T00:00:00Z"},
            expected_version=1,
            idempotency_key="idem-release-011-bad",
        )

    released = service.release_legal_hold(
        compliance_supervisor_b,
        hold["id"],
        {"reason_code": "resolved", "release_authority_evidence_id": EV1, "independent_approver_id": str(uuid4()), "effective_at": "2026-03-01T00:00:00Z"},
        expected_version=1,
        idempotency_key="idem-release-011-ok",
    ).data
    assert released["state"] == "released"

    eligible = service.evaluate_purge_candidate(compliance_supervisor_a, "customer", subject_id)
    assert eligible == {"action": "eligible_for_delete", "reason_code": "no_active_hold"}


def test_uat_m14_012_control_exception_restore_drill_requires_independent_closure(m14, remediation_owner, auditor):
    service = m14["service"]
    _evidence(m14["evidence"], EV2)
    exception = service.record_control_exception(
        auditor,
        {"control_code": "restore_drill", "severity": "high", "description": "quarterly restore drill", "evidence_asset_ids": [EV2], "remediation_owner_id": remediation_owner.actor_id, "due_at": "2026-03-01T00:00:00Z"},
        idempotency_key="idem-ce-012",
    ).data
    assert exception["state"] == "open"

    requested = service.act_on_control_exception(
        remediation_owner, exception["id"], {"action": "request_closure", "reason_code": "drill_rerun_complete", "evidence_asset_ids": [EV2]}, expected_version=1, idempotency_key="idem-ce-012-req",
    ).data
    assert requested["state"] == "ready_for_review"

    # the remediation owner cannot approve their own closure
    with pytest.raises(ForbiddenError):
        service.act_on_control_exception(
            remediation_owner, exception["id"], {"action": "approve_closure", "reason_code": "self", "evidence_asset_ids": [EV2]}, expected_version=2, idempotency_key="idem-ce-012-self",
        )

    closed = service.act_on_control_exception(
        auditor, exception["id"], {"action": "approve_closure", "reason_code": "verified", "evidence_asset_ids": [EV2]}, expected_version=2, idempotency_key="idem-ce-012-approve",
    ).data
    assert closed["state"] == "closed"
    assert closed["closed_by"] == auditor.actor_id
