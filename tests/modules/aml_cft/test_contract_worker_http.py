from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.aml_cft.domain import ActorContext, ConflictError, ValidationError, canonical_hash, wire_time
from modules.aml_cft.http import M04Controller, ROUTES
from modules.aml_cft.ports import FakeApplicationAccess, FakeEvidenceStore, FakeScreeningProvider
from modules.aml_cft.repository import InMemoryRepository
from modules.aml_cft.service import AmlCftService
from workers.tasks.aml_cft.consumer import AmlEventConsumer
from workers.tasks.aml_cft.tasks import LocalEventBus, OutboxPublisher

from conftest import (
    APPLICATION_A,
    CUSTOMER_A,
    EVIDENCE_1,
    NOW,
    TENANT,
    declaration_body,
    screening_callback,
    screening_request,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_8_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "submit_aml_declaration", "start_aml_screening", "receive_aml_screening", "list_aml_cases",
        "get_aml_case", "add_aml_case_action", "decide_aml_case", "get_business_clearance",
    }


def test_controller_returns_etag_problem_and_replay_headers(m04, officer):
    controller = M04Controller(m04["service"])
    body = screening_request()
    first = controller.handle("start_aml_screening", officer, body=body, headers={"Idempotency-Key": "controller-key-001"})
    replay = controller.handle("start_aml_screening", officer, body=body, headers={"Idempotency-Key": "controller-key-001"})
    missing_if_match = controller.handle(
        "decide_aml_case", officer, path={"aml_case_id": str(uuid4())},
        body={"decision": "approve", "risk_level": "low", "reason_code": "x", "restricted_evidence_ids": [EVIDENCE_1]},
        headers={"Idempotency-Key": "controller-key-002"},
    )
    assert first.status == 202 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert missing_if_match.status == 403  # role check runs before the If-Match precondition
    assert missing_if_match.headers["Content-Type"] == "application/problem+json"


def test_controller_requires_if_match_for_supervisor_decision(m04, officer, supervisor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="ctl-screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id=str(uuid4())), idempotency_key="ctl-callback-001")
    case_id = service.list_aml_cases(officer).data["items"][0]["id"]
    controller = M04Controller(service)
    missing = controller.handle(
        "decide_aml_case", supervisor, path={"aml_case_id": case_id},
        body={"decision": "approve", "risk_level": "low", "reason_code": "x", "restricted_evidence_ids": [EVIDENCE_1]},
        headers={"Idempotency-Key": "ctl-decide-001"},
    )
    assert missing.status == 428
    assert missing.body["code"] == "precondition_required"


def test_m04_producer_envelopes_validate_against_shared_json_schemas(m04, officer):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="contract-screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id=str(uuid4())), idempotency_key="contract-callback-001")
    snapshot = m04["repository"].snapshot()
    event_types = {event.event_type for event in snapshot.outbox.values()}
    assert event_types == {"aml.case_changed", "business_clearance.changed"}
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = __import__("json").loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m04, officer, supervisor, company_acceptor):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    service = m04["service"]

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    declaration = service.submit_aml_declaration(officer, APPLICATION_A, declaration_body(), expected_version=None, idempotency_key="wire-decl-0001").data
    validate("AmlDeclarationReceipt", declaration)

    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="wire-screen-0001").data
    validate("AmlScreening", screening)

    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    receipt = service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id=str(uuid4())), idempotency_key="wire-callback-0001").data
    validate("CallbackReceipt", receipt)

    cases = service.list_aml_cases(officer).data
    validate("AmlCasePage", cases)
    case_id = cases["items"][0]["id"]
    case = service.get_aml_case(officer, case_id).data
    validate("AmlCase", case)

    acted = service.add_aml_case_action(
        officer, case_id, {"action": "record_investigation", "reason_code": "x", "restricted_note_asset_id": EVIDENCE_1},
        expected_version=case["version"], idempotency_key="wire-action-0001",
    ).data
    validate("AmlCase", acted)

    decided = service.decide_aml_case(
        supervisor, case_id,
        {"decision": "approve", "risk_level": "low", "reason_code": "x", "restricted_evidence_ids": [EVIDENCE_1]},
        expected_version=acted["version"], idempotency_key="wire-decide-0001",
    ).data
    validate("BusinessClearance", decided)
    validate("BusinessClearance", service.get_business_clearance(company_acceptor, APPLICATION_A).data)

    problem = M04Controller(service).handle(
        "decide_aml_case", supervisor, path={"aml_case_id": case_id},
        body={"decision": "approve", "risk_level": "low", "reason_code": "x", "restricted_evidence_ids": [EVIDENCE_1]},
        headers={"Idempotency-Key": "wire-decide-0002", "If-Match": f'"{acted["version"] + 1}"'},
    )
    assert problem.status == 409  # case already resolved
    validate("Problem", problem.body)


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict):
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": "customer_confidential", "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m04, officer):
    consumer = AmlEventConsumer(m04["repository"])
    payload = {"customer_id": CUSTOMER_A, "changed_field_codes": ["identity_evidence"], "profile_evidence_ref": str(uuid4())}
    first = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 1, payload)
    assert consumer.consume(first).result_code == "no_refresh_needed"
    assert consumer.consume(first).status == "duplicate"

    tampered = dict(first)
    tampered["payload"] = {**payload, "changed_field_codes": ["display_name"]}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    incomplete_payload = {"submission_id": str(uuid4()), "application_id": APPLICATION_A, "decision": "incomplete", "next_review_due_at": None}
    seq1 = incoming("kyc.decision_recorded", "M03", "kyc_submission", APPLICATION_A, 1, incomplete_payload)
    assert consumer.consume(seq1).status == "applied"

    decided_payload = {"submission_id": str(uuid4()), "application_id": APPLICATION_A, "decision": "approved", "next_review_due_at": None}
    seq3 = incoming("kyc.decision_recorded", "M03", "kyc_submission", APPLICATION_A, 3, decided_payload)
    assert consumer.consume(seq3).status == "retry"
    seq2 = incoming("kyc.decision_recorded", "M03", "kyc_submission", APPLICATION_A, 2, decided_payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m04):
    consumer = AmlEventConsumer(m04["repository"])
    payload = {"customer_id": CUSTOMER_A, "changed_field_codes": ["identity_evidence"], "profile_evidence_ref": str(uuid4())}
    wrong_schema = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 1, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)

    wrong_tenant = incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 1, payload)
    wrong_tenant["tenant_id"] = "10000000-0000-0000-0000-000000000099"
    result = consumer.consume(wrong_tenant)
    assert result.status == "applied"
    assert m04["repository"].snapshot().inbox.get((TENANT, "M04", "M01", wrong_tenant["event_id"])) is None


def test_consumer_flags_approved_clearance_for_refresh_on_identity_change(m04, officer, supervisor):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="consumer-screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "clear", event_id=str(uuid4())), idempotency_key="consumer-callback-001")
    before = service.get_business_clearance(
        ActorContext(TENANT, "20000000-0000-0000-0000-000000000004", frozenset({"company_acceptor"}), principal_type="staff"), APPLICATION_A,
    ).data
    assert before["decision"] == "approved"

    consumer = AmlEventConsumer(m04["repository"])
    payload = {"customer_id": CUSTOMER_A, "changed_field_codes": ["identity_evidence"], "profile_evidence_ref": str(uuid4())}
    result = consumer.consume(incoming("customer.profile_updated", "M01", "customer", CUSTOMER_A, 1, payload))
    assert result.result_code.startswith("clearances_flagged_for_refresh")

    after = service.get_business_clearance(
        ActorContext(TENANT, "20000000-0000-0000-0000-000000000004", frozenset({"company_acceptor"}), principal_type="staff"), APPLICATION_A,
    ).data
    assert after["decision"] == "pending"


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m04, officer):
    service = m04["service"]
    screening = service.start_aml_screening(officer, screening_request(), idempotency_key="worker-screen-001").data
    adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000006", frozenset({"aml_adapter"}), principal_type="service")
    service.receive_aml_screening(adapter, screening_callback(screening["id"], "possible_match", event_id=str(uuid4())), idempotency_key="worker-callback-001")
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m04["repository"], bus, max_attempts=2)
    first = publisher.run_once("worker-a")
    assert first.retried == 2

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = m04["clock"].value - timedelta(seconds=1)
    m04["repository"].atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("worker-b")
    assert second.processed == 2
    assert len(bus.envelopes) == 2
    third = publisher.run_once("worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 2


def test_production_composition_refuses_fake_adapters():
    repository = InMemoryRepository()
    provider = FakeScreeningProvider()
    evidence = FakeEvidenceStore()
    application_access = FakeApplicationAccess()
    with pytest.raises(RuntimeError):
        AmlCftService(repository, provider, evidence, application_access, environment="production")
