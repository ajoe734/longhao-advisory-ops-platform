from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from uuid import uuid4

from modules.report_signature.domain import ActorContext, canonical_hash, sha256_text, wire_time
from modules.report_signature.ports import (
    FakeCandidateLineage,
    FakeObjectAssetStore,
    FakeQualificationRegistry,
    FakeReviewCaseDirectory,
    FakeSignatureProvider,
    QualifiedCredential,
)
from modules.report_signature.repository import InMemoryRepository
from modules.report_signature.service import ReportSignatureService


TENANT = "10000000-0000-0000-0000-000000000001"
OTHER_TENANT = "10000000-0000-0000-0000-000000000099"
ANALYST = "20000000-0000-0000-0000-000000000001"
ANALYST_B = "20000000-0000-0000-0000-000000000002"
REVIEWER_PRO = "20000000-0000-0000-0000-000000000003"
REVIEWER_COMPLIANCE = "20000000-0000-0000-0000-000000000004"
SIGNER = "20000000-0000-0000-0000-000000000005"
SUPERVISOR = "20000000-0000-0000-0000-000000000006"
ADAPTER_ACTOR = "20000000-0000-0000-0000-000000000007"

REVIEW_CASE_ID = "30000000-0000-0000-0000-000000000001"
CANDIDATE_ID = "30000000-0000-0000-0000-000000000002"
OTHER_CANDIDATE_ID = "30000000-0000-0000-0000-000000000003"
REPORT_ASSET_ID = "40000000-0000-0000-0000-000000000001"
EVIDENCE_ASSET_ID = "40000000-0000-0000-0000-000000000002"
CHECKLIST_ASSET_ID = "40000000-0000-0000-0000-000000000003"
SIGNED_ASSET_ID = "40000000-0000-0000-0000-000000000004"
VERIFICATION_ASSET_ID = "40000000-0000-0000-0000-000000000005"
REVOCATION_EVIDENCE_ASSET_ID = "40000000-0000-0000-0000-000000000006"
CREDENTIAL_ID = "50000000-0000-0000-0000-000000000001"
AUDIENCE_POLICY_ID = "60000000-0000-0000-0000-000000000001"

REPORT_CONTENT_BYTES = "synthetic-report-v1-bytes"
REPORT_CONTENT_SHA256 = sha256_text(REPORT_CONTENT_BYTES)
SIGNED_FILE_SHA256 = sha256_text("synthetic-signed-container-bytes")

NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m07():
    repository = InMemoryRepository()
    signature_provider = FakeSignatureProvider()
    qualifications = FakeQualificationRegistry()
    qualifications.register(
        TENANT, SIGNER, CREDENTIAL_ID,
        # FakeQualificationRegistry compares against the real wall clock, not the
        # fixture's FixedClock, so this must stay far beyond any test-run time.
        QualifiedCredential(CREDENTIAL_ID, SIGNER, "analyst_report", datetime(2099, 1, 1, tzinfo=timezone.utc), "subject:signer"),
    )
    assets = FakeObjectAssetStore()
    assets.add(TENANT, REPORT_ASSET_ID, REPORT_CONTENT_SHA256)
    for asset_id in (EVIDENCE_ASSET_ID, CHECKLIST_ASSET_ID, SIGNED_ASSET_ID, VERIFICATION_ASSET_ID, REVOCATION_EVIDENCE_ASSET_ID):
        assets.add(TENANT, asset_id)
    review_cases = FakeReviewCaseDirectory()
    review_cases.add(TENANT, REVIEW_CASE_ID)
    candidates = FakeCandidateLineage()
    candidates.add(TENANT, CANDIDATE_ID)
    candidates.add(TENANT, OTHER_CANDIDATE_ID)
    clock = FixedClock()
    service = ReportSignatureService(
        repository, signature_provider, qualifications, assets, review_cases, candidates, clock=clock,
    )
    return {
        "service": service, "repository": repository, "signature_provider": signature_provider,
        "qualifications": qualifications, "assets": assets, "review_cases": review_cases,
        "candidates": candidates, "clock": clock,
    }


@pytest.fixture
def analyst() -> ActorContext:
    return ActorContext(TENANT, ANALYST, frozenset({"analyst"}), principal_type="staff")


@pytest.fixture
def analyst_b() -> ActorContext:
    return ActorContext(TENANT, ANALYST_B, frozenset({"analyst"}), principal_type="staff")


@pytest.fixture
def reviewer_pro() -> ActorContext:
    return ActorContext(TENANT, REVIEWER_PRO, frozenset({"analyst_reviewer"}), principal_type="staff")


@pytest.fixture
def reviewer_compliance() -> ActorContext:
    return ActorContext(TENANT, REVIEWER_COMPLIANCE, frozenset({"compliance_reviewer"}), principal_type="staff")


@pytest.fixture
def signer() -> ActorContext:
    return ActorContext(TENANT, SIGNER, frozenset({"analyst_signer"}), principal_type="staff")


@pytest.fixture
def supervisor() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"compliance_supervisor"}), principal_type="staff")


@pytest.fixture
def adapter() -> ActorContext:
    return ActorContext(TENANT, ADAPTER_ACTOR, frozenset({"signature_adapter"}), principal_type="service")


def channel_text(body: str = "合成通路文案第一版", channel: str = "legendflow") -> dict:
    return {
        "channel": channel, "locale": "zh-TW", "body": body, "body_sha256": sha256_text(body),
        "template_version": "synthetic-template-v1", "recommendation_version_ids": ["70000000-0000-0000-0000-000000000001"],
    }


def audience_policy(policy_id: str = AUDIENCE_POLICY_ID) -> dict:
    base = {
        "policy_id": policy_id, "policy_version": "synthetic-audience-v1",
        "service_codes": ["annual_advisory"], "required_entitlement_state": "active",
        "restriction_policy_version": "synthetic-restriction-v1",
    }
    return {**base, "audience_policy_sha256": canonical_hash(base)}


def report_create_body(review_case_id: str = REVIEW_CASE_ID) -> dict:
    return {
        "review_case_id": review_case_id, "title": "合成投顧報告",
        "instrument_refs": [{"market": "TWSE", "instrument_code": "2330", "instrument_type": "equity"}],
        "report_type": "initial",
    }


def report_version_body(*, candidate_id: str = CANDIDATE_ID, channels: list[dict] | None = None, supersedes_version_id: str | None = None) -> dict:
    body = {
        "source_candidate_id": candidate_id,
        "source_artifact_ref": {"system": "pantheon", "external_id": "ext-candidate-001", "kind": "research_candidate"},
        "report_asset_id": REPORT_ASSET_ID, "report_content_sha256": REPORT_CONTENT_SHA256,
        "evidence_snapshot_asset_ids": [EVIDENCE_ASSET_ID],
        "recommendation_version_ids": ["70000000-0000-0000-0000-000000000001"],
        "channel_texts": channels or [channel_text()],
        "audience_policy": audience_policy(),
        "valid_from": "2026-11-23T00:00:00Z", "valid_until": "2026-12-23T00:00:00Z",
    }
    if supersedes_version_id is not None:
        body["supersedes_version_id"] = supersedes_version_id
    return body


def review_body(review_type: str, version: dict, *, decision: str = "approve", reason_code: str = "synthetic_review_ok") -> dict:
    return {
        "review_type": review_type, "decision": decision,
        "report_content_sha256": version["report_content_sha256"], "bundle_manifest_sha256": version["bundle_manifest_sha256"],
        "review_policy_version": "m07-review-policy-v1", "checklist_result_asset_id": CHECKLIST_ASSET_ID, "reason_code": reason_code,
    }


def freeze_body(version: dict, required_review_ids: list[str]) -> dict:
    return {
        "report_content_sha256": version["report_content_sha256"], "bundle_manifest_sha256": version["bundle_manifest_sha256"],
        "required_review_ids": required_review_ids, "canonicalization": "RFC8785",
    }


def signature_request_body(version: dict, required_review_ids: list[str], *, credential_id: str = CREDENTIAL_ID) -> dict:
    return {
        "report_content_sha256": version["report_content_sha256"], "bundle_manifest_sha256": version["bundle_manifest_sha256"],
        "credential_id": credential_id, "required_review_ids": required_review_ids, "signing_intent_confirmed": True,
    }


def callback_body(
    session: dict, version: dict, *, verification_result: str = "verified", signed_file_sha256: str = SIGNED_FILE_SHA256,
    report_content_sha256: str | None = None, bundle_manifest_sha256: str | None = None,
) -> dict:
    body = {
        "event_id": "80000000-0000-0000-0000-000000000001", "signature_request_id": session["id"],
        "provider_session_ref": session["provider_session_ref"], "report_version_id": version["id"],
        "report_content_sha256": report_content_sha256 or version["report_content_sha256"],
        "bundle_manifest_sha256": bundle_manifest_sha256 or version["bundle_manifest_sha256"],
        "verification_result": verification_result,
    }
    if verification_result == "verified":
        body.update({
            "signed_asset_id": SIGNED_ASSET_ID, "signed_file_sha256": signed_file_sha256,
            "signer_subject_ref": "subject:signer", "signed_at": "2026-11-23T01:30:00Z",
            "verification_evidence_asset_id": VERIFICATION_ASSET_ID,
        })
    return body


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict, *, occurred_at: datetime = NOW) -> dict:
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(occurred_at), "emitted_at": wire_time(occurred_at),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": "research_confidential", "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def take_to_ready_to_sign(m07, analyst_ctx, reviewer_pro_ctx, reviewer_compliance_ctx, *, key_prefix: str) -> dict:
    """Shared happy-path fixture: create report+version, approve both reviews, freeze."""
    service = m07["service"]
    report = service.create_report(analyst_ctx, report_create_body(), idempotency_key=f"{key_prefix}-report").data
    version = service.create_report_version(
        analyst_ctx, report["id"], report_version_body(), expected_version=1, idempotency_key=f"{key_prefix}-version",
    ).data
    pro_review = service.record_report_review(
        reviewer_pro_ctx, version["id"], review_body("professional", version), expected_version=1, idempotency_key=f"{key_prefix}-review-pro",
    ).data
    compliance_review = service.record_report_review(
        reviewer_compliance_ctx, version["id"], review_body("compliance", version), expected_version=2, idempotency_key=f"{key_prefix}-review-compliance",
    ).data
    frozen = service.freeze_report_version(
        analyst_ctx, version["id"], freeze_body(version, [pro_review["id"], compliance_review["id"]]),
        expected_version=3, idempotency_key=f"{key_prefix}-freeze",
    ).data
    return {"report": report, "version": frozen, "pro_review": pro_review, "compliance_review": compliance_review}
