from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.pantheon_intake.automation import CandidateTriageAutomationService, LocalTriageAssistant
from modules.pantheon_intake.adapter import PantheonAdapter
from modules.pantheon_intake.domain import ActorContext
from modules.pantheon_intake.ports import (
    ALLOWED_EVIDENCE_HOSTS,
    FakeEvidenceTransport,
    FakeWebhookSignatureVerifier,
    SsrfSafeEvidenceFetcher,
    TransportResponse,
)
from modules.pantheon_intake.repository import InMemoryRepository
from modules.pantheon_intake.service import PantheonIntakeService
from workers.tasks.pantheon_intake.consumer import PantheonIntakeEventConsumer
from workers.tasks.pantheon_intake.tasks import CandidateVerificationWorker, FakeEvidenceSource, LocalEventBus, OutboxPublisher, TaskRunner


TENANT = "10000000-0000-0000-0000-000000000001"
ADAPTER_ACTOR = "20000000-0000-0000-0000-000000000001"
ANALYST = "20000000-0000-0000-0000-000000000002"
REVIEWER = "20000000-0000-0000-0000-000000000003"
SERVICE_ACTOR = "20000000-0000-0000-0000-000000000004"
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)

ALLOWED_HOST = next(iter(ALLOWED_EVIDENCE_HOSTS))
DISALLOWED_HOST = "untrusted.example.invalid"
GLOBAL_IP = "93.184.216.34"  # a real globally-routable address; 203.0.113.0/24 etc. are documentation ranges and fail is_global


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m05():
    repository = InMemoryRepository()
    clock = FixedClock()
    service = PantheonIntakeService(repository, clock=clock)
    adapter = PantheonAdapter(repository)
    consumer = PantheonIntakeEventConsumer(repository)
    transport = FakeEvidenceTransport()
    transport.set_dns(ALLOWED_HOST, [GLOBAL_IP])
    fetcher = SsrfSafeEvidenceFetcher(transport)
    evidence_source = FakeEvidenceSource()
    service_context = ActorContext(TENANT, SERVICE_ACTOR, frozenset({"research_intake_service"}), principal_type="service")
    worker = CandidateVerificationWorker(repository, service, evidence_source, fetcher, service_actor_context=service_context)
    bus = LocalEventBus()
    publisher = OutboxPublisher(repository, bus)
    signature = FakeWebhookSignatureVerifier(clock=clock)
    signature.register_key("pantheon-key-1", b"synthetic-pantheon-hmac-key-32-bytes-minimum")
    automation = CandidateTriageAutomationService(repository, LocalTriageAssistant())
    return {
        "service": service,
        "repository": repository,
        "adapter": adapter,
        "consumer": consumer,
        "transport": transport,
        "fetcher": fetcher,
        "evidence_source": evidence_source,
        "worker": worker,
        "task_runner": TaskRunner(repository),
        "bus": bus,
        "publisher": publisher,
        "signature": signature,
        "automation": automation,
        "clock": clock,
        "service_context": service_context,
    }


@pytest.fixture
def adapter_context() -> ActorContext:
    return ActorContext(TENANT, ADAPTER_ACTOR, frozenset({"pantheon_adapter"}), principal_type="integration")


@pytest.fixture
def analyst_context() -> ActorContext:
    return ActorContext(TENANT, ANALYST, frozenset({"analyst"}), principal_type="staff")


@pytest.fixture
def reviewer_context() -> ActorContext:
    return ActorContext(TENANT, REVIEWER, frozenset({"analyst_reviewer"}), principal_type="staff")


def candidate_body(
    *,
    external_event_id: str,
    external_id: str = "artifact-1",
    version: str = "1",
    content_sha256: str = "a" * 64,
    evidence_checksum: str | None = "b" * 64,
    origin: str = "persona_autonomous",
    license_scope_ref: str = "internal_only",
    source_created_at: str = "2026-11-20T00:00:00Z",
    source_available_at: str = "2026-11-20T00:00:00Z",
    source_committed_at: str | None = None,
    provenance_snapshot_asset_id: str = "30000000-0000-0000-0000-000000000001",
) -> dict:
    evidence_ref: dict = {"system": "pantheon", "external_id": "evidence-1", "kind": "evidence"}
    if evidence_checksum is not None:
        evidence_ref["checksum_sha256"] = evidence_checksum
    body = {
        "external_event_id": external_event_id,
        "origin": origin,
        "ticket_ref": {"system": "pantheon", "external_id": "ticket-1", "kind": "ticket"},
        "artifact_ref": {"system": "pantheon", "external_id": external_id, "kind": "report", "version": version},
        "artifact_content_sha256": content_sha256,
        "evidence_bundle_refs": [evidence_ref],
        "source_created_at": source_created_at,
        "source_available_at": source_available_at,
        "data_scope": "research_only",
        "provenance_snapshot_asset_id": provenance_snapshot_asset_id,
        "license_scope_ref": license_scope_ref,
    }
    if source_committed_at is not None:
        body["source_committed_at"] = source_committed_at
    return body


def ingest(service, context, key: str, **overrides):
    body = candidate_body(**overrides)
    return service.ingest_research_candidate(context, body, idempotency_key=key)
