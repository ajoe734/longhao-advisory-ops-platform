from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from modules.finance_control.automation import LocalReconciliationAssistant, ReconciliationNarrationService
from modules.finance_control.domain import ActorContext, ForbiddenError, NotFoundError, ValidationError

from conftest import TENANT, invoice_body, money, seed_paid_payment


def test_cross_tenant_staff_cannot_read_finance_resources(m14, operator):
    service = m14["service"]
    customer_id, contract_id, contract_service_id = str(uuid4()), str(uuid4()), str(uuid4())
    invoice = service.create_invoice(operator, invoice_body(customer_id=customer_id, contract_id=contract_id, contract_service_id=contract_service_id), idempotency_key="tenant-inv-001").data
    payment_id = str(uuid4())
    seed_paid_payment(m14["repository"], tenant=TENANT, payment_id=payment_id, customer_id=customer_id, amount=Decimal("500"), provider_transaction_id="ptx-tenant-001")
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-00000000c001")
    refund = service.request_refund(
        operator,
        {
            "customer_id": customer_id, "contract_id": contract_id, "original_payment_id": payment_id, "reason_code": "service_cancelled",
            "requested_amount": money("500"),
            "allocations": [{"invoice_line_ref": str(uuid4()), "recognition_id": None, "unearned_amount": money("500"), "earned_reversal_amount": money("0"), "tax_adjustment_amount": money("0")}],
            "calculation_policy_version": "v1", "customer_request_evidence_id": "00000000-0000-0000-0000-00000000c001",
        },
        idempotency_key="tenant-refund-001",
    ).data

    other_tenant_operator = ActorContext("10000000-0000-0000-0000-000000000099", str(uuid4()), frozenset({"finance_operator"}))
    with pytest.raises(NotFoundError):
        service.get_refund(other_tenant_operator, refund["id"])
    # evidence is scoped per tenant too: the other tenant's actor cannot even satisfy evidence checks for TENANT's assets
    m14["evidence"].add("10000000-0000-0000-0000-000000000099", "00000000-0000-0000-0000-00000000c001")
    with pytest.raises(NotFoundError):
        service.get_revenue_run(other_tenant_operator, str(uuid4()))

    # the owning tenant can still read it
    assert service.get_refund(operator, refund["id"]).data["id"] == refund["id"]
    assert invoice["customer_id"] == customer_id


def test_execute_refund_requires_a_service_principal_not_a_human_role(m14, operator):
    service = m14["service"]
    human_with_refund_service_role = ActorContext(TENANT, str(uuid4()), frozenset({"refund_service"}), principal_type="staff")
    with pytest.raises(ForbiddenError) as excinfo:
        service.execute_refund(
            human_with_refund_service_role, str(uuid4()),
            {"approval_record_id": str(uuid4()), "original_payment_id": str(uuid4()), "provider_idempotency_key": "x" * 10, "last_provider_status": "not_submitted"},
            expected_version=1, idempotency_key="rbac-exec-001",
        )
    assert excinfo.value.code == "service_identity_required"


def test_receive_payment_webhook_requires_payment_adapter_role(m14, operator):
    service = m14["service"]
    with pytest.raises(ForbiddenError):
        service.receive_payment_webhook(
            operator,
            {"event_id": str(uuid4()), "provider_transaction_ref": "x", "payment_intent_id": str(uuid4()), "status": "paid", "amount": money("1"), "provider_occurred_at": "2026-01-01T00:00:00Z", "evidence_asset_id": str(uuid4())},
            idempotency_key="rbac-wh-001",
        )


def test_restricted_evidence_export_requires_compliance_clearance(m14, auditor):
    service = m14["service"]
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-00000000c002")
    with pytest.raises(ForbiddenError) as excinfo:
        service.create_evidence_export(
            auditor,
            {"scope": "customer", "subject_id": str(uuid4()), "purpose": "regulatory_exam", "include_restricted_aml": True, "approval_record_id": "00000000-0000-0000-0000-00000000c002", "expires_at": "2027-01-01T00:00:00Z"},
            idempotency_key="rbac-ee-001",
        )
    assert excinfo.value.code == "restricted_evidence_forbidden"


def test_restricted_audit_events_mask_for_auditor_but_not_compliance_reviewer(m14, auditor):
    """Compliance clearance ({compliance_reviewer, compliance_supervisor}), not
    internal_auditor, gates visibility of aml_restricted audit entries -- the
    same clearance create_evidence_export already requires for
    include_restricted_aml. Previously list_audit_events inverted this: an
    internal_auditor saw restricted entries unmasked while a compliance
    reviewer did not."""
    service = m14["service"]
    m14["evidence"].add(TENANT, "00000000-0000-0000-0000-00000000c003")
    # create_evidence_export requires internal_auditor to call it at all, *and*
    # compliance clearance to request include_restricted_aml: both roles together.
    cleared_auditor = ActorContext(TENANT, str(uuid4()), frozenset({"internal_auditor", "compliance_reviewer"}))
    export = service.create_evidence_export(
        cleared_auditor,
        {"scope": "customer", "subject_id": str(uuid4()), "purpose": "regulatory_exam", "include_restricted_aml": True, "approval_record_id": "00000000-0000-0000-0000-00000000c003", "expires_at": "2027-01-01T00:00:00Z"},
        idempotency_key="rbac-ee-restricted-001",
    ).data
    service.get_evidence_export(auditor, export["id"])  # records an aml_restricted audit event

    masked = service.list_audit_events(auditor).data
    restricted_entries = [item for item in masked["items"] if item["aggregate_type"] == "evidence_export" and item.get("action") == "restricted"]
    assert restricted_entries, "an internal_auditor without compliance clearance must see the aml_restricted event masked"
    assert restricted_entries[0]["aggregate_id"] != export["id"]
    assert restricted_entries[0]["evidence_asset_ids"] == []

    compliance_reviewer = ActorContext(TENANT, str(uuid4()), frozenset({"compliance_reviewer"}))
    unmasked = service.list_audit_events(compliance_reviewer).data
    unmasked_entries = [item for item in unmasked["items"] if item["aggregate_type"] == "evidence_export" and item["classification"] == "aml_restricted"]
    assert unmasked_entries, "a compliance_reviewer must see the aml_restricted event unmasked"
    assert unmasked_entries[0]["aggregate_id"] == export["id"]


def test_minimized_automation_is_untrusted_and_has_no_resolution_authority(m14, operator):
    service = ReconciliationNarrationService(m14["repository"], LocalReconciliationAssistant())
    records = [
        {"item_id": str(uuid4()), "difference_type": "amount_mismatch", "expected_amount": "1000", "actual_amount": "970", "currency": "TWD", "evidence_refs": [str(uuid4())]},
    ]
    run = service.suggest(operator, records)
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert len(run.suggestions) == 1
    assert "amount_mismatch" in run.suggestions[0].narrative
    snapshot = m14["repository"].snapshot()
    assert snapshot.automation_runs[(TENANT, run.id)] == run
    assert len(snapshot.reconciliation_items) == 0  # drafting never resolves anything

    review = service.record_review(operator, run.id, action="adopted", reason_code="human_verified")
    assert review.action == "adopted"
    assert m14["repository"].snapshot().automation_reviews[(TENANT, review.id)] == review

    with pytest.raises(ValidationError) as raw_pii:
        service.suggest(operator, [{**records[0], "customer_email": "forbidden@example.invalid"}])
    assert raw_pii.value.code == "automation_input_not_minimized"


def test_production_automation_refuses_local_fake(m14):
    with pytest.raises(RuntimeError):
        ReconciliationNarrationService(m14["repository"], LocalReconciliationAssistant(), environment="production")
