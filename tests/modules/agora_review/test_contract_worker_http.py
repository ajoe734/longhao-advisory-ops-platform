from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.agora_review.domain import ConflictError, ValidationError, canonical_hash
from modules.agora_review.http import M06Controller, ROUTES
from modules.agora_review.ports import FakeArtifactDiff, FakeServant
from modules.agora_review.repository import InMemoryRepository
from modules.agora_review.service import AgoraReviewService
from workers.tasks.agora_review.tasks import LocalEventBus, OutboxPublisher, ServantDispatchPublisher

from conftest import (
    ANALYST,
    CANDIDATE_A,
    CONTENT_V4,
    NOW,
    TENANT,
    artifact_ref,
    assign,
    candidate_validated_envelope,
    open_conversation,
    request_revision,
    seed_case,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_7_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "list_review_cases", "get_review_case", "assign_review_case", "open_servant_conversation",
        "request_servant_revision", "receive_servant_revision", "get_revision_request",
    }


def test_controller_returns_etag_problem_and_replay_headers(m06, analyst, supervisor):
    case_id = seed_case(m06)
    controller = M06Controller(m06["service"])
    first = controller.handle("get_review_case", analyst, path={"review_case_id": case_id})
    assert first.status == 200 and first.headers["ETag"] == '"1"'

    assign_body = {"assignee_id": ANALYST, "reason_code": "workload_balance"}
    assigned = controller.handle(
        "assign_review_case", supervisor, path={"review_case_id": case_id}, body=assign_body,
        headers={"Idempotency-Key": "controller-key-001", "If-Match": '"1"'},
    )
    replay = controller.handle(
        "assign_review_case", supervisor, path={"review_case_id": case_id}, body=assign_body,
        headers={"Idempotency-Key": "controller-key-001", "If-Match": '"1"'},
    )
    stale = controller.handle(
        "assign_review_case", supervisor, path={"review_case_id": case_id}, body=assign_body,
        headers={"Idempotency-Key": "controller-key-002", "If-Match": '"99"'},
    )
    assert assigned.status == 200 and assigned.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert stale.status == 412 and stale.body["current_version"] == 2
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m06_producer_envelopes_validate_against_shared_json_schemas(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    m06["service"].receive_servant_revision(
        servant_adapter,
        {
            "event_id": str(uuid4()),
            "revision_id": revision["id"],
            "conversation_ref": conversation["external_conversation_ref"],
            "base_artifact_ref": artifact_ref(CANDIDATE_A),
            "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
            "result_content_sha256": CONTENT_V4,
            "status": "completed",
            "completed_at": "2026-11-23T02:00:00Z",
            "transcript_snapshot_asset_id": str(uuid4()),
        },
        idempotency_key="contract-callback01",
    )
    snapshot = m06["repository"].snapshot()
    assert {event.event_type for event in snapshot.outbox.values()} == {"review.revision_requested", "review.revision_received"}
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m06, analyst, supervisor, servant_adapter):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    case_id = seed_case(m06)
    validate("ReviewCase", m06["service"].get_review_case(analyst, case_id).data)
    validate("ReviewCasePage", m06["service"].list_review_cases(analyst).data)
    assigned = assign(m06, supervisor, case_id).data
    validate("ReviewCase", assigned)
    conversation = open_conversation(m06, analyst, case_id).data
    validate("Conversation", conversation)
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    validate("Revision", revision)
    fetched_revision = m06["service"].get_revision_request(analyst, revision["id"]).data
    validate("Revision", fetched_revision)
    receipt = m06["service"].receive_servant_revision(
        servant_adapter,
        {
            "event_id": str(uuid4()),
            "revision_id": revision["id"],
            "conversation_ref": conversation["external_conversation_ref"],
            "base_artifact_ref": artifact_ref(CANDIDATE_A),
            "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
            "result_content_sha256": CONTENT_V4,
            "status": "completed",
            "completed_at": "2026-11-23T02:00:00Z",
            "transcript_snapshot_asset_id": str(uuid4()),
        },
        idempotency_key="wire-callback-001",
    ).data
    validate("CallbackReceipt", receipt)

    problem = M06Controller(m06["service"]).handle(
        "assign_review_case", supervisor, path={"review_case_id": case_id},
        body={"assignee_id": ANALYST, "reason_code": "x"}, headers={"Idempotency-Key": "wire-problem-001", "If-Match": '"1"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m06):
    consumer = m06["consumer"]
    first = candidate_validated_envelope("30000000-0000-0000-0000-000000000011", "40000000-0000-0000-0000-000000000011", sequence=1)
    assert consumer.consume(first).result_code == "case_created"
    assert consumer.consume(first).status == "duplicate"
    tampered = dict(first)
    tampered["payload"] = {**first["payload"], "validation_result": "rejected"}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    candidate_012 = "30000000-0000-0000-0000-000000000012"
    seq2 = candidate_validated_envelope(candidate_012, "40000000-0000-0000-0000-000000000012", sequence=2)
    assert consumer.consume(seq2).status == "retry"
    seq1 = candidate_validated_envelope(candidate_012, "40000000-0000-0000-0000-000000000013", sequence=1, validation_result="quarantined")
    assert consumer.consume(seq1).status == "applied"
    assert consumer.consume(seq2).status == "applied"
    assert m06["repository"].snapshot().cases.get((TENANT, "40000000-0000-0000-0000-000000000012")) is not None


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m06):
    consumer = m06["consumer"]
    envelope = candidate_validated_envelope("30000000-0000-0000-0000-000000000021", "40000000-0000-0000-0000-000000000021")
    wrong_schema = dict(envelope)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)
    assert consumer.consume(envelope).result_code == "case_created"
    assert m06["repository"].snapshot().cases.get((TENANT, "40000000-0000-0000-0000-000000000021")) is not None
    wrong_tenant_envelope = candidate_validated_envelope(
        "30000000-0000-0000-0000-000000000022", "40000000-0000-0000-0000-000000000022", tenant="10000000-0000-0000-0000-000000000099",
    )
    consumer.consume(wrong_tenant_envelope)
    assert m06["repository"].snapshot().cases.get((TENANT, "40000000-0000-0000-0000-000000000022")) is None


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    request_revision(m06, analyst, case_id, conversation["id"])

    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    dispatcher = ServantDispatchPublisher(m06["repository"], m06["servant"], bus)
    publisher = OutboxPublisher(m06["repository"], dispatcher, max_attempts=2)
    # review.revision_requested dispatches cleanly (FakeServant has no failure mode set); only
    # review.revision_received forwarding to the downstream bus is made to fail here.
    first = publisher.run_once("contract-worker-a")
    assert first.processed == 1
    assert first.retried == 0

    def complete_revision(state):
        for revision in state.revisions.values():
            revision.status = "completed"
            revision.result_content_sha256 = CONTENT_V4
    # Simulate the callback already landed so a review.revision_received event exists to forward.
    m06["repository"].atomic(complete_revision)

    def emit_received(state):
        from modules.agora_review.domain import OutboxEvent, new_id

        state.outbox[(TENANT, "evt-1")] = OutboxEvent(
            event_id="evt-1", event_type="review.revision_received", tenant_id=TENANT, aggregate_type="review_case",
            aggregate_id=case_id, aggregate_version=1, sequence_no=99, correlation_id=new_id(), causation_id=None,
            idempotency_key="synthetic-received", classification="research_confidential",
            payload={"review_case_id": case_id, "revision_id": "x", "candidate_id": CANDIDATE_A, "result_content_sha256": CONTENT_V4, "stale": False},
            occurred_at=NOW, emitted_at=NOW,
        )
    m06["repository"].atomic(emit_received)
    second = publisher.run_once("contract-worker-b")
    assert second.retried == 1

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)
    m06["repository"].atomic(make_due)
    bus.failure_mode = None
    third = publisher.run_once("contract-worker-c")
    assert third.processed == 1
    assert len(bus.envelopes) == 1
    fourth = publisher.run_once("contract-worker-d")
    assert fourth.processed == 0 and len(bus.envelopes) == 1


def test_production_composition_refuses_fake_adapters():
    repository = InMemoryRepository()
    servant = FakeServant()
    diff = FakeArtifactDiff()
    with pytest.raises(RuntimeError):
        AgoraReviewService(repository, servant, diff, environment="production")
