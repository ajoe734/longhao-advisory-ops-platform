from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.service_cases.automation import LocalDraftingAssistant
from modules.service_cases.domain import ActorContext
from modules.service_cases.ports import (
    FakeChannelBindings,
    FakeIntegrationKeys,
    FakeKnowledgeBase,
    FakeObjectAssetStore,
    FakeSuppressionRequests,
)
from modules.service_cases.repository import InMemoryRepository
from modules.service_cases.service import ServiceCaseService


TENANT = "10000000-0000-0000-0000-000000000001"
OTHER_TENANT = "10000000-0000-0000-0000-000000000099"
AGENT = "20000000-0000-0000-0000-000000000001"
AGENT_TWO = "20000000-0000-0000-0000-000000000002"
SUPERVISOR = "20000000-0000-0000-0000-000000000003"
MESSAGING_ADAPTER = "20000000-0000-0000-0000-000000000004"
CUSTOMER_ID = "30000000-0000-0000-0000-000000000001"
CUSTOMER_ID_TWO = "30000000-0000-0000-0000-000000000002"
ORIGINAL_CONTENT_ASSET = "40000000-0000-0000-0000-000000000001"
INTERACTION_CONTENT_ASSET = "40000000-0000-0000-0000-000000000002"
NOTICE_EVIDENCE = "40000000-0000-0000-0000-000000000003"
CONTEXT_EVIDENCE = "40000000-0000-0000-0000-000000000004"
CONSENT_EVIDENCE = "40000000-0000-0000-0000-000000000005"
RECEIPT_EVIDENCE = "40000000-0000-0000-0000-000000000006"
REOPEN_EVIDENCE = "40000000-0000-0000-0000-000000000007"
KNOWLEDGE_SOURCE_REF = "50000000-0000-0000-0000-000000000001"
CHANNEL_BINDING = "60000000-0000-0000-0000-000000000001"
KNOWLEDGE_VERSION = "kb-v1"
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)
WEBHOOK_PROVIDER = "email-adapter"
WEBHOOK_SECRET = b"m13-test-webhook-secret-0123456789abcdef"
WEBHOOK_RECEIPT_PATH = "/webhooks/service-response-receipts"


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m13():
    repository = InMemoryRepository()
    assets = FakeObjectAssetStore()
    for asset_id in (
        ORIGINAL_CONTENT_ASSET, INTERACTION_CONTENT_ASSET, NOTICE_EVIDENCE, CONTEXT_EVIDENCE,
        CONSENT_EVIDENCE, RECEIPT_EVIDENCE, REOPEN_EVIDENCE,
    ):
        assets.add(TENANT, asset_id)
    knowledge_base = FakeKnowledgeBase()
    knowledge_base.approve(TENANT, KNOWLEDGE_VERSION, KNOWLEDGE_SOURCE_REF)
    channel_bindings = FakeChannelBindings()
    channel_bindings.add(TENANT, CHANNEL_BINDING, CUSTOMER_ID, "email")
    suppression = FakeSuppressionRequests()
    assistant = LocalDraftingAssistant()
    integration_keys = FakeIntegrationKeys()
    integration_keys.add(TENANT, WEBHOOK_PROVIDER, WEBHOOK_SECRET)
    clock = FixedClock()
    service = ServiceCaseService(
        repository, assets, knowledge_base, channel_bindings, suppression, assistant, integration_keys, clock=clock,
    )
    return {
        "service": service,
        "repository": repository,
        "assets": assets,
        "knowledge_base": knowledge_base,
        "channel_bindings": channel_bindings,
        "suppression": suppression,
        "assistant": assistant,
        "integration_keys": integration_keys,
        "clock": clock,
    }


@pytest.fixture
def agent() -> ActorContext:
    return ActorContext(TENANT, AGENT, frozenset({"service_agent"}), principal_type="staff")


@pytest.fixture
def agent_two() -> ActorContext:
    return ActorContext(TENANT, AGENT_TWO, frozenset({"service_agent"}), principal_type="staff")


@pytest.fixture
def supervisor() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"service_supervisor"}), principal_type="staff")


@pytest.fixture
def messaging_adapter() -> ActorContext:
    return ActorContext(TENANT, MESSAGING_ADAPTER, frozenset({"service_messaging_adapter"}), principal_type="service")


@pytest.fixture
def response_service() -> ActorContext:
    return ActorContext(
        TENANT, "20000000-0000-0000-0000-000000000005",
        frozenset({"service_response_service"}), principal_type="service",
    )


def webhook_signature(secret: bytes, timestamp: str, nonce: str, raw_body: bytes) -> str:
    """Mirrors ServiceCaseService._webhook_signature so tests act as an
    independent external caller rather than reusing the implementation."""
    body_hash = sha256(raw_body).hexdigest()
    message = f"{timestamp}\n{nonce}\nPOST\n{WEBHOOK_RECEIPT_PATH}\n{body_hash}".encode("utf-8")
    return hmac.new(secret, message, sha256).hexdigest()


def receive_receipt(
    m13, context, body, *, idempotency_key: str, provider: str = WEBHOOK_PROVIDER,
    secret: bytes = WEBHOOK_SECRET, timestamp: str | None = None, nonce: str | None = None, tamper_signature: bool = False,
):
    """Signs and submits a service-response receipt the way a real webhook
    sender would: a fixed raw body, a fresh nonce and an HMAC over both."""
    service = m13["service"]
    raw_body = json.dumps(body, sort_keys=True).encode("utf-8")
    ts = timestamp if timestamp is not None else str(int(m13["clock"]().timestamp()))
    nc = nonce if nonce is not None else sha256(f"nonce:{idempotency_key}".encode("utf-8")).hexdigest()
    sig = webhook_signature(secret, ts, nc, raw_body)
    if tamper_signature:
        sig = ("0" if sig[0] != "0" else "1") + sig[1:]
    return service.receive_service_response_receipt(
        context, body, provider=provider, idempotency_key=idempotency_key,
        webhook_timestamp=ts, webhook_nonce=nc, signature=sig, raw_body=raw_body,
    )


def customer_context(customer_id: str = CUSTOMER_ID, actor_id: str = CUSTOMER_ID) -> ActorContext:
    return ActorContext(TENANT, actor_id, frozenset(), principal_type="customer", customer_id=customer_id, purpose="self_service")


def case_body(**overrides) -> dict:
    body = {
        "customer_id": CUSTOMER_ID,
        "intake_channel": "web",
        "category": "billing",
        "subject": "帳單問題",
        "description": "客戶詢問本期帳單金額是否正確。",
        "priority": "normal",
    }
    body.update(overrides)
    return body


def create_case(service, context, key: str, **overrides):
    return service.create_service_case(context, case_body(**overrides), idempotency_key=key)


def draft_body(**overrides) -> dict:
    body = {
        "knowledge_version": KNOWLEDGE_VERSION,
        "approved_source_refs": [KNOWLEDGE_SOURCE_REF],
        "intent": "billing_explanation",
        "requested_tone": "professional",
        "model_policy_version": "policy-v1",
    }
    body.update(overrides)
    return body
