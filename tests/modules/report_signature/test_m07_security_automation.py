from __future__ import annotations

import pytest

from modules.report_signature.automation import LocalDraftAssistant, ReportDraftAssistService
from modules.report_signature.domain import (
    ActorContext,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionRequiredError,
    StaleVersionError,
    ValidationError,
)
from modules.report_signature.http import M07Controller
from modules.report_signature.repository import InMemoryRepository
from modules.report_signature.service import ReportSignatureService
from workers.tasks.report_signature.consumer import M07EventConsumer

from conftest import (
    CANDIDATE_ID,
    OTHER_TENANT,
    REVOCATION_EVIDENCE_ASSET_ID,
    TENANT,
    callback_body,
    incoming,
    report_create_body,
    report_version_body,
    review_body,
    signature_request_body,
    take_to_ready_to_sign,
)


def test_maker_checker_blocks_reviewer_from_reviewing_own_version(m07):
    dual_role = ActorContext(TENANT, "20000000-0000-0000-0000-000000000011", frozenset({"analyst", "analyst_reviewer"}), principal_type="staff")
    service = m07["service"]
    report = service.create_report(dual_role, report_create_body(), idempotency_key="sod-report").data
    version = service.create_report_version(dual_role, report["id"], report_version_body(), expected_version=1, idempotency_key="sod-version").data
    with pytest.raises(ForbiddenError) as denied:
        service.record_report_review(dual_role, version["id"], review_body("professional", version), expected_version=1, idempotency_key="sod-review")
    assert denied.value.code == "maker_checker_violation"


def test_cross_tenant_read_hides_existence(m07, analyst, reviewer_pro, reviewer_compliance):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="tenant")
    other_tenant_actor = ActorContext(OTHER_TENANT, "20000000-0000-0000-0000-000000000099", frozenset({"analyst"}), principal_type="staff")
    with pytest.raises(NotFoundError):
        m07["service"].get_report_version(other_tenant_actor, result["version"]["id"])


def test_idempotency_key_reuse_with_different_payload_is_409(m07, analyst):
    m07["service"].create_report(analyst, report_create_body(), idempotency_key="same-key-0001")
    with pytest.raises(ConflictError) as conflict:
        m07["service"].create_report(analyst, {**report_create_body(), "title": "不同標題"}, idempotency_key="same-key-0001")
    assert conflict.value.code == "idempotency_conflict"


def test_stale_version_is_rejected_with_current_version(m07, analyst, analyst_b):
    service = m07["service"]
    report = service.create_report(analyst, report_create_body(), idempotency_key="stale-report").data
    service.create_report_version(analyst, report["id"], report_version_body(), expected_version=1, idempotency_key="stale-v1")
    with pytest.raises(StaleVersionError) as stale:
        service.create_report_version(analyst, report["id"], report_version_body(), expected_version=1, idempotency_key="stale-v2")
    assert stale.value.status == 412 and stale.value.current_version == 2


def test_missing_if_match_is_428(m07, analyst):
    service = m07["service"]
    report = service.create_report(analyst, report_create_body(), idempotency_key="precond-report").data
    with pytest.raises(PreconditionRequiredError):
        service.create_report_version(analyst, report["id"], report_version_body(), expected_version=None, idempotency_key="precond-version")


def test_request_analyst_signature_idempotent_replay_succeeds_after_state_moved_on(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    """F4(a): check_signable previously ran before the idempotency receipt lookup, so a
    retried request_analyst_signature call 409'd once the first call's success had already
    moved the version from ready_to_sign to signature_pending."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="replay")
    version = result["version"]
    service = m07["service"]
    body = signature_request_body(version, version["required_review_ids"])
    first = service.request_analyst_signature(signer, version["id"], body, expected_version=4, idempotency_key="replay-req-sign")
    assert first.status == 202
    replay = service.request_analyst_signature(signer, version["id"], body, expected_version=4, idempotency_key="replay-req-sign")
    assert replay.idempotency_replayed is True
    assert replay.data == first.data
    assert replay.etag == first.etag  # F4(c): the receipt must preserve the aggregate's ETag
    snapshot = m07["repository"].snapshot()
    assert len(snapshot.signature_requests) == 1


def test_request_analyst_signature_stale_cas_does_not_leak_a_provider_session(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    """F4(b): CAS must fail before signature_provider.create_session is ever called."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="cas-leak")
    version = result["version"]
    service = m07["service"]
    provider = m07["signature_provider"]
    sessions_before = len(provider._sessions)
    with pytest.raises(StaleVersionError):
        service.request_analyst_signature(
            signer, version["id"], signature_request_body(version, version["required_review_ids"]),
            expected_version=99, idempotency_key="cas-leak-req-sign",
        )
    assert len(provider._sessions) == sessions_before
    assert len(m07["repository"].snapshot().signature_requests) == 0


def test_request_analyst_signature_missing_if_match_does_not_leak_a_provider_session(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="cas-missing")
    version = result["version"]
    service = m07["service"]
    provider = m07["signature_provider"]
    sessions_before = len(provider._sessions)
    with pytest.raises(PreconditionRequiredError):
        service.request_analyst_signature(
            signer, version["id"], signature_request_body(version, version["required_review_ids"]),
            expected_version=None, idempotency_key="cas-missing-req-sign",
        )
    assert len(provider._sessions) == sessions_before
    assert len(m07["repository"].snapshot().signature_requests) == 0


def test_revoking_an_already_revoked_signature_is_conflict(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter, supervisor):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="revoke-twice")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="revoke-twice-sign").data
    service.receive_analyst_signature(adapter, callback_body(session, version), idempotency_key="revoke-twice-callback")
    signature_id = m07["repository"].snapshot().versions[(TENANT, version["id"])].signature_id
    body = {"reason_code": "first_revocation", "evidence_asset_id": REVOCATION_EVIDENCE_ASSET_ID, "effective_at": "2026-11-24T00:00:00Z", "notify_recipients": True}
    service.revoke_signature(supervisor, signature_id, body, expected_version=1, idempotency_key="revoke-twice-first")
    with pytest.raises(ConflictError) as already:
        service.revoke_signature(supervisor, signature_id, {**body, "reason_code": "second_attempt"}, expected_version=2, idempotency_key="revoke-twice-second")
    assert already.value.code == "signature_already_revoked"


def test_webhook_requires_service_principal_not_staff(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="webhook-role")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="webhook-role-sign").data
    staff_pretending_to_be_adapter = ActorContext(TENANT, "20000000-0000-0000-0000-000000000012", frozenset({"signature_adapter"}), principal_type="staff")
    with pytest.raises(ForbiddenError):
        service.receive_analyst_signature(staff_pretending_to_be_adapter, callback_body(session, version), idempotency_key="webhook-role-callback")


def test_signed_at_outside_frozen_expiry_or_validity_window_is_quarantined(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="timing")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="timing-sign").data
    body = callback_body(session, version)
    body["signed_at"] = "2026-11-22T00:00:00Z"  # before frozen_at
    receipt = service.receive_analyst_signature(adapter, body, idempotency_key="timing-callback").data
    assert receipt["status"] == "quarantined"
    snapshot = m07["repository"].snapshot()
    assert snapshot.versions[(TENANT, version["id"])].status == "ready_to_sign"
    assert len(snapshot.signatures) == 0


def test_signed_at_in_the_future_is_quarantined(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    """F3(d): verified_at (==now) must never be < signed_at (PG report.signature CHECK)."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="future-signed")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="future-signed-sign").data
    body = callback_body(session, version)
    body["signed_at"] = "2026-11-23T01:30:00Z"  # after the fixture clock's NOW (01:02:03)
    receipt = service.receive_analyst_signature(adapter, body, idempotency_key="future-signed-callback").data
    assert receipt["status"] == "quarantined"
    snapshot = m07["repository"].snapshot()
    assert snapshot.versions[(TENANT, version["id"])].status == "ready_to_sign"
    assert len(snapshot.signatures) == 0


def test_callback_with_wrong_provider_session_ref_is_quarantined(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    """F3(a): a callback must be bound to the exact provider session this request opened."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="session-ref")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="session-ref-sign").data
    body = {**callback_body(session, version), "provider_session_ref": "WRONG-SESSION-REF"}
    receipt = service.receive_analyst_signature(adapter, body, idempotency_key="session-ref-callback").data
    assert receipt["status"] == "quarantined"
    snapshot = m07["repository"].snapshot()
    assert snapshot.versions[(TENANT, version["id"])].status == "ready_to_sign"
    assert len(snapshot.signatures) == 0


def test_callback_impersonating_a_different_signer_subject_is_quarantined(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    """F3(b): qualifications.verify()'s returned subject_ref must match the callback's
    signer_subject_ref; previously the returned credential was discarded entirely."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="impersonator")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="impersonator-sign").data
    body = {**callback_body(session, version), "signer_subject_ref": "subject:IMPERSONATOR"}
    receipt = service.receive_analyst_signature(adapter, body, idempotency_key="impersonator-callback").data
    assert receipt["status"] == "quarantined"
    snapshot = m07["repository"].snapshot()
    assert snapshot.versions[(TENANT, version["id"])].status == "ready_to_sign"
    assert len(snapshot.signatures) == 0


def test_callback_for_superseded_source_candidate_is_quarantined_at_verification(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    """F3(c): a candidate can be superseded after request_analyst_signature already opened
    a provider session; the callback must still be blocked at verification time."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="superseded-cb")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="superseded-cb-sign").data
    consumer = M07EventConsumer(m07["repository"])
    envelope = incoming(
        "research.candidate_superseded", "M05", "research_candidate", CANDIDATE_ID, 1,
        {"candidate_id": CANDIDATE_ID, "replacement_candidate_id": "30000000-0000-0000-0000-000000000009",
         "previous_artifact_sha256": "a" * 64, "replacement_artifact_sha256": "b" * 64},
    )
    assert consumer.consume(envelope).result_code == "candidate_marked_superseded"
    receipt = service.receive_analyst_signature(adapter, callback_body(session, version), idempotency_key="superseded-cb-callback").data
    assert receipt["status"] == "quarantined"
    snapshot = m07["repository"].snapshot()
    assert len(snapshot.signatures) == 0


def test_callback_rejects_malformed_asset_and_event_uuids(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    """F3(e): event_id and the evidence asset ids are UUID-typed on the wire contract."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="malformed-ids")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="malformed-ids-sign").data
    body = {**callback_body(session, version), "event_id": "not-a-uuid"}
    with pytest.raises(ValidationError):
        service.receive_analyst_signature(adapter, body, idempotency_key="malformed-ids-callback")


def test_receive_analyst_signature_quarantine_triggers_alert_through_controller(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    """F6(b): the quarantine alert is dead code in http.py when raised only from a
    DomainError branch, because receive_analyst_signature always returns 202."""
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="alert")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="alert-sign").data
    controller = M07Controller(service)
    body = {**callback_body(session, version), "report_content_sha256": "f" * 64}
    response = controller.handle("receive_analyst_signature", adapter, body=body, headers={"Idempotency-Key": "alert-callback"})
    assert response.status == 202 and response.body["status"] == "quarantined"
    assert controller.telemetry.snapshot().alerts.get("receive_analyst_signature_quarantined") == 1


def test_production_composition_refuses_fake_adapters(m07):
    with pytest.raises(RuntimeError):
        ReportSignatureService(
            InMemoryRepository(), m07["signature_provider"], m07["qualifications"], m07["assets"],
            m07["review_cases"], m07["candidates"], environment="production",
        )


def test_minimized_draft_assist_is_untrusted_and_has_no_authority(m07):
    assistant_service = ReportDraftAssistService(m07["repository"], LocalDraftAssistant())
    staff = ActorContext(TENANT, "20000000-0000-0000-0000-000000000013", frozenset({"analyst"}), principal_type="staff")
    channel_texts = [
        {"channel": "legendflow", "body": "短文案"},
        {"channel": "line", "body": "這是一段故意拉長很多倍的通路文案內容，用來觸發長度離群檢查" * 10},
    ]
    run = assistant_service.suggest(staff, "a" * 64, channel_texts, ["40000000-0000-0000-0000-000000000002"])
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert len(run.findings) == 1
    assert m07["repository"].snapshot().automation_runs[(TENANT, run.id)] == run


def test_production_automation_refuses_local_fake(m07):
    with pytest.raises(RuntimeError):
        ReportDraftAssistService(m07["repository"], LocalDraftAssistant(), environment="production")
