from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.report_signature.domain import ConflictError, NotFoundError, canonical_hash
from modules.report_signature.http import M07Controller, ROUTES
from workers.tasks.report_signature.consumer import M07EventConsumer
from workers.tasks.report_signature.tasks import LocalEventBus, OutboxPublisher, SignatureExpiryReconciler

from conftest import (
    CANDIDATE_ID,
    NOW,
    TENANT,
    callback_body,
    incoming,
    report_create_body,
    report_version_body,
    signature_request_body,
    take_to_ready_to_sign,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_8_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "create_report", "create_report_version", "get_report_version", "record_report_review",
        "freeze_report_version", "request_analyst_signature", "receive_analyst_signature", "revoke_signature",
    }


def test_controller_returns_etag_problem_and_replay_headers(m07, analyst):
    controller = M07Controller(m07["service"])
    first = controller.handle("create_report", analyst, body=report_create_body(), headers={"Idempotency-Key": "controller-key-001"})
    replay = controller.handle("create_report", analyst, body=report_create_body(), headers={"Idempotency-Key": "controller-key-001"})
    stale = controller.handle(
        "create_report_version", analyst, path={"report_id": first.body["id"]}, body=report_version_body(),
        headers={"Idempotency-Key": "controller-key-002", "If-Match": '"9"'},
    )
    assert first.status == 201 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert stale.status == 412 and stale.body["current_version"] == 1
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m07_producer_envelopes_validate_against_shared_json_schemas(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="contract")
    version = result["version"]
    session = m07["service"].request_analyst_signature(
        signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="contract-sign",
    ).data
    m07["service"].receive_analyst_signature(adapter, callback_body(session, version), idempotency_key="contract-callback")
    snapshot = m07["repository"].snapshot()
    assert {event.event_type for event in snapshot.outbox.values()} == {"report.version_frozen", "report.signature_verified"}
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter, supervisor):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    service = m07["service"]
    report = service.create_report(analyst, report_create_body(), idempotency_key="wire-report-0001").data
    validate("AdvisoryReport", report)
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="wire")
    validate("ReportVersion", result["version"])
    validate("ReviewDecision", result["pro_review"])
    session = service.request_analyst_signature(
        signer, result["version"]["id"], signature_request_body(result["version"], result["version"]["required_review_ids"]),
        expected_version=4, idempotency_key="wire-sign-0001",
    ).data
    validate("SignatureSession", session)
    receipt = service.receive_analyst_signature(adapter, callback_body(session, result["version"]), idempotency_key="wire-callback-0001").data
    validate("CallbackReceipt", receipt)
    signature_id = m07["repository"].snapshot().versions[(TENANT, result["version"]["id"])].signature_id
    revocation = service.revoke_signature(
        supervisor, signature_id,
        {"reason_code": "wire_contract_test", "evidence_asset_id": "40000000-0000-0000-0000-000000000006", "effective_at": "2026-11-24T00:00:00Z", "notify_recipients": False},
        expected_version=1, idempotency_key="wire-revoke-0001",
    ).data
    validate("SignatureRevocation", revocation)

    problem = M07Controller(service).handle(
        "create_report_version", analyst, path={"report_id": report["id"]}, body=report_version_body(),
        headers={"Idempotency-Key": "wire-stale-0001", "If-Match": '"99"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m07, analyst, reviewer_pro, reviewer_compliance):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="consumer")
    consumer = M07EventConsumer(m07["repository"])
    payload = {"candidate_id": CANDIDATE_ID, "replacement_candidate_id": str(uuid4()), "previous_artifact_sha256": "a" * 64, "replacement_artifact_sha256": "b" * 64}
    seq1 = incoming("research.candidate_superseded", "M05", "research_candidate", CANDIDATE_ID, 1, payload)
    assert consumer.consume(seq1).status == "applied"
    seq3 = incoming("research.candidate_superseded", "M05", "research_candidate", CANDIDATE_ID, 3, payload)
    assert consumer.consume(seq3).status == "retry"
    seq2 = incoming("research.candidate_superseded", "M05", "research_candidate", CANDIDATE_ID, 2, payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"
    assert consumer.consume(seq3).status == "duplicate"
    tampered = dict(seq2)
    tampered["payload"] = {**payload, "previous_artifact_sha256": "c" * 64}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)
    assert (TENANT, CANDIDATE_ID) in m07["repository"].snapshot().superseded_candidates


def test_consumer_self_loop_detects_frozen_projection_drift_and_isolates_tenant(m07, analyst, reviewer_pro, reviewer_compliance):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="selfloop")
    version = result["version"]
    consumer = M07EventConsumer(m07["repository"])
    good_payload = {
        "report_version_id": version["id"], "report_content_sha256": version["report_content_sha256"],
        "bundle_manifest_sha256": version["bundle_manifest_sha256"], "review_decision_ids": version["required_review_ids"],
    }
    good = incoming("report.version_frozen", "M07", "report_version", version["id"], 1, good_payload)
    assert consumer.consume(good).result_code == "frozen_projection_confirmed"
    drifted_payload = {**good_payload, "report_content_sha256": "9" * 64}
    drifted = incoming("report.version_frozen", "M07", "report_version", version["id"], 2, drifted_payload)
    with pytest.raises(ConflictError):
        consumer.consume(drifted)
    wrong_tenant = incoming("report.version_frozen", "M07", "report_version", version["id"], 1, good_payload)
    wrong_tenant["tenant_id"] = "10000000-0000-0000-0000-000000000098"
    with pytest.raises(NotFoundError):
        consumer.consume(wrong_tenant)


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m07, analyst, reviewer_pro, reviewer_compliance):
    take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="worker")
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m07["repository"], bus, max_attempts=2)
    first = publisher.run_once("m07-worker-a")
    assert first.retried == 1

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)
    m07["repository"].atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("m07-worker-b")
    assert second.processed == 1
    assert len(bus.envelopes) == 1
    third = publisher.run_once("m07-worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 1


def _expire_signature_request(repository, request_id: str) -> None:
    from modules.report_signature.domain import utc_now

    def backdate(state):
        state.signature_requests[(TENANT, request_id)].expires_at = utc_now() - timedelta(seconds=1)

    repository.atomic(backdate)


def test_signature_expiry_reconciler_releases_version_without_blind_resign(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="reconcile")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="reconcile-sign").data
    _expire_signature_request(m07["repository"], session["id"])
    reconciler = SignatureExpiryReconciler(m07["repository"], m07["signature_provider"])
    outcome = reconciler.run_once("m07-reconcile-a")
    assert outcome.expired == 1 and outcome.manual_review == 0
    snapshot = m07["repository"].snapshot()
    assert snapshot.signature_requests[(TENANT, session["id"])].status == "expired"
    assert snapshot.versions[(TENANT, version["id"])].status == "ready_to_sign"


def test_signature_expiry_reconciler_flags_manual_review_when_provider_disagrees(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="reconcile-manual")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="reconcile-manual-sign").data
    transaction_id = m07["repository"].snapshot().signature_requests[(TENANT, session["id"])].provider_transaction_id
    m07["signature_provider"]._sessions[transaction_id] = "verified"
    _expire_signature_request(m07["repository"], session["id"])
    reconciler = SignatureExpiryReconciler(m07["repository"], m07["signature_provider"])
    outcome = reconciler.run_once("m07-reconcile-b")
    assert outcome.manual_review == 1 and outcome.expired == 0
    snapshot = m07["repository"].snapshot()
    assert snapshot.signature_requests[(TENANT, session["id"])].status == "pending"  # not blindly resolved either way
    assert any(event.result == "requires_manual_reconciliation" for event in snapshot.audit_events)
