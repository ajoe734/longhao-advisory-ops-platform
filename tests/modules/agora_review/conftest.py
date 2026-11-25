from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.agora_review.domain import ActorContext, canonical_hash, wire_time
from modules.agora_review.ports import FakeArtifactDiff, FakeServant
from modules.agora_review.repository import InMemoryRepository
from modules.agora_review.service import AgoraReviewService
from workers.tasks.agora_review.consumer import M06EventConsumer


TENANT = "10000000-0000-0000-0000-000000000001"
OTHER_TENANT = "10000000-0000-0000-0000-000000000099"
ANALYST = "20000000-0000-0000-0000-000000000001"
ANALYST_2 = "20000000-0000-0000-0000-000000000002"
REVIEWER = "20000000-0000-0000-0000-000000000003"
SUPERVISOR = "20000000-0000-0000-0000-000000000004"
SERVANT_ACTOR = "20000000-0000-0000-0000-000000000005"
CANDIDATE_A = "30000000-0000-0000-0000-000000000001"
CANDIDATE_B = "30000000-0000-0000-0000-000000000002"
REPLACEMENT_CANDIDATE = "30000000-0000-0000-0000-000000000003"
CASE_A = "40000000-0000-0000-0000-000000000001"
CASE_B = "40000000-0000-0000-0000-000000000002"
CONTENT_V3 = "a" * 64
CONTENT_V4 = "b" * 64
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def m06():
    repository = InMemoryRepository()
    servant = FakeServant()
    diff = FakeArtifactDiff()
    clock = FixedClock()
    service = AgoraReviewService(repository, servant, diff, clock=clock)
    consumer = M06EventConsumer(repository, clock=clock)
    return {"service": service, "repository": repository, "servant": servant, "diff": diff, "clock": clock, "consumer": consumer}


@pytest.fixture
def analyst() -> ActorContext:
    return ActorContext(TENANT, ANALYST, frozenset({"analyst"}), principal_type="staff")


@pytest.fixture
def analyst_2() -> ActorContext:
    return ActorContext(TENANT, ANALYST_2, frozenset({"analyst"}), principal_type="staff")


@pytest.fixture
def analyst_reviewer() -> ActorContext:
    return ActorContext(TENANT, REVIEWER, frozenset({"analyst_reviewer"}), principal_type="staff")


@pytest.fixture
def supervisor() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"research_supervisor"}), principal_type="staff")


@pytest.fixture
def servant_adapter() -> ActorContext:
    return ActorContext(TENANT, SERVANT_ACTOR, frozenset({"servant_adapter"}), principal_type="service", purpose="integration_webhook")


def artifact_ref(candidate_id: str = CANDIDATE_A) -> dict:
    return {"system": "pantheon", "external_id": candidate_id, "kind": "research_candidate"}


def evidence_ref(kind: str = "market_note", external_id: str = "evidence-1") -> dict:
    return {"system": "pantheon", "external_id": external_id, "kind": kind}


def _envelope(event_type: str, aggregate_id: str, payload: dict, *, sequence: int, tenant: str) -> dict:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "schema_version": "1.0.0",
        "tenant_id": tenant,
        "producer": "M05",
        "aggregate_type": "research_candidate",
        "aggregate_id": aggregate_id,
        "aggregate_version": sequence,
        "sequence_no": sequence,
        "occurred_at": wire_time(NOW),
        "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "idempotency_key": f"{event_type}:{aggregate_id}:{sequence:04d}",
        "classification": "research_confidential",
        "payload_sha256": canonical_hash(payload),
        "payload": payload,
    }


def candidate_validated_envelope(
    candidate_id: str = CANDIDATE_A,
    review_case_id: str = CASE_A,
    *,
    sequence: int = 1,
    content_sha256: str = CONTENT_V3,
    validation_result: str = "accepted",
    tenant: str = TENANT,
) -> dict:
    payload = {
        "candidate_id": candidate_id,
        "review_case_id": review_case_id,
        "artifact_content_sha256": content_sha256,
        "validation_result": validation_result,
    }
    return _envelope("research.candidate_validated", candidate_id, payload, sequence=sequence, tenant=tenant)


def candidate_superseded_envelope(
    candidate_id: str = CANDIDATE_A,
    replacement_candidate_id: str = REPLACEMENT_CANDIDATE,
    *,
    sequence: int = 1,
    tenant: str = TENANT,
) -> dict:
    payload = {
        "candidate_id": candidate_id,
        "replacement_candidate_id": replacement_candidate_id,
        "previous_artifact_sha256": CONTENT_V3,
        "replacement_artifact_sha256": CONTENT_V4,
    }
    return _envelope("research.candidate_superseded", candidate_id, payload, sequence=sequence, tenant=tenant)


def seed_case(m06, candidate_id: str = CANDIDATE_A, review_case_id: str = CASE_A, content_sha256: str = CONTENT_V3) -> str:
    result = m06["consumer"].consume(candidate_validated_envelope(candidate_id, review_case_id, content_sha256=content_sha256))
    assert result.result_code == "case_created"
    return review_case_id


def version_of(service, context, case_id: str) -> int:
    return service.get_review_case(context, case_id).data["version"]


def case_version(m06, case_id: str, tenant: str = TENANT) -> int:
    """Reads the raw aggregate version straight from the repository.

    ``research_supervisor`` is not in ``get_review_case``'s allowed roles, so
    tests acting as the supervisor cannot source their own If-Match token
    through the service the way an analyst can.
    """

    return m06["repository"].snapshot().cases[(tenant, case_id)].version


def assign(m06, supervisor, case_id: str, assignee_id: str = ANALYST, key: str = "assign-0000001"):
    return m06["service"].assign_review_case(
        supervisor, case_id, {"assignee_id": assignee_id, "reason_code": "workload_balance"},
        expected_version=case_version(m06, case_id), idempotency_key=key,
    )


def open_conversation(m06, analyst, case_id: str, *, candidate_id: str = CANDIDATE_A, key: str = "conv-00000001", evidence=None):
    body = {
        "candidate_id": candidate_id,
        "artifact_ref": artifact_ref(candidate_id),
        "existing_conversation_ref": None,
        "allowed_evidence_refs": evidence or [evidence_ref()],
        "purpose": "revision",
    }
    return m06["service"].open_servant_conversation(
        analyst, case_id, body, expected_version=version_of(m06["service"], analyst, case_id), idempotency_key=key,
    )


def request_revision(
    m06, analyst, case_id: str, conversation_id: str, *,
    content_sha256: str = CONTENT_V3, candidate_id: str = CANDIDATE_A, key: str = "rev-000000001", instructions: str = "請補充三個月目標價估算依據。",
):
    body = {
        "conversation_id": conversation_id,
        "base_artifact_ref": artifact_ref(candidate_id),
        "base_content_sha256": content_sha256,
        "instructions": instructions,
        "required_evidence_refs": [],
        "requested_output": "revised_report",
    }
    return m06["service"].request_servant_revision(
        analyst, case_id, body, expected_version=version_of(m06["service"], analyst, case_id), idempotency_key=key,
    )
