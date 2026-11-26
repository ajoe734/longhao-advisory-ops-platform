from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.commissions.domain import ConflictError, ValidationError, canonical_hash, wire_time
from modules.commissions.http import M12Controller, ROUTES
from modules.commissions.repository import InMemoryRepository
from modules.commissions.service import CommissionService
from modules.commissions.ports import FakeEvidenceStore, FakeOwnershipCohort, FakeRevenueBasis, FakeSalesConversion
from workers.tasks.commissions.consumer import CommissionEventConsumer
from workers.tasks.commissions.tasks import CommissionJobRunner, LocalEventBus, OutboxPublisher, UnknownExternalResult

from conftest import (
    EVIDENCE_ACK,
    EVIDENCE_ADJUSTMENT,
    EVIDENCE_PARAMETER,
    NOW,
    TENANT,
    approve_policy,
    create_policy,
    entries_for_run,
    run_jobs,
    run_request,
    seed_cohort,
    seed_recognition,
    seed_refund,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_12_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "create_commission_policy", "approve_commission_policy", "preview_commissions",
        "create_commission_run", "get_commission_run", "list_commission_entries", "approve_commission_run",
        "create_commission_adjustment", "create_commission_payout_batch", "get_commission_payout_batch",
        "approve_commission_payout_batch", "acknowledge_commission_payout_batch",
    }


def test_controller_returns_etag_idempotency_replay_and_412_428_problems(m12, operator, approver):
    controller = M12Controller(m12["service"])
    policy = create_policy(m12["service"], operator, "http-contract-policy")

    created = controller.handle(
        "create_commission_policy", operator,
        body={"policy_code": "http-contract-policy-2", "effective_from": "2026-01-01T00:00:00.000000Z",
              "base_pool_rate": "0.10", "marketing_year_1": "1.0", "marketing_year_2": "0.5",
              "marketing_year_3_auto": "0.25", "assisted_marketing_share": "0.3", "assisted_sales_share": "0.7",
              "allocation_interpretation": "marketing_decay_only", "age_anchor": "first_paid_effective_start",
              "service_lineage_rule": "sticky_assisted_share", "year3_assisted_marketing_rule": "zero_unless_continuous_auto",
              "pool_policy_version": "pool-v1", "recognition_policy_version": "recognition-v1",
              "rounding_mode": "ROUND_HALF_UP", "rounding_scale": 2},
        headers={"Idempotency-Key": "http-contract-key-001"},
    )
    assert created.status == 201 and created.headers["ETag"] == '"1"'
    assert created.headers["Idempotency-Replayed"] == "false"

    replay = controller.handle(
        "create_commission_policy", operator,
        body={"policy_code": "http-contract-policy-2", "effective_from": "2026-01-01T00:00:00.000000Z",
              "base_pool_rate": "0.10", "marketing_year_1": "1.0", "marketing_year_2": "0.5",
              "marketing_year_3_auto": "0.25", "assisted_marketing_share": "0.3", "assisted_sales_share": "0.7",
              "allocation_interpretation": "marketing_decay_only", "age_anchor": "first_paid_effective_start",
              "service_lineage_rule": "sticky_assisted_share", "year3_assisted_marketing_rule": "zero_unless_continuous_auto",
              "pool_policy_version": "pool-v1", "recognition_policy_version": "recognition-v1",
              "rounding_mode": "ROUND_HALF_UP", "rounding_scale": 2},
        headers={"Idempotency-Key": "http-contract-key-001"},
    )
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.body["id"] == created.body["id"]

    # Approving without an If-Match header is a 428 Problem (CAS required).
    missing_if_match = controller.handle(
        "approve_commission_policy", approver, path={"policy_id": policy["id"]},
        body={
            "decision": "approve", "policy_sha256": policy["policy_sha256"], "approved_base_pool_rate": "0.10",
            "parameter_decision_evidence_asset_id": EVIDENCE_PARAMETER, "effective_from": policy["effective_from"],
            "reason_code": "finance_approved",
        },
        headers={"Idempotency-Key": "http-contract-key-002"},
    )
    assert missing_if_match.status == 428
    assert missing_if_match.headers["Content-Type"] == "application/problem+json"

    # A stale If-Match version is a 412 Problem that reports current_version.
    stale = controller.handle(
        "approve_commission_policy", approver, path={"policy_id": policy["id"]},
        body={
            "decision": "approve", "policy_sha256": policy["policy_sha256"], "approved_base_pool_rate": "0.10",
            "parameter_decision_evidence_asset_id": EVIDENCE_PARAMETER, "effective_from": policy["effective_from"],
            "reason_code": "finance_approved",
        },
        headers={"Idempotency-Key": "http-contract-key-003", "If-Match": '"99"'},
    )
    assert stale.status == 412 and stale.body["current_version"] == 1
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m12_producer_envelopes_validate_against_shared_json_schemas(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "contract-event-policy"), "contract-event-approve")
    recognition_id = "50000000-0000-0000-0000-000000000101"
    customer_id = "70000000-0000-0000-0000-000000000101"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000101", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="contract-event-run").data
    run_jobs(m12)
    marketing_entry = next(e for e in entries_for_run(m12, run["id"]) if e["recipient_type"] == "marketing")
    service.approve_commission_run(approver, run["id"], {"decision": "approve", "reason_code": "reviewed"}, expected_version=run["version"], idempotency_key="contract-event-run-approve")
    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000101", original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-50")
    service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": marketing_entry["id"], "refund_id": "90000000-0000-0000-0000-000000000101",
            "attributable_recognition_id": recognition_id, "adjustment_amount": {"amount": "-50", "currency": "TWD"},
            "reason_code": "refund_earned_portion", "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="contract-event-adjustment",
    )

    snapshot = m12["repository"].snapshot()
    event_types = {event.event_type for event in snapshot.outbox.values()}
    assert event_types == {"commission.run_calculated", "commission.run_approved", "commission.adjustment_recorded"}
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m12, operator, approver, payroll_operator, payroll_adapter):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    service = m12["service"]
    policy = create_policy(service, operator, "wire-contract-policy")
    validate("CommissionPolicy", policy)
    approved_policy = approve_policy(service, approver, policy, "wire-contract-approve")
    validate("CommissionPolicy", approved_policy)

    recognition_id = "50000000-0000-0000-0000-000000000102"
    customer_id = "70000000-0000-0000-0000-000000000102"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000102", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    preview = service.preview_commissions(operator, run_request(approved_policy["id"], [recognition_id]), idempotency_key="wire-contract-preview").data
    validate("CommissionPreview", preview)

    run = service.create_commission_run(operator, run_request(approved_policy["id"], [recognition_id]), idempotency_key="wire-contract-run").data
    validate("CommissionRun", run)
    run_jobs(m12)
    calculated_run = service.get_commission_run(approver, run["id"]).data
    validate("CommissionRun", calculated_run)

    entries_page = service.list_commission_entries(approver, limit=50).data
    validate("CommissionEntryPage", entries_page)
    marketing_entry = next(e for e in entries_page["items"] if e["run_id"] == run["id"] and e["recipient_type"] == "marketing")

    approved_run = service.approve_commission_run(approver, run["id"], {"decision": "approve", "reason_code": "reviewed"}, expected_version=calculated_run["version"], idempotency_key="wire-contract-run-approve").data
    validate("CommissionRun", approved_run)

    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000102", original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-30")
    adjustment = service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": marketing_entry["id"], "refund_id": "90000000-0000-0000-0000-000000000102",
            "attributable_recognition_id": recognition_id, "adjustment_amount": {"amount": "-30", "currency": "TWD"},
            "reason_code": "refund_earned_portion", "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="wire-contract-adjustment",
    ).data
    validate("CommissionAdjustment", adjustment)

    batch = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [run["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="wire-contract-batch",
    ).data
    validate("PayoutBatch", batch)
    run_jobs(m12)
    assembled_batch = service.get_commission_payout_batch(payroll_operator, batch["id"]).data
    validate("PayoutBatch", assembled_batch)
    approved_batch = service.approve_commission_payout_batch(
        approver, batch["id"], {"decision": "approve", "reason_code": "control_total_checked"},
        expected_version=assembled_batch["version"], idempotency_key="wire-contract-batch-approve",
    ).data
    validate("PayoutBatch", approved_batch)
    acked_batch = service.acknowledge_commission_payout_batch(
        payroll_adapter, batch["id"],
        {
            "event_id": str(uuid4()), "external_batch_ref": "payroll-ref-wire", "control_sha256": approved_batch["control_sha256"],
            "status": "accepted", "accepted_entry_ids": [marketing_entry["id"]], "rejected_entry_ids": [],
            "acknowledgement_evidence_id": EVIDENCE_ACK,
        },
        idempotency_key="wire-contract-ack",
    ).data
    validate("PayoutBatch", acked_batch)

    problem = M12Controller(service).handle(
        "approve_commission_run", approver, path={"run_id": run["id"]}, body={"decision": "approve", "reason_code": "stale"},
        headers={"Idempotency-Key": "wire-contract-stale", "If-Match": '"1"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict) -> dict:
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"m12-incoming-{sequence:08d}",
        "classification": "internal", "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_accepts_m12_self_loop_run_calculated_event(m12, operator, approver):
    """Regression for F6: consumer used to raise unsupported_event_type for M12's own commission.run_calculated."""
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "consumer-self-loop-policy"), "consumer-self-loop-approve")
    recognition_id = "50000000-0000-0000-0000-000000000103"
    customer_id = "70000000-0000-0000-0000-000000000103"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000103", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="consumer-self-loop-run").data
    run_jobs(m12)

    snapshot = m12["repository"].snapshot()
    event = next(e for e in snapshot.outbox.values() if e.event_type == "commission.run_calculated")
    consumer = CommissionEventConsumer(m12["repository"], m12["revenue"])
    result = consumer.consume(event.envelope())
    assert result.status == "applied"
    assert result.result_code == f"run_calculated_noted:{run['id']}"
    # Replaying the exact same envelope is deduplicated, not reprocessed.
    assert consumer.consume(event.envelope()).status == "duplicate"


def test_consumer_prepares_pending_adjustment_draft_so_the_adjustment_has_a_distinct_checker(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "consumer-refund-policy"), "consumer-refund-approve")
    recognition_id = "50000000-0000-0000-0000-000000000104"
    customer_id = "70000000-0000-0000-0000-000000000104"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000104", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="consumer-refund-run").data
    run_jobs(m12)
    marketing_entry = next(e for e in entries_for_run(m12, run["id"]) if e["recipient_type"] == "marketing")

    refund_id = "90000000-0000-0000-0000-000000000103"
    seed_refund(m12, refund_id=refund_id, original_entry_id=marketing_entry["id"], recognition_id=recognition_id, amount="-40")
    payload = {
        "refund_id": refund_id, "contract_id": "60000000-0000-0000-0000-000000000104",
        "original_payment_id": str(uuid4()), "allocation_snapshot_id": str(uuid4()), "confirmed_at": wire_time(NOW),
    }
    consumer = CommissionEventConsumer(m12["repository"], m12["revenue"])
    envelope = incoming("finance.refund_confirmed", "M14", "refund", str(uuid4()), 1, payload)
    result = consumer.consume(envelope)
    assert result.status == "applied" and result.result_code == "pending_adjustment_drafted"
    # Resending the exact same confirmation does not draft a second pending adjustment.
    envelope2 = incoming("finance.refund_confirmed", "M14", "refund", str(uuid4()), 1, payload)
    envelope2["event_id"] = str(uuid4())
    assert consumer.consume(envelope2).result_code == "pending_adjustment_already_drafted"

    adjustment = service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": marketing_entry["id"], "refund_id": refund_id,
            "attributable_recognition_id": recognition_id, "adjustment_amount": {"amount": "-40", "currency": "TWD"},
            "reason_code": "refund_earned_portion", "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="consumer-refund-adjustment",
    ).data
    snapshot = m12["repository"].snapshot()
    stored = snapshot.adjustments[(TENANT, adjustment["id"])]
    # The service-actor draft is the maker; the human command is a distinct checker.
    assert stored.prepared_by != stored.approved_by
    assert stored.approved_by == operator.actor_id


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m12):
    consumer = CommissionEventConsumer(m12["repository"], m12["revenue"])
    aggregate_id = str(uuid4())
    payload = {"conversion_id": aggregate_id, "contract_id": str(uuid4()), "decision": "approved", "approved_salesperson_id": str(uuid4()), "evidence_snapshot_id": str(uuid4())}
    first = incoming("crm.conversion_decided", "M10", "conversion", aggregate_id, 1, payload)
    assert consumer.consume(first).result_code == "conversion_projection_noted:approved"
    assert consumer.consume(first).status == "duplicate"

    tampered = dict(first)
    tampered["event_id"] = str(uuid4())
    tampered["payload"] = {**payload, "decision": "rejected"}
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    decision_payload = {"conversion_id": aggregate_id, "contract_id": str(uuid4()), "decision": "approved", "approved_salesperson_id": str(uuid4()), "evidence_snapshot_id": str(uuid4())}
    seq3 = incoming("crm.conversion_decided", "M10", "conversion", aggregate_id, 3, decision_payload)
    assert consumer.consume(seq3).status == "retry"
    seq2 = incoming("crm.conversion_decided", "M10", "conversion", aggregate_id, 2, decision_payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"
    # A stale/out-of-order replay below the last applied sequence is ignored, not reprocessed.
    stale = incoming("crm.conversion_decided", "M10", "conversion", aggregate_id, 1, payload)
    stale["event_id"] = str(uuid4())
    assert consumer.consume(stale).result_code == "stale_sequence_ignored"


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m12):
    consumer = CommissionEventConsumer(m12["repository"], m12["revenue"])
    aggregate_id = str(uuid4())
    payload = {"conversion_id": aggregate_id, "contract_id": str(uuid4()), "decision": "approved", "approved_salesperson_id": str(uuid4()), "evidence_snapshot_id": str(uuid4())}

    wrong_schema = incoming("crm.conversion_decided", "M10", "conversion", aggregate_id, 1, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)

    wrong_producer = incoming("crm.conversion_decided", "wrong-producer", "conversion", aggregate_id, 1, payload)
    with pytest.raises(ValidationError):
        consumer.consume(wrong_producer)

    missing_field = incoming("crm.conversion_decided", "M10", "conversion", aggregate_id, 1, {k: v for k, v in payload.items() if k != "decision"})
    with pytest.raises(ValidationError):
        consumer.consume(missing_field)

    unsupported = incoming("finance.revenue_recognized", "M14", "revenue_recognition", aggregate_id, 1, {
        "recognition_id": aggregate_id, "contract_id": str(uuid4()), "contract_service_id": str(uuid4()),
        "policy_version": "v1", "service_period_start": "2026-01-01T00:00:00.000000Z",
        "service_period_end": "2026-02-01T00:00:00.000000Z", "posted_journal_id": str(uuid4()),
    })
    assert consumer.consume(unsupported).result_code == "revenue_recognition_noted"

    totally_unsupported = dict(unsupported)
    totally_unsupported["event_type"] = "not.a.contract"
    totally_unsupported["event_id"] = str(uuid4())
    with pytest.raises(ValidationError) as excinfo:
        consumer.consume(totally_unsupported)
    assert excinfo.value.code == "unsupported_event_type"


def test_outbox_publisher_lease_bounded_retry_unknown_lookup_and_no_duplicate_publish(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "outbox-worker-policy"), "outbox-worker-approve")
    recognition_id = "50000000-0000-0000-0000-000000000105"
    customer_id = "70000000-0000-0000-0000-000000000105"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000105", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="outbox-worker-run")
    run_jobs(m12)

    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m12["repository"], bus, max_attempts=3)
    first = publisher.run_once("outbox-worker-a")
    assert first.processed == 0 and first.retried == 1 and first.dead_lettered == 0

    bus.failure_mode = "unknown"

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)
    m12["repository"].atomic(make_due)
    second = publisher.run_once("outbox-worker-b")
    # The provider cannot be found by lookup either, so this still counts as a retry, not a publish.
    assert second.processed == 0 and second.retried == 1

    bus.failure_mode = None
    m12["repository"].atomic(make_due)
    third = publisher.run_once("outbox-worker-c")
    assert third.processed == 1
    assert len(bus.envelopes) == 1
    fourth = publisher.run_once("outbox-worker-d")
    assert fourth.processed == 0 and len(bus.envelopes) == 1  # already published; nothing left leasable


def test_outbox_publisher_unknown_result_resolved_by_lookup_completes_without_republish(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "outbox-lookup-policy"), "outbox-lookup-approve")
    recognition_id = "50000000-0000-0000-0000-000000000106"
    customer_id = "70000000-0000-0000-0000-000000000106"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000106", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="outbox-lookup-run")
    run_jobs(m12)

    class ConfirmingBus(LocalEventBus):
        """Publish ``accepted`` but report it as ``UnknownExternalResult`` once, like a provider timeout after it already committed."""

        def __init__(self) -> None:
            super().__init__()
            self._raised = False

        def publish(self, envelope):
            accepted_envelope = dict(envelope)
            self.envelopes.setdefault(accepted_envelope["event_id"], accepted_envelope)
            if not self._raised:
                self._raised = True
                raise UnknownExternalResult(accepted_envelope["event_id"])
            return "accepted"

    bus = ConfirmingBus()
    publisher = OutboxPublisher(m12["repository"], bus, max_attempts=3)
    result = publisher.run_once("outbox-lookup-worker")
    assert result.processed == 1  # lookup found it already accepted; no duplicate publish attempted
    assert len(bus.envelopes) == 1


def test_commission_job_runner_retries_dead_letters_and_manual_redrive_recovers(m12, operator, approver):
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "job-runner-policy"), "job-runner-approve")
    recognition_id = "50000000-0000-0000-0000-000000000107"
    customer_id = "70000000-0000-0000-0000-000000000107"
    seed_recognition(m12, recognition_id=recognition_id, contract_id="60000000-0000-0000-0000-000000000107", customer_id=customer_id, amount="1000")
    seed_cohort(m12, customer_id=customer_id, customer_year=1)
    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_id]), idempotency_key="job-runner-run").data

    m12["revenue"].failure_mode = "timeout"
    runner = CommissionJobRunner(m12["repository"], service, max_attempts=2)
    first = runner.run_once("job-runner-worker-a")
    assert first.retried == 1 and first.dead_lettered == 0

    def make_due(state):
        for job in state.jobs.values():
            job.next_attempt_at = NOW - timedelta(seconds=1)
    m12["repository"].atomic(make_due)
    second = runner.run_once("job-runner-worker-a")
    assert second.dead_lettered == 1

    snapshot = m12["repository"].snapshot()
    job = next(j for j in snapshot.jobs.values() if j.kind == "calculate_commission_run")
    assert job.status == "dead_letter"

    with pytest.raises(ValueError):
        runner.redrive(TENANT, job.id, "   ")

    m12["revenue"].failure_mode = None
    runner.redrive(TENANT, job.id, "finance basis dependency recovered")
    third = runner.run_once("job-runner-worker-b")
    assert third.processed == 1

    calculated_run = service.get_commission_run(approver, run["id"]).data
    assert calculated_run["state"] == "calculated"


def test_payout_assembly_and_approval_agree_on_control_total_when_an_adjustment_is_not_offsettable(m12, operator, approver, payroll_operator):
    """Regression for F2: assemble_payout_batch_job and _payable_total used to diverge
    whenever an adjustment exceeded the original award (payroll_offset_allowed=False).

    Before the fix: assembly dropped the exceeds-award entry entirely (net<=0 after an
    unconditional deduction) while _payable_total still summed every adjustment
    unconditionally, so approve_commission_payout_batch raised control_total_mismatch.
    """
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "payout-divergence-policy"), "payout-divergence-approve")
    recognition_small = "50000000-0000-0000-0000-000000000301"
    customer_small = "70000000-0000-0000-0000-000000000301"
    seed_recognition(m12, recognition_id=recognition_small, contract_id="60000000-0000-0000-0000-000000000301", customer_id=customer_small, amount="1000")
    seed_cohort(m12, customer_id=customer_small, customer_year=1)
    recognition_large = "50000000-0000-0000-0000-000000000302"
    customer_large = "70000000-0000-0000-0000-000000000302"
    seed_recognition(m12, recognition_id=recognition_large, contract_id="60000000-0000-0000-0000-000000000302", customer_id=customer_large, amount="2000")
    seed_cohort(m12, customer_id=customer_large, customer_year=1)

    run = service.create_commission_run(operator, run_request(policy["id"], [recognition_small, recognition_large]), idempotency_key="payout-divergence-run").data
    run_jobs(m12)
    entries = entries_for_run(m12, run["id"])
    entry_small = next(e for e in entries if e["recipient_type"] == "marketing" and e["amount"]["amount"] == "100")
    run = service.approve_commission_run(approver, run["id"], {"decision": "approve", "reason_code": "reviewed"}, expected_version=run["version"], idempotency_key="payout-divergence-run-approve").data

    # -150 against a 100 award exceeds the original award: payroll_offset_allowed=False.
    seed_refund(m12, refund_id="90000000-0000-0000-0000-000000000301", original_entry_id=entry_small["id"], recognition_id=recognition_small, amount="-150")
    adjustment = service.create_commission_adjustment(
        operator,
        {
            "original_entry_id": entry_small["id"], "refund_id": "90000000-0000-0000-0000-000000000301",
            "attributable_recognition_id": recognition_small, "adjustment_amount": {"amount": "-150", "currency": "TWD"},
            "reason_code": "refund_includes_deferred_portion", "evidence_asset_id": EVIDENCE_ADJUSTMENT,
        },
        idempotency_key="payout-divergence-adjustment",
    ).data
    assert adjustment["payroll_offset_allowed"] is False

    batch = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [run["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="payout-divergence-batch",
    ).data
    run_jobs(m12)
    assembled = service.get_commission_payout_batch(payroll_operator, batch["id"]).data
    # Both entries are payable in full: the non-offsettable adjustment stays a pending
    # reconciliation item and never reduces the payroll batch.
    assert assembled["line_count"] == 2
    assert assembled["total"]["amount"] == "300"

    approved = service.approve_commission_payout_batch(
        approver, batch["id"], {"decision": "approve", "reason_code": "control_total_checked"},
        expected_version=assembled["version"], idempotency_key="payout-divergence-batch-approve",
    ).data
    assert approved["state"] == "approved"


def test_acknowledge_rejects_cross_batch_entries_and_overlapping_id_sets(m12, operator, approver, payroll_operator, payroll_adapter):
    """Regression for F4: acknowledge_commission_payout_batch used to mutate payout
    lines without checking line.batch_id==batch.id or that accepted/rejected sets
    are disjoint.
    """
    service = m12["service"]
    policy = approve_policy(service, approver, create_policy(service, operator, "cross-batch-policy"), "cross-batch-approve")

    recognition_1 = "50000000-0000-0000-0000-000000000303"
    customer_1 = "70000000-0000-0000-0000-000000000303"
    seed_recognition(m12, recognition_id=recognition_1, contract_id="60000000-0000-0000-0000-000000000303", customer_id=customer_1, amount="1000")
    seed_cohort(m12, customer_id=customer_1, customer_year=1)
    run1 = service.create_commission_run(operator, run_request(policy["id"], [recognition_1]), idempotency_key="cross-batch-run-1").data
    run_jobs(m12)
    entry1 = next(e for e in entries_for_run(m12, run1["id"]) if e["recipient_type"] == "marketing")
    run1 = service.approve_commission_run(approver, run1["id"], {"decision": "approve", "reason_code": "reviewed"}, expected_version=run1["version"], idempotency_key="cross-batch-run-1-approve").data

    recognition_2 = "50000000-0000-0000-0000-000000000304"
    customer_2 = "70000000-0000-0000-0000-000000000304"
    seed_recognition(m12, recognition_id=recognition_2, contract_id="60000000-0000-0000-0000-000000000304", customer_id=customer_2, amount="1000")
    seed_cohort(m12, customer_id=customer_2, customer_year=1)
    run2 = service.create_commission_run(operator, run_request(policy["id"], [recognition_2]), idempotency_key="cross-batch-run-2").data
    run_jobs(m12)
    entry2 = next(e for e in entries_for_run(m12, run2["id"]) if e["recipient_type"] == "marketing")
    run2 = service.approve_commission_run(approver, run2["id"], {"decision": "approve", "reason_code": "reviewed"}, expected_version=run2["version"], idempotency_key="cross-batch-run-2-approve").data

    batch1 = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [run1["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="cross-batch-batch-1",
    ).data
    batch2 = service.create_commission_payout_batch(
        payroll_operator, {"approved_run_ids": [run2["id"]], "payout_period": "2026-02", "payroll_mapping_version": "payroll-v1"},
        idempotency_key="cross-batch-batch-2",
    ).data
    run_jobs(m12)
    batch1 = service.get_commission_payout_batch(payroll_operator, batch1["id"]).data
    batch2 = service.get_commission_payout_batch(payroll_operator, batch2["id"]).data
    approved1 = service.approve_commission_payout_batch(
        approver, batch1["id"], {"decision": "approve", "reason_code": "checked"}, expected_version=batch1["version"], idempotency_key="cross-batch-batch-1-approve",
    ).data
    acked1 = service.acknowledge_commission_payout_batch(
        payroll_adapter, batch1["id"],
        {"event_id": str(uuid4()), "external_batch_ref": "payroll-ref-1", "control_sha256": approved1["control_sha256"],
         "status": "accepted", "accepted_entry_ids": [entry1["id"]], "rejected_entry_ids": [], "acknowledgement_evidence_id": EVIDENCE_ACK},
        idempotency_key="cross-batch-ack-1",
    ).data
    assert acked1["state"] == "acknowledged"

    approved2 = service.approve_commission_payout_batch(
        approver, batch2["id"], {"decision": "approve", "reason_code": "checked"}, expected_version=batch2["version"], idempotency_key="cross-batch-batch-2-approve",
    ).data

    # Batch 1's entry id does not belong to batch 2: this must be rejected, not silently mutate batch 1's line.
    with pytest.raises(ValidationError) as cross_batch:
        service.acknowledge_commission_payout_batch(
            payroll_adapter, batch2["id"],
            {"event_id": str(uuid4()), "external_batch_ref": "payroll-ref-2", "control_sha256": approved2["control_sha256"],
             "status": "partial", "accepted_entry_ids": [], "rejected_entry_ids": [entry1["id"]], "acknowledgement_evidence_id": EVIDENCE_ACK},
            idempotency_key="cross-batch-ack-2-cross",
        )
    assert cross_batch.value.code == "entry_not_in_batch"

    # Overlapping accepted/rejected sets for the same entry are never valid.
    with pytest.raises(ValidationError) as overlap:
        service.acknowledge_commission_payout_batch(
            payroll_adapter, batch2["id"],
            {"event_id": str(uuid4()), "external_batch_ref": "payroll-ref-2", "control_sha256": approved2["control_sha256"],
             "status": "partial", "accepted_entry_ids": [entry2["id"]], "rejected_entry_ids": [entry2["id"]], "acknowledgement_evidence_id": EVIDENCE_ACK},
            idempotency_key="cross-batch-ack-2-overlap",
        )
    assert overlap.value.code == "accepted_rejected_overlap"

    # Batch 1's line was never touched by the rejected cross-batch attempt above.
    untouched_batch1 = m12["repository"].snapshot()
    line1 = next(line for line in untouched_batch1.payout_lines.values() if line.batch_id == batch1["id"])
    assert line1.accepted is True

    # A correctly-scoped acknowledgement of batch 2 still succeeds.
    acked2 = service.acknowledge_commission_payout_batch(
        payroll_adapter, batch2["id"],
        {"event_id": str(uuid4()), "external_batch_ref": "payroll-ref-2", "control_sha256": approved2["control_sha256"],
         "status": "accepted", "accepted_entry_ids": [entry2["id"]], "rejected_entry_ids": [], "acknowledgement_evidence_id": EVIDENCE_ACK},
        idempotency_key="cross-batch-ack-2-valid",
    ).data
    assert acked2["state"] == "acknowledged"


def test_production_composition_refuses_fake_adapters():
    repository = InMemoryRepository()
    revenue = FakeRevenueBasis()
    conversions = FakeSalesConversion()
    ownership = FakeOwnershipCohort()
    evidence = FakeEvidenceStore()
    with pytest.raises(RuntimeError):
        CommissionService(repository, revenue, conversions, ownership, evidence, environment="production")
