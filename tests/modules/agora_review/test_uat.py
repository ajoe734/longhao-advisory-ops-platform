from __future__ import annotations

import pytest

from modules.agora_review.domain import ConflictError, ForbiddenError, StaleVersionError
from modules.agora_review.automation import assert_no_privileged_capability

from conftest import (
    ANALYST,
    ANALYST_2,
    CANDIDATE_A,
    CONTENT_V3,
    CONTENT_V4,
    REPLACEMENT_CANDIDATE,
    artifact_ref,
    assign,
    candidate_superseded_envelope,
    case_version,
    evidence_ref,
    open_conversation,
    request_revision,
    seed_case,
    version_of,
    webhook_request,
)


def test_uat_m06_001_valid_candidate_enters_analyst_queue_and_case_can_be_claimed(m06, analyst, supervisor):
    case_id = seed_case(m06)
    queue = m06["service"].list_review_cases(analyst)
    assert any(item["id"] == case_id for item in queue.data["items"])
    assigned = assign(m06, supervisor, case_id)
    assert assigned.data["state"] == "assigned"
    assert assigned.data["assignee_id"] == ANALYST
    assert assigned.data["candidate_id"] == CANDIDATE_A
    # No re-ingest was required: the candidate_id and original source version are preserved.
    assert m06["service"].get_review_case(analyst, case_id).data["candidate_id"] == CANDIDATE_A


def test_uat_m06_002_revision_request_binds_the_exact_candidate_version(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    assert revision["base_artifact_ref"] == artifact_ref(CANDIDATE_A)
    assert revision["state"] == "queued"
    # An external callback referencing this exact revision_id can correlate back to the same request.
    fetched = m06["service"].get_revision_request(analyst, revision["id"]).data
    assert fetched["id"] == revision["id"]
    assert fetched["review_case_id"] == case_id
    assert fetched["conversation_id"] == conversation["id"]


def test_uat_m06_003_returned_revision_only_creates_a_new_version(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    callback_body = {
        "event_id": "50000000-0000-0000-0000-000000000001",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": CONTENT_V4,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000001",
    }
    receipt = m06["service"].receive_servant_revision(
        servant_adapter, callback_body, idempotency_key="callback-00000001", webhook=webhook_request(callback_body),
    ).data
    assert receipt["status"] == "accepted"
    case_after = m06["service"].get_review_case(analyst, case_id).data
    # v3 is preserved as the case's accepted candidate reference; v4 is not silently approved.
    assert case_after["current_artifact_ref"]["checksum_sha256"] == CONTENT_V3
    assert case_after["state"] == "under_review"
    updated_revision = m06["service"].get_revision_request(analyst, revision["id"]).data
    assert updated_revision["state"] == "completed"
    assert updated_revision["result_content_sha256"] == CONTENT_V4


def test_uat_m06_004_stale_version_reload_shows_new_version_without_overwrite(m06, analyst, supervisor):
    case_id = seed_case(m06)
    current_version = case_version(m06, case_id)
    first = assign(m06, supervisor, case_id, key="assign-stale001")
    with pytest.raises(StaleVersionError) as stale:
        m06["service"].assign_review_case(
            supervisor, case_id, {"assignee_id": ANALYST_2, "reason_code": "reassign_attempt"},
            expected_version=current_version, idempotency_key="assign-stale002",
        )
    assert stale.value.status == 412
    assert stale.value.current_version == first.data["version"]
    reloaded = m06["service"].get_review_case(analyst, case_id).data
    assert reloaded["assignee_id"] == ANALYST  # first write was not overwritten by the stale retry
    assert reloaded["version"] == first.data["version"]


def test_uat_m06_005_servant_cannot_assign_or_act_as_analyst(m06, servant_adapter, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    current_version = version_of(m06["service"], analyst, case_id)
    with pytest.raises(ForbiddenError):
        m06["service"].assign_review_case(
            servant_adapter, case_id, {"assignee_id": ANALYST, "reason_code": "servant_self_assign"},
            expected_version=current_version, idempotency_key="forbid-0000001",
        )
    with pytest.raises(ForbiddenError):
        m06["service"].open_servant_conversation(
            servant_adapter, case_id,
            {
                "candidate_id": CANDIDATE_A, "artifact_ref": artifact_ref(CANDIDATE_A),
                "existing_conversation_ref": None, "allowed_evidence_refs": [evidence_ref()], "purpose": "revision",
            },
            expected_version=current_version, idempotency_key="forbid-0000002",
        )
    with pytest.raises(ForbiddenError):
        assert_no_privileged_capability("sign")
    with pytest.raises(ForbiddenError):
        assert_no_privileged_capability("publish")
    # No signature or publication event was ever produced by this module.
    event_types = {event.event_type for event in m06["repository"].snapshot().outbox.values()}
    assert event_types <= {"review.revision_requested", "review.revision_received"}


def test_uat_m06_006_conversation_scope_cannot_cross_case_or_tenant(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    forged_ref = {"system": "agora", "external_id": "conv-from-another-tenant", "kind": "servant_conversation"}
    callback_body = {
        "event_id": "50000000-0000-0000-0000-000000000002",
        "revision_id": revision["id"],
        "conversation_ref": forged_ref,
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-forged", "kind": "revised_report"},
        "result_content_sha256": CONTENT_V4,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000002",
    }
    receipt = m06["service"].receive_servant_revision(
        servant_adapter, callback_body, idempotency_key="callback-forged001", webhook=webhook_request(callback_body),
    ).data
    assert receipt["status"] == "quarantined"
    unchanged = m06["service"].get_revision_request(analyst, revision["id"]).data
    assert unchanged["state"] == "queued"
    assert unchanged["result_content_sha256"] is None
    denied_audits = [event for event in m06["repository"].snapshot().audit_events if event.reason_code == "revision_scope_violation"]
    assert len(denied_audits) == 1


def test_uat_m06_007_external_timeout_does_not_open_a_second_job(m06, analyst, supervisor):
    from workers.tasks.agora_review.tasks import LocalEventBus, OutboxPublisher, ServantDispatchPublisher

    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    job_ref = revision["id"]

    m06["servant"].fail_job(job_ref, "timeout_after_register")
    dispatcher = ServantDispatchPublisher(m06["repository"], m06["servant"], LocalEventBus())
    publisher = OutboxPublisher(m06["repository"], dispatcher, max_attempts=3)
    first = publisher.run_once("m06-worker-a")
    assert first.retried == 1
    assert m06["servant"].submitted_job_count(job_ref) == 1
    assert m06["service"].get_revision_request(analyst, revision["id"]).data["state"] == "queued"

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = m06["clock"].value
    m06["repository"].atomic(make_due)
    second = publisher.run_once("m06-worker-b")
    assert second.processed == 1
    assert m06["servant"].submitted_job_count(job_ref) == 1  # still exactly one external job
    assert m06["service"].get_revision_request(analyst, revision["id"]).data["state"] == "running"


def test_uat_m06_008_new_source_version_marks_the_case_stale(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    open_conversation(m06, analyst, case_id)
    result = m06["consumer"].consume(candidate_superseded_envelope(CANDIDATE_A, REPLACEMENT_CANDIDATE, sequence=2))
    assert result.result_code == "case_marked_stale"
    case = m06["service"].get_review_case(analyst, case_id).data
    assert case["state"] == "stale"
    assert case["source_version_stale"] is True
    # A stale case cannot silently continue being treated as if v3 were still under live review.
    with pytest.raises(ConflictError) as blocked:
        open_conversation(m06, analyst, case_id, key="conv-afterstale")
    assert blocked.value.code == "case_source_stale"


def test_uat_m06_009_failed_revision_can_be_retried_and_keeps_the_failed_evidence(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    first_revision = request_revision(m06, analyst, case_id, conversation["id"], key="rev-0000firstA").data
    fail_body = {
        "event_id": "50000000-0000-0000-0000-000000000003",
        "revision_id": first_revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": None,
        "result_content_sha256": None,
        "status": "failed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000003",
    }
    m06["service"].receive_servant_revision(
        servant_adapter, fail_body, idempotency_key="callback-failed001", webhook=webhook_request(fail_body),
    )
    failed = m06["service"].get_revision_request(analyst, first_revision["id"]).data
    assert failed["state"] == "failed"

    retry_revision = request_revision(m06, analyst, case_id, conversation["id"], key="rev-0000retryA").data
    assert retry_revision["id"] != first_revision["id"]
    still_failed = m06["service"].get_revision_request(analyst, first_revision["id"]).data
    assert still_failed == failed  # the original failed attempt/artifact is untouched
    case = m06["service"].get_review_case(analyst, case_id).data
    # first_revision is preserved as evidence (still fetchable above) but is superseded
    # by the retry it is now bound to, so it no longer counts as outstanding work.
    assert set(case["pending_revision_ids"]) == {retry_revision["id"]}
    assert m06["service"].get_revision_request(analyst, retry_revision["id"]).data["id"] == retry_revision["id"]


def test_uat_m06_010_degraded_dependency_shows_last_known_state_not_an_empty_or_completed_view(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data

    m06["servant"].healthy = False
    workspace = m06["service"].get_review_case_workspace(analyst, case_id)
    assert workspace["servant_health"]["status"] == "degraded"
    # Degraded Servant health never empties or fakes completion of the real, already-known pending work.
    assert [item["id"] for item in workspace["revisions"]] == [revision["id"]]
    assert workspace["revisions"][0]["state"] == "queued"


def test_uat_m06_011_significant_numeric_changes_surface_as_a_visible_pending_diff(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    m06["diff"].register(CONTENT_V3, CONTENT_V4, ("target_price: 100 -> 120", "risk_rating: B2 -> B3"))

    diff_callback_body = {
        "event_id": "50000000-0000-0000-0000-000000000004",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": CONTENT_V4,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000004",
    }
    m06["service"].receive_servant_revision(
        servant_adapter, diff_callback_body, idempotency_key="callback-diff0001", webhook=webhook_request(diff_callback_body),
    )
    results = [row for row in m06["repository"].snapshot().revision_results.values() if row.request_id == revision["id"]]
    assert len(results) == 1
    assert results[0].significant_diff is True
    assert "target_price: 100 -> 120" in results[0].diff_summary
    # The analyst still has to act; the new content never silently replaces the accepted v3 preview.
    case = m06["service"].get_review_case(analyst, case_id).data
    assert case["current_artifact_ref"]["checksum_sha256"] == CONTENT_V3


def test_uat_m06_012_research_conversation_never_carries_kyc_or_aml_content(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    with pytest.raises(ForbiddenError) as blocked:
        open_conversation(m06, analyst, case_id, key="conv-amlblock1", evidence=[evidence_ref(kind="kyc_profile")])
    assert blocked.value.code == "aml_restricted_content_blocked"
    assert m06["servant"].received_conversation_payloads == {}

    allowed = open_conversation(m06, analyst, case_id, key="conv-amlallow1").data
    sent_payload = m06["servant"].received_conversation_payloads["conv-amlallow1"]
    assert set(sent_payload) == {"artifact_ref", "purpose", "evidence_refs", "service_tone"}
    assert allowed["status"] == "active"
