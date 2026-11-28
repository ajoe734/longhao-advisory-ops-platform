from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.finance_control.domain import AccountingExport, ActorContext, ConflictError, RevenueRun, ValidationError, canonical_hash, wire_time
from modules.finance_control.http import M14Controller, ROUTES
from modules.finance_control.ports import FakeEvidenceAssets, FakeRefundProvider, FakeSettlementStatements, FakeTaxMappings, RefundProviderResult
from modules.finance_control.repository import InMemoryRepository
from modules.finance_control.service import FinanceControlService
from workers.tasks.finance_control.consumer import M14EventConsumer
from workers.tasks.finance_control.tasks import LocalEventBus, OutboxPublisher

from conftest import NOW, OTHER_TENANT, TENANT, invoice_body, money, seed_paid_payment


ROOT = Path(__file__).resolve().parents[3]

_ALL_OPERATIONS = {
    "create_invoice", "create_payment_intent", "receive_payment_webhook", "import_settlement",
    "list_reconciliation_items", "resolve_reconciliation_item", "run_revenue_recognition", "list_revenue_runs",
    "get_revenue_run", "approve_revenue_run", "request_refund", "list_refunds", "get_refund", "approve_refund",
    "execute_refund", "receive_refund_webhook", "create_accounting_export", "get_accounting_export",
    "list_accounting_exports", "list_period_closes", "acknowledge_accounting_export", "list_audit_events", "create_evidence_export", "get_evidence_export",
    "create_legal_hold", "release_legal_hold", "record_control_exception", "act_on_control_exception",
}


def test_all_28_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == _ALL_OPERATIONS
    assert len(ROUTES) == 28


def test_controller_returns_etag_problem_and_replay_headers(m14, operator):
    controller = M14Controller(m14["service"])
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    body = invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id)
    first = controller.handle("create_invoice", operator, body=body, headers={"Idempotency-Key": "controller-key-001"})
    replay = controller.handle("create_invoice", operator, body=body, headers={"Idempotency-Key": "controller-key-001"})
    assert first.status == 201 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"

    denied = controller.handle(
        "resolve_reconciliation_item", operator, path={"item_id": str(uuid4())},
        body={"decision": "match", "payment_id": str(uuid4()), "reason_code": "x", "evidence_asset_id": str(uuid4())},
        headers={"Idempotency-Key": "controller-key-002", "If-Match": '"9"'},
    )
    assert denied.status == 403  # operator lacks finance_checker; role gate runs before resource lookup
    assert denied.headers["Content-Type"] == "application/problem+json"


def test_controller_rejects_malformed_if_match(m14, operator):
    controller = M14Controller(m14["service"])
    response = controller.handle(
        "resolve_reconciliation_item", operator, path={"item_id": str(uuid4())}, body={},
        headers={"Idempotency-Key": "controller-key-003", "If-Match": "not-an-etag"},
    )
    assert response.status == 422


def test_refund_allocation_must_reference_a_line_on_the_original_payment_invoice(m14, operator, payment_adapter_ctx):
    service = m14["service"]
    evidence_id = "00000000-0000-0000-0000-0000000000e1"
    m14["evidence"].add(TENANT, evidence_id)
    customer_id, contract_id = str(uuid4()), str(uuid4())
    paid_invoice = service.create_invoice(
        operator,
        invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=str(uuid4())),
        idempotency_key="refund-line-paid-invoice-001",
    ).data
    other_invoice = service.create_invoice(
        operator,
        invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=str(uuid4())),
        idempotency_key="refund-line-other-invoice-001",
    ).data
    intent = service.create_payment_intent(
        operator,
        {
            "application_id": str(uuid4()),
            "invoice_id": paid_invoice["id"],
            "amount": money("1050"),
            "method": "card",
            "return_route": "application_progress",
        },
        idempotency_key="refund-line-payment-001",
    ).data
    service.receive_payment_webhook(
        payment_adapter_ctx,
        {
            "event_id": str(uuid4()),
            "provider_transaction_ref": "refund-line-payment-001",
            "payment_intent_id": intent["id"],
            "status": "paid",
            "amount": money("1050"),
            "provider_occurred_at": wire_time(NOW),
            "evidence_asset_id": evidence_id,
        },
        idempotency_key="refund-line-webhook-001",
    )

    with pytest.raises(ValidationError, match="original payment invoice"):
        service.request_refund(
            operator,
            {
                "customer_id": customer_id,
                "contract_id": contract_id,
                "original_payment_id": intent["id"],
                "reason_code": "service_cancelled",
                "requested_amount": money("1050"),
                "allocations": [
                    {
                        # The other invoice is deliberately used to reproduce the former
                        # UUID-only validation gap.
                        "invoice_line_ref": other_invoice["lines"][0]["invoice_line_id"],
                        "recognition_id": None,
                        "unearned_amount": money("1050"),
                        "earned_reversal_amount": money("0"),
                        "tax_adjustment_amount": money("0"),
                    }
                ],
                "calculation_policy_version": "v1",
                "customer_request_evidence_id": evidence_id,
            },
            idempotency_key="refund-line-reject-001",
        )

    accepted = service.request_refund(
        operator,
        {
            "customer_id": customer_id,
            "contract_id": contract_id,
            "original_payment_id": intent["id"],
            "reason_code": "service_cancelled",
            "requested_amount": money("1050"),
            "allocations": [
                {
                    "invoice_line_ref": paid_invoice["lines"][0]["invoice_line_id"],
                    "recognition_id": None,
                    "unearned_amount": money("1050"),
                    "earned_reversal_amount": money("0"),
                    "tax_adjustment_amount": money("0"),
                }
            ],
            "calculation_policy_version": "v1",
            "customer_request_evidence_id": evidence_id,
        },
        idempotency_key="refund-line-accept-001",
    ).data
    assert accepted["allocations"][0]["invoice_line_ref"] == paid_invoice["lines"][0]["invoice_line_id"]


def test_finance_lists_are_paginated_filtered_and_role_isolated(m14, operator, service_agent):
    service = m14["service"]
    controller = M14Controller(service)
    evidence_id = "00000000-0000-0000-0000-0000000000e2"
    m14["evidence"].add(TENANT, evidence_id)
    customer_id, contract_id = str(uuid4()), str(uuid4())
    refunds = []
    for index in range(2):
        payment_id = str(uuid4())
        seed_paid_payment(
            m14["repository"],
            tenant=TENANT,
            payment_id=payment_id,
            customer_id=customer_id,
            amount=Decimal("100"),
            provider_transaction_id=f"ptx-finance-list-{index}",
        )
        refunds.append(
            service.request_refund(
                operator,
                {
                    "customer_id": customer_id,
                    "contract_id": contract_id,
                    "original_payment_id": payment_id,
                    "reason_code": "service_cancelled",
                    "requested_amount": money("100"),
                    "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("100"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
                    "calculation_policy_version": "v1",
                    "customer_request_evidence_id": evidence_id,
                },
                idempotency_key=f"finance-list-refund-{index}",
            ).data
        )

    period_start, period_end = date(2026, 2, 1), date(2026, 3, 1)
    run_id, export_id = str(uuid4()), str(uuid4())

    def seed_close_projection(state):
        state.revenue_runs[(TENANT, run_id)] = RevenueRun(
            id=run_id,
            tenant_id=TENANT,
            run_key="finance-list-run",
            period_start=period_start,
            period_end=period_end,
            recognition_policy_version="service_period_straight_line_v1",
            cutoff_at=NOW,
            dry_run=False,
            prepared_by=operator.actor_id,
            status="posted",
            calculation_manifest_sha256="b" * 64,
            created_at=NOW,
            updated_at=NOW,
        )
        state.accounting_exports[(TENANT, export_id)] = AccountingExport(
            id=export_id,
            tenant_id=TENANT,
            destination="generic_erp_v1",
            period_start=period_start,
            period_end=period_end,
            journal_ids=[],
            account_mapping_version="coa-v1",
            tax_mapping_version="tax-v1",
            export_format="json_v1",
            prepared_by=operator.actor_id,
            currency="TWD",
            debit_total=Decimal("0"),
            credit_total=Decimal("0"),
            entry_count=0,
            export_asset_id=str(uuid4()),
            export_sha256="a" * 64,
            status="acknowledged",
            created_at=NOW,
            updated_at=NOW,
        )

    m14["repository"].atomic(seed_close_projection)
    query = {"period_start": "2026-02-01", "period_end": "2026-02-02", "state": "approval_pending", "limit": "1"}
    first_refund_page = controller.handle("list_refunds", operator, query=query)
    assert first_refund_page.status == 200
    assert len(first_refund_page.body["items"]) == 1
    assert first_refund_page.body["next_cursor"] is not None
    second_refund_page = controller.handle("list_refunds", operator, query={**query, "cursor": first_refund_page.body["next_cursor"]})
    assert second_refund_page.status == 200
    assert {item["id"] for item in first_refund_page.body["items"] + second_refund_page.body["items"]} == {refund["id"] for refund in refunds}

    revenue_page = controller.handle("list_revenue_runs", operator, query={"state": "posted", "period_start": "2026-02-01", "period_end": "2026-03-01"})
    export_page = controller.handle("list_accounting_exports", operator, query={"state": "acknowledged", "period_start": "2026-02-01", "period_end": "2026-03-01"})
    close_page = controller.handle("list_period_closes", operator, query={"state": "closed", "period_start": "2026-02-01", "period_end": "2026-03-01"})
    assert revenue_page.body["items"][0]["id"] == run_id
    assert export_page.body["items"][0]["id"] == export_id
    assert export_page.body["items"][0]["period_start"] == "2026-02-01"
    assert export_page.body["items"][0]["period_end"] == "2026-03-01"
    assert close_page.body["items"] == [{"close_key": "2026-02-01:2026-03-01", "period_start": "2026-02-01", "period_end": "2026-03-01", "state": "closed", "revenue_run_ids": [run_id], "accounting_export_ids": [export_id], "unresolved_reconciliation_count": 0}]

    platform_admin = ActorContext(TENANT, str(uuid4()), frozenset({"platform_admin"}))
    other_tenant_operator = ActorContext(OTHER_TENANT, str(uuid4()), frozenset({"finance_operator"}))
    for operation_id in ("list_refunds", "list_revenue_runs", "list_accounting_exports", "list_period_closes"):
        assert controller.handle(operation_id, None).status == 401
        assert controller.handle(operation_id, platform_admin).status == 403
        assert controller.handle(operation_id, service_agent).status == 403
        other_tenant = controller.handle(operation_id, other_tenant_operator)
        assert other_tenant.status == 200 and other_tenant.body["items"] == []

    unposted_run_id = str(uuid4())

    def seed_unposted_revenue_run(state):
        state.revenue_runs[(TENANT, unposted_run_id)] = RevenueRun(
            id=unposted_run_id,
            tenant_id=TENANT,
            run_key="finance-list-unposted-run",
            period_start=period_start,
            period_end=period_end,
            recognition_policy_version="service_period_straight_line_v1",
            cutoff_at=NOW,
            dry_run=False,
            prepared_by=operator.actor_id,
            status="review_pending",
            created_at=NOW,
            updated_at=NOW,
        )

    m14["repository"].atomic(seed_unposted_revenue_run)
    close_readiness = controller.handle("list_period_closes", operator, query={"period_start": "2026-02-01", "period_end": "2026-03-01"})
    assert close_readiness.status == 200
    assert close_readiness.body["items"] == [{"close_key": "2026-02-01:2026-03-01", "period_start": "2026-02-01", "period_end": "2026-03-01", "state": "revenue_pending", "revenue_run_ids": sorted([run_id, unposted_run_id]), "accounting_export_ids": [export_id], "unresolved_reconciliation_count": 0}]


def _envelope(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict) -> dict:
    return {
        "event_id": str(uuid4()), "event_type": event_type, "producer": producer, "tenant_id": TENANT,
        "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "sequence_no": sequence, "payload": payload,
    }


def test_m14_producer_envelopes_validate_against_shared_json_schemas(m14, operator, checker, payment_adapter_ctx):
    service = m14["service"]
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-0000000000e1")
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    # run_revenue_recognition only recognizes schedules whose contract is confirmed
    # accepted/effective with service already started (UAT-M14-002 gate).
    service.apply_application_company_decided(_envelope("application.company_decided", "M02", "application", str(uuid4()), 1, {"decision": "accept", "contract_id": contract_id}))
    service.apply_contract_effective(_envelope("contract.effective", "M02", "contract", contract_id, 1, {
        "contract_id": contract_id, "customer_id": customer_id,
        "effective_at": "2025-12-01T00:00:00Z", "service_starts_at": "2025-12-01T00:00:00Z", "service_ends_at": "2027-12-01T00:00:00Z",
    }))
    invoice = service.create_invoice(operator, invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id), idempotency_key="contract-inv-001").data
    intent = service.create_payment_intent(
        operator, {"application_id": str(uuid4()), "invoice_id": invoice["id"], "amount": money("1050"), "method": "card", "return_route": "application_progress"},
        idempotency_key="contract-pi-001",
    ).data
    service.receive_payment_webhook(
        payment_adapter_ctx,
        {
            "event_id": str(uuid4()), "provider_transaction_ref": "ptx-contract-001", "payment_intent_id": intent["id"], "status": "paid",
            "amount": money("1050"), "provider_occurred_at": wire_time(NOW), "evidence_asset_id": "00000000-0000-0000-0000-0000000000e1",
        },
        idempotency_key="contract-wh-001",
    )
    run = service.run_revenue_recognition(
        operator, {"period_start": "2026-01-01", "period_end": "2026-02-01", "recognition_policy_version": "service_period_straight_line_v1", "cutoff_at": "2026-01-15T00:00:00Z", "dry_run": False},
        idempotency_key="contract-run-001",
    ).data
    service.approve_revenue_run(
        checker, run["id"], {"decision": "approve", "calculation_manifest_sha256": run["calculation_manifest_sha256"], "reason_code": "close", "review_evidence_id": "00000000-0000-0000-0000-0000000000e1"},
        expected_version=1, idempotency_key="contract-approve-001",
    )

    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-0000000000e2")
    hold = service.create_legal_hold(
        ActorContext(TENANT, str(uuid4()), frozenset({"compliance_supervisor"})),
        {"scope": "customer", "subject_ids": [customer_id], "reason_code": "audit", "authority_evidence_asset_id": "00000000-0000-0000-0000-0000000000e2", "review_due_at": "2027-01-01T00:00:00Z"},
        idempotency_key="contract-hold-001",
    ).data

    snapshot = m14["repository"].snapshot()
    produced_types = {event.event_type for event in snapshot.outbox.values()}
    assert {"finance.payment_confirmed", "finance.revenue_recognized", "audit.legal_hold_changed"} <= produced_types
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m14, operator, checker, service_agent, auditor):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": f"#/components/schemas/{name}", "components": specification["components"]}
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    service = m14["service"]
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-0000000000f1")
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    invoice = service.create_invoice(operator, invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id), idempotency_key="wire-inv-001").data
    assert invoice["lines"][0]["invoice_line_id"]
    validate("Invoice", invoice)

    intent = service.create_payment_intent(
        operator, {"application_id": str(uuid4()), "invoice_id": invoice["id"], "amount": money("1050"), "method": "card", "return_route": "application_progress"},
        idempotency_key="wire-pi-001",
    ).data
    validate("PaymentIntent", intent)

    payment_id = str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("500"), provider_transaction_id="ptx-wire-001")
    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id, "contract_id": contract_id, "original_payment_id": payment_id, "reason_code": "service_cancelled",
            "requested_amount": money("500"),
            "allocations": [{"invoice_line_ref": payment_id, "recognition_id": None, "unearned_amount": money("500"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1", "customer_request_evidence_id": "00000000-0000-0000-0000-0000000000f1",
        },
        idempotency_key="wire-refund-001",
    ).data
    validate("Refund", refund)

    period_start, period_end = date(2026, 1, 1), date(2026, 2, 1)
    run_id, export_id = str(uuid4()), str(uuid4())

    def seed_non_empty_read_pages(state):
        state.revenue_runs[(TENANT, run_id)] = RevenueRun(
            id=run_id,
            tenant_id=TENANT,
            run_key="wire-read-page-run",
            period_start=period_start,
            period_end=period_end,
            recognition_policy_version="service_period_straight_line_v1",
            cutoff_at=NOW,
            dry_run=False,
            prepared_by=operator.actor_id,
            status="posted",
            calculation_manifest_sha256="b" * 64,
            created_at=NOW,
            updated_at=NOW,
        )
        state.accounting_exports[(TENANT, export_id)] = AccountingExport(
            id=export_id,
            tenant_id=TENANT,
            destination="generic_erp_v1",
            period_start=period_start,
            period_end=period_end,
            journal_ids=[],
            account_mapping_version="coa-v1",
            tax_mapping_version="tax-v1",
            export_format="json_v1",
            prepared_by=operator.actor_id,
            currency="TWD",
            debit_total=Decimal("0"),
            credit_total=Decimal("0"),
            entry_count=0,
            export_asset_id=str(uuid4()),
            export_sha256="a" * 64,
            status="acknowledged",
            created_at=NOW,
            updated_at=NOW,
        )

    m14["repository"].atomic(seed_non_empty_read_pages)
    revenue_run_page = service.list_revenue_runs(operator).data
    accounting_export_page = service.list_accounting_exports(operator).data
    period_close_page = service.list_period_closes(operator).data
    assert revenue_run_page["items"][0]["id"] == run_id
    assert accounting_export_page["items"][0]["period_start"] == "2026-01-01"
    assert accounting_export_page["items"][0]["period_end"] == "2026-02-01"

    # a maker/checker violation raised inside the command transaction must still leave a wire-valid audit trail.
    with pytest.raises(Exception):
        service.approve_refund(operator, refund["id"], {"decision": "approve", "reason_code": "self"}, expected_version=1, idempotency_key="wire-approve-fail-001")
    validate("AuditEventPage", service.list_audit_events(auditor).data)
    validate("ReconciliationItemPage", service.list_reconciliation_items(operator).data)
    validate("RefundPage", service.list_refunds(operator).data)
    validate("RevenueRunPage", revenue_run_page)
    validate("AccountingExportPage", accounting_export_page)
    validate("PeriodClosePage", period_close_page)

    hold = service.create_legal_hold(
        ActorContext(TENANT, str(uuid4()), frozenset({"compliance_supervisor"})),
        {"scope": "customer", "subject_ids": [customer_id], "reason_code": "audit", "authority_evidence_asset_id": "00000000-0000-0000-0000-0000000000f1", "review_due_at": "2027-01-01T00:00:00Z"},
        idempotency_key="wire-hold-001",
    ).data
    validate("LegalHold", hold)

    exception = service.record_control_exception(
        auditor,
        {"control_code": "restore_drill", "severity": "medium", "description": "drill", "evidence_asset_ids": ["00000000-0000-0000-0000-0000000000f1"], "remediation_owner_id": str(uuid4()), "due_at": "2026-03-01T00:00:00Z"},
        idempotency_key="wire-ce-001",
    ).data
    validate("ControlException", exception)

    export = service.create_evidence_export(
        auditor, {"scope": "refund", "subject_id": refund["id"], "purpose": "internal_audit", "include_restricted_aml": False, "approval_record_id": "00000000-0000-0000-0000-0000000000f1", "expires_at": "2027-01-01T00:00:00Z"},
        idempotency_key="wire-ee-001",
    ).data
    validate("EvidenceExport", export)

    problem = M14Controller(service).handle(
        "approve_refund", checker, path={"refund_id": refund["id"]}, body={"decision": "approve", "reason_code": "x"},
        headers={"Idempotency-Key": "wire-approve-001", "If-Match": '"99"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def _incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict) -> dict:
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": "internal" if event_type.startswith("commission.") else "customer_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m14):
    consumer = M14EventConsumer(m14["service"])
    contract_id = str(uuid4())
    payload = {"contract_id": contract_id, "customer_id": str(uuid4()), "effective_at": wire_time(NOW), "service_starts_at": wire_time(NOW), "service_ends_at": wire_time(NOW + timedelta(days=365))}
    first = _incoming("contract.effective", "M02", "contract", contract_id, 1, payload)
    assert consumer.consume(first).status == "applied"
    assert consumer.consume(first).status == "duplicate"

    tampered = dict(first)
    tampered["payload"] = {**payload, "customer_id": str(uuid4())}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    terminated_payload = {"contract_id": contract_id, "customer_id": payload["customer_id"], "terminated_at": wire_time(NOW), "refund_evaluation_required": True}
    seq3 = _incoming("contract.terminated", "M02", "contract", contract_id, 3, terminated_payload)
    assert consumer.consume(seq3).status == "retry"
    seq2 = _incoming("contract.terminated", "M02", "contract", contract_id, 2, terminated_payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"

    state = m14["repository"].snapshot()
    assert state.contracts[(TENANT, contract_id)].terminated_at is not None


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m14):
    consumer = M14EventConsumer(m14["service"])
    contract_id = str(uuid4())
    payload = {"contract_id": contract_id, "customer_id": str(uuid4()), "effective_at": wire_time(NOW), "service_starts_at": wire_time(NOW), "service_ends_at": wire_time(NOW)}
    wrong_schema = _incoming("contract.effective", "M02", "contract", contract_id, 1, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)

    wrong_tenant = _incoming("contract.effective", "M02", "contract", contract_id, 1, payload)
    wrong_tenant["tenant_id"] = "10000000-0000-0000-0000-000000000099"
    consumer.consume(wrong_tenant)  # a different tenant's projection must not collide with TENANT's state
    state = m14["repository"].snapshot()
    assert (TENANT, contract_id) not in state.contracts
    assert ("10000000-0000-0000-0000-000000000099", contract_id) in state.contracts


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m14, operator):
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-0000000000e9")
    service = m14["service"]
    hold = service.create_legal_hold(
        ActorContext(TENANT, str(uuid4()), frozenset({"compliance_supervisor"})),
        {"scope": "customer", "subject_ids": [str(uuid4())], "reason_code": "audit", "authority_evidence_asset_id": "00000000-0000-0000-0000-0000000000e9", "review_due_at": "2027-01-01T00:00:00Z"},
        idempotency_key="worker-hold-001",
    )
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m14["repository"], bus, max_attempts=2)
    first = publisher.run_once("worker-a")
    assert first.retried == 1

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)

    m14["repository"].atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("worker-b")
    assert second.processed == 1
    assert len(bus.envelopes) == 1
    third = publisher.run_once("worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 1


def test_outbox_worker_reclaims_a_lease_abandoned_by_a_crashed_worker(m14):
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-0000000000ea")
    service = m14["service"]
    service.create_legal_hold(
        ActorContext(TENANT, str(uuid4()), frozenset({"compliance_supervisor"})),
        {"scope": "customer", "subject_ids": [str(uuid4())], "reason_code": "audit", "authority_evidence_asset_id": "00000000-0000-0000-0000-0000000000ea", "review_due_at": "2027-01-01T00:00:00Z"},
        idempotency_key="worker-hold-002",
    )
    bus = LocalEventBus()
    clock_state = {"now": NOW}
    publisher = OutboxPublisher(m14["repository"], bus, max_attempts=3, clock=lambda: clock_state["now"])

    # worker-a leases the row but crashes before ever calling _complete/_fail
    leased = publisher._lease("worker-a", 50)
    assert len(leased) == 1

    # before the 30s lease expires, nobody else may steal it
    clock_state["now"] = NOW + timedelta(seconds=10)
    assert publisher._lease("worker-b", 50) == []

    # once the lease has expired, a different worker reclaims and publishes it
    clock_state["now"] = NOW + timedelta(seconds=31)
    second = publisher.run_once("worker-b")
    assert second.processed == 1
    assert len(bus.envelopes) == 1


def test_consumer_applies_audit_legal_hold_changed_idempotently(m14):
    service = m14["service"]
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-0000000000eb")
    hold = service.create_legal_hold(
        ActorContext(TENANT, str(uuid4()), frozenset({"compliance_supervisor"})),
        {"scope": "customer", "subject_ids": [str(uuid4())], "reason_code": "audit", "authority_evidence_asset_id": "00000000-0000-0000-0000-0000000000eb", "review_due_at": "2027-01-01T00:00:00Z"},
        idempotency_key="consumer-hold-001",
    ).data

    consumer = M14EventConsumer(service)
    payload = {"legal_hold_id": hold["id"], "state": "active", "scope": "customer"}
    envelope = _incoming("audit.legal_hold_changed", "M14", "legal_hold", hold["id"], 1, payload)
    first = consumer.consume(envelope)
    assert first.status == "applied"
    assert consumer.consume(envelope).status == "duplicate"  # redelivery is a no-op, not a second apply

    state = m14["repository"].snapshot()
    assert state.legal_holds[(TENANT, hold["id"])].status == "active"


def test_production_composition_refuses_fake_adapters():
    with pytest.raises(RuntimeError):
        FinanceControlService(
            InMemoryRepository(), FakeRefundProvider(), FakeSettlementStatements(), FakeEvidenceAssets(), FakeTaxMappings(),
            environment="production",
        )
