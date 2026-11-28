from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.customer_identity_application.domain import ActorContext, ConflictError, NotFoundError, ValidationError, canonical_hash, wire_time
from modules.customer_identity_application.http import M01Controller, ROUTES, create_fastapi_router
from modules.customer_identity_application.ports import FakeCustomerSources, FakeEvidenceStore, FakeIdentityProvider
from modules.customer_identity_application.repository import InMemoryRepository
from modules.customer_identity_application.service import CustomerIdentityApplicationService
from workers.tasks.customer_identity_application.consumer import M01EventConsumer
from workers.tasks.customer_identity_application.tasks import LocalEventBus, OutboxPublisher, TaskRunner, UnknownExternalResult

from conftest import (
    CONSENT_A,
    NOW,
    OPERATOR,
    PACKAGE,
    PROOF_A,
    PROOF_B,
    PROOF_C,
    MERGE_EVIDENCE,
    TENANT,
    application_body,
    create_customer,
    customer_context,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_14_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "create_customer", "list_customers", "get_customer", "update_customer", "link_external_identity",
        "propose_customer_merge", "list_customer_merges", "get_customer_merge", "decide_customer_merge",
        "create_application", "list_applications", "get_application", "withdraw_application", "record_consent",
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
    validate("ApplicationPage", m01["service"].list_applications(operator).data)
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


def test_create_application_lands_openapi_default_status_fields(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "default-status-001").data
    application = m01["service"].create_application(
        customer_context(customer["id"]), application_body(customer["id"]), idempotency_key="default-status-app-001",
    ).data
    assert application["state"] == "draft"
    assert application["customer_signed_at"] is None
    assert application["company_accepted_at"] is None
    assert application["customer_signature_id"] is None
    assert application["kyc_submission_id"] is None
    assert application["suitability_assessment_id"] is None
    assert application["business_clearance_id"] is None
    assert application["kyc_status"] == "not_started"
    assert application["suitability_status"] == "pending"
    assert application["business_clearance"] == "pending"
    assert application["payment_status"] == "not_required"
    assert application["contract_id"] is None


def test_list_applications_filters_pages_and_preserves_read_authorization(m01, operator):
    first_customer = create_customer(m01["service"], operator, PROOF_A, "list-app-customer-001").data
    second_customer = create_customer(m01["service"], operator, PROOF_C, "list-app-customer-002").data
    first = m01["service"].create_application(
        customer_context(first_customer["id"]), application_body(first_customer["id"]), idempotency_key="list-app-create-001",
    ).data
    second = m01["service"].create_application(
        customer_context(second_customer["id"]), application_body(second_customer["id"]), idempotency_key="list-app-create-002",
    ).data
    controller = M01Controller(m01["service"])

    filtered = controller.handle(
        "list_applications", operator, query={"state": "draft", "customer_id": first_customer["id"]},
    )
    assert filtered.status == 200
    assert [item["id"] for item in filtered.body["items"]] == [first["id"]]

    first_page = controller.handle("list_applications", operator, query={"limit": "1"})
    assert first_page.status == 200 and first_page.body["next_cursor"] is not None
    second_page = controller.handle(
        "list_applications", operator, query={"limit": "1", "cursor": first_page.body["next_cursor"]},
    )
    assert set(item["id"] for item in first_page.body["items"] + second_page.body["items"]) == {first["id"], second["id"]}
    assert controller.handle("list_applications", operator, query={"state": "invented"}).status == 422

    platform_admin = ActorContext(TENANT, str(uuid4()), frozenset({"platform_admin"}), principal_type="staff")
    assert controller.handle("list_applications", platform_admin).status == 403
    assert controller.handle("get_application", platform_admin, path={"application_id": first["id"]}).status == 404
    assert controller.handle("list_applications", None).status == 401

    other_tenant = ActorContext(
        "10000000-0000-0000-0000-000000000099", str(uuid4()), frozenset({"cms_operator"}), principal_type="staff",
    )
    assert controller.handle("list_applications", other_tenant).body["items"] == []
    assert controller.handle("get_application", other_tenant, path={"application_id": first["id"]}).status == 404


def test_if_match_requires_strict_positive_integer_etag(m01, operator):
    controller = M01Controller(m01["service"])
    customer = create_customer(m01["service"], operator, PROOF_A, "if-match-001").data
    leading_zero = controller.handle(
        "update_customer", operator, path={"customer_id": customer["id"]}, body={"display_name": "x"},
        headers={"Idempotency-Key": "if-match-002", "If-Match": '"01"'},
    )
    assert leading_zero.status == 422
    zero = controller.handle(
        "update_customer", operator, path={"customer_id": customer["id"]}, body={"display_name": "x"},
        headers={"Idempotency-Key": "if-match-003", "If-Match": '"0"'},
    )
    assert zero.status == 422
    accepted = controller.handle(
        "update_customer", operator, path={"customer_id": customer["id"]}, body={"display_name": "y"},
        headers={"Idempotency-Key": "if-match-004", "If-Match": '"1"'},
    )
    assert accepted.status == 200


def test_create_customer_rejects_oversized_or_non_string_marketing_source_ref(m01, operator):
    with pytest.raises(ValidationError):
        m01["service"].create_customer(
            operator,
            {"external_system": "legendflow", "verified_identity_proof_ref": PROOF_A, "marketing_source_ref": "x" * 300},
            idempotency_key="marketing-ref-oversized-001",
        )
    with pytest.raises(ValidationError):
        m01["service"].create_customer(
            operator,
            {"external_system": "legendflow", "verified_identity_proof_ref": PROOF_A, "marketing_source_ref": 12345},
            idempotency_key="marketing-ref-nonstring-001",
        )


def test_fastapi_router_default_prefix_matches_platform_mount(m01, operator):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    customer = create_customer(m01["service"], operator, PROOF_A, "router-prefix-001").data

    def resolver(_request):
        return operator

    app = fastapi.FastAPI()
    app.include_router(create_fastapi_router(m01["service"], resolver), prefix="/api/v1")
    client = TestClient(app)

    direct = client.get(f"/api/v1/customers/{customer['id']}")
    assert direct.status_code == 200
    assert direct.json()["id"] == customer["id"]

    doubled_prefix = client.get(f"/api/v1/api/v1/customers/{customer['id']}")
    assert doubled_prefix.status_code == 404

    rejected = client.post(
        "/api/v1/customers",
        json={"external_system": "legendflow", "verified_identity_proof_ref": PROOF_B, "marketing_source_ref": "x" * 300},
        headers={"Idempotency-Key": "router-prefix-create-001"},
    )
    assert rejected.status_code == 422


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
    assert m01["repository"].snapshot().applications[(TENANT, app["id"])].customer_signature_id == signed_payload["customer_signature_id"]
    tampered = dict(signed)
    tampered["payload"] = {**signed_payload, "document_sha256": "e" * 64}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    # Company decision sequence tracking is independent of the signature
    # stream above because they share the same M02 producer/aggregate_id;
    # exercise a gap with sequence 3 first.
    decision_payload = {"application_id": app["id"], "acceptance_id": str(uuid4()), "decision": "accept", "contract_id": str(uuid4())}
    seq3 = incoming("application.company_decided", "M02", "application", app["id"], 3, decision_payload)
    assert consumer.consume(seq3).status == "retry"
    seq2 = incoming("application.company_decided", "M02", "application", app["id"], 2, decision_payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"
    assert m01["repository"].snapshot().applications[(TENANT, app["id"])].state == "accepted"


def test_consumer_projects_gate_record_ids_and_assignment_versions(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "gate-projection-customer-001").data
    app = m01["service"].create_application(
        customer_context(customer["id"]), application_body(customer["id"]), idempotency_key="gate-projection-app-001",
    ).data
    consumer = M01EventConsumer(m01["repository"])
    signature_id = str(uuid4())
    kyc_submission_id = str(uuid4())
    assessment_id = str(uuid4())
    clearance_id = str(uuid4())
    owner_id = str(uuid4())
    assignment_id = str(uuid4())

    assert consumer.consume(incoming("application.customer_signed", "M02", "application", app["id"], 1, {
        "application_id": app["id"], "customer_signature_id": signature_id, "package_version_id": PACKAGE,
        "document_sha256": "a" * 64, "customer_signed_at": wire_time(NOW),
    })).result_code == "customer_signature_applied"
    assert consumer.consume(incoming("kyc.decision_recorded", "M03", "kyc_submission", kyc_submission_id, 3, {
        "submission_id": kyc_submission_id, "application_id": app["id"], "decision": "approved", "next_review_due_at": None,
    })).result_code == "kyc_gate_projected"
    assert consumer.consume(incoming("kyc.refresh_required", "M03", "kyc_submission", kyc_submission_id, 4, {
        "customer_id": customer["id"], "submission_id": kyc_submission_id,
        "refresh_case_id": str(uuid4()), "due_at": wire_time(NOW + timedelta(days=30)),
    })).result_code == "kyc_refresh_projected"
    assert consumer.consume(incoming("suitability.assessed", "M03", "suitability_assessment", assessment_id, 2, {
        "assessment_id": assessment_id, "application_id": app["id"], "state": "conditional", "rule_version": "synthetic-v1",
    })).result_code == "suitability_gate_projected"
    assert consumer.consume(incoming("business_clearance.changed", "M04", "business_clearance", clearance_id, 2, {
        "clearance_id": clearance_id, "application_id": app["id"], "decision": "approved", "expires_at": None,
    })).result_code == "business_clearance_projected"

    projected = m01["service"].get_application(operator, app["id"]).data
    assert {key: projected[key] for key in (
        "customer_signature_id", "kyc_submission_id", "suitability_assessment_id", "business_clearance_id",
    )} == {
        "customer_signature_id": signature_id,
        "kyc_submission_id": kyc_submission_id,
        "suitability_assessment_id": assessment_id,
        "business_clearance_id": clearance_id,
    }
    assert projected["kyc_status"] == "refresh_required"
    assert projected["suitability_status"] == "conditional"
    assert projected["business_clearance"] == "approved"

    active = incoming("ownership.assignment_changed", "M11", "maintenance_assignment", assignment_id, 2, {
        "assignment_id": assignment_id, "customer_id": customer["id"], "salesperson_id": owner_id,
        "effective_at": wire_time(NOW), "state": "active",
    })
    assert consumer.consume(active).result_code == "maintenance_owner_projected"
    assert consumer.consume(active).status == "duplicate"
    stale = incoming("ownership.assignment_changed", "M11", "maintenance_assignment", assignment_id, 1, {
        "assignment_id": assignment_id, "customer_id": customer["id"], "salesperson_id": None,
        "effective_at": wire_time(NOW - timedelta(days=1)), "state": "released",
    })
    assert consumer.consume(stale).result_code == "stale_aggregate_version_ignored"
    assert m01["repository"].snapshot().customers[(TENANT, customer["id"])].maintenance_owner_id == owner_id
    released = incoming("ownership.assignment_changed", "M11", "maintenance_assignment", assignment_id, 3, {
        "assignment_id": assignment_id, "customer_id": customer["id"], "salesperson_id": owner_id,
        "effective_at": wire_time(NOW + timedelta(days=1)), "state": "released",
    })
    assert consumer.consume(released).result_code == "maintenance_owner_projected"
    assert m01["repository"].snapshot().customers[(TENANT, customer["id"])].maintenance_owner_id is None


def test_consumer_rejects_company_accept_decision_on_unsigned_draft(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "accept-draft-001").data
    app = m01["service"].create_application(
        customer_context(customer["id"]), application_body(customer["id"]), idempotency_key="accept-draft-app-001",
    ).data
    consumer = M01EventConsumer(m01["repository"])
    decision_payload = {"application_id": app["id"], "acceptance_id": str(uuid4()), "decision": "accept", "contract_id": str(uuid4())}
    envelope = incoming("application.company_decided", "M02", "application", app["id"], 1, decision_payload)
    with pytest.raises(ConflictError):
        consumer.consume(envelope)
    unsigned = m01["repository"].snapshot().applications[(TENANT, app["id"])]
    assert unsigned.state == "draft"
    assert unsigned.customer_signed_at is None


def test_consumer_employee_departed_clears_owner_with_dedup_and_tenant_isolation(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "departed-create-001").data
    other_tenant = "10000000-0000-0000-0000-000000000099"
    other_context = ActorContext(other_tenant, OPERATOR, frozenset({"cms_operator"}), principal_type="staff")
    other_customer = create_customer(m01["service"], other_context, PROOF_A, "departed-create-002").data
    employee_id = str(uuid4())

    def assign_owners(state):
        state.customers[(TENANT, customer["id"])].maintenance_owner_id = employee_id
        state.customers[(other_tenant, other_customer["id"])].maintenance_owner_id = employee_id

    m01["repository"].atomic(assign_owners)

    consumer = M01EventConsumer(m01["repository"])
    payload = {"employee_id": employee_id, "departure_id": str(uuid4()), "effective_at": wire_time(NOW)}
    envelope = incoming("ownership.employee_departed", "M11", "employee", employee_id, 1, payload)
    result = consumer.consume(envelope)
    assert result.status == "applied" and result.result_code == "departed_owner_cleared:1"

    snapshot = m01["repository"].snapshot()
    assert snapshot.customers[(TENANT, customer["id"])].maintenance_owner_id is None
    assert snapshot.customers[(other_tenant, other_customer["id"])].maintenance_owner_id == employee_id

    assert consumer.consume(envelope).status == "duplicate"

    mismatched = incoming("ownership.employee_departed", "M11", "employee", str(uuid4()), 2, payload)
    with pytest.raises(ValidationError):
        consumer.consume(mismatched)


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


class _AmbiguousOnceBus:
    """Publish succeeds on the provider side but the ack is lost once; lookup finds it."""

    is_fake = True

    def __init__(self) -> None:
        self.envelopes: dict[str, dict] = {}
        self.publish_calls = 0

    def publish(self, envelope: dict) -> str:
        self.publish_calls += 1
        self.envelopes[envelope["event_id"]] = envelope
        raise UnknownExternalResult(envelope["event_id"])

    def lookup(self, event_id: str) -> str:
        return "accepted" if event_id in self.envelopes else "not_found"


def test_outbox_worker_checks_lookup_before_retrying_unknown_external_result(m01, operator):
    create_customer(m01["service"], operator, PROOF_A, "worker-ambiguous-001")
    bus = _AmbiguousOnceBus()
    publisher = OutboxPublisher(m01["repository"], bus, max_attempts=5)
    result = publisher.run_once("worker-ambiguous")
    assert result.processed == 2 and result.retried == 0 and result.dead_lettered == 0
    assert bus.publish_calls == 2
    assert {event.status for event in m01["repository"].snapshot().outbox.values()} == {"published"}


def test_outbox_worker_dead_letters_after_max_attempts_on_unknown_external_result(m01, operator):
    create_customer(m01["service"], operator, PROOF_B, "worker-deadletter-001")
    bus = LocalEventBus()
    bus.failure_mode = "unknown"
    publisher = OutboxPublisher(m01["repository"], bus, max_attempts=1)
    result = publisher.run_once("worker-deadletter")
    assert result.processed == 0 and result.retried == 0 and result.dead_lettered == 2
    for event in m01["repository"].snapshot().outbox.values():
        assert event.status == "dead_letter"
        assert event.attempt_count == 1
        assert event.last_error_code == "publisher_unknown"


def test_task_runner_lease_blocks_double_lease_and_redrive_requires_dead_letter_and_reason(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "task-runner-001").data
    app = m01["service"].create_application(
        customer_context(customer["id"]), application_body(customer["id"]), idempotency_key="task-runner-app-001",
    ).data

    def mark_paid(state):
        state.applications[(TENANT, app["id"])].payment_status = "paid"

    m01["repository"].atomic(mark_paid)
    m01["service"].withdraw_application(
        customer_context(customer["id"]), app["id"], {"reason_code": "customer_requested"},
        expected_version=app["version"], idempotency_key="task-runner-withdraw-001",
    )
    jobs = list(m01["repository"].snapshot().jobs.values())
    assert len(jobs) == 1
    job = jobs[0]
    assert job.kind == "withdrawal_finance_review"
    assert job.payload == {"application_id": app["id"], "payment_status": "paid", "reason_code": "customer_requested"}

    runner = TaskRunner(m01["repository"])
    leased = runner.lease("runner-a")
    assert [item.id for item in leased] == [job.id]
    assert m01["repository"].snapshot().jobs[(TENANT, job.id)].status == "leased"
    assert runner.lease("runner-b") == []

    with pytest.raises(ValueError):
        runner.redrive(TENANT, job.id, "   ")
    with pytest.raises(ConflictError):
        runner.redrive(TENANT, job.id, "not dead-letter yet")

    def force_dead_letter(state):
        state.jobs[(TENANT, job.id)].status = "dead_letter"

    m01["repository"].atomic(force_dead_letter)
    redriven = runner.redrive(TENANT, job.id, "confirmed with finance ops")
    assert redriven.status == "retry"
    assert redriven.redrive_reason == "confirmed with finance ops"
    assert redriven.lease_owner is None


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
