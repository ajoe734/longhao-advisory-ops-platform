from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    clock = FixedClock()
    service = ServiceCaseService(
        repository, assets, knowledge_base, channel_bindings, suppression, assistant, clock=clock,
    )
    return {
        "service": service,
        "repository": repository,
        "assets": assets,
        "knowledge_base": knowledge_base,
        "channel_bindings": channel_bindings,
        "suppression": suppression,
        "assistant": assistant,
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
