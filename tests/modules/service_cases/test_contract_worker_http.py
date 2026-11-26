from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.service_cases.domain import ActorContext, ConflictError, NotFoundError, ValidationError, canonical_hash, wire_time
from modules.service_cases.http import M13Controller, ROUTES
from modules.service_cases.ports import FakeChannelBindings, FakeKnowledgeBase, FakeObjectAssetStore, FakeSuppressionRequests
from modules.service_cases.automation import LocalDraftingAssistant
from modules.service_cases.repository import InMemoryRepository
from modules.service_cases.service import ServiceCaseService
from workers.tasks.service_cases.consumer import M13EventConsumer
from workers.tasks.service_cases.tasks import LocalEventBus, LocalMessagingProvider, OutboxPublisher, ResponseDispatchSender

from conftest import (
    CHANNEL_BINDING,
    CONTEXT_EVIDENCE,
    CUSTOMER_ID,
    KNOWLEDGE_SOURCE_REF,
    KNOWLEDGE_VERSION,
    NOTICE_EVIDENCE,
    NOW,
    RECEIPT_EVIDENCE,
    TENANT,
    case_body,
    create_case,
    draft_body,
)


ROOT = Path(__file__).resolve().parents[3]


def test_all_14_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "create_service_case", "list_service_cases", "get_service_case", "add_case_interaction",
        "assign_service_case", "escalate_service_case", "draft_service_response", "resolve_service_case",
        "reopen_service_case", "get_service_response_draft", "send_service_response", "get_service_response",
        "receive_service_response_receipt", "approve_service_response_draft",
    }


def test_controller_returns_etag_problem_and_replay_headers(m13, agent, supervisor):
    controller = M13Controller(m13["service"])
    body = case_body()
    first = controller.handle("create_service_case", agent, body=body, headers={"Idempotency-Key": "controller-key-001"})
    replay = controller.handle("create_service_case", agent, body=body, headers={"Idempotency-Key": "controller-key-001"})
    stale = controller.handle(
        "assign_service_case", supervisor, path={"case_id": first.body["id"]},
        body={"assignee_id": "20000000-0000-0000-0000-000000000001", "reason_code": "stale_write_check"},
        headers={"Idempotency-Key": "controller-key-002", "If-Match": '"9"'},
    )
    assert first.status == 201 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert stale.status == 412 and stale.body["current_version"] == 1
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m13_producer_envelopes_validate_against_shared_json_schemas(m13, agent, agent_two):
    service = m13["service"]
    case = create_case(service, agent, "contract-create-001").data
    service.escalate_service_case(agent, case["id"], {
        "target_queue": "technical", "reason_code": "needs_followup", "due_at": wire_time(NOW),
        "minimum_necessary_context_asset_id": CONTEXT_EVIDENCE,
    }, expected_version=case["version"], idempotency_key="contract-escalate-001")
    case_latest = service.get_service_case(agent, case["id"]).data
    service.resolve_service_case(agent, case["id"], {
        "resolution_code": "contract_resolved", "resolution_summary": "完成契約測試案件",
        "customer_notice_evidence_id": NOTICE_EVIDENCE,
    }, expected_version=case_latest["version"], idempotency_key="contract-resolve-001")

    second_case = create_case(service, agent, "contract-create-002").data
    draft = service.draft_service_response(agent, second_case["id"], draft_body(), expected_version=second_case["version"], idempotency_key="contract-draft-001").data
    approval = service.approve_service_response_draft(agent_two, draft["id"], {
        "draft_content_sha256": _draft_content_sha(m13, draft), "decision": "approve", "approval_mode": "human_reviewed",
        "template_version": "manual", "policy_version": "policy-v1", "reason_code": "ok", "publication_reference_ids": [],
        "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=1, idempotency_key="contract-approve-001").data
    second_latest = service.get_service_case(agent, second_case["id"]).data
    service.send_service_response(agent, second_case["id"], {
        "draft_id": draft["id"], "draft_content_sha256": approval["draft_content_sha256"], "channel_binding_id": CHANNEL_BINDING,
        "channel": "email", "approval_mode": "human_reviewed", "approval_id": approval["id"], "template_version": "manual",
        "publication_reference_ids": [], "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=second_latest["version"], idempotency_key="contract-send-001")

    snapshot = m13["repository"].snapshot()
    event_types = {event.event_type for event in snapshot.outbox.values()}
    assert event_types == {"support.case_opened", "support.case_escalated", "support.case_resolved", "support.response_status_changed"}
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def _draft_content_sha(m13, draft):
    got = m13["service"].get_service_response_draft(
        ActorContext(TENANT, "20000000-0000-0000-0000-000000000001", frozenset({"service_agent"}), principal_type="staff"),
        draft["id"],
    ).data
    import hashlib
    return hashlib.sha256(got["text"].encode("utf-8")).hexdigest()


def test_wire_responses_validate_against_openapi_1_0(m13, agent, agent_two, supervisor, messaging_adapter):
    service = m13["service"]
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    case = create_case(service, agent, "wire-create-0001").data
    validate("ServiceCase", case)
    validate("ServiceCasePage", service.list_service_cases(agent).data)
    interaction = service.add_case_interaction(agent, case["id"], {
        "channel": "email", "direction": "inbound", "occurred_at": wire_time(NOW),
        "content_asset_id": "40000000-0000-0000-0000-000000000002", "summary": "客戶補充說明",
    }, expected_version=case["version"], idempotency_key="wire-interaction-0001").data
    validate("CaseInteraction", interaction)
    draft = service.draft_service_response(agent, case["id"], draft_body(), expected_version=case["version"] + 1, idempotency_key="wire-draft-0001").data
    validate("ResponseDraft", draft)
    approval = service.approve_service_response_draft(agent_two, draft["id"], {
        "draft_content_sha256": _draft_content_sha(m13, draft), "decision": "approve", "approval_mode": "human_reviewed",
        "template_version": "manual", "policy_version": "policy-v1", "reason_code": "ok", "publication_reference_ids": [],
        "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=1, idempotency_key="wire-approve-0001").data
    validate("ServiceResponseApproval", approval)
    latest = service.get_service_case(agent, case["id"]).data
    sent = service.send_service_response(agent, case["id"], {
        "draft_id": draft["id"], "draft_content_sha256": approval["draft_content_sha256"], "channel_binding_id": CHANNEL_BINDING,
        "channel": "email", "approval_mode": "human_reviewed", "approval_id": approval["id"], "template_version": "manual",
        "publication_reference_ids": [], "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=latest["version"], idempotency_key="wire-send-0001").data
    validate("ServiceResponse", sent)
    receipt_result = service.receive_service_response_receipt(messaging_adapter, {
        "event_id": str(uuid4()), "response_id": sent["id"], "provider_message_ref": "wire-ref-0001",
        "provider_status": "accepted", "provider_occurred_at": wire_time(NOW), "provider_receipt_asset_id": RECEIPT_EVIDENCE,
    }, provider="email-adapter", idempotency_key="wire-receipt-0001").data
    validate("CallbackReceipt", receipt_result)

    problem = M13Controller(service).handle(
        "assign_service_case", supervisor, path={"case_id": case["id"]},
        body={"assignee_id": "20000000-0000-0000-0000-000000000001", "reason_code": "stale_write_check"},
        headers={"Idempotency-Key": "wire-assign-0001", "If-Match": '"1"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict, classification: str = "customer_confidential"):
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": classification, "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_deduplicates_checks_hash_and_recovers_sequence_gap(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "consumer-create-001").data
    consumer = M13EventConsumer(m13["repository"])
    assignment_id = str(uuid4())
    payload = {"assignment_id": assignment_id, "customer_id": CUSTOMER_ID, "salesperson_id": str(uuid4()), "effective_at": wire_time(NOW), "state": "active"}
    first = incoming("ownership.assignment_changed", "M11", "maintenance_assignment", assignment_id, 1, payload)
    assert consumer.consume(first).result_code.startswith("maintenance_owner_projected")
    assert consumer.consume(first).status == "duplicate"
    tampered = dict(first)
    tampered["payload"] = {**payload, "state": "released"}
    tampered["payload_sha256"] = canonical_hash(tampered["payload"])
    with pytest.raises(ConflictError):
        consumer.consume(tampered)

    refund_id = str(uuid4())

    def refund_payload(confirmed_at):
        return {"refund_id": refund_id, "contract_id": str(uuid4()), "original_payment_id": str(uuid4()), "allocation_snapshot_id": str(uuid4()), "confirmed_at": confirmed_at}

    seq1_payload = refund_payload(wire_time(NOW))
    seq1 = incoming("finance.refund_confirmed", "M14", "refund", refund_id, 1, seq1_payload)
    assert consumer.consume(seq1).status == "applied"
    seq3_payload = refund_payload(wire_time(NOW + timedelta(seconds=2)))
    seq3 = incoming("finance.refund_confirmed", "M14", "refund", refund_id, 3, seq3_payload)
    assert consumer.consume(seq3).status == "retry"
    seq2_payload = refund_payload(wire_time(NOW + timedelta(seconds=1)))
    seq2 = incoming("finance.refund_confirmed", "M14", "refund", refund_id, 2, seq2_payload)
    assert consumer.consume(seq2).status == "applied"
    assert consumer.consume(seq3).status == "applied"


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "consumer-create-101").data
    consumer = M13EventConsumer(m13["repository"])
    assignment_id = str(uuid4())
    payload = {"assignment_id": assignment_id, "customer_id": CUSTOMER_ID, "salesperson_id": str(uuid4()), "effective_at": wire_time(NOW), "state": "active"}
    wrong_schema = incoming("ownership.assignment_changed", "M11", "maintenance_assignment", assignment_id, 1, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)
    wrong_tenant = incoming("ownership.assignment_changed", "M11", "maintenance_assignment", assignment_id, 1, payload)
    wrong_tenant["tenant_id"] = "10000000-0000-0000-0000-000000000099"
    consumer.consume(wrong_tenant)  # a different tenant's event never touches this tenant's cases
    assert m13["repository"].snapshot().cases[(TENANT, case["id"])].maintenance_owner_id is None


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m13, agent):
    create_case(m13["service"], agent, "worker-create-001")
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m13["repository"], bus, max_attempts=2)
    first = publisher.run_once("worker-a")
    assert first.retried == 1

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)
    m13["repository"].atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("worker-b")
    assert second.processed == 1
    assert len(bus.envelopes) == 1
    third = publisher.run_once("worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 1


def test_response_dispatch_worker_lease_bounded_retry_and_queries_unknown_before_resend(m13, agent, agent_two):
    service = m13["service"]
    case = create_case(service, agent, "dispatch-worker-001").data
    draft = service.draft_service_response(agent, case["id"], draft_body(), expected_version=case["version"], idempotency_key="dispatch-worker-draft-001").data
    approval = service.approve_service_response_draft(agent_two, draft["id"], {
        "draft_content_sha256": _draft_content_sha(m13, draft), "decision": "approve", "approval_mode": "human_reviewed",
        "template_version": "manual", "policy_version": "policy-v1", "reason_code": "ok", "publication_reference_ids": [],
        "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=1, idempotency_key="dispatch-worker-approve-001").data
    latest = service.get_service_case(agent, case["id"]).data
    sent = service.send_service_response(agent, case["id"], {
        "draft_id": draft["id"], "draft_content_sha256": approval["draft_content_sha256"], "channel_binding_id": CHANNEL_BINDING,
        "channel": "email", "approval_mode": "human_reviewed", "approval_id": approval["id"], "template_version": "manual",
        "publication_reference_ids": [], "valid_until": "2027-01-01T00:00:00Z",
    }, expected_version=latest["version"], idempotency_key="dispatch-worker-send-001").data

    provider = LocalMessagingProvider()
    provider.failure_mode = "unknown"
    sender = ResponseDispatchSender(m13["repository"], provider, max_attempts=3)
    first = sender.run_once("worker-a")
    assert first.retried == 1
    snapshot = m13["repository"].snapshot()
    attempts = [a for (tenant, _), a in snapshot.response_attempts.items() if tenant == TENANT and a.response_id == sent["id"]]
    assert len(attempts) == 1 and attempts[0].outcome == "unknown"

    def make_due(state):
        for dispatch in state.response_dispatches.values():
            dispatch.next_attempt_at = NOW - timedelta(seconds=1)
    m13["repository"].atomic(make_due)
    provider.failure_mode = None
    second = sender.run_once("worker-b")
    assert second.processed == 1
    final = m13["repository"].snapshot().response_dispatches[(TENANT, sent["id"])]
    assert final.status == "accepted"
    third = sender.run_once("worker-c")
    assert third.processed == 0


def test_production_composition_refuses_fake_adapters():
    repository = InMemoryRepository()
    assets = FakeObjectAssetStore()
    knowledge_base = FakeKnowledgeBase()
    channel_bindings = FakeChannelBindings()
    suppression = FakeSuppressionRequests()
    assistant = LocalDraftingAssistant()
    with pytest.raises(RuntimeError):
        ServiceCaseService(repository, assets, knowledge_base, channel_bindings, suppression, assistant, environment="production")
