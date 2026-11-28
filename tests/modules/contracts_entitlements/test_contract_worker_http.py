from __future__ import annotations

from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.contracts_entitlements.domain import ActorContext, Job, canonical_hash, new_id, wire_time
from workers.tasks.contracts_entitlements.consumer import M02EventConsumer
from workers.tasks.contracts_entitlements.tasks import (
    EntitlementRecheckWorker,
    LocalEventBus,
    OutboxPublisher,
    RetryableTaskError,
    TaskRunner,
)

from conftest import (
    APPLICATION_A,
    CUSTOMER_ACTOR,
    NOW,
    TENANT,
    accept_application,
    create_approved_package,
    entitlement_service_ctx,
    operator_ctx,
    register_application,
    set_gates,
    sign_application,
)


# ---- HTTP controller dispatch -------------------------------------------------


def test_http_create_contract_package_requires_staff_role(m02):
    result = m02["controller"].handle(
        "create_contract_package",
        None,
        body={"package_code": "x", "version_label": "v1", "documents": [], "service_codes": [], "effective_from": wire_time(NOW), "acceptance_terms_version": "t"},
        headers={"idempotency-key": "http-pkg-0001"},
    )
    assert result.status == 401


def test_http_webhook_rejects_missing_signature_headers(m02):
    result = m02["controller"].handle(
        "receive_customer_signature", None, body={}, headers={"idempotency-key": "http-webhook-0001"}, raw_body=b"{}",
    )
    assert result.status == 422


def test_http_webhook_rejects_invalid_signature(m02):
    result = m02["controller"].handle(
        "receive_customer_signature", None, body={}, headers={
            "idempotency-key": "http-webhook-0002", "x-webhook-timestamp": "1700000000", "x-webhook-nonce": "a" * 16,
            "x-integration-key-id": "key-0000001", "x-webhook-signature": "garbage",
        }, raw_body=b"{}",
    )
    assert result.status == 401
    assert result.body["code"] == "webhook_signature_invalid"


def test_http_webhook_timeout_maps_to_503():
    from modules.contracts_entitlements.http import M02Controller
    from modules.contracts_entitlements.ports import FakeWebhookVerifier

    class _TimeoutVerifier(FakeWebhookVerifier):
        def verify(self, **kwargs):
            raise __import__("modules.contracts_entitlements.domain", fromlist=["DependencyUnavailableError"]).DependencyUnavailableError(
                "timeout", code="signature_verifier_timeout",
            )

    verifier = _TimeoutVerifier()
    verifier.register_key("key-0000001", TENANT)
    controller = M02Controller(service=None, webhook_verifier=verifier)
    result = controller.handle(
        "receive_customer_signature", None, body={}, headers={
            "idempotency-key": "http-webhook-0003", "x-webhook-timestamp": "1700000000", "x-webhook-nonce": "a" * 16,
            "x-integration-key-id": "key-0000001", "x-webhook-signature": "timeout",
        }, raw_body=b"{}",
    )
    assert result.status == 503


def test_http_full_signature_round_trip(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    consent_id = "60000000-0000-0000-0000-000000000011"
    m02["evidence"].add(TENANT, consent_id)

    create_result = m02["controller"].handle(
        "create_customer_signature", operator_ctx(),
        body={"package_version_id": package["id"], "package_sha256": package_sha256, "electronic_signing_consent_id": consent_id, "return_route": "application_progress"},
        path={"application_id": APPLICATION_A}, headers={"idempotency-key": "http-sign-0001", "if-match": '"1"'},
    )
    assert create_result.status == 202
    session_ref = create_result.body["provider_session_ref"]

    callback_body = {
        "event_id": new_id(), "provider_session_ref": session_ref, "application_id": APPLICATION_A,
        "package_version_id": package["id"], "document_sha256": package_sha256,
        "signed_asset_id": new_id(), "signed_file_sha256": "c" * 64, "provider_signed_at": wire_time(NOW),
        "verification_evidence_asset_id": new_id(), "result": "signed",
    }
    m02["webhook"].accept = True
    receive_result = m02["controller"].handle(
        "receive_customer_signature", None, body=callback_body, headers={
            "idempotency-key": "http-receive-0001", "x-webhook-timestamp": "1700000000", "x-webhook-nonce": "a" * 16,
            "x-integration-key-id": "key-0000001", "x-webhook-signature": "valid",
        }, raw_body=b"{}",
    )
    assert receive_result.status == 202
    assert receive_result.body["status"] == "accepted"


def test_http_if_match_rejects_malformed_etag(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    result = m02["controller"].handle(
        "create_customer_signature", operator_ctx(),
        body={"package_version_id": package["id"], "package_sha256": package_sha256, "electronic_signing_consent_id": new_id(), "return_route": "application_progress"},
        path={"application_id": APPLICATION_A}, headers={"idempotency-key": "http-sign-0002", "if-match": "not-an-etag"},
    )
    assert result.status == 422


def test_http_get_contract_exposes_invoiceable_services_without_relaxing_access(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-contract-read", idempotency_key="pkg-contract-read")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suitability_id, clearance_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suitability_id, clearance_id=clearance_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-contract-read")
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id,
        kyc_id=kyc_id, suitability_id=suitability_id, clearance_id=clearance_id,
        idempotency_key="accept-contract-read",
    ).data["contract_id"]
    m02["service"].evaluate_entitlements(
        entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"},
        expected_version=1, idempotency_key="evaluate-contract-read",
    )
    stored_service = next(row for row in m02["repository"].snapshot().contract_services.values() if row.contract_id == contract_id)

    result = m02["controller"].handle("get_contract", operator_ctx(), path={"contract_id": contract_id})

    assert result.status == 200
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    contract_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/components/schemas/Contract",
        "components": specification["components"],
    }
    schema_errors = list(
        Draft202012Validator(contract_schema, format_checker=FormatChecker()).iter_errors(result.body)
    )
    assert schema_errors == [], [f"Contract: {error.json_path}: {error.message}" for error in schema_errors]
    assert result.body["services"] == [{
        "contract_service_id": stored_service.id,
        "service_code": stored_service.service_code,
        "service_period_start": wire_time(stored_service.starts_at),
        "service_period_end": wire_time(stored_service.ends_at),
        "untaxed_service_amount": {"amount": stored_service.untaxed_contract_amount, "currency": "TWD"},
        "status": "active",
    }]

    unauthenticated = m02["controller"].handle("get_contract", None, path={"contract_id": contract_id})
    assert unauthenticated.status == 401

    platform_admin = ActorContext(TENANT, new_id(), frozenset({"platform_admin"}), principal_type="staff")
    forbidden = m02["controller"].handle("get_contract", platform_admin, path={"contract_id": contract_id})
    assert forbidden.status == 403

    other_tenant = ActorContext("10000000-0000-0000-0000-000000000099", new_id(), frozenset({"contract_operator"}), principal_type="staff")
    cross_tenant = m02["controller"].handle("get_contract", other_tenant, path={"contract_id": contract_id})
    assert cross_tenant.status == 404


# ---- outbox publisher and task runner -----------------------------------------


def test_outbox_publisher_recovers_from_a_transient_failure(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    sign_application(m02, APPLICATION_A, package, package_sha256)

    bus = LocalEventBus()
    publisher = OutboxPublisher(m02["repository"], bus, max_attempts=3, clock=m02["clock"])
    bus.failure_mode = "timeout"
    first = publisher.run_once("m02-drill")
    assert first.retried == 1
    outbox_row = next(iter(m02["repository"].snapshot().outbox.values()))
    assert outbox_row.status == "retry"
    assert outbox_row.attempt_count == 1

    bus.failure_mode = None
    m02["clock"].advance(seconds=10)
    second = publisher.run_once("m02-drill")
    assert second.processed == 1
    published_row = next(iter(m02["repository"].snapshot().outbox.values()))
    assert published_row.status == "published"
    assert len(bus.envelopes) == 1


def test_outbox_publisher_dead_letters_after_max_attempts(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    sign_application(m02, APPLICATION_A, package, package_sha256)

    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m02["repository"], bus, max_attempts=2, clock=m02["clock"])
    publisher.run_once("m02-drill-dead")
    m02["clock"].advance(seconds=10)
    publisher.run_once("m02-drill-dead")
    outbox_row = next(iter(m02["repository"].snapshot().outbox.values()))
    assert outbox_row.status == "dead_letter"
    assert outbox_row.attempt_count == 2

    runner = TaskRunner(m02["repository"], clock=m02["clock"])
    with pytest.raises(ValueError):
        runner.redrive(TENANT, "unused", "")


# ---- inbound event consumer ----------------------------------------------------


def _kyc_envelope(application_id: str, submission_id: str, sequence_no: int = 1) -> dict:
    payload = {"submission_id": submission_id, "application_id": application_id, "decision": "approved", "next_review_due_at": None}
    return {
        "event_id": new_id(), "event_type": "kyc.decision_recorded", "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": "M03", "aggregate_type": "kyc_submission", "aggregate_id": submission_id, "aggregate_version": 1,
        "sequence_no": sequence_no, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW), "correlation_id": new_id(),
        "causation_id": None, "idempotency_key": new_id(), "classification": "customer_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_enqueues_recheck_job_and_dedupes_replays(m02):
    consumer = M02EventConsumer(m02["repository"])
    envelope = _kyc_envelope(APPLICATION_A, new_id())

    first = consumer.consume(envelope)
    assert first.status == "applied"
    assert first.result_code == "gate_changed_recheck_enqueued"
    jobs = [job for job in m02["repository"].snapshot().jobs.values() if job.kind == "gate_changed_recheck"]
    assert len(jobs) == 1

    duplicate = consumer.consume(envelope)
    assert duplicate.status == "duplicate"
    jobs_after_replay = [job for job in m02["repository"].snapshot().jobs.values() if job.kind == "gate_changed_recheck"]
    assert len(jobs_after_replay) == 1


def test_consumer_rejects_payload_hash_mismatch(m02):
    consumer = M02EventConsumer(m02["repository"])
    envelope = _kyc_envelope(APPLICATION_A, new_id())
    envelope["payload_sha256"] = "0" * 64
    from modules.contracts_entitlements.domain import ConflictError

    with pytest.raises(ConflictError):
        consumer.consume(envelope)


def test_consumer_holds_out_of_order_events_for_retry(m02):
    consumer = M02EventConsumer(m02["repository"])
    envelope = _kyc_envelope(APPLICATION_A, new_id(), sequence_no=3)
    result = consumer.consume(envelope)
    assert result.status == "retry"
    assert result.result_code == "sequence_gap"


def test_entitlement_recheck_worker_evaluates_existing_contract(m02):
    package, package_sha256 = create_approved_package(m02)
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-worker")
    contract_id = accept_application(m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id).data["contract_id"]

    consumer = M02EventConsumer(m02["repository"])
    consumer.consume(_kyc_envelope(APPLICATION_A, kyc_id))

    worker = EntitlementRecheckWorker(m02["repository"], m02["service"], clock=m02["clock"])
    result = worker.run_once("m02-recheck-drill")
    assert result.processed == 1
    contract = m02["repository"].snapshot().contracts[(TENANT, contract_id)]
    assert contract.status == "effective"


# ---- regression: candidate 409bd4111339 reopen finding F5 ---------------------


def test_regression_f5_task_runner_reclaims_an_expired_lease(m02):
    runner = TaskRunner(m02["repository"], clock=m02["clock"])

    def seed(state):
        job = Job(id=new_id(), tenant_id=TENANT, kind="refund_review", payload={}, created_at=NOW, updated_at=NOW)
        state.jobs[(TENANT, job.id)] = job
        return job.id

    job_id = m02["repository"].atomic(seed)

    leased = runner.lease("worker-a", 10)
    assert [job.id for job in leased] == [job_id]

    # worker-a disappears without completing the job; before the 30s lease expires
    # no other worker may pick it up.
    assert runner.lease("worker-b", 10) == []

    m02["clock"].advance(seconds=31)
    reclaimed = runner.lease("worker-b", 10)
    assert [job.id for job in reclaimed] == [job_id]
    assert reclaimed[0].lease_owner == "worker-b"


def test_regression_f5_entitlement_recheck_worker_only_touches_its_own_job_kinds(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f5-kinds", idempotency_key="pkg-f5-kinds")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-f5-kinds")
    accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f5-kinds",
    )

    def seed(state):
        other_job = Job(id=new_id(), tenant_id=TENANT, kind="refund_review", payload={"application_id": APPLICATION_A}, created_at=NOW, updated_at=NOW)
        state.jobs[(TENANT, other_job.id)] = other_job
        recheck_job = Job(id=new_id(), tenant_id=TENANT, kind="gate_changed_recheck", payload={"application_id": APPLICATION_A}, created_at=NOW, updated_at=NOW)
        state.jobs[(TENANT, recheck_job.id)] = recheck_job
        return other_job.id

    other_job_id = m02["repository"].atomic(seed)
    worker = EntitlementRecheckWorker(m02["repository"], m02["service"], clock=m02["clock"])
    result = worker.run_once("m02-recheck-kinds")
    assert result.processed == 1
    other_job = m02["repository"].snapshot().jobs[(TENANT, other_job_id)]
    assert other_job.status == "pending"
    assert other_job.lease_owner is None


def test_regression_f5_worker_recheck_retry_uses_a_deterministic_cause_event_id(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f5-retry", idempotency_key="pkg-f5-retry")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-f5-retry")
    accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f5-retry",
    )
    consumer = M02EventConsumer(m02["repository"])
    consumer.consume(_kyc_envelope(APPLICATION_A, kyc_id))

    # Simulate a worker crash *after* evaluate_entitlements already committed its
    # idempotency receipt but *before* the job could be marked complete: the job is
    # retried with the same job.id. The retry must reuse a deterministic cause_event_id
    # (job.id) -- a fresh new_id() per attempt would make the idempotency fingerprint
    # differ from the already-committed receipt and 409 instead of safely replaying.
    real_evaluate = m02["service"].evaluate_entitlements
    calls = {"count": 0}

    def flaky_evaluate(*args, **kwargs):
        result = real_evaluate(*args, **kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated worker crash after commit, before job completion")
        return result

    m02["service"].evaluate_entitlements = flaky_evaluate
    worker = EntitlementRecheckWorker(m02["repository"], m02["service"], clock=m02["clock"])

    first_run = worker.run_once("m02-retry-drill")
    assert first_run.retried == 1
    job = next(j for j in m02["repository"].snapshot().jobs.values() if j.kind == "gate_changed_recheck")
    assert job.status == "retry"

    m02["clock"].advance(seconds=5)
    second_run = worker.run_once("m02-retry-drill")
    assert second_run.processed == 1
    assert calls["count"] == 2
    assert m02["repository"].snapshot().jobs[(TENANT, job.id)].status == "done"


def _refund_confirmed_envelope(contract_id: str, refund_id: str, sequence_no: int = 1) -> dict:
    payload = {
        "refund_id": refund_id, "contract_id": contract_id, "original_payment_id": new_id(),
        "allocation_snapshot_id": new_id(), "confirmed_at": wire_time(NOW),
    }
    return {
        "event_id": new_id(), "event_type": "finance.refund_confirmed", "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": "M14", "aggregate_type": "refund", "aggregate_id": contract_id, "aggregate_version": 1,
        "sequence_no": sequence_no, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW), "correlation_id": new_id(),
        "causation_id": None, "idempotency_key": new_id(), "classification": "customer_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_regression_f5_refund_confirmed_enqueues_and_runs_an_entitlement_recheck(m02):
    package, package_sha256 = create_approved_package(m02, package_code="advisory-f5-refund", idempotency_key="pkg-f5-refund")
    register_application(m02, APPLICATION_A, CUSTOMER_ACTOR, package["id"])
    _, _, signature_id = sign_application(m02, APPLICATION_A, package, package_sha256)
    kyc_id, suit_id, clear_id = new_id(), new_id(), new_id()
    set_gates(m02, APPLICATION_A, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id)
    m02["payments"].set_condition(TENANT, APPLICATION_A, "pay-f5-refund")
    contract_id = accept_application(
        m02, APPLICATION_A, package, signature_id, kyc_id=kyc_id, suitability_id=suit_id, clearance_id=clear_id, idempotency_key="accept-f5-refund",
    ).data["contract_id"]
    m02["service"].evaluate_entitlements(
        entitlement_service_ctx(), contract_id, {"cause_event_id": new_id(), "policy_version": "v1"}, expected_version=1, idempotency_key="eval-f5-refund-pre",
    )

    consumer = M02EventConsumer(m02["repository"])
    result = consumer.consume(_refund_confirmed_envelope(contract_id, new_id()))
    assert result.result_code == "refund_confirmed_recheck_enqueued"
    job = next(j for j in m02["repository"].snapshot().jobs.values() if j.kind == "refund_confirmed_recheck")
    assert job.payload["contract_id"] == contract_id

    decisions_before = len(m02["repository"].snapshot().entitlement_decisions)
    worker = EntitlementRecheckWorker(m02["repository"], m02["service"], clock=m02["clock"])
    outcome = worker.run_once("m02-refund-recheck")
    assert outcome.processed == 1
    assert len(m02["repository"].snapshot().entitlement_decisions) == decisions_before + 1
