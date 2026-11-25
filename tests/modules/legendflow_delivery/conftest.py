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

from modules.legendflow_delivery.domain import ActorContext
from modules.legendflow_delivery.ports import (
    EntitlementStatus,
    FakeChannelProofPort,
    FakeChannelSendPort,
    FakeEntitlementPort,
    FakeIntegrationKeyPort,
    FakePublicationPort,
    FakePublicationRecipientPort,
    FakeReportAccessPort,
    IntegrationKey,
    PublicationSnapshot,
    RecipientSnapshot,
    VerifiedChannelProof,
)
from modules.legendflow_delivery.repository import InMemoryRepository
from modules.legendflow_delivery.service import LegendflowDeliveryService


TENANT = "10000000-0000-0000-0000-000000000001"
MEMBER_A = "20000000-0000-0000-0000-000000000001"
MEMBER_B = "20000000-0000-0000-0000-000000000002"
OPERATOR = "20000000-0000-0000-0000-000000000003"
SUPERVISOR = "20000000-0000-0000-0000-000000000004"
SERVICE_AGENT = "20000000-0000-0000-0000-000000000005"

RECIPIENT_A1 = "30000000-0000-0000-0000-000000000001"
ENTITLEMENT_A1 = "40000000-0000-0000-0000-000000000001"
PUBLICATION_X = "50000000-0000-0000-0000-000000000001"
REPORT_VERSION_X = "60000000-0000-0000-0000-000000000001"
SIGNATURE_X = "70000000-0000-0000-0000-000000000001"

CHANNEL_PROOF_LINE_OK = "80000000-0000-0000-0000-000000000001"
CHANNEL_PROOF_FORGED = "80000000-0000-0000-0000-000000000002"
CHANNEL_PROOF_TIMEOUT = "80000000-0000-0000-0000-000000000003"

WEBHOOK_KEY_ID = "synthetic-line-adapter-key-0001"
WEBHOOK_SECRET = b"m09-synthetic-webhook-secret-32-bytes-minimum!!"
HMAC_KEY = b"m09-synthetic-hmac-key-32-bytes-minimum-only!!"

NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m09():
    repository = InMemoryRepository()
    publications = FakePublicationPort()
    recipients = FakePublicationRecipientPort()
    entitlements = FakeEntitlementPort()
    report_access = FakeReportAccessPort()
    channel_proof = FakeChannelProofPort()
    channel_send = FakeChannelSendPort()
    integration_keys = FakeIntegrationKeyPort()
    # Registered as "legendflow" because every default-fixture delivery used by
    # webhook tests is the legendflow feed message; receipts must come from the
    # provider that actually owns the channel being receipted (see
    # service.receive_message_receipt's key.provider check).
    integration_keys.register(IntegrationKey(key_id=WEBHOOK_KEY_ID, tenant_id=TENANT, secret=WEBHOOK_SECRET, provider="legendflow"))
    clock = FixedClock()

    channel_proof.register(
        CHANNEL_PROOF_LINE_OK,
        VerifiedChannelProof(masked_destination="LINE:****abcd", destination_fingerprint="f" * 64, proof_asset_id="90000000-0000-0000-0000-000000000001", verified_at=NOW),
    )
    channel_proof.fail(CHANNEL_PROOF_FORGED, "forged")
    channel_proof.fail(CHANNEL_PROOF_TIMEOUT, "timeout")

    service = LegendflowDeliveryService(
        repository, publications=publications, recipients=recipients, entitlements=entitlements,
        report_access=report_access, channel_proof=channel_proof, channel_send=channel_send,
        integration_keys=integration_keys, hmac_key=HMAC_KEY, clock=clock,
    )
    return {
        "service": service,
        "repository": repository,
        "publications": publications,
        "recipients": recipients,
        "entitlements": entitlements,
        "report_access": report_access,
        "channel_proof": channel_proof,
        "channel_send": channel_send,
        "integration_keys": integration_keys,
        "clock": clock,
    }


@pytest.fixture
def member_a() -> ActorContext:
    return ActorContext(TENANT, MEMBER_A, frozenset({"member"}), principal_type="customer", customer_id=MEMBER_A, purpose="self_service")


@pytest.fixture
def member_b() -> ActorContext:
    return ActorContext(TENANT, MEMBER_B, frozenset({"member"}), principal_type="customer", customer_id=MEMBER_B, purpose="self_service")


@pytest.fixture
def operator() -> ActorContext:
    return ActorContext(TENANT, OPERATOR, frozenset({"delivery_operator"}), principal_type="staff")


@pytest.fixture
def supervisor() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"delivery_supervisor"}), principal_type="staff")


@pytest.fixture
def service_agent() -> ActorContext:
    return ActorContext(TENANT, SERVICE_AGENT, frozenset({"service_agent"}), principal_type="staff")


def seed_publication(
    env, *, publication_id: str = PUBLICATION_X, recipient_id: str = RECIPIENT_A1, customer_id: str = MEMBER_A,
    entitlement_id: str = ENTITLEMENT_A1, action: str = "entry", valid_until: datetime | None = None,
    body_sha256: str | None = None, report_version_id: str = REPORT_VERSION_X,
) -> PublicationSnapshot:
    valid_until = valid_until or (NOW + timedelta(days=30))
    body_sha256 = body_sha256 or ("a" * 64)
    snapshot = PublicationSnapshot(
        publication_id=publication_id, report_version_id=report_version_id, signature_id=SIGNATURE_X,
        recommendation_series_id="a" * 8 + "-0000-0000-0000-000000000000", recommendation_version_id="b" * 8 + "-0000-0000-0000-000000000000",
        action=action, approved_text="合成核定文案", body_sha256=body_sha256, report_version_ids=(report_version_id,),
        published_at=NOW, valid_until=valid_until,
    )
    env["publications"].register(TENANT, snapshot)
    env["recipients"].set(TENANT, publication_id, [RecipientSnapshot(recipient_id, customer_id, entitlement_id, 1, "active_entitlement")])
    env["report_access"].register(TENANT, report_version_id, body_sha256)
    return snapshot


def set_entitlement(env, *, entitlement_id: str = ENTITLEMENT_A1, customer_id: str = MEMBER_A, state: str = "active", valid_until: datetime | None = None, decision_id: str | None = None) -> None:
    env["entitlements"].set(
        TENANT,
        EntitlementStatus(
            entitlement_id=entitlement_id, customer_id=customer_id, state=state,
            decision_id=decision_id or ("d" * 8 + "-0000-0000-0000-000000000000"),
            valid_until=valid_until or (NOW + timedelta(days=30)),
        ),
    )


def dispatch(env, publication_id: str = PUBLICATION_X, correlation_id: str | None = None):
    return env["service"].dispatch_publication(TENANT, publication_id, correlation_id=correlation_id or "c0000000-0000-0000-0000-000000000001")


WEBHOOK_PATH = "/api/v1/webhooks/message-receipts"


def sign_webhook(secret: bytes, *, timestamp: str, nonce: str, method: str, path: str, raw_body: bytes) -> str:
    material = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{sha256(raw_body).hexdigest()}".encode("utf-8")
    return hmac.new(secret, material, sha256).hexdigest()


def send_receipt(env, body: dict, *, idempotency_key: str, timestamp: str | None = None, nonce: str = "n" * 16, path: str = WEBHOOK_PATH, secret: bytes = WEBHOOK_SECRET, key_id: str = WEBHOOK_KEY_ID, correlation_id: str | None = None):
    timestamp = timestamp or str(int(env["clock"]().timestamp()))
    raw_body = json.dumps(body, sort_keys=True).encode("utf-8")
    signature = sign_webhook(secret, timestamp=timestamp, nonce=nonce, method="POST", path=path, raw_body=raw_body)
    return env["service"].receive_message_receipt(
        body, idempotency_key=idempotency_key, key_id=key_id, timestamp=timestamp, nonce=nonce,
        signature=signature, method="POST", path=path, raw_body=raw_body, correlation_id=correlation_id,
    )
