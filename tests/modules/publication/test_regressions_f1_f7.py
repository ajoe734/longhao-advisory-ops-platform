from __future__ import annotations

from datetime import timedelta

import pytest

from modules.publication.domain import (
    ConflictError,
    ForbiddenError,
    StaleVersionError,
    ValidationError,
    canonical_hash,
    new_id,
    wire_time,
)
from modules.publication.ports import AudienceCandidate
from workers.tasks.publication.consumer import M08EventConsumer
from workers.tasks.publication.tasks import PublicationWorker

from conftest import (
    NOW,
    RESEARCH_ORIGIN_REF,
    TENANT,
    create_series,
    default_candidates,
    register_bundle_for,
    revocation_event,
    set_audience_for_series,
    signature_event,
    version_body,
)


REPORT_A = "80000000-0000-0000-0000-000000000001"
REPORT_B = "80000000-0000-0000-0000-000000000002"
SIGNATURE_A = "90000000-0000-0000-0000-000000000001"
SIGNATURE_B = "90000000-0000-0000-0000-000000000002"


# ---- F1: correlation/causation/event_id wiring ----------------------------


def test_f1_publication_and_audit_use_the_triggering_event_not_the_report_id(m08, analyst):
    series = create_series(m08["service"], analyst, "f1-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f1-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    event = signature_event(REPORT_A, SIGNATURE_A)
    M08EventConsumer(m08["service"]).consume(event)
    PublicationWorker(m08["service"]).run_once("worker-f1")

    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    publication = snapshot.publications[(TENANT, publication_id)]
    assert publication.created_from_signature_event_id == event["event_id"]
    assert publication.created_from_signature_event_id != REPORT_A

    publish_audit = next(
        row for row in snapshot.audit_events
        if row.action == "m08.publish_recommendation_version" and row.aggregate_id == publication.id
    )
    assert publish_audit.correlation_id == event["correlation_id"]

    created = next(row for row in snapshot.outbox.values() if row.event_type == "publication.created")
    assert created.correlation_id == event["correlation_id"]
    assert created.causation_id == event["event_id"]


# ---- F2: bundle cross-check, dependency failures, primary-report binding --


def test_f2_signed_event_hash_mismatch_against_registry_is_dead_lettered(m08, analyst):
    series = create_series(m08["service"], analyst, "f2a-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f2a-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A, bundle_manifest_sha256="e" * 64))
    result = PublicationWorker(m08["service"]).run_once("worker-f2a")
    assert result.dead_lettered == 1
    snapshot = m08["repository"].snapshot()
    assert (TENANT, version["id"]) not in snapshot.publications_by_version
    assert [row for row in snapshot.audit_events if row.result == "rejected" and row.reason_code == "bundle_hash_mismatch"]


def test_f2_bundle_registry_timeout_retries_without_crashing_worker(m08, analyst):
    series = create_series(m08["service"], analyst, "f2b-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f2b-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    m08["report_bundles"].fail(TENANT, REPORT_A, version["id"], "timeout")
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    worker = PublicationWorker(m08["service"])

    first = worker.run_once("worker-f2b")
    assert first.retried == 1
    job = next(row for (tenant, _), row in m08["repository"].snapshot().jobs.items() if tenant == TENANT)
    assert job.status == "retry"
    assert job.lease_owner is None and job.lease_until is None

    m08["report_bundles"]._failures.clear()
    m08["clock"].advance(10)
    second = worker.run_once("worker-f2b")
    assert second.processed == 1


def test_f2_unregistered_bundle_is_dead_lettered_not_crashed(m08, analyst):
    series = create_series(m08["service"], analyst, "f2c-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f2c-version",
    ).data
    set_audience_for_series(m08, series["id"], default_candidates(1))
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    result = PublicationWorker(m08["service"]).run_once("worker-f2c")
    assert result.dead_lettered == 1
    job = next(row for (tenant, _), row in m08["repository"].snapshot().jobs.items() if tenant == TENANT)
    assert job.status == "dead_letter" and job.result_code == "bundle_not_registered"


def test_f2_multi_report_publication_binds_to_primary_report_not_last(m08, analyst):
    series = create_series(m08["service"], analyst, "f2d-series").data
    body = version_body(REPORT_A)
    body["supporting_report_version_ids"] = [REPORT_A, REPORT_B]
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], body, expected_version=1, idempotency_key="f2d-version",
    ).data
    manifest_a = register_bundle_for(m08, version["id"], REPORT_A)
    manifest_b = register_bundle_for(m08, version["id"], REPORT_B)
    assert manifest_a != manifest_b
    set_audience_for_series(m08, series["id"], default_candidates(1))
    consumer = M08EventConsumer(m08["service"])
    worker = PublicationWorker(m08["service"])

    consumer.consume(signature_event(REPORT_A, SIGNATURE_A))
    first_round = worker.run_once("worker-f2d")
    assert (first_round.processed, first_round.retried, first_round.dead_lettered) == (0, 0, 0)

    consumer.consume(signature_event(REPORT_B, SIGNATURE_B))
    second_round = worker.run_once("worker-f2d")
    assert second_round.processed == 1

    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    publication = snapshot.publications[(TENANT, publication_id)]
    assert publication.report_version_id == REPORT_A
    assert publication.signature_id == SIGNATURE_A
    assert publication.bundle_manifest_sha256 == manifest_a


# ---- F3: out-of-order revocation -------------------------------------------


def test_f3_revocation_arriving_before_verification_still_blocks_publication(m08, analyst):
    series = create_series(m08["service"], analyst, "f3-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f3-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    consumer = M08EventConsumer(m08["service"])

    assert consumer.consume(revocation_event(REPORT_A, SIGNATURE_A)).status == "applied"
    assert consumer.consume(signature_event(REPORT_A, SIGNATURE_A)).status == "applied"

    result = PublicationWorker(m08["service"]).run_once("worker-f3")
    assert result.dead_lettered == 1
    snapshot = m08["repository"].snapshot()
    assert (TENANT, version["id"]) not in snapshot.publications_by_version
    assert not [row for (tenant, _), row in snapshot.publications.items() if tenant == TENANT]
    assert [row for row in snapshot.audit_events if row.result == "rejected" and row.reason_code == "supporting_report_signature_revoked"]


# ---- F4: worker lease reclaim, worker_id validation, valid_until clamp -----


def test_f4_expired_leased_job_is_reclaimed_by_another_worker(m08, analyst):
    series = create_series(m08["service"], analyst, "f4a-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f4a-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    worker = PublicationWorker(m08["service"])

    leased_a = worker.lease("worker-a")
    assert len(leased_a) == 1
    assert worker.lease("worker-b") == []

    m08["clock"].advance(31)
    leased_b = worker.lease("worker-b")
    assert len(leased_b) == 1
    job = m08["repository"].snapshot().jobs[leased_b[0]]
    assert job.lease_owner == "worker-b"


def test_f4_mismatched_worker_id_cannot_process_a_held_lease(m08, analyst):
    series = create_series(m08["service"], analyst, "f4b-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f4b-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    worker = PublicationWorker(m08["service"])
    tenant_id, job_id = worker.lease("worker-a")[0]

    with pytest.raises(ConflictError) as exc:
        m08["service"].process_publish_job(job_id, tenant_id, "worker-zombie")
    assert exc.value.code == "worker_lease_lost"


def test_f4_publication_valid_until_is_clamped_to_the_earliest_source(m08, analyst):
    series = create_series(m08["service"], analyst, "f4c-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f4c-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    report_valid_until = NOW + timedelta(hours=2)
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A, valid_until=report_valid_until))
    PublicationWorker(m08["service"]).run_once("worker-f4c")

    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    publication = snapshot.publications[(TENANT, publication_id)]
    assert publication.valid_until == report_valid_until
    assert publication.valid_until < snapshot.versions[(TENANT, version["id"])].valid_until


# ---- F5: contracts, idempotency replay, audit, side-effect ordering -------


def test_f5_research_origin_ref_requires_kind(m08, analyst):
    with pytest.raises(ValidationError) as exc:
        m08["service"].create_recommendation_series(
            analyst,
            {
                "instrument_ref": {"market": "TWSE", "instrument_code": "2330", "instrument_type": "equity"},
                "strategy_scope_ref": "f5-kind-missing",
                "research_origin_ref": {"system": "pantheon", "external_id": "candidate-f5"},
            },
            idempotency_key="f5-kind-missing",
        )
    assert exc.value.field_name == "research_origin_ref"


def test_f5_non_string_strategy_scope_ref_is_rejected_not_stringified(m08, analyst):
    with pytest.raises(ValidationError):
        m08["service"].create_recommendation_series(
            analyst,
            {
                "instrument_ref": {"market": "TWSE", "instrument_code": "2330", "instrument_type": "equity"},
                "strategy_scope_ref": 12345,
                "research_origin_ref": RESEARCH_ORIGIN_REF,
            },
            idempotency_key="f5-non-string",
        )


def test_f5_idempotent_replay_keeps_the_original_etag(m08, analyst):
    body = {
        "instrument_ref": {"market": "TWSE", "instrument_code": "2330", "instrument_type": "equity"},
        "strategy_scope_ref": "f5-etag",
        "research_origin_ref": RESEARCH_ORIGIN_REF,
    }
    first = m08["service"].create_recommendation_series(analyst, body, idempotency_key="f5-etag-key")
    replay = m08["service"].create_recommendation_series(analyst, body, idempotency_key="f5-etag-key")
    assert replay.idempotency_replayed is True
    assert replay.etag == first.etag
    assert replay.etag is not None


def test_f5_content_asset_is_not_written_on_a_rejected_version_create(m08, analyst):
    series = create_series(m08["service"], analyst, "f5-content-series").data
    before = len(m08["content_assets"]._assets)
    with pytest.raises(StaleVersionError):
        m08["service"].create_recommendation_version(
            analyst, series["id"], version_body(REPORT_A), expected_version=99, idempotency_key="f5-content-key",
        )
    assert len(m08["content_assets"]._assets) == before


def test_f5_forbidden_authorize_is_audited(m08, service_agent):
    with pytest.raises(ForbiddenError):
        create_series(m08["service"], service_agent, "f5-forbidden-series")
    snapshot = m08["repository"].snapshot()
    denied = [
        row for row in snapshot.audit_events
        if row.action == "m08.create_recommendation_series" and row.result == "rejected" and row.reason_code == "forbidden"
    ]
    assert denied


def test_f5_entitlement_projection_uses_envelope_aggregate_version(m08):
    payload = {
        "entitlement_id": "60000000-0000-0000-0000-000000000099",
        "customer_id": "50000000-0000-0000-0000-000000000099",
        "contract_id": "70000000-0000-0000-0000-000000000099",
        "state": "active",
        "decision_id": new_id(),
        "valid_until": wire_time(NOW + timedelta(days=30)),
    }
    envelope = {
        "event_id": new_id(), "event_type": "entitlement.changed", "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": "M02", "aggregate_type": "entitlement", "aggregate_id": payload["entitlement_id"],
        "aggregate_version": 7, "sequence_no": 1, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": new_id(), "causation_id": None, "idempotency_key": "f5-entitlement-001",
        "classification": "customer_confidential", "payload_sha256": canonical_hash(payload), "payload": payload,
    }
    M08EventConsumer(m08["service"]).consume(envelope)
    record = m08["repository"].snapshot().entitlements[(TENANT, payload["entitlement_id"])]
    assert record.version == 7


def test_f5_audience_snapshot_records_rule_version_and_excluded_reason_summary(m08, analyst):
    series = create_series(m08["service"], analyst, "f5-audience-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="f5-audience-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    candidates = (
        AudienceCandidate("50000000-0000-0000-0000-000000000001", "60000000-0000-0000-0000-000000000001", "70000000-0000-0000-0000-000000000001", 1, True, "active_entitlement"),
        AudienceCandidate("50000000-0000-0000-0000-000000000002", "60000000-0000-0000-0000-000000000002", "70000000-0000-0000-0000-000000000002", 1, False, "entitlement_expired"),
        AudienceCandidate("50000000-0000-0000-0000-000000000003", "60000000-0000-0000-0000-000000000003", "70000000-0000-0000-0000-000000000003", 1, False, "entitlement_expired"),
    )
    set_audience_for_series(m08, series["id"], candidates)
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    PublicationWorker(m08["service"]).run_once("worker-f5f")

    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    publication = snapshot.publications[(TENANT, publication_id)]
    stored_snapshot = snapshot.snapshots[(TENANT, publication.audience_snapshot_id)]
    assert stored_snapshot.rule_version == "entitlement_audience_resolution:v1"
    assert stored_snapshot.rule_version != REPORT_A
    assert stored_snapshot.reason_summary == {"entitlement_expired": 2}
