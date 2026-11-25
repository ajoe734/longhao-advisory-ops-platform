"""Regression tests for the Round-1 Antigravity review of 7cfb35522b9f (PR #22).

Each test reproduces one probe from the review artifact
(``review-m06-7cfb35522b9f.md``) against the fixed implementation; every test
would fail against the pre-fix code. See ``tests/modules/agora_review/traceability.md``
for the finding -> test mapping.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from modules.agora_review.domain import (
    ActorContext,
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
)
from modules.agora_review.http import M06Controller
from modules.agora_review.repository import StoreState

from conftest import (
    ANALYST,
    CANDIDATE_A,
    SERVANT_ACTOR,
    TENANT,
    artifact_ref,
    assign,
    evidence_ref,
    open_conversation,
    request_revision,
    seed_case,
    version_of,
    webhook_request,
)


# ---- F1: webhook HMAC / headers must actually be enforced -----------------


def test_f1_unsigned_webhook_is_rejected_by_the_controller(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    controller = M06Controller(m06["service"])
    body = {
        "event_id": "50000000-0000-0000-0000-000000000011",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000011",
    }
    response = controller.handle(
        "receive_servant_revision", servant_adapter, body=body, headers={"Idempotency-Key": "unsigned-0001"},
    )
    assert response.status == 422
    assert response.body["field_errors"][0]["field"] == "X-Webhook-Signature"


def test_f1_tampered_signature_is_rejected(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    body = {
        "event_id": "50000000-0000-0000-0000-000000000012",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000012",
    }
    webhook = webhook_request(body)
    tampered = webhook.__class__(
        signature=webhook.signature, timestamp=webhook.timestamp, nonce=webhook.nonce,
        integration_key_id=webhook.integration_key_id, raw_body=b"{}", method=webhook.method, path=webhook.path,
    )
    with pytest.raises(ForbiddenError) as exc:
        m06["service"].receive_servant_revision(
            servant_adapter, body, idempotency_key="tampered-0001", webhook=tampered,
        )
    assert exc.value.code == "webhook_signature_invalid"


def test_f1_stale_timestamp_is_rejected(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    body = {
        "event_id": "50000000-0000-0000-0000-000000000013",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000013",
    }
    stale_webhook = webhook_request(body, now=m06["clock"].value - timedelta(seconds=301))
    with pytest.raises(ForbiddenError) as exc:
        m06["service"].receive_servant_revision(
            servant_adapter, body, idempotency_key="stale-ts-0001", webhook=stale_webhook,
        )
    assert exc.value.code == "webhook_timestamp_skew"


def test_f1_unknown_integration_key_is_rejected(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    body = {
        "event_id": "50000000-0000-0000-0000-000000000014",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000014",
    }
    with pytest.raises(ForbiddenError) as exc:
        m06["service"].receive_servant_revision(
            servant_adapter, body, idempotency_key="unknown-key-0001",
            webhook=webhook_request(body, key_id="unregistered-integration-key"),
        )
    assert exc.value.code == "webhook_key_unknown"


def test_f1_replayed_nonce_is_rejected_even_with_a_new_idempotency_key(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    body = {
        "event_id": "50000000-0000-0000-0000-000000000015",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000015",
    }
    webhook = webhook_request(body, nonce="replay-me-0000016-xx")
    first = m06["service"].receive_servant_revision(
        servant_adapter, body, idempotency_key="nonce-replay-01", webhook=webhook,
    )
    assert first.data["status"] == "accepted"
    with pytest.raises(ForbiddenError) as exc:
        m06["service"].receive_servant_revision(
            servant_adapter, body, idempotency_key="nonce-replay-02", webhook=webhook,
        )
    assert exc.value.code == "webhook_nonce_replay"


# ---- F2: principal isolation and the 403-vs-404 existence oracle ----------


def test_f2_service_principal_cannot_execute_a_staff_command(m06, supervisor):
    case_id = seed_case(m06)
    service_principal = ActorContext(TENANT, supervisor.actor_id, frozenset({"research_supervisor"}), principal_type="service")
    with pytest.raises(ForbiddenError):
        m06["service"].assign_review_case(
            service_principal, case_id, {"assignee_id": ANALYST, "reason_code": "service_impersonation"},
            expected_version=1, idempotency_key="f2-service-0001",
        )


def test_f2_staff_principal_cannot_call_the_servant_webhook(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    staff_as_servant = ActorContext(TENANT, SERVANT_ACTOR, frozenset({"servant_adapter"}), principal_type="staff")
    body = {
        "event_id": "50000000-0000-0000-0000-000000000016",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000016",
    }
    with pytest.raises(ForbiddenError):
        m06["service"].receive_servant_revision(
            staff_as_servant, body, idempotency_key="f2-staff-0001", webhook=webhook_request(body),
        )


def test_f2_unauthorized_caller_gets_consistent_403_for_existing_and_missing_cases(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    unauthorized = ActorContext(TENANT, "20000000-0000-0000-0000-000000000099", frozenset({"analyst_reviewer"}), principal_type="staff")
    body = {
        "candidate_id": CANDIDATE_A, "artifact_ref": artifact_ref(CANDIDATE_A),
        "existing_conversation_ref": None, "allowed_evidence_refs": [evidence_ref()], "purpose": "revision",
    }
    with pytest.raises(ForbiddenError):
        m06["service"].open_servant_conversation(
            unauthorized, case_id, body, expected_version=1, idempotency_key="f2-oracle-exist1",
        )
    missing_case_id = "40000000-0000-0000-0000-000000009999"
    with pytest.raises(ForbiddenError):
        m06["service"].open_servant_conversation(
            unauthorized, missing_case_id, body, expected_version=1, idempotency_key="f2-oracle-miss01",
        )


# ---- F3: terminal/frozen cases and inactive conversations must not mutate --


def _force_case_status(m06, case_id: str, status: str, tenant: str = TENANT) -> None:
    def mutate(state: StoreState) -> None:
        state.cases[(tenant, case_id)].status = status

    m06["repository"].atomic(mutate)


def test_f3_closed_case_rejects_new_revision_requests(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    _force_case_status(m06, case_id, "closed")
    body = {
        "conversation_id": conversation["id"], "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "base_content_sha256": "a" * 64, "instructions": "x", "required_evidence_refs": [], "requested_output": "revised_report",
    }
    with pytest.raises(ConflictError) as exc:
        m06["service"].request_servant_revision(
            analyst, case_id, body, expected_version=version_of(m06["service"], analyst, case_id), idempotency_key="f3-closed-0001",
        )
    assert exc.value.code == "case_terminal"
    assert m06["service"].get_review_case(analyst, case_id).data["state"] == "signed"


def test_f3_ready_to_sign_case_rejects_opening_a_new_conversation(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    _force_case_status(m06, case_id, "ready_to_sign")
    body = {
        "candidate_id": CANDIDATE_A, "artifact_ref": artifact_ref(CANDIDATE_A),
        "existing_conversation_ref": None, "allowed_evidence_refs": [evidence_ref()], "purpose": "revision",
    }
    with pytest.raises(ConflictError) as exc:
        m06["service"].open_servant_conversation(
            analyst, case_id, body, expected_version=version_of(m06["service"], analyst, case_id), idempotency_key="f3-rts-00001",
        )
    assert exc.value.code == "case_terminal"


def test_f3_inactive_conversation_cannot_be_reused(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data

    def close_conversation(state: StoreState) -> None:
        state.conversations[(TENANT, conversation["id"])].status = "closed"

    m06["repository"].atomic(close_conversation)
    body = {
        "candidate_id": CANDIDATE_A, "artifact_ref": artifact_ref(CANDIDATE_A),
        "existing_conversation_ref": conversation["external_conversation_ref"],
        "allowed_evidence_refs": [evidence_ref()], "purpose": "revision",
    }
    with pytest.raises(ConflictError) as exc:
        m06["service"].open_servant_conversation(
            analyst, case_id, body, expected_version=version_of(m06["service"], analyst, case_id), idempotency_key="f3-inactive-01",
        )
    assert exc.value.code == "conversation_not_active"


# ---- F4: no DB transaction held across the network call; worker fixes -----


def test_f4_open_conversation_commits_after_the_network_call_without_holding_the_lock(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    result = open_conversation(m06, analyst, case_id)
    assert result.status == 201
    assert result.data["status"] == "active"
    # The repository was not left locked/corrupted by the out-of-transaction network call.
    assert m06["repository"].snapshot().conversations[(TENANT, result.data["id"])].external_conversation_id


def test_f4_dispatch_submits_the_full_revision_scope_to_servant(m06, analyst, supervisor):
    from workers.tasks.agora_review.tasks import LocalEventBus, ServantDispatchPublisher

    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"], instructions="請補充三個月目標價估算依據。").data

    dispatcher = ServantDispatchPublisher(m06["repository"], m06["servant"], LocalEventBus())
    envelope = next(e for e in m06["repository"].snapshot().outbox.values() if e.event_type == "review.revision_requested")
    dispatcher.publish(envelope.envelope())

    submitted = m06["servant"].received_revision_payloads[revision["id"]]
    assert submitted["instructions"] == "請補充三個月目標價估算依據。"
    assert submitted["base_artifact_ref"] == artifact_ref(CANDIDATE_A)
    assert submitted["required_evidence_refs"] == []
    assert submitted["requested_output"] == "revised_report"
    assert submitted["external_conversation_id"] == conversation["external_conversation_ref"]["external_id"]


def test_f4_servant_denial_marks_the_revision_failed_instead_of_crashing_the_worker(m06, analyst, supervisor):
    from workers.tasks.agora_review.tasks import LocalEventBus, ServantDispatchPublisher

    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    m06["servant"].fail_job(revision["id"], "denied")

    dispatcher = ServantDispatchPublisher(m06["repository"], m06["servant"], LocalEventBus())
    envelope = next(e for e in m06["repository"].snapshot().outbox.values() if e.event_type == "review.revision_requested")
    result = dispatcher.publish(envelope.envelope())

    assert result == "accepted"  # the outbox row is not stuck retrying a permanently denied job
    stored = m06["repository"].snapshot().revisions[(TENANT, revision["id"])]
    assert stored.status == "failed"
    assert stored.last_error_code == "servant_revision_denied"


def test_f4_get_revision_status_dependency_failure_is_retryable_not_a_crash(m06, analyst, supervisor, monkeypatch):
    from workers.tasks.agora_review.tasks import LocalEventBus, RetryableTaskError, ServantDispatchPublisher

    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data

    def boom(_job_ref):
        raise DependencyUnavailableError("servant status probe timed out", code="servant_status_timeout")

    monkeypatch.setattr(m06["servant"], "get_revision_status", boom)
    dispatcher = ServantDispatchPublisher(m06["repository"], m06["servant"], LocalEventBus())
    envelope = next(e for e in m06["repository"].snapshot().outbox.values() if e.event_type == "review.revision_requested")
    with pytest.raises(RetryableTaskError):
        dispatcher.publish(envelope.envelope())
    assert m06["repository"].snapshot().revisions[(TENANT, revision["id"])].status == "requested"


def test_f4_outbox_worker_reclaims_an_expired_lease(m06):
    from workers.tasks.agora_review.tasks import OutboxPublisher
    from modules.agora_review.domain import OutboxEvent, new_id

    def seed(state: StoreState) -> None:
        state.outbox[(TENANT, "stuck-event")] = OutboxEvent(
            event_id="stuck-event", event_type="review.revision_received", tenant_id=TENANT, aggregate_type="review_case",
            aggregate_id="x", aggregate_version=1, sequence_no=1, correlation_id=new_id(), causation_id=None,
            idempotency_key="stuck", classification="research_confidential", payload={}, occurred_at=m06["clock"].value,
            emitted_at=m06["clock"].value, status="leased", lease_owner="dead-worker",
            lease_until=m06["clock"].value - timedelta(seconds=1),
        )

    m06["repository"].atomic(seed)

    class AcceptingBus:
        is_fake = True

        def publish(self, envelope):
            return "accepted"

    publisher = OutboxPublisher(m06["repository"], AcceptingBus())
    result = publisher.run_once("new-worker")
    assert result.processed == 1
    assert m06["repository"].snapshot().outbox[(TENANT, "stuck-event")].status == "published"


# ---- F5: versioning, ETag replay, audit duplication, pending/retry, refs --


def test_f5_completed_callback_increments_the_revision_version(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    assert revision["version"] == 1
    body = {
        "event_id": "50000000-0000-0000-0000-000000000017",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000017",
    }
    m06["service"].receive_servant_revision(servant_adapter, body, idempotency_key="f5-version-01", webhook=webhook_request(body))
    assert m06["repository"].snapshot().revisions[(TENANT, revision["id"])].version == 2


def test_f5_idempotent_replay_of_the_webhook_still_returns_an_etag(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    revision = request_revision(m06, analyst, case_id, conversation["id"]).data
    body = {
        "event_id": "50000000-0000-0000-0000-000000000018",
        "revision_id": revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000018",
    }
    # A real delivery retry re-signs the HTTP request (fresh nonce/timestamp) but keeps
    # the same Idempotency-Key/body; that is what makes it a replay at the command layer
    # while still passing webhook signature verification as a distinct delivery attempt.
    first = m06["service"].receive_servant_revision(
        servant_adapter, body, idempotency_key="f5-replay-etag-01", webhook=webhook_request(body, nonce="f5-attempt-one-aaaa"),
    )
    assert first.etag is not None and not first.idempotency_replayed
    replay = m06["service"].receive_servant_revision(
        servant_adapter, body, idempotency_key="f5-replay-etag-01", webhook=webhook_request(body, nonce="f5-attempt-two-bbbb"),
    )
    assert replay.idempotency_replayed is True
    assert replay.etag == first.etag


def test_f5_quarantined_callback_produces_exactly_one_rejected_audit(m06, servant_adapter):
    body = {
        "event_id": "50000000-0000-0000-0000-000000000019",
        "revision_id": "40000000-0000-0000-0000-000000009998",
        "conversation_ref": {"system": "agora", "external_id": "unknown-conv", "kind": "servant_conversation"},
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": {"system": "agora", "external_id": "artifact-v4", "kind": "revised_report"},
        "result_content_sha256": "b" * 64,
        "status": "completed",
        "completed_at": "2026-11-23T02:00:00Z",
        "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000019",
    }
    receipt = m06["service"].receive_servant_revision(
        servant_adapter, body, idempotency_key="f5-quarantine-01", webhook=webhook_request(body),
    )
    assert receipt.data["status"] == "quarantined"
    audits = [
        event for event in m06["repository"].snapshot().audit_events
        if event.action == "m06.receive_servant_revision"
    ]
    assert len(audits) == 1
    assert audits[0].result == "rejected"
    assert audits[0].reason_code == "revision_not_found"


def test_f5_retry_binds_to_the_most_recent_unclaimed_failure(m06, analyst, supervisor, servant_adapter):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data

    first_revision = request_revision(m06, analyst, case_id, conversation["id"], key="rev-retry-first").data
    fail_body_1 = {
        "event_id": "50000000-0000-0000-0000-000000000020",
        "revision_id": first_revision["id"],
        "conversation_ref": conversation["external_conversation_ref"],
        "base_artifact_ref": artifact_ref(CANDIDATE_A),
        "result_artifact_ref": None, "result_content_sha256": None, "status": "failed",
        "completed_at": "2026-11-23T02:00:00Z", "transcript_snapshot_asset_id": "60000000-0000-0000-0000-000000000020",
    }
    m06["service"].receive_servant_revision(servant_adapter, fail_body_1, idempotency_key="f5-fail-01", webhook=webhook_request(fail_body_1))
    m06["clock"].advance(5)

    second_revision = request_revision(m06, analyst, case_id, conversation["id"], key="rev-retry-second").data
    assert second_revision["id"] != first_revision["id"]
    fail_body_2 = {**fail_body_1, "event_id": "50000000-0000-0000-0000-000000000021", "revision_id": second_revision["id"]}
    m06["service"].receive_servant_revision(servant_adapter, fail_body_2, idempotency_key="f5-fail-02", webhook=webhook_request(fail_body_2))
    m06["clock"].advance(5)

    third_revision = request_revision(m06, analyst, case_id, conversation["id"], key="rev-retry-third")
    third_stored = m06["repository"].snapshot().revisions[(TENANT, third_revision.data["id"])]
    # The most recently failed, not-yet-claimed attempt is the one retried; the older
    # failure (already superseded conceptually, since it happened first) is left alone.
    assert third_stored.retry_of_revision_id == second_revision["id"]


def test_f5_conversation_response_reports_real_allowed_actions(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    assert conversation["allowed_actions"] == ["request_servant_revision"]


def test_f5_external_ref_match_tolerates_optional_fields(m06, analyst, supervisor):
    case_id = seed_case(m06)
    assign(m06, supervisor, case_id)
    conversation = open_conversation(m06, analyst, case_id).data
    ref_with_optional_fields = {**conversation["external_conversation_ref"], "version": "v1"}
    body = {
        "candidate_id": CANDIDATE_A, "artifact_ref": artifact_ref(CANDIDATE_A),
        "existing_conversation_ref": ref_with_optional_fields,
        "allowed_evidence_refs": [evidence_ref()], "purpose": "revision",
    }
    reused = m06["service"].open_servant_conversation(
        analyst, case_id, body, expected_version=version_of(m06["service"], analyst, case_id), idempotency_key="f5-refmatch-01",
    )
    assert reused.data["id"] == conversation["id"]
