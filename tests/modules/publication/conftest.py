from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.publication.domain import ActorContext
from modules.publication.ports import (
    AudienceCandidate,
    FakeContentAssetStore,
    FakeEntitlementAudienceRegistry,
    FakeEvidenceStore,
    FakeReportBundleRegistry,
)
from modules.publication.repository import InMemoryRepository
from modules.publication.service import PublicationService


TENANT = "10000000-0000-0000-0000-000000000001"
ANALYST = "20000000-0000-0000-0000-000000000001"
ANALYST_REVIEWER = "20000000-0000-0000-0000-000000000002"
COMPLIANCE_SUPERVISOR = "20000000-0000-0000-0000-000000000003"
SERVICE_AGENT = "20000000-0000-0000-0000-000000000004"
COMPLIANCE_REVIEWER = "20000000-0000-0000-0000-000000000005"
EVIDENCE_WITHDRAW = "30000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 23, 6, 30, 0, tzinfo=timezone.utc)

INSTRUMENT_REF = {"market": "TWSE", "instrument_code": "2330", "instrument_type": "equity"}
RESEARCH_ORIGIN_REF = {"system": "pantheon", "external_id": "candidate-0001"}


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m08():
    repository = InMemoryRepository()
    content_assets = FakeContentAssetStore()
    evidence = FakeEvidenceStore()
    evidence.add(TENANT, EVIDENCE_WITHDRAW)
    report_bundles = FakeReportBundleRegistry()
    entitlements = FakeEntitlementAudienceRegistry()
    clock = FixedClock()
    service = PublicationService(
        repository, content_assets, evidence, report_bundles, entitlements, clock=clock,
    )
    return {
        "service": service,
        "repository": repository,
        "content_assets": content_assets,
        "evidence": evidence,
        "report_bundles": report_bundles,
        "entitlements": entitlements,
        "clock": clock,
    }


@pytest.fixture
def analyst() -> ActorContext:
    return ActorContext(TENANT, ANALYST, frozenset({"analyst"}))


@pytest.fixture
def analyst_reviewer() -> ActorContext:
    return ActorContext(TENANT, ANALYST_REVIEWER, frozenset({"analyst_reviewer"}))


@pytest.fixture
def compliance_supervisor() -> ActorContext:
    return ActorContext(TENANT, COMPLIANCE_SUPERVISOR, frozenset({"compliance_supervisor"}))


@pytest.fixture
def service_agent() -> ActorContext:
    return ActorContext(TENANT, SERVICE_AGENT, frozenset({"service_agent"}))


@pytest.fixture
def compliance_reviewer() -> ActorContext:
    return ActorContext(TENANT, COMPLIANCE_REVIEWER, frozenset({"compliance_reviewer"}))


def create_series(service, analyst_context, key: str, strategy: str = "momentum-core-v1"):
    return service.create_recommendation_series(
        analyst_context,
        {"instrument_ref": INSTRUMENT_REF, "strategy_scope_ref": strategy, "research_origin_ref": RESEARCH_ORIGIN_REF},
        idempotency_key=key,
    )


def version_body(report_version_id: str, *, action: str = "entry", supersedes: str | None = None, source_candidate_id: str = "40000000-0000-0000-0000-000000000001"):
    now = NOW
    return {
        "action": action,
        "rationale": f"研究依據：{report_version_id}",
        "supporting_report_version_ids": [report_version_id],
        "valid_from": now.isoformat().replace("+00:00", "Z"),
        "valid_until": (now + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
        "source_candidate_id": source_candidate_id,
        **({"supersedes_version_id": supersedes} if supersedes else {}),
    }


def signature_event(
    report_version_id: str,
    signature_id: str,
    *,
    tenant: str = TENANT,
    sequence: int = 1,
    report_content_sha256: str = "a" * 64,
    bundle_manifest_sha256: str = "b" * 64,
    verified_at: datetime = NOW,
    valid_until: datetime | None = None,
):
    valid_until = valid_until or (NOW + timedelta(hours=6))
    payload = {
        "report_version_id": report_version_id,
        "signature_id": signature_id,
        "report_content_sha256": report_content_sha256,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "signed_file_sha256": "c" * 64,
        "verified_at": verified_at.isoformat().replace("+00:00", "Z"),
        "valid_until": valid_until.isoformat().replace("+00:00", "Z"),
    }
    from modules.publication.domain import canonical_hash, new_id

    return {
        "event_id": new_id(),
        "event_type": "report.signature_verified",
        "schema_version": "1.0.0",
        "tenant_id": tenant,
        "producer": "M07",
        "aggregate_type": "report_version",
        "aggregate_id": report_version_id,
        "aggregate_version": sequence,
        "sequence_no": sequence,
        "occurred_at": verified_at.isoformat().replace("+00:00", "Z"),
        "emitted_at": verified_at.isoformat().replace("+00:00", "Z"),
        "correlation_id": new_id(),
        "causation_id": None,
        "idempotency_key": f"m07-sig-{sequence:08d}",
        "classification": "research_confidential",
        "payload_sha256": canonical_hash(payload),
        "payload": payload,
    }


def default_candidates(count: int = 1) -> tuple[AudienceCandidate, ...]:
    return tuple(
        AudienceCandidate(
            customer_id=f"50000000-0000-0000-0000-{index:012d}",
            entitlement_id=f"60000000-0000-0000-0000-{index:012d}",
            contract_id=f"70000000-0000-0000-0000-{index:012d}",
            entitlement_version=1,
            eligible=True,
            reason_code="active_entitlement",
        )
        for index in range(1, count + 1)
    )


def register_bundle_for(m08, recommendation_version_id: str, report_version_id: str, *, report_content_sha256: str = "a" * 64, tamper: bool = False):
    snapshot = m08["repository"].snapshot()
    version = snapshot.versions[(TENANT, recommendation_version_id)]
    content_sha256 = ("0" * 64) if tamper else version.content_sha256
    return m08["report_bundles"].register(
        TENANT, report_version_id, recommendation_version_id,
        content_sha256=content_sha256, report_content_sha256=report_content_sha256,
    )


def set_audience_for_series(m08, series_id: str, candidates: tuple[AudienceCandidate, ...]) -> None:
    snapshot = m08["repository"].snapshot()
    series = snapshot.series[(TENANT, series_id)]
    m08["entitlements"].set_candidates(TENANT, series.instrument_ref, candidates)
