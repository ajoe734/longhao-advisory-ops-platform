from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.pantheon_intake.domain import ActorContext, ConflictError, ValidationError, canonical_hash, wire_time
from modules.pantheon_intake.http import M05Controller, ROUTES
from modules.pantheon_intake.repository import InMemoryRepository
from modules.pantheon_intake.service import PantheonIntakeService
from workers.tasks.pantheon_intake.consumer import PantheonIntakeEventConsumer
from workers.tasks.pantheon_intake.tasks import LocalEventBus, OutboxPublisher

from conftest import NOW, TENANT, candidate_body, ingest


ROOT = Path(__file__).resolve().parents[3]


def test_all_5_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "ingest_research_candidate", "list_research_candidates", "get_research_candidate",
        "validate_research_candidate", "reject_research_candidate",
    }


def test_controller_returns_etag_problem_and_replay_headers(m05, adapter_context, reviewer_context):
    controller = M05Controller(m05["service"])
    body = candidate_body(external_event_id="00000000-0000-0000-0000-000000000201")
    first = controller.handle("ingest_research_candidate", adapter_context, body=body, headers={"Idempotency-Key": "controller-key-001"})
    replay = controller.handle("ingest_research_candidate", adapter_context, body=body, headers={"Idempotency-Key": "controller-key-001"})
    stale = controller.handle(
        "reject_research_candidate", reviewer_context, path={"candidate_id": first.body["id"]}, body={"reason_code": "test_reason"},
        headers={"Idempotency-Key": "controller-key-002", "If-Match": '"9"'},
    )
    assert first.status == 202 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert stale.status == 412 and stale.body["current_version"] == 1
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m05_producer_envelopes_validate_against_shared_json_schemas(m05, adapter_context):
    service = m05["service"]
    v1 = ingest(service, adapter_context, "contract-ingest-001", external_event_id="00000000-0000-0000-0000-000000000202", version="1", content_sha256="a" * 64)
    service.validate_research_candidate(
        m05["service_context"], v1.data["id"],
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": str(uuid4())},
        expected_version=v1.data["version"], idempotency_key="contract-validate-001",
    )
    ingest(service, adapter_context, "contract-ingest-002", external_event_id="00000000-0000-0000-0000-000000000203", version="2", content_sha256="b" * 64)
    snapshot = m05["repository"].snapshot()
    event_types = {event.event_type for event in snapshot.outbox.values()}
    assert event_types == {"research.candidate_received", "research.candidate_superseded", "research.candidate_validated"}
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m05, adapter_context, analyst_context, reviewer_context):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    service = m05["service"]
    created = ingest(service, adapter_context, "wire-ingest-001", external_event_id="00000000-0000-0000-0000-000000000204")
    validate("ResearchCandidate", created.data)
    validate("ResearchCandidatePage", service.list_research_candidates(analyst_context).data)
    fetched = service.get_research_candidate(analyst_context, created.data["id"])
    validate("ResearchCandidate", fetched.data)
    validated = service.validate_research_candidate(
        m05["service_context"], created.data["id"],
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": str(uuid4())},
        expected_version=created.data["version"], idempotency_key="wire-validate-001",
    )
    validate("ResearchCandidate", validated.data)

    second = ingest(service, adapter_context, "wire-ingest-002", external_event_id="00000000-0000-0000-0000-000000000205")
    rejected = service.reject_research_candidate(
        reviewer_context, second.data["id"], {"reason_code": "wire_contract_test"},
        expected_version=second.data["version"], idempotency_key="wire-reject-001",
    )
    validate("ResearchCandidate", rejected.data)

    problem = M05Controller(service).handle(
        "reject_research_candidate", reviewer_context, path={"candidate_id": second.data["id"]}, body={"reason_code": "already_rejected"},
        headers={"Idempotency-Key": "wire-reject-002", "If-Match": '"1"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict):
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": "research_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m05, adapter_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "consumer-ingest-001", external_event_id="00000000-0000-0000-0000-000000000206")
    candidate_id = response.data["id"]
    consumer = PantheonIntakeEventConsumer(m05["repository"])
    received_payload = {
        "candidate_id": candidate_id, "external_event_id": "00000000-0000-0000-0000-000000000206",
        "artifact_content_sha256": "a" * 64, "origin": "persona_autonomous",
    }
    envelope = incoming("research.candidate_received", "M05", "research_candidate", candidate_id, 1, received_payload)
    assert consumer.consume(envelope).result_code == "verification_job_queued"
    assert consumer.consume(envelope).status == "duplicate"
    tampered = dict(envelope)
    tampered["payload"] = {**received_payload, "artifact_content_sha256": "b" * 64}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    seq3 = incoming("research.candidate_received", "M05", "research_candidate", candidate_id, 3, received_payload)
    assert consumer.consume(seq3).status == "retry"
    seq2 = incoming("research.candidate_received", "M05", "research_candidate", candidate_id, 2, received_payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"
    jobs = [job for job in m05["repository"].snapshot().jobs.values() if job.payload.get("candidate_id") == candidate_id]
    assert len(jobs) == 1  # the real seq=1 apply already queued one job; later synthetic sequences found it queued


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m05, adapter_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "consumer-ingest-101", external_event_id="00000000-0000-0000-0000-000000000207")
    candidate_id = response.data["id"]
    consumer = PantheonIntakeEventConsumer(m05["repository"])
    payload = {
        "candidate_id": candidate_id, "external_event_id": "00000000-0000-0000-0000-000000000207",
        "artifact_content_sha256": "a" * 64, "origin": "persona_autonomous",
    }
    wrong_schema = incoming("research.candidate_received", "M05", "research_candidate", candidate_id, 5, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)

    other_tenant = "10000000-0000-0000-0000-000000000099"
    wrong_tenant = incoming("research.candidate_received", "M05", "research_candidate", candidate_id, 1, payload)
    wrong_tenant["tenant_id"] = other_tenant
    consumer.consume(wrong_tenant)
    jobs_for_real_tenant = [job for job in m05["repository"].snapshot().jobs.values() if job.tenant_id == TENANT and job.payload.get("candidate_id") == candidate_id]
    jobs_for_other_tenant = [job for job in m05["repository"].snapshot().jobs.values() if job.tenant_id == other_tenant]
    assert jobs_for_real_tenant == []  # neither the rejected schema nor the cross-tenant envelope queued work for TENANT
    assert len(jobs_for_other_tenant) == 1  # isolated under its own tenant key, never merged into TENANT's queue


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m05, adapter_context):
    ingest(m05["service"], adapter_context, "worker-ingest-001", external_event_id="00000000-0000-0000-0000-000000000208")
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m05["repository"], bus, max_attempts=2)
    first = publisher.run_once("worker-a")
    assert first.retried == 1

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)
    m05["repository"].atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("worker-b")
    assert second.processed == 1
    assert len(bus.envelopes) == 1
    third = publisher.run_once("worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 1


def test_production_composition_refuses_fake_repository():
    repository = InMemoryRepository()
    with pytest.raises(RuntimeError):
        PantheonIntakeService(repository, environment="production")
