from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.service_cases.domain import ActorContext, ForbiddenError, NotFoundError, wire_time
from modules.service_cases.http import M13Controller

from tests.modules.service_cases.conftest import (
    CHANNEL_BINDING,
    CUSTOMER_ID_TWO,
    INTERACTION_CONTENT_ASSET,
    NOW,
    RECEIPT_EVIDENCE,
    TENANT,
    case_body,
    create_case,
    customer_context,
    draft_body,
    receive_receipt,
)


ROOT = Path(__file__).resolve().parents[3]


def _approve_and_send(m13, agent, agent_two, case_id: str) -> tuple[dict, dict]:
    service = m13["service"]
    m13["clock"].advance(10)
    draft = service.draft_service_response(
        agent, case_id, draft_body(), expected_version=service.get_service_case(agent, case_id).data["version"],
        idempotency_key="timeline-draft-001",
    ).data
    m13["clock"].advance(10)
    approval = service.approve_service_response_draft(
        agent_two, draft["id"], {
            "draft_content_sha256": sha256(draft["text"].encode("utf-8")).hexdigest(),
            "decision": "approve", "approval_mode": "human_reviewed", "template_version": "manual",
            "policy_version": "policy-v1", "reason_code": "timeline_ok", "publication_reference_ids": [],
            "valid_until": "2027-01-01T00:00:00Z",
        }, expected_version=1, idempotency_key="timeline-approval-001",
    ).data
    m13["clock"].advance(10)
    dispatch = service.send_service_response(
        agent, case_id, {
            "draft_id": draft["id"], "draft_content_sha256": approval["draft_content_sha256"],
            "channel_binding_id": CHANNEL_BINDING, "channel": "email", "approval_mode": "human_reviewed",
            "approval_id": approval["id"], "template_version": "manual", "publication_reference_ids": [],
            "valid_until": "2027-01-01T00:00:00Z",
        }, expected_version=service.get_service_case(agent, case_id).data["version"],
        idempotency_key="timeline-send-001",
    ).data
    return draft, dispatch


def _validate(schema_name: str, value: dict) -> None:
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    root_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{schema_name}",
        "components": specification["components"],
    }
    errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
    assert errors == [], [f"{schema_name}: {error.json_path}: {error.message}" for error in errors]


def test_interaction_timeline_is_time_sorted_paginated_and_customer_safe(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "timeline-interactions-case-001").data
    for key, direction, occurred_at, summary in [
        ("timeline-interaction-001", "inbound", NOW.replace(second=1), "客戶先提出問題"),
        ("timeline-interaction-002", "internal", NOW.replace(second=4), "內部調查筆記"),
        ("timeline-interaction-003", "outbound", NOW.replace(second=2), "客服已回電"),
    ]:
        service.add_case_interaction(
            agent, case["id"], {
                "channel": "email", "direction": direction, "occurred_at": wire_time(occurred_at),
                "content_asset_id": INTERACTION_CONTENT_ASSET, "summary": summary,
            }, expected_version=service.get_service_case(agent, case["id"]).data["version"], idempotency_key=key,
        )

    customer = customer_context()
    first = service.list_case_interactions(customer, case["id"], limit=1).data
    second = service.list_case_interactions(customer, case["id"], cursor=first["next_cursor"], limit=1).data
    assert first["total_estimate"] == 2
    assert first["items"][0]["summary"] == "客戶先提出問題"
    assert second["items"][0]["summary"] == "客服已回電"
    assert all(item["direction"] != "internal" for item in first["items"] + second["items"])
    assert {"content_asset_id", "recorded_by", "publication_reference_ids", "tenant_id"}.isdisjoint(first["items"][0])
    _validate("CaseInteractionTimelinePage", first)

    staff_page = service.list_case_interactions(agent, case["id"], limit=10).data
    assert [item["summary"] for item in staff_page["items"]] == ["客戶先提出問題", "客服已回電", "內部調查筆記"]
    assert staff_page["items"][2]["content_asset_id"] == INTERACTION_CONTENT_ASSET
    _validate("CaseInteractionTimelinePage", staff_page)


def test_timeline_reads_require_auth_and_preserve_customer_tenant_boundaries(m13, agent):
    service = m13["service"]
    case = create_case(service, agent, "timeline-read-access-001").data
    controller = M13Controller(service)
    assert controller.handle("list_case_interactions", None, path={"case_id": case["id"]}).status == 401
    assert controller.handle("list_case_responses", None, path={"case_id": case["id"]}).status == 401

    with pytest.raises(NotFoundError):
        service.list_case_interactions(customer_context(CUSTOMER_ID_TWO), case["id"])
    with pytest.raises(NotFoundError):
        service.list_case_responses(customer_context(CUSTOMER_ID_TWO), case["id"])
    with pytest.raises(NotFoundError):
        service.list_case_interactions(
            ActorContext("10000000-0000-0000-0000-000000000099", str(uuid4()), frozenset({"service_agent"})),
            case["id"],
        )
    platform_admin = ActorContext(TENANT, str(uuid4()), frozenset({"platform_admin"}), principal_type="staff")
    with pytest.raises(ForbiddenError):
        service.list_case_interactions(platform_admin, case["id"])
    with pytest.raises(ForbiddenError):
        service.list_case_responses(platform_admin, case["id"])


def test_response_timeline_exposes_internal_workflow_but_only_sent_content_to_owner(m13, agent, agent_two, messaging_adapter):
    service = m13["service"]
    case = create_case(service, agent, "timeline-responses-case-001", **case_body()).data
    draft, dispatch = _approve_and_send(m13, agent, agent_two, case["id"])

    staff_before_receipt = service.list_case_responses(agent, case["id"], limit=10).data
    assert [item["record_type"] for item in staff_before_receipt["items"]] == ["draft", "approval", "response"]
    assert staff_before_receipt["items"][-1]["channel"] == "email"
    assert staff_before_receipt["items"][-1]["response"]["state"] == "queued"
    _validate("CaseResponseTimelinePage", staff_before_receipt)
    assert service.list_case_responses(customer_context(), case["id"]).data["items"] == []

    m13["clock"].advance(10)
    receive_receipt(m13, messaging_adapter, {
        "event_id": str(uuid4()), "response_id": dispatch["id"], "provider_message_ref": "timeline-sent-001",
        "provider_status": "sent", "provider_occurred_at": wire_time(m13["clock"]()),
        "provider_receipt_asset_id": RECEIPT_EVIDENCE,
    }, idempotency_key="timeline-receipt-001")

    customer_page = service.list_case_responses(customer_context(), case["id"], limit=10).data
    assert customer_page["total_estimate"] == 1
    public = customer_page["items"][0]
    assert public["state"] == "sent"
    assert public["channel"] == "email"
    assert public["text"] == draft["text"]
    assert {"draft_id", "approval_id", "content_sha256", "provider_message_ref", "tenant_id"}.isdisjoint(public)
    _validate("CaseResponseTimelinePage", customer_page)
