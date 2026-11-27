from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.kyc_suitability.domain import ConflictError, NotFoundError, ValidationError, canonical_hash, wire_time
from modules.kyc_suitability.http import M03Controller, ROUTES
from modules.kyc_suitability.ports import ScanOutcome

from workers.tasks.kyc_suitability.consumer import M03EventConsumer
from workers.tasks.kyc_suitability.tasks import DocumentScanWorker, LocalEventBus, OutboxPublisher

from conftest import (
    APPLICATION_A,
    CUSTOMER_A,
    NOW,
    SIGNATURE_A,
    TENANT,
    _version,
    approve_full_cycle,
    complete_answers,
    create_submission,
    customer_context,
    mark_document_clean,
    suitability_answers,
    upload_document,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_8_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "create_kyc_submission", "request_kyc_upload", "submit_kyc", "get_kyc_submission",
        "decide_kyc", "assess_suitability", "request_due_diligence_refresh", "get_document_status",
    }


def test_controller_returns_etag_problem_and_replay_headers(m03, kyc_reviewer):
    service = m03["service"]
    controller = M03Controller(service)
    customer = customer_context(CUSTOMER_A)
    body = {"form_version": "m03-kyc-form-v1", "customer_signature_id": SIGNATURE_A}
    first = controller.handle(
        "create_kyc_submission", customer, path={"application_id": APPLICATION_A}, body=body,
        headers={"Idempotency-Key": "controller-key-001", "If-Match": '"1"'},
    )
    replay = controller.handle(
        "create_kyc_submission", customer, path={"application_id": APPLICATION_A}, body=body,
        headers={"Idempotency-Key": "controller-key-001", "If-Match": '"1"'},
    )
    stale = controller.handle(
        "get_document_status", kyc_reviewer, path={"asset_id": str(uuid4())}, headers={},
    )
    assert first.status == 201 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.headers["ETag"] == '"1"'  # a replayed response still carries the resource's ETag
    assert stale.status == 404
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m03_producer_envelopes_validate_against_shared_json_schemas(m03, kyc_reviewer, suitability_reviewer):
    service = m03["service"]
    decided = approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "contract")
    service.assess_suitability(
        suitability_reviewer, APPLICATION_A,
        {
            "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
            "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
            "service_codes": ["annual_advisory"], "decision": "suitable", "conditions": [],
            "reason_code": "rule_pass", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
        },
        expected_version=1, idempotency_key="contract-assess",
    )
    service.request_due_diligence_refresh(
        kyc_reviewer, CUSTOMER_A,
        {"trigger": "scheduled", "reason_code": "periodic_refresh", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
        expected_version=1, idempotency_key="contract-refresh",
    )
    snapshot = service.repository.snapshot()
    assert {event.event_type for event in snapshot.outbox.values()} == {
        "kyc.decision_recorded", "suitability.assessed", "kyc.refresh_required",
    }
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m03, kyc_reviewer, suitability_reviewer):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "wire-create-0001")
    validate("KycSubmission", created.data)
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "wire-upload-0001")
    validate("UploadTicket", uploaded.data)
    submitted = service.submit_kyc(
        customer, created.data["id"],
        {"form_version": "m03-kyc-form-v1", "answers": complete_answers(), "document_ids": [uploaded.data["asset_id"]], "declaration_consent_id": SIGNATURE_A},
        expected_version=_version(uploaded), idempotency_key="wire-submit-0001",
    )
    validate("KycSubmission", submitted.data)
    mark_document_clean(service, uploaded.data["asset_id"])
    decided = service.decide_kyc(
        kyc_reviewer, submitted.data["id"],
        {
            "decision": "approve", "identity_verification_method": "manual_document_review",
            "verification_evidence_ids": [uploaded.data["asset_id"]], "reason_code": "evidence_confirmed",
            "next_review_due_at": "2027-11-23T00:00:00.000000Z",
        },
        expected_version=_version(submitted), idempotency_key="wire-decide-0001",
    )
    validate("KycSubmission", decided.data)
    assessed = service.assess_suitability(
        suitability_reviewer, APPLICATION_A,
        {
            "kyc_submission_id": decided.data["id"], "questionnaire_version": "m03-suitability-questionnaire-v1",
            "rule_version": "m03-suitability-rule-v1", "answers": suitability_answers("low", "1"),
            "service_codes": ["annual_advisory"], "decision": "suitable", "conditions": [],
            "reason_code": "rule_pass", "next_review_due_at": "2027-05-23T00:00:00.000000Z",
        },
        expected_version=1, idempotency_key="wire-assess-0001",
    )
    validate("SuitabilityAssessment", assessed.data)
    status = service.get_document_status(kyc_reviewer, uploaded.data["asset_id"])
    validate("DocumentStatus", status.data)
    assert status.etag == '"2"'  # ETag present on a get_document_status 200 (version bumped by the clean scan)

    refreshed = service.request_due_diligence_refresh(
        kyc_reviewer, CUSTOMER_A,
        {"trigger": "scheduled", "reason_code": "periodic_refresh", "due_at": "2027-01-01T00:00:00.000000Z", "form_version": "m03-kyc-form-v1"},
        expected_version=1, idempotency_key="wire-refresh-0001",
    )
    validate("KycSubmission", refreshed.data)

    problem = M03Controller(service).handle(
        "decide_kyc", kyc_reviewer, path={"submission_id": decided.data["id"]},
        body={
            "decision": "approve", "identity_verification_method": "manual_document_review",
            "verification_evidence_ids": [uploaded.data["asset_id"]], "reason_code": "stale",
        },
        headers={"Idempotency-Key": "wire-decide-stale-0001", "If-Match": '"1"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict):
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": "customer_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m03):
    consumer = M03EventConsumer(m03["service"].repository)
    first_payload = {
        "application_id": APPLICATION_A, "customer_signature_id": SIGNATURE_A, "package_version_id": str(uuid4()),
        "document_sha256": "d" * 64, "customer_signed_at": "2026-11-20T00:00:00.000000Z",
    }
    first = incoming("application.customer_signed", "M02", "application", APPLICATION_A, 1, first_payload)
    assert consumer.consume(first).result_code == "signed_application_projected"
    assert consumer.consume(first).status == "duplicate"
    tampered = dict(first)
    tampered["payload"] = {**first_payload, "document_sha256": "e" * 64}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    profile_payload = {"customer_id": CUSTOMER_A, "changed_field_codes": ["display_name"], "profile_evidence_ref": str(uuid4())}
    seq1 = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 1, profile_payload)
    assert consumer.consume(seq1).status == "applied"
    # M03 only subscribes to customer.profile_updated of the M01 customer stream.
    seq3 = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 3, profile_payload)
    assert consumer.consume(seq3).status == "applied"
    seq2 = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 2, profile_payload)
    assert consumer.consume(seq2).result_code == "stale_sequence_ignored"
    assert consumer.consume(seq3).status == "duplicate"


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m03):
    consumer = M03EventConsumer(m03["service"].repository)
    payload = {"customer_id": CUSTOMER_A, "changed_field_codes": ["display_name"], "profile_evidence_ref": str(uuid4())}
    wrong_schema = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 1, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)
    wrong_tenant = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 1, payload)
    wrong_tenant["tenant_id"] = "10000000-0000-0000-0000-000000000099"
    result = consumer.consume(wrong_tenant)
    assert result.status == "applied"  # a different tenant's inbox row, isolated from TENANT's state
    snapshot = m03["service"].repository.snapshot()
    assert not any(row.tenant_id == TENANT for row in snapshot.audit_events if row.action.startswith("m03.consume"))


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m03, kyc_reviewer):
    service = m03["service"]
    approve_full_cycle(service, kyc_reviewer, APPLICATION_A, SIGNATURE_A, "worker")
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(service.repository, bus, max_attempts=2)
    first = publisher.run_once("m03-worker-a")
    assert first.retried == 1

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = m03["clock"]() - timedelta(seconds=1)
            event.lease_until = None
    service.repository.atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("m03-worker-b")
    assert second.processed == 1
    assert len(bus.envelopes) == 1
    third = publisher.run_once("m03-worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 1


def test_document_scan_worker_never_marks_pending_as_passed_on_transient_failure(m03):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "scan-worker-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "scan-worker-upload")
    m03["scanner"].fail(uploaded.data["asset_id"], "timeout")
    worker = DocumentScanWorker(service.repository, m03["scanner"])
    worker.run_once("scan-drill")
    document = service.repository.snapshot().documents[(TENANT, uploaded.data["asset_id"])]
    assert document.scan_status == "pending"


def test_document_scan_worker_tracks_lease_attempts_and_alerts_after_max_retries(m03):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "scan-retry-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "scan-retry-upload")
    asset_id = uploaded.data["asset_id"]
    m03["scanner"].fail(asset_id, "timeout")
    worker = DocumentScanWorker(service.repository, m03["scanner"], max_attempts=2)

    def make_due(state):
        for document in state.documents.values():
            document.next_attempt_at = None
            document.lease_until = None

    worker.run_once("scan-retry-a")
    document = service.repository.snapshot().documents[(TENANT, asset_id)]
    assert document.attempt_count == 1 and document.last_error_code == "document_scanner_timeout"
    assert document.lease_owner is None  # released so another worker can retry
    assert not worker.alerts

    service.repository.atomic(make_due)
    worker.run_once("scan-retry-b")
    document = service.repository.snapshot().documents[(TENANT, asset_id)]
    assert document.attempt_count == 2 and document.scan_status == "pending"  # still never "passed" on failure
    assert worker.alerts and worker.alerts[0].startswith("m03_document_scanner_unavailable:")


def test_document_scan_worker_rejects_stolen_lease(m03):
    service = m03["service"]
    customer = customer_context(CUSTOMER_A)
    created = create_submission(service, customer, APPLICATION_A, "scan-lease-create")
    uploaded = upload_document(service, customer, created.data["id"], _version(created), "scan-lease-upload")
    asset_id = uploaded.data["asset_id"]
    worker = DocumentScanWorker(service.repository, m03["scanner"])
    worker._lease("scan-lease-worker-a", 50)  # another worker already owns the lease

    with pytest.raises(ConflictError) as exc:
        worker._complete(TENANT, asset_id, "scan-lease-worker-b", ScanOutcome("clean"))
    assert exc.value.code == "worker_lease_lost"


def test_production_composition_refuses_fake_adapters():
    from modules.kyc_suitability.ports import FakeApplicationGateway, FakeCustomerGateway
    from modules.kyc_suitability.repository import InMemoryRepository
    from modules.kyc_suitability.service import KycSuitabilityService

    repository = InMemoryRepository()
    gateway = FakeApplicationGateway()
    customers = FakeCustomerGateway()
    with pytest.raises(RuntimeError):
        KycSuitabilityService(repository, gateway, customers, environment="production")
