from __future__ import annotations

import pytest

from modules.report_signature.domain import ConflictError, ValidationError
from workers.tasks.report_signature.consumer import M07EventConsumer

from conftest import (
    CANDIDATE_ID,
    SIGNED_FILE_SHA256,
    TENANT,
    callback_body,
    freeze_body,
    incoming,
    report_create_body,
    report_version_body,
    review_body,
    signature_request_body,
    take_to_ready_to_sign,
)


def test_uat_m07_001_missing_review_blocks_freeze_and_sign(m07, analyst, reviewer_pro):
    service = m07["service"]
    report = service.create_report(analyst, report_create_body(), idempotency_key="uat001-report").data
    version = service.create_report_version(analyst, report["id"], report_version_body(), expected_version=1, idempotency_key="uat001-version").data
    pro_review = service.record_report_review(reviewer_pro, version["id"], review_body("professional", version), expected_version=1, idempotency_key="uat001-review-pro").data
    with pytest.raises(ValidationError) as missing:
        service.freeze_report_version(analyst, version["id"], freeze_body(version, [pro_review["id"]]), expected_version=2, idempotency_key="uat001-freeze")
    assert missing.value.code == "missing_review"
    snapshot = m07["repository"].snapshot()
    stored_version = snapshot.versions[(TENANT, version["id"])]
    assert stored_version.status == "in_review"
    assert len(snapshot.signature_requests) == 0 and len(snapshot.outbox) == 0


def test_uat_m07_002_freeze_binds_report_recommendation_channel_and_audience_hash(m07, analyst, reviewer_pro, reviewer_compliance):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat002")
    version = result["version"]
    assert version["state"] == "ready_to_sign" and version["frozen_at"] is not None
    assert set(version["required_review_ids"]) == {result["pro_review"]["id"], result["compliance_review"]["id"]}
    assert version["channel_texts"][0]["recommendation_version_ids"]
    assert version["audience_policy"]["required_entitlement_state"] == "active"
    assert version["valid_until"] > version["valid_from"]
    stored_hash = m07["assets"].sha256_of(TENANT, version["bundle_manifest_asset_id"])
    assert stored_hash == version["bundle_manifest_sha256"]


def test_uat_m07_003_unqualified_signer_is_denied_without_provider_request(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat003")
    version = result["version"]
    unregistered_credential = "50000000-0000-0000-0000-000000000099"
    body = signature_request_body(version, version["required_review_ids"], credential_id=unregistered_credential)
    from modules.report_signature.domain import ForbiddenError

    with pytest.raises(ForbiddenError) as denied:
        m07["service"].request_analyst_signature(signer, version["id"], body, expected_version=4, idempotency_key="uat003-sign")
    assert denied.value.status == 403 and denied.value.code == "qualification_invalid"
    snapshot = m07["repository"].snapshot()
    assert len(snapshot.signature_requests) == 0
    assert snapshot.versions[(TENANT, version["id"])].status == "ready_to_sign"


def test_uat_m07_004_callback_verifies_signature_and_emits_exactly_one_event_before_m08(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat004")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(
        signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="uat004-sign",
    ).data
    receipt = service.receive_analyst_signature(adapter, callback_body(session, version), idempotency_key="uat004-callback").data
    assert receipt["status"] == "accepted"
    snapshot = m07["repository"].snapshot()
    signed_version = snapshot.versions[(TENANT, version["id"])]
    assert signed_version.status == "signed" and signed_version.signature_id is not None
    verified_events = [event for event in snapshot.outbox.values() if event.event_type == "report.signature_verified"]
    assert len(verified_events) == 1
    assert verified_events[0].payload["report_version_id"] == version["id"]
    assert len(snapshot.signatures) == 1


def test_uat_m07_005_post_signature_change_creates_new_version_and_keeps_v1_evidence(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat005")
    v1 = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, v1["id"], signature_request_body(v1, v1["required_review_ids"]), expected_version=4, idempotency_key="uat005-sign").data
    service.receive_analyst_signature(adapter, callback_body(session, v1), idempotency_key="uat005-callback")
    before = m07["repository"].snapshot()
    v1_before = before.versions[(TENANT, v1["id"])]
    assert v1_before.status == "signed"

    v2 = service.create_report_version(
        analyst, result["report"]["id"], report_version_body(supersedes_version_id=v1["id"]), expected_version=2, idempotency_key="uat005-v2",
    ).data
    after = m07["repository"].snapshot()
    v1_after = after.versions[(TENANT, v1["id"])]
    assert v1_after.status == "superseded"
    assert v1_after.report_content_sha256 == v1_before.report_content_sha256
    assert v1_after.bundle_manifest_sha256 == v1_before.bundle_manifest_sha256
    assert v1_after.signature_id == v1_before.signature_id  # v1 evidence is retained, not rewritten
    assert v2["state"] == "draft" and v2["version_number"] == 2


def test_uat_m07_006_content_hash_and_signed_file_hash_are_distinct(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat006")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="uat006-sign").data
    service.receive_analyst_signature(adapter, callback_body(session, version), idempotency_key="uat006-callback")
    signed = m07["service"].get_report_version(analyst, version["id"]).data
    assert signed["report_content_sha256"] != signed["signed_file_sha256"]
    assert signed["signed_file_sha256"] == SIGNED_FILE_SHA256
    signature = next(iter(m07["repository"].snapshot().signatures.values()))
    assert signature.content_sha256 == signed["report_content_sha256"]
    assert signature.signed_file_sha256 == signed["signed_file_sha256"]


def test_uat_m07_007_superseded_source_blocks_signing(m07, analyst, reviewer_pro, reviewer_compliance, signer):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat007")
    version = result["version"]
    consumer = M07EventConsumer(m07["repository"])
    envelope = incoming(
        "research.candidate_superseded", "M05", "research_candidate", CANDIDATE_ID, 1,
        {"candidate_id": CANDIDATE_ID, "replacement_candidate_id": "30000000-0000-0000-0000-000000000009",
         "previous_artifact_sha256": "a" * 64, "replacement_artifact_sha256": "b" * 64},
    )
    assert consumer.consume(envelope).result_code == "candidate_marked_superseded"
    body = signature_request_body(version, version["required_review_ids"])
    with pytest.raises(ConflictError) as blocked:
        m07["service"].request_analyst_signature(signer, version["id"], body, expected_version=4, idempotency_key="uat007-sign")
    assert blocked.value.code == "source_candidate_superseded"
    assert len(m07["repository"].snapshot().signature_requests) == 0


def test_uat_m07_008_duplicate_callback_does_not_republish(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat008")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="uat008-sign").data
    body = callback_body(session, version)
    first = service.receive_analyst_signature(adapter, body, idempotency_key="uat008-callback-1").data
    second = service.receive_analyst_signature(adapter, {**body, "event_id": "80000000-0000-0000-0000-000000000002"}, idempotency_key="uat008-callback-2").data
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["receipt_id"] == first["receipt_id"]
    snapshot = m07["repository"].snapshot()
    assert len(snapshot.signatures) == 1
    assert len([event for event in snapshot.outbox.values() if event.event_type == "report.signature_verified"]) == 1


def test_uat_m07_009_tampered_payload_is_quarantined_and_frozen_package_survives(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat009")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="uat009-sign").data
    tampered = callback_body(session, version, report_content_sha256="f" * 64)
    receipt = service.receive_analyst_signature(adapter, tampered, idempotency_key="uat009-callback").data
    assert receipt["status"] == "quarantined"
    snapshot = m07["repository"].snapshot()
    stored_version = snapshot.versions[(TENANT, version["id"])]
    assert stored_version.status == "ready_to_sign"  # original frozen package is retained and signable again
    assert stored_version.report_content_sha256 == version["report_content_sha256"]
    assert len(snapshot.signatures) == 0
    assert len([event for event in snapshot.outbox.values() if event.event_type == "report.signature_verified"]) == 0


def test_uat_m07_010_revocation_keeps_history_and_notifies_downstream(m07, analyst, reviewer_pro, reviewer_compliance, signer, adapter, supervisor):
    result = take_to_ready_to_sign(m07, analyst, reviewer_pro, reviewer_compliance, key_prefix="uat010")
    version = result["version"]
    service = m07["service"]
    session = service.request_analyst_signature(signer, version["id"], signature_request_body(version, version["required_review_ids"]), expected_version=4, idempotency_key="uat010-sign").data
    service.receive_analyst_signature(adapter, callback_body(session, version), idempotency_key="uat010-callback")
    signature_id = m07["repository"].snapshot().versions[(TENANT, version["id"])].signature_id
    revocation = service.revoke_signature(
        supervisor, signature_id,
        {"reason_code": "compliance_correction", "evidence_asset_id": "40000000-0000-0000-0000-000000000006", "effective_at": "2026-11-24T00:00:00Z", "notify_recipients": True},
        expected_version=1, idempotency_key="uat010-revoke",
    ).data
    snapshot = m07["repository"].snapshot()
    assert signature_id in {row.id for row in snapshot.signatures.values()}  # never deleted
    assert len(snapshot.revocations) == 1
    assert revocation["signature_id"] == signature_id
    revoked_events = [event for event in snapshot.outbox.values() if event.event_type == "report.signature_revoked"]
    assert len(revoked_events) == 1
    assert revoked_events[0].payload["revocation_id"] == revocation["id"]
    assert revocation["publication_withdrawal_event_id"] == revoked_events[0].event_id


def test_uat_m07_012_repeated_reviews_across_versions_retain_full_history(m07, analyst, reviewer_pro):
    service = m07["service"]
    report = service.create_report(analyst, report_create_body(), idempotency_key="uat012-report").data
    v1 = service.create_report_version(analyst, report["id"], report_version_body(), expected_version=1, idempotency_key="uat012-v1").data
    changes_requested_review = service.record_report_review(
        reviewer_pro, v1["id"], review_body("professional", v1, decision="request_changes"), expected_version=1, idempotency_key="uat012-review-v1",
    ).data
    v2 = service.create_report_version(analyst, report["id"], report_version_body(supersedes_version_id=None), expected_version=2, idempotency_key="uat012-v2").data
    v2_review = service.record_report_review(
        reviewer_pro, v2["id"], review_body("professional", v2), expected_version=1, idempotency_key="uat012-review-v2",
    ).data
    snapshot = m07["repository"].snapshot()
    assert snapshot.reviews[(TENANT, changes_requested_review["id"])].report_version_id == v1["id"]
    assert snapshot.reviews[(TENANT, v2_review["id"])].report_version_id == v2["id"]
    assert {changes_requested_review["id"], v2_review["id"]}.issubset({row.id for row in snapshot.reviews.values()})
    assert snapshot.reviews[(TENANT, changes_requested_review["id"])].reviewer_id == snapshot.reviews[(TENANT, v2_review["id"])].reviewer_id
