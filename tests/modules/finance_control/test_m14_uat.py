"""Executable UAT-M14-001..012 cases from docs/modules/M14-finance_control.md §14.

Each test calls the real FinanceControlService command/query surface (no
shortcut assertions on constants); failures reproduce the documented
business defect and the implementation must make them pass.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from modules.finance_control.domain import ConflictError, DependencyUnavailableError, ForbiddenError, Payment, canonical_hash, utc_now, wire_time
from modules.finance_control.ports import RefundProviderResult, SettlementLine
from modules.finance_control.repository import InMemoryRepository
from modules.finance_control.service import FinanceControlService

from workers.tasks.finance_control.consumer import M14EventConsumer
from workers.tasks.finance_control.tasks import LocalEventBus, OutboxPublisher

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


def _envelope(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict) -> dict:
    return {
        "event_id": str(uuid4()), "event_type": event_type, "producer": producer, "tenant_id": TENANT,
        "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "sequence_no": sequence, "payload": payload,
    }


EV1 = "00000000-0000-0000-0000-000000000001"
EV2 = "00000000-0000-0000-0000-000000000002"
EV3 = "00000000-0000-0000-0000-000000000003"
EV4 = "00000000-0000-0000-0000-000000000004"
EV5 = "00000000-0000-0000-0000-000000000005"


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
    assert refreshed_invoice.status == "paid"  # fully covered -> no second payment intent should be offerable


def test_uat_m14_001b_payment_webhook_rejects_new_event_id_double_credit_downgrade_and_mismatch(m14, operator, payment_adapter_ctx):
    """A second 'paid' webhook under a *different* event_id (a provider retry
    that mints a new id, or two independent deliveries of the same fact) must
    not double-credit the invoice or re-emit finance.payment_confirmed; a
    stale downgrade to 'authorized' must be quarantined, not applied; and a
    'paid' webhook with a mismatched amount must raise a reconciliation
    exception instead of silently crediting the wrong figure."""
    service = m14["service"]
    _evidence(m14["evidence"], EV5)
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    invoice = service.create_invoice(operator, invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id), idempotency_key="idem-inv-001b").data
    intent = service.create_payment_intent(
        operator,
        {"application_id": str(uuid4()), "invoice_id": invoice["id"], "amount": money("1050"), "method": "card", "return_route": "application_progress"},
        idempotency_key="idem-pi-001b",
    ).data

    def callback(payment_intent_id: str, event_id: str, *, status: str, amount: str) -> dict:
        return {
            "event_id": event_id, "provider_transaction_ref": "ptx-uat-001b", "payment_intent_id": payment_intent_id,
            "status": status, "amount": money(amount), "provider_occurred_at": "2026-01-02T00:00:00Z", "evidence_asset_id": EV5,
        }

    first = service.receive_payment_webhook(payment_adapter_ctx, callback(intent["id"], str(uuid4()), status="paid", amount="1050"), idempotency_key="idem-wh-001b-1")
    assert first.data["status"] == "accepted"

    # a brand-new event_id reporting the *same* fact must be a no-op, not a second credit
    second = service.receive_payment_webhook(payment_adapter_ctx, callback(intent["id"], str(uuid4()), status="paid", amount="1050"), idempotency_key="idem-wh-001b-2")
    assert second.data["status"] == "duplicate"

    # a stale/out-of-order downgrade must never move a paid payment backwards
    third = service.receive_payment_webhook(payment_adapter_ctx, callback(intent["id"], str(uuid4()), status="authorized", amount="1050"), idempotency_key="idem-wh-001b-3")
    assert third.data["status"] == "quarantined"

    # an amount mismatch against the already-settled payment must raise an exception, not credit blindly
    fourth = service.receive_payment_webhook(payment_adapter_ctx, callback(intent["id"], str(uuid4()), status="paid", amount="999"), idempotency_key="idem-wh-001b-4")
    assert fourth.data["status"] == "quarantined"

    state = m14["repository"].snapshot()
    payment = state.payments[(TENANT, intent["id"])]
    assert payment.status == "paid"
    assert payment.version == 2  # only the first webhook ever mutated the payment
    refreshed_invoice = state.invoices[(TENANT, invoice["id"])]
    assert refreshed_invoice.paid_amount == Decimal("1050")  # never doubled by the duplicate or the mismatch
    confirmed_events = [e for e in state.outbox.values() if e.aggregate_id == intent["id"] and e.event_type == "finance.payment_confirmed"]
    assert len(confirmed_events) == 1
    mismatches = [item for item in state.reconciliation_items.values() if item.payment_id == intent["id"] and item.difference_type == "amount_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].expected_amount == Decimal("1050") and mismatches[0].actual_amount == Decimal("999")

    # a duplicate 'failed' webhook (new event_id, already-failed payment) must not
    # bump the version or re-emit finance.payment_failed a second time (R4/F3 regression)
    intent2 = service.create_payment_intent(
        operator, {"application_id": str(uuid4()), "amount": money("200"), "method": "card", "return_route": "application_progress"}, idempotency_key="idem-pi-001b-2",
    ).data
    failed_once = service.receive_payment_webhook(payment_adapter_ctx, callback(intent2["id"], str(uuid4()), status="failed", amount="200"), idempotency_key="idem-wh-001b-failed-1")
    assert failed_once.data["status"] == "accepted"
    failed_twice = service.receive_payment_webhook(payment_adapter_ctx, callback(intent2["id"], str(uuid4()), status="failed", amount="200"), idempotency_key="idem-wh-001b-failed-2")
    assert failed_twice.data["status"] == "duplicate"
    state2 = m14["repository"].snapshot()
    payment2 = state2.payments[(TENANT, intent2["id"])]
    assert payment2.status == "failed"
    assert payment2.version == 2  # only the first 'failed' webhook ever mutated the payment
    failed_events = [e for e in state2.outbox.values() if e.aggregate_id == intent2["id"] and e.event_type == "finance.payment_failed"]
    assert len(failed_events) == 1  # not re-emitted on the duplicate

    # the same applies to a same-state 'authorized' redelivery under a new event_id
    intent3 = service.create_payment_intent(
        operator, {"application_id": str(uuid4()), "amount": money("300"), "method": "card", "return_route": "application_progress"}, idempotency_key="idem-pi-001b-3",
    ).data
    authorized_once = service.receive_payment_webhook(payment_adapter_ctx, callback(intent3["id"], str(uuid4()), status="authorized", amount="300"), idempotency_key="idem-wh-001b-auth-1")
    assert authorized_once.data["status"] == "accepted"
    authorized_twice = service.receive_payment_webhook(payment_adapter_ctx, callback(intent3["id"], str(uuid4()), status="authorized", amount="300"), idempotency_key="idem-wh-001b-auth-2")
    assert authorized_twice.data["status"] == "duplicate"
    payment3 = m14["repository"].snapshot().payments[(TENANT, intent3["id"])]
    assert payment3.status == "authorized"
    assert payment3.version == 2  # only the first 'authorized' webhook ever mutated the payment


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

    # a schedule whose contract was never confirmed accepted/effective must be excluded from
    # recognition, not silently recognized, even when its period and cutoff otherwise match.
    customer2, contract2, service2 = str(uuid4()), str(uuid4()), str(uuid4())
    invoice2 = service.create_invoice(
        operator,
        invoice_body(customer_id=customer2, contract_id=contract2, contract_service_id=service2, period_start="2026-03-01", period_end="2026-04-01"),
        idempotency_key="idem-inv-002b",
    ).data
    intent2 = service.create_payment_intent(
        operator,
        {"application_id": str(uuid4()), "invoice_id": invoice2["id"], "amount": money("1050"), "method": "card", "return_route": "application_progress"},
        idempotency_key="idem-pi-002b",
    ).data
    service.receive_payment_webhook(
        payment_adapter_ctx,
        {
            "event_id": str(uuid4()), "provider_transaction_ref": "ptx-uat-002b", "payment_intent_id": intent2["id"], "status": "paid",
            "amount": money("1050"), "provider_occurred_at": "2026-03-02T00:00:00Z", "evidence_asset_id": EV2,
        },
        idempotency_key="idem-wh-002b",
    )
    run_unaccepted = service.run_revenue_recognition(
        operator,
        {"period_start": "2026-03-01", "period_end": "2026-04-01", "recognition_policy_version": RECOGNITION_POLICY_VERSION, "cutoff_at": "2026-03-15T00:00:00Z", "dry_run": False},
        idempotency_key="idem-run-002b",
    ).data
    assert run_unaccepted["entry_count"] == 0
    assert run_unaccepted["exception_count"] == 1  # excluded for lacking contract acceptance, not dropped silently

    # once the contract is confirmed accepted/effective with service already started, the
    # same schedule becomes eligible under a fresh run.
    service.apply_application_company_decided(_envelope("application.company_decided", "M02", "application", str(uuid4()), 1, {"decision": "accept", "contract_id": contract2}))
    service.apply_contract_effective(_envelope("contract.effective", "M02", "contract", contract2, 1, {
        "contract_id": contract2, "customer_id": customer2,
        "effective_at": "2026-02-15T00:00:00Z", "service_starts_at": "2026-02-15T00:00:00Z", "service_ends_at": "2027-02-15T00:00:00Z",
    }))
    run_accepted = service.run_revenue_recognition(
        operator,
        {"period_start": "2026-03-01", "period_end": "2026-04-01", "recognition_policy_version": RECOGNITION_POLICY_VERSION, "cutoff_at": "2026-03-20T00:00:00Z", "dry_run": False},
        idempotency_key="idem-run-002c",
    ).data
    assert run_accepted["entry_count"] == 1
    assert run_accepted["recognized_untaxed_total"]["amount"] == "1000"


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
            "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("500"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
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


def test_uat_m14_005b_refund_amount_cannot_exceed_original_payment_and_unapproved_webhook_is_quarantined(m14, operator, checker, executor):
    service = m14["service"]
    _evidence(m14["evidence"], EV5)
    customer_id, payment_id = str(uuid4()), str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("100"), provider_transaction_id="ptx-uat-005b")

    with pytest.raises(ConflictError) as excinfo:
        service.request_refund(
            operator,
            {
                "customer_id": customer_id, "contract_id": str(uuid4()), "original_payment_id": payment_id, "reason_code": "service_cancelled",
                "requested_amount": money("9999"),
                "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("9999"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
                "calculation_policy_version": "v1", "customer_request_evidence_id": EV5,
            },
            idempotency_key="idem-refund-005b-bad",
        )
    assert excinfo.value.code == "refund_amount_exceeds_payment"

    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id, "contract_id": str(uuid4()), "original_payment_id": payment_id, "reason_code": "service_cancelled",
            "requested_amount": money("100"),
            "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("100"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1", "customer_request_evidence_id": EV5,
        },
        idempotency_key="idem-refund-005b-ok",
    ).data

    # a refund that was never approved/executed must never be confirmed by a provider callback
    callback = {
        "event_id": str(uuid4()), "refund_id": refund["id"], "provider_refund_ref": "provref-005b-rogue", "status": "confirmed",
        "amount": money("100"), "provider_occurred_at": "2026-01-02T00:00:00Z", "evidence_asset_id": EV5,
    }
    from conftest import PAYMENT_ADAPTER_ACTOR

    from modules.finance_control.domain import ActorContext

    payment_adapter_ctx = ActorContext(TENANT, PAYMENT_ADAPTER_ACTOR, frozenset({"payment_adapter"}), principal_type="service")
    response = service.receive_refund_webhook(payment_adapter_ctx, callback, idempotency_key="idem-refund-webhook-005b")
    assert response.data["status"] == "quarantined"

    state = m14["repository"].snapshot()
    persisted = state.refunds[(TENANT, refund["id"])]
    assert persisted.status == "approval_pending"  # untouched by the rogue callback


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
            "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("250"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
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


def test_uat_m14_006b_refund_timeout_retry_with_a_new_provider_key_is_rejected(m14, operator, checker, executor):
    """A true provider timeout (no outcome ever registered) must durably persist
    provider_idempotency_key and status='submitted' *before* the provider call, so
    that even a crash/redrive that forgets the original key and sends a new one on
    retry is rejected -- the provider must never see two distinct submissions for
    the same refund (UAT-M14-006)."""
    service = m14["service"]
    _evidence(m14["evidence"], EV5)
    customer_id, payment_id = str(uuid4()), str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("300"), provider_transaction_id="ptx-uat-006b")
    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id,
            "contract_id": str(uuid4()),
            "original_payment_id": payment_id,
            "reason_code": "service_cancelled",
            "requested_amount": money("300"),
            "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("300"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1",
            "customer_request_evidence_id": EV5,
        },
        idempotency_key="idem-refund-006b",
    ).data
    approved = service.approve_refund(checker, refund["id"], {"decision": "approve", "reason_code": "ok"}, expected_version=1, idempotency_key="idem-approve-006b").data

    # no outcome registered for this key at all -> a genuine, unresolved provider timeout
    exec_body_1 = {"approval_record_id": refund["id"], "original_payment_id": payment_id, "provider_idempotency_key": "prov-timeout-key-1", "last_provider_status": "not_submitted"}
    with pytest.raises(DependencyUnavailableError):
        service.execute_refund(executor, refund["id"], exec_body_1, expected_version=approved["version"], idempotency_key="idem-exec-006b-1")

    state = m14["repository"].snapshot()
    persisted = state.refunds[(TENANT, refund["id"])]
    assert persisted.provider_idempotency_key == "prov-timeout-key-1"  # durably recorded, not rolled back by the raised error
    assert persisted.status == "submitted"
    assert m14["refund_provider"].submit_calls == ["prov-timeout-key-1"]

    # a retry that does not preserve the original provider key must never reach the provider again
    exec_body_2 = {**exec_body_1, "provider_idempotency_key": "prov-timeout-key-2"}
    with pytest.raises(ConflictError) as excinfo:
        service.execute_refund(executor, refund["id"], exec_body_2, expected_version=persisted.version, idempotency_key="idem-exec-006b-2")
    assert excinfo.value.code == "provider_idempotency_key_mismatch"
    assert m14["refund_provider"].submit_calls == ["prov-timeout-key-1"]  # exactly one external submission, ever


def test_execute_refund_does_not_double_finalize_when_webhook_confirms_during_provider_submit(m14, operator, checker, executor, payment_adapter_ctx):
    """A finance.refund_confirmed webhook racing in while execute_refund's
    Phase 2 provider call is still in flight must not let Phase 3 re-finalize
    the refund: that would double-reverse the GL and double-emit
    finance.refund_confirmed (F1/R2 regression)."""
    service = m14["service"]
    _evidence(m14["evidence"], EV3)
    customer_id, payment_id = str(uuid4()), str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("400"), provider_transaction_id="ptx-uat-race-001")
    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id, "contract_id": str(uuid4()), "original_payment_id": payment_id, "reason_code": "service_cancelled",
            "requested_amount": money("400"),
            "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("400"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1", "customer_request_evidence_id": EV3,
        },
        idempotency_key="idem-refund-race-001",
    ).data
    approved = service.approve_refund(checker, refund["id"], {"decision": "approve", "reason_code": "ok"}, expected_version=1, idempotency_key="idem-approve-race-001").data

    class RacingRefundProvider:
        is_fake = True

        def __init__(self, inner):
            self._inner = inner

        def submit(self, idempotency_key, amount, currency, reference):
            result = self._inner.submit(idempotency_key, amount, currency, reference)
            # the provider's own webhook beats execute_refund's Phase 3 commit back to the service
            webhook = {
                "event_id": str(uuid4()), "refund_id": refund["id"], "provider_refund_ref": result.provider_refund_ref,
                "status": "confirmed", "amount": money("400"), "provider_occurred_at": "2026-01-02T00:00:00Z", "evidence_asset_id": EV3,
            }
            response = service.receive_refund_webhook(payment_adapter_ctx, webhook, idempotency_key="idem-refund-webhook-race-001")
            assert response.data["status"] == "accepted"
            return result

        def query(self, idempotency_key):
            return self._inner.query(idempotency_key)

    m14["refund_provider"].register("prov-key-race-001", RefundProviderResult("confirmed", "provref-race-001", None))
    service.refund_provider = RacingRefundProvider(m14["refund_provider"])
    exec_body = {"approval_record_id": refund["id"], "original_payment_id": payment_id, "provider_idempotency_key": "prov-key-race-001", "last_provider_status": "not_submitted"}
    executed = service.execute_refund(executor, refund["id"], exec_body, expected_version=approved["version"], idempotency_key="idem-exec-race-001").data
    assert executed["state"] == "accounting_adjusted"

    state = m14["repository"].snapshot()
    journals = [j for j in state.journals.values() if j.source_id == refund["id"]]
    assert len(journals) == 1  # exactly one GL reversal, not two
    confirmed_events = [e for e in state.outbox.values() if e.aggregate_id == refund["id"] and e.event_type == "finance.refund_confirmed"]
    assert len(confirmed_events) == 1  # exactly one finance.refund_confirmed, not two


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
                {"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("600"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")},
                {"invoice_line_ref": payment_id, "recognition_id": recognition_id, "unearned_amount": money("0"), "earned_reversal_amount": money("300"), "tax_adjustment_amount": money("0")},
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
                "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("0"), "earned_reversal_amount": money("50"), "tax_adjustment_amount": money("0")}],
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
            "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("300"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
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


def test_uat_m14_012_control_exception_restore_drill_requires_independent_closure(
    m14, clock, operator, checker, executor, auditor, remediation_owner, payment_adapter_ctx,
):
    """A genuine restore drill: snapshot the live repository into a brand new
    InMemoryRepository plus brand new FinanceControlService/OutboxPublisher/
    M14EventConsumer instances -- as a restarted worker fleet would run
    against a database restored from backup -- then replay the same payment
    webhook, consumer event and execute_refund request. Idempotency state
    (webhook receipts, inbox receipts, the execute_refund receipt) must
    survive the restore so none of the replays double-charge, double-refund
    or double-journal, while outbox rows still pending at snapshot time must
    resume and drain exactly once."""
    service = m14["service"]
    repository = m14["repository"]
    evidence_pay, evidence_refund, evidence_ce = str(uuid4()), str(uuid4()), str(uuid4())
    _evidence(m14["evidence"], evidence_pay, evidence_refund, evidence_ce)

    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    invoice = service.create_invoice(operator, invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id), idempotency_key="idem-inv-012").data
    intent = service.create_payment_intent(
        operator,
        {"application_id": str(uuid4()), "invoice_id": invoice["id"], "amount": money("1050"), "method": "card", "return_route": "application_progress"},
        idempotency_key="idem-pi-012",
    ).data
    webhook_body = {
        "event_id": str(uuid4()), "provider_transaction_ref": "ptx-uat-012", "payment_intent_id": intent["id"],
        "status": "paid", "amount": money("1050"), "provider_occurred_at": "2026-01-02T00:00:00Z", "evidence_asset_id": evidence_pay,
    }
    first_webhook = service.receive_payment_webhook(payment_adapter_ctx, webhook_body, idempotency_key="idem-wh-012-1")
    assert first_webhook.data["status"] == "accepted"

    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id, "contract_id": contract_id, "original_payment_id": intent["id"], "reason_code": "service_cancelled",
            "requested_amount": money("1050"),
            "allocations": [{"invoice_line_ref": invoice["lines"][0]["invoice_line_id"], "recognition_id": None, "unearned_amount": money("1050"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1", "customer_request_evidence_id": evidence_refund,
        },
        idempotency_key="idem-refund-012",
    ).data
    approved = service.approve_refund(checker, refund["id"], {"decision": "approve", "reason_code": "ok"}, expected_version=1, idempotency_key="idem-approve-012").data
    m14["refund_provider"].register("prov-key-012", RefundProviderResult("confirmed", "provref-012", None))
    exec_body = {"approval_record_id": refund["id"], "original_payment_id": intent["id"], "provider_idempotency_key": "prov-key-012", "last_provider_status": "not_submitted"}
    executed = service.execute_refund(executor, refund["id"], exec_body, expected_version=approved["version"], idempotency_key="idem-exec-012").data
    assert executed["state"] == "accounting_adjusted"
    assert m14["refund_provider"].submit_calls == ["prov-key-012"]

    contract_payload = {
        "contract_id": contract_id, "customer_id": customer_id, "effective_at": "2026-01-01T00:00:00Z",
        "service_starts_at": "2026-01-01T00:00:00Z", "service_ends_at": "2027-01-01T00:00:00Z",
    }
    contract_envelope = {
        "event_id": str(uuid4()), "event_type": "contract.effective", "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": "M02", "aggregate_type": "contract", "aggregate_id": contract_id,
        "aggregate_version": 1, "sequence_no": 1, "occurred_at": wire_time(clock()), "emitted_at": wire_time(clock()),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": "idem-contract-012",
        "classification": "customer_confidential", "payload_sha256": canonical_hash(contract_payload), "payload": contract_payload,
    }
    assert M14EventConsumer(service).consume(contract_envelope).status == "applied"

    exception = service.record_control_exception(
        auditor,
        {"control_code": "restore_drill", "severity": "high", "description": "quarterly restore drill", "evidence_asset_ids": [evidence_ce], "remediation_owner_id": remediation_owner.actor_id, "due_at": "2026-03-01T00:00:00Z"},
        idempotency_key="idem-ce-012",
    ).data
    assert exception["state"] == "open"
    requested = service.act_on_control_exception(
        remediation_owner, exception["id"], {"action": "request_closure", "reason_code": "drill_rerun_complete", "evidence_asset_ids": [evidence_ce]}, expected_version=1, idempotency_key="idem-ce-012-req",
    ).data
    assert requested["state"] == "ready_for_review"

    pre_restore = repository.snapshot()
    pending_before = [e for e in pre_restore.outbox.values() if e.status == "pending"]
    assert len(pending_before) == 2  # finance.payment_confirmed + finance.refund_confirmed, never published yet
    journal_count_before = len(pre_restore.journals)
    paid_amount_before = pre_restore.invoices[(TENANT, invoice["id"])].paid_amount
    submit_calls_before = list(m14["refund_provider"].submit_calls)

    # --- restore drill: brand new repository + service + worker instances loaded from the backup ---
    restored_repository = InMemoryRepository()
    restored_repository.replace_for_test(pre_restore)
    restored_service = FinanceControlService(restored_repository, m14["refund_provider"], m14["statements"], m14["evidence"], m14["tax_mappings"], clock=clock)
    restored_bus = LocalEventBus()
    restored_publisher = OutboxPublisher(restored_repository, restored_bus, clock=clock)

    # replay the same payment webhook delivery (new Idempotency-Key, same provider event_id): must dedup, not double-credit
    replay_webhook = restored_service.receive_payment_webhook(payment_adapter_ctx, webhook_body, idempotency_key="idem-wh-012-replay")
    assert replay_webhook.data["status"] == "duplicate"

    # replay the same consumer envelope against a fresh consumer: inbox dedup must survive the restore
    assert M14EventConsumer(restored_service).consume(contract_envelope).status == "duplicate"

    # replay the same execute_refund request: the idempotency receipt must short-circuit before ever touching the provider
    replay_exec = restored_service.execute_refund(executor, refund["id"], exec_body, expected_version=approved["version"], idempotency_key="idem-exec-012").data
    assert replay_exec["state"] == "accounting_adjusted"
    assert m14["refund_provider"].submit_calls == submit_calls_before  # zero duplicate submits

    after_replay = restored_repository.snapshot()
    assert after_replay.invoices[(TENANT, invoice["id"])].paid_amount == paid_amount_before  # zero duplicate credits
    assert len(after_replay.journals) == journal_count_before  # zero duplicate journals

    # pending outbox rows resume and drain exactly once
    first_drain = restored_publisher.run_once("restore-worker-a")
    assert first_drain.processed == 2
    assert len(restored_bus.envelopes) == 2
    second_drain = restored_publisher.run_once("restore-worker-a")
    assert second_drain.processed == 0
    assert len(restored_bus.envelopes) == 2

    # the restore drill's own control exception still requires an independent closer after the restore
    with pytest.raises(ForbiddenError):
        restored_service.act_on_control_exception(
            remediation_owner, exception["id"], {"action": "approve_closure", "reason_code": "self", "evidence_asset_ids": [evidence_ce]}, expected_version=2, idempotency_key="idem-ce-012-self",
        )

    closed = restored_service.act_on_control_exception(
        auditor, exception["id"], {"action": "approve_closure", "reason_code": "verified", "evidence_asset_ids": [evidence_ce]}, expected_version=2, idempotency_key="idem-ce-012-approve",
    ).data
    assert closed["state"] == "closed"
    assert closed["closed_by"] == auditor.actor_id
