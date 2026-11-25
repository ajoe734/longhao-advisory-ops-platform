from __future__ import annotations

from datetime import timedelta

import pytest

from modules.publication.domain import ConflictError, ForbiddenError, StaleVersionError, ValidationError
from workers.tasks.publication.consumer import M08EventConsumer
from workers.tasks.publication.tasks import PublicationWorker

from conftest import (
    EVIDENCE_WITHDRAW,
    NOW,
    RESEARCH_ORIGIN_REF,
    TENANT,
    create_series,
    default_candidates,
    register_bundle_for,
    set_audience_for_series,
    signature_event,
    version_body,
)


REPORT_A = "80000000-0000-0000-0000-000000000001"
REPORT_B = "80000000-0000-0000-0000-000000000002"
SIGNATURE_A = "90000000-0000-0000-0000-000000000001"
SIGNATURE_B = "90000000-0000-0000-0000-000000000002"


def _publish(m08, series_id, version_id, report_version_id, signature_id, *, candidates=None, sequence=1):
    register_bundle_for(m08, version_id, report_version_id)
    set_audience_for_series(m08, series_id, candidates if candidates is not None else default_candidates(1))
    consumer = M08EventConsumer(m08["service"])
    consumed = consumer.consume(signature_event(report_version_id, signature_id, sequence=sequence))
    worker = PublicationWorker(m08["service"])
    result = worker.run_once("worker-a")
    return consumed, result


def test_uat_m08_001_signature_verified_publishes_without_a_second_step(m08, analyst):
    series = create_series(m08["service"], analyst, "series-001").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-001",
    ).data
    consumed, result = _publish(m08, series["id"], version["id"], REPORT_A, SIGNATURE_A)
    assert consumed.status == "applied"
    assert result.processed == 1
    page = m08["service"].list_publications(analyst).data
    assert len(page["items"]) == 1
    assert page["items"][0]["state"] == "published"
    outbox = m08["repository"].snapshot().outbox
    assert any(event.event_type == "publication.created" for event in outbox.values())


def test_uat_m08_002_unsigned_draft_never_appears_in_publications(m08, analyst):
    series = create_series(m08["service"], analyst, "series-002").data
    m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-002",
    )
    page = m08["service"].list_publications(analyst).data
    assert page["items"] == []


def test_uat_m08_003_entry_and_exit_each_cite_their_own_report(m08, analyst):
    series = create_series(m08["service"], analyst, "series-003").data
    entry = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A, action="entry"), expected_version=1, idempotency_key="version-003a",
    ).data
    exit_version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_B, action="exit", supersedes=entry["id"]),
        expected_version=2, idempotency_key="version-003b",
    ).data
    _publish(m08, series["id"], entry["id"], REPORT_A, SIGNATURE_A)
    _publish(m08, series["id"], exit_version["id"], REPORT_B, SIGNATURE_B)
    snapshot = m08["repository"].snapshot()
    entry_pub = snapshot.publications[(TENANT, snapshot.publications_by_version[(TENANT, entry["id"])])]
    exit_pub = snapshot.publications[(TENANT, snapshot.publications_by_version[(TENANT, exit_version["id"])])]
    assert entry_pub.report_version_id == REPORT_A
    assert exit_pub.report_version_id == REPORT_B


def test_uat_m08_004_one_signed_report_supports_two_messages(m08, analyst):
    series = create_series(m08["service"], analyst, "series-004").data
    first = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A, action="entry"), expected_version=1, idempotency_key="version-004a",
    ).data
    second = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A, action="update", supersedes=first["id"]),
        expected_version=2, idempotency_key="version-004b",
    ).data
    register_bundle_for(m08, first["id"], REPORT_A)
    register_bundle_for(m08, second["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    consumer = M08EventConsumer(m08["service"])
    consumer.consume(signature_event(REPORT_A, SIGNATURE_A))
    worker = PublicationWorker(m08["service"])
    result = worker.run_once("worker-a", limit=10)
    assert result.processed == 2
    snapshot = m08["repository"].snapshot()
    first_pub = snapshot.publications[(TENANT, snapshot.publications_by_version[(TENANT, first["id"])])]
    second_pub = snapshot.publications[(TENANT, snapshot.publications_by_version[(TENANT, second["id"])])]
    assert first_pub.report_version_id == second_pub.report_version_id == REPORT_A
    assert first_pub.id != second_pub.id


def test_uat_m08_005_post_signature_content_swap_is_blocked(m08, analyst):
    series = create_series(m08["service"], analyst, "series-005").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-005",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A, tamper=True)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    consumer = M08EventConsumer(m08["service"])
    consumer.consume(signature_event(REPORT_A, SIGNATURE_A))
    worker = PublicationWorker(m08["service"])
    result = worker.run_once("worker-a")
    assert result.dead_lettered == 1
    snapshot = m08["repository"].snapshot()
    assert (TENANT, version["id"]) not in snapshot.publications_by_version
    rejected = [row for row in snapshot.audit_events if row.result == "rejected" and row.reason_code == "bundle_hash_mismatch"]
    assert rejected


def test_uat_m08_006_audience_snapshot_excludes_ineligible_candidates(m08, analyst):
    series = create_series(m08["service"], analyst, "series-006").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-006",
    ).data
    from modules.publication.ports import AudienceCandidate

    candidates = (
        AudienceCandidate("50000000-0000-0000-0000-000000000001", "60000000-0000-0000-0000-000000000001", "70000000-0000-0000-0000-000000000001", 1, True, "active_entitlement"),
        AudienceCandidate("50000000-0000-0000-0000-000000000002", "60000000-0000-0000-0000-000000000002", "70000000-0000-0000-0000-000000000002", 1, False, "entitlement_expired"),
        AudienceCandidate("50000000-0000-0000-0000-000000000003", "60000000-0000-0000-0000-000000000003", "70000000-0000-0000-0000-000000000003", 1, False, "entitlement_pending_review"),
    )
    _publish(m08, series["id"], version["id"], REPORT_A, SIGNATURE_A, candidates=candidates)
    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    publication = snapshot.publications[(TENANT, publication_id)]
    assert publication.recipient_count == 1
    stored_snapshot = snapshot.snapshots[(TENANT, publication.audience_snapshot_id)]
    assert stored_snapshot.recipient_count == 1
    recipients = [row for (tenant, _), row in snapshot.recipients.items() if tenant == TENANT and row.publication_id == publication_id]
    assert len(recipients) == 1
    assert recipients[0].customer_id == "50000000-0000-0000-0000-000000000001"


def test_uat_m08_007_revocation_after_snapshot_does_not_rewrite_history(m08, analyst):
    series = create_series(m08["service"], analyst, "series-007").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-007",
    ).data
    _publish(m08, series["id"], version["id"], REPORT_A, SIGNATURE_A)
    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    recipient = next(row for (tenant, _), row in snapshot.recipients.items() if tenant == TENANT and row.publication_id == publication_id)

    consumer = M08EventConsumer(m08["service"])
    from modules.publication.domain import canonical_hash, new_id

    payload = {
        "entitlement_id": recipient.entitlement_id, "customer_id": recipient.customer_id,
        "contract_id": "70000000-0000-0000-0000-000000000001", "state": "revoked",
        "decision_id": new_id(), "valid_until": (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
    }
    envelope = {
        "event_id": new_id(), "event_type": "entitlement.changed", "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": "M02", "aggregate_type": "entitlement", "aggregate_id": recipient.entitlement_id,
        "aggregate_version": 2, "sequence_no": 1, "occurred_at": NOW.isoformat().replace("+00:00", "Z"),
        "emitted_at": NOW.isoformat().replace("+00:00", "Z"), "correlation_id": new_id(), "causation_id": None,
        "idempotency_key": "entitlement-revoked-001", "classification": "customer_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }
    consumer.consume(envelope)
    after = m08["repository"].snapshot()
    unchanged_recipient = next(row for (tenant, _), row in after.recipients.items() if tenant == TENANT and row.publication_id == publication_id)
    assert unchanged_recipient.entitlement_version == recipient.entitlement_version
    live_state = after.entitlements[(TENANT, recipient.entitlement_id)]
    assert live_state.state == "revoked"


def test_uat_m08_008_duplicate_signature_event_publishes_once(m08, analyst):
    series = create_series(m08["service"], analyst, "series-008").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-008",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    consumer = M08EventConsumer(m08["service"])
    event = signature_event(REPORT_A, SIGNATURE_A)
    first = consumer.consume(event)
    second = consumer.consume(event)
    third = consumer.consume(event)
    assert first.status == "applied"
    assert second.status == "duplicate" and third.status == "duplicate"
    worker = PublicationWorker(m08["service"])
    worker.run_once("worker-a")
    worker.run_once("worker-a")
    snapshot = m08["repository"].snapshot()
    publications = [row for (tenant, _), row in snapshot.publications.items() if tenant == TENANT]
    assert len(publications) == 1


def test_uat_m08_009_withdrawal_preserves_published_evidence(m08, analyst, analyst_reviewer):
    series = create_series(m08["service"], analyst, "series-009").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-009",
    ).data
    _publish(m08, series["id"], version["id"], REPORT_A, SIGNATURE_A)
    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    before = snapshot.publications[(TENANT, publication_id)]
    withdrawn = m08["service"].withdraw_publication(
        analyst_reviewer, publication_id,
        {"reason_code": "compliance_recall", "evidence_asset_id": EVIDENCE_WITHDRAW, "notify_recipients": True, "notice_template_version": "notice-v1"},
        expected_version=1, idempotency_key="withdraw-009",
    ).data
    after = m08["repository"].snapshot()
    recipients_after = [row for (tenant, _), row in after.recipients.items() if tenant == TENANT and row.publication_id == publication_id]
    assert withdrawn["state"] == "withdrawn"
    assert withdrawn["recipient_count"] == before.recipient_count
    assert len(recipients_after) == before.recipient_count
    assert any(event.event_type == "publication.withdrawn" for event in after.outbox.values())


def test_uat_m08_010_correction_links_original_and_replacement(m08, analyst, analyst_reviewer):
    original_series = create_series(m08["service"], analyst, "series-010a").data
    original_version = m08["service"].create_recommendation_version(
        analyst, original_series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-010a",
    ).data
    _publish(m08, original_series["id"], original_version["id"], REPORT_A, SIGNATURE_A)

    replacement_series = create_series(m08["service"], analyst, "series-010b", strategy="momentum-core-v2").data
    replacement_version = m08["service"].create_recommendation_version(
        analyst, replacement_series["id"], version_body(REPORT_B), expected_version=1, idempotency_key="version-010b",
    ).data
    _publish(m08, replacement_series["id"], replacement_version["id"], REPORT_B, SIGNATURE_B)

    snapshot = m08["repository"].snapshot()
    original_id = snapshot.publications_by_version[(TENANT, original_version["id"])]
    replacement_id = snapshot.publications_by_version[(TENANT, replacement_version["id"])]
    corrected = m08["service"].link_publication_correction(
        analyst_reviewer, original_id,
        {"replacement_publication_id": replacement_id, "replacement_report_version_id": REPORT_B, "reason_code": "material_correction"},
        expected_version=1, idempotency_key="correction-010",
    ).data
    assert corrected["state"] == "corrected"
    assert corrected["correction_publication_id"] == replacement_id
    after = m08["repository"].snapshot()
    assert any(event.event_type == "publication.corrected" for event in after.outbox.values())
    replacement_after = after.publications[(TENANT, replacement_id)]
    assert replacement_after.state == "published"


def test_uat_m08_011_expired_redrive_is_blocked_not_resent(m08, analyst):
    series = create_series(m08["service"], analyst, "series-011").data
    body = version_body(REPORT_A)
    body["valid_until"] = (NOW + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], body, expected_version=1, idempotency_key="version-011",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    m08["entitlements"].failure_mode = "timeout"
    consumer = M08EventConsumer(m08["service"])
    consumer.consume(signature_event(REPORT_A, SIGNATURE_A))
    job_id = next(row.id for (tenant, _), row in m08["repository"].snapshot().jobs.items() if tenant == TENANT)
    worker = PublicationWorker(m08["service"])
    # Drive the bounded retry directly (lease cooldown is a scheduling concern
    # already covered by the generic worker-lease test, not this business rule).
    for _ in range(6):
        m08["service"].process_publish_job(job_id, TENANT, "worker-a")
    snapshot = m08["repository"].snapshot()
    job = snapshot.jobs[(TENANT, job_id)]
    assert job.status == "dead_letter"

    m08["clock"].advance(60)
    with pytest.raises(ValueError):
        worker.redrive(TENANT, job.id, "   ")
    outcome = worker.redrive(TENANT, job.id, "on_call_requested_resend_after_incident_1234")
    assert outcome.status == "blocked" and outcome.result_code == "expired_blocked"
    assert (TENANT, version["id"]) not in m08["repository"].snapshot().publications_by_version


def test_uat_m08_012_publication_timing_is_separate_from_source_candidate_timing(m08, analyst):
    series = create_series(m08["service"], analyst, "series-012").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-012",
    ).data
    created_at = version["created_at"]
    m08["clock"].advance(3 * 60 * 60)
    _publish(m08, series["id"], version["id"], REPORT_A, SIGNATURE_A)
    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    publication = snapshot.publications[(TENANT, publication_id)]
    assert created_at != publication.published_at.isoformat()
    assert publication.published_at - snapshot.versions[(TENANT, version["id"])].created_at >= timedelta(hours=3)


def test_idempotency_key_reuse_with_different_payload_is_409(m08, analyst):
    create_series(m08["service"], analyst, "same-key-0001")
    with pytest.raises(ConflictError) as conflict:
        m08["service"].create_recommendation_series(
            analyst, {"instrument_ref": {"market": "TWSE", "instrument_code": "2317", "instrument_type": "equity"}, "strategy_scope_ref": "momentum-core-v1", "research_origin_ref": RESEARCH_ORIGIN_REF},
            idempotency_key="same-key-0001",
        )
    assert conflict.value.code == "idempotency_conflict"


def test_stale_version_is_rejected(m08, analyst):
    series = create_series(m08["service"], analyst, "series-stale").data
    m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="version-stale-1",
    )
    with pytest.raises(StaleVersionError) as stale:
        m08["service"].create_recommendation_version(
            analyst, series["id"], version_body(REPORT_B), expected_version=1, idempotency_key="version-stale-2",
        )
    assert stale.value.status == 412 and stale.value.current_version == 2


def test_non_analyst_cannot_create_series(m08, service_agent):
    with pytest.raises(ForbiddenError):
        create_series(m08["service"], service_agent, "series-denied")


def test_entry_cannot_set_supersedes_and_other_actions_require_it(m08, analyst):
    series = create_series(m08["service"], analyst, "series-validation").data
    with pytest.raises(ValidationError):
        m08["service"].create_recommendation_version(
            analyst, series["id"], version_body(REPORT_A, action="entry", supersedes="40000000-0000-0000-0000-000000000099"),
            expected_version=1, idempotency_key="version-validation-1",
        )
    with pytest.raises(ValidationError):
        m08["service"].create_recommendation_version(
            analyst, series["id"], version_body(REPORT_A, action="update"),
            expected_version=1, idempotency_key="version-validation-2",
        )
