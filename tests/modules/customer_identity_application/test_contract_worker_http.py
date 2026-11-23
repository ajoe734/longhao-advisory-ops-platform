from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.customer_identity_application.domain import ActorContext, ConflictError, NotFoundError, ValidationError, canonical_hash, wire_time
from modules.customer_identity_application.http import M01Controller, ROUTES
from modules.customer_identity_application.ports import FakeCustomerSources, FakeEvidenceStore, FakeIdentityProvider
from modules.customer_identity_application.repository import InMemoryRepository
from modules.customer_identity_application.service import CustomerIdentityApplicationService
from workers.tasks.customer_identity_application.consumer import M01EventConsumer
from workers.tasks.customer_identity_application.tasks import LocalEventBus, OutboxPublisher

from conftest import (
    CONSENT_A,
    NOW,
    PACKAGE,
    PROOF_A,
    PROOF_C,
    MERGE_EVIDENCE,
    TENANT,
    application_body,
    create_customer,
    customer_context,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_13_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "create_customer", "list_customers", "get_customer", "update_customer", "link_external_identity",
        "propose_customer_merge", "list_customer_merges", "get_customer_merge", "decide_customer_merge",
        "create_application", "get_application", "withdraw_application", "record_consent",
    }


def test_controller_returns_etag_problem_and_replay_headers(m01, operator):
    controller = M01Controller(m01["service"])
    body = {"external_system": "legendflow", "verified_identity_proof_ref": PROOF_A}
    first = controller.handle("create_customer", operator, body=body, headers={"Idempotency-Key": "controller-key-001"})
    replay = controller.handle("create_customer", operator, body=body, headers={"Idempotency-Key": "controller-key-001"})
    stale = controller.handle(
        "update_customer", operator, path={"customer_id": first.body["id"]}, body={"display_name": "先更新"},
        headers={"Idempotency-Key": "controller-key-002", "If-Match": '"9"'},
    )
    assert first.status == 201 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert stale.status == 412 and stale.body["current_version"] == 1
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m01_producer_envelopes_validate_against_shared_json_schemas(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "contract-create-001").data
    context = customer_context(customer["id"])
    m01["service"].create_application(context, application_body(customer["id"]), idempotency_key="contract-app-001")
    m01["service"].update_customer(operator, customer["id"], {"display_name": "事件測試"}, expected_version=2, idempotency_key="contract-update-001")
    m01["evidence"].add(TENANT, CONSENT_A, customer["id"])
    m01["service"].record_consent(context, customer["id"], {
        "purpose": "privacy", "document_version": "v1", "document_sha256": "c" * 64,
        "granted": True, "evidence_asset_id": CONSENT_A,
    }, expected_version=3, idempotency_key="contract-consent-001")
    snapshot = m01["repository"].snapshot()
    assert {event.event_type for event in snapshot.outbox.values()} == {
        "customer.created", "customer.identity_linked", "application.created", "customer.profile_updated", "customer.consent_changed",
    }
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m01, operator):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    customer = create_customer(m01["service"], operator, PROOF_A, "wire-create-0001").data
    validate("Customer", customer)
    validate("CustomerPage", m01["service"].list_customers(operator).data)
    linked = m01["service"].link_external_identity(
        operator, customer["id"], {"provider": "legendflow", "verified_proof_ref": PROOF_A},
        expected_version=1, idempotency_key="wire-link-00001",
    ).data
    validate("ExternalIdentity", linked)
    application = m01["service"].create_application(
        customer_context(customer["id"]), application_body(customer["id"]), idempotency_key="wire-app-000001",
    ).data
    validate("Application", application)
    m01["evidence"].add(TENANT, CONSENT_A, customer["id"])
    consent = m01["service"].record_consent(customer_context(customer["id"]), customer["id"], {
        "purpose": "privacy", "document_version": "wire-v1", "document_sha256": "f" * 64,
        "granted": True, "evidence_asset_id": CONSENT_A,
    }, expected_version=2, idempotency_key="wire-consent-01").data
    validate("Consent", consent)
    second = create_customer(m01["service"], operator, PROOF_C, "wire-create-0002").data
    merge = m01["service"].propose_customer_merge(operator, {
        "source_customer_id": customer["id"], "target_customer_id": second["id"],
        "evidence_asset_ids": [MERGE_EVIDENCE], "reason_code": "wire_contract_test",
    }, idempotency_key="wire-merge-0001").data
    validate("MergeCase", merge)
    validate("MergeCasePage", m01["service"].list_customer_merges(
        ActorContext(TENANT, str(uuid4()), frozenset({"cms_supervisor"}), principal_type="staff")
    ).data)

    problem = M01Controller(m01["service"]).handle(
        "update_customer", operator, path={"customer_id": customer["id"]}, body={"display_name": "stale"},
        headers={"Idempotency-Key": "wire-update-001", "If-Match": '"1"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict):
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": "internal" if event_type.startswith("ownership.") else "customer_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "consumer-create-001").data
    app = m01["service"].create_application(customer_context(customer["id"]), application_body(customer["id"]), idempotency_key="consumer-app-001").data
    consumer = M01EventConsumer(m01["repository"])
    signed_payload = {
        "application_id": app["id"], "customer_signature_id": str(uuid4()), "package_version_id": PACKAGE,
        "document_sha256": "d" * 64, "customer_signed_at": wire_time(NOW),
    }
    signed = incoming("application.customer_signed", "M02", "application", app["id"], 1, signed_payload)
    assert consumer.consume(signed).result_code == "customer_signature_applied"
    assert consumer.consume(signed).status == "duplicate"
    tampered = dict(signed)
    tampered["payload"] = {**signed_payload, "document_sha256": "e" * 64}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    departed_payload = {"employee_id": str(uuid4()), "departure_id": str(uuid4()), "effective_at": wire_time(NOW)}
    # Ownership events have a different aggregate/producer ordering stream, so
    # exercise a gap with M02 company decision sequence 3 first.
    decision_payload = {"application_id": app["id"], "acceptance_id": str(uuid4()), "decision": "accept", "contract_id": str(uuid4())}
    seq3 = incoming("application.company_decided", "M02", "application", app["id"], 3, decision_payload)
    assert consumer.consume(seq3).status == "retry"
    seq2 = incoming("application.company_decided", "M02", "application", app["id"], 2, decision_payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"
    assert m01["repository"].snapshot().applications[(TENANT, app["id"])].state == "accepted"


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "consumer-create-101").data
    app = m01["service"].create_application(customer_context(customer["id"]), application_body(customer["id"]), idempotency_key="consumer-app-101").data
    consumer = M01EventConsumer(m01["repository"])
    payload = {
        "application_id": app["id"], "customer_signature_id": str(uuid4()), "package_version_id": PACKAGE,
        "document_sha256": "1" * 64, "customer_signed_at": wire_time(NOW),
    }
    wrong_schema = incoming("application.customer_signed", "M02", "application", app["id"], 1, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)
    wrong_tenant = incoming("application.customer_signed", "M02", "application", app["id"], 1, payload)
    wrong_tenant["tenant_id"] = "10000000-0000-0000-0000-000000000099"
    with pytest.raises(NotFoundError):
        consumer.consume(wrong_tenant)
    assert m01["repository"].snapshot().applications[(TENANT, app["id"])].state == "draft"


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m01, operator):
    create_customer(m01["service"], operator, PROOF_A, "worker-create-001")
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m01["repository"], bus, max_attempts=2)
    first = publisher.run_once("worker-a")
    assert first.retried == 2

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)
    m01["repository"].atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("worker-b")
    assert second.processed == 2
    assert len(bus.envelopes) == 2
    third = publisher.run_once("worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 2


def test_production_composition_refuses_fake_adapters():
    repository = InMemoryRepository()
    identity = FakeIdentityProvider({"issuer"}, "audience")
    evidence = FakeEvidenceStore()
    sources = FakeCustomerSources()
    with pytest.raises(RuntimeError):
        CustomerIdentityApplicationService(
            repository, identity, evidence, sources,
            hmac_key=b"a" * 32, environment="production",
        )
