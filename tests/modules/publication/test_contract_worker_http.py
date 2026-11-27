from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from modules.publication.domain import ConflictError, NotFoundError, ValidationError, canonical_hash, new_id, wire_time
from modules.publication.http import M08Controller, ROUTES
from modules.publication.ports import AudienceCandidate
from workers.tasks.publication.consumer import M08EventConsumer
from workers.tasks.publication.tasks import LocalEventBus, OutboxPublisher, PublicationWorker

from conftest import (
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


ROOT = Path(__file__).resolve().parents[3]
REPORT_A = "80000000-0000-0000-0000-000000000001"
SIGNATURE_A = "90000000-0000-0000-0000-000000000001"


def _publish_one(m08, analyst, series_key: str = "contract-series"):
    series = create_series(m08["service"], analyst, series_key).data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key=f"{series_key}-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    PublicationWorker(m08["service"]).run_once("worker-a")
    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    return series, version, publication_id


def test_all_6_openapi_operations_have_http_routes():
    assert {operation for _, _, operation in ROUTES} == {
        "create_recommendation_series", "create_recommendation_version",
        "list_publications", "get_publication", "withdraw_publication", "link_publication_correction",
    }


def test_controller_returns_etag_problem_and_replay_headers(m08, analyst):
    controller = M08Controller(m08["service"])
    body = {"instrument_ref": {"market": "TWSE", "instrument_code": "2454", "instrument_type": "equity"}, "strategy_scope_ref": "momentum-core-v9", "research_origin_ref": RESEARCH_ORIGIN_REF}
    first = controller.handle("create_recommendation_series", analyst, body=body, headers={"Idempotency-Key": "controller-key-001"})
    replay = controller.handle("create_recommendation_series", analyst, body=body, headers={"Idempotency-Key": "controller-key-001"})
    stale = controller.handle(
        "create_recommendation_version", analyst, path={"series_id": first.body["id"]},
        body=version_body(REPORT_A), headers={"Idempotency-Key": "controller-key-002", "If-Match": '"9"'},
    )
    assert first.status == 201 and first.headers["ETag"] == '"1"'
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert stale.status == 412 and stale.body["current_version"] == 1
    assert stale.headers["Content-Type"] == "application/problem+json"


def test_m08_producer_envelopes_validate_against_shared_json_schemas(m08, analyst, analyst_reviewer):
    _series, version, publication_id = _publish_one(m08, analyst)
    m08["evidence"].add(TENANT, "30000000-0000-0000-0000-000000000099")
    m08["service"].withdraw_publication(
        analyst_reviewer, publication_id,
        {"reason_code": "compliance_recall", "evidence_asset_id": "30000000-0000-0000-0000-000000000099", "notify_recipients": True, "notice_template_version": "notice-v1"},
        expected_version=1, idempotency_key="withdraw-contract-001",
    )
    snapshot = m08["repository"].snapshot()
    assert {event.event_type for event in snapshot.outbox.values()} == {"publication.created", "publication.withdrawn"}
    for event in snapshot.outbox.values():
        schema_path = ROOT / "events" / "schemas" / f"{event.event_type}.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event.envelope()))
        assert errors == [], [error.message for error in errors]


def test_wire_responses_validate_against_openapi_1_0(m08, analyst, analyst_reviewer):
    specification = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))

    def validate(name: str, value: dict):
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": specification["components"],
        }
        errors = list(Draft202012Validator(root_schema, format_checker=FormatChecker()).iter_errors(value))
        assert errors == [], [f"{name}: {error.json_path}: {error.message}" for error in errors]

    series = create_series(m08["service"], analyst, "wire-series").data
    validate("RecommendationSeries", series)
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="wire-version",
    ).data
    validate("RecommendationVersion", version)
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    PublicationWorker(m08["service"]).run_once("worker-a")
    snapshot = m08["repository"].snapshot()
    publication_id = snapshot.publications_by_version[(TENANT, version["id"])]
    publication = m08["service"].get_publication(analyst, publication_id).data
    validate("Publication", publication)
    validate("PublicationPage", m08["service"].list_publications(analyst).data)

    m08["evidence"].add(TENANT, "30000000-0000-0000-0000-000000000098")
    withdrawn = m08["service"].withdraw_publication(
        analyst_reviewer, publication_id,
        {"reason_code": "wire_contract_test", "evidence_asset_id": "30000000-0000-0000-0000-000000000098", "notify_recipients": False, "notice_template_version": "notice-v1"},
        expected_version=1, idempotency_key="wire-withdraw-001",
    ).data
    validate("Publication", withdrawn)

    problem = M08Controller(m08["service"]).handle(
        "withdraw_publication", analyst_reviewer, path={"publication_id": publication_id},
        body={"reason_code": "x", "evidence_asset_id": "30000000-0000-0000-0000-000000000098", "notify_recipients": False, "notice_template_version": "notice-v1"},
        headers={"Idempotency-Key": "wire-withdraw-002", "If-Match": '"1"'},
    )
    assert problem.status == 412
    validate("Problem", problem.body)


def incoming(event_type: str, producer: str, aggregate_type: str, aggregate_id: str, sequence: int, payload: dict):
    return {
        "event_id": str(uuid4()), "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
        "aggregate_version": sequence, "sequence_no": sequence, "occurred_at": wire_time(NOW), "emitted_at": wire_time(NOW),
        "correlation_id": str(uuid4()), "causation_id": None, "idempotency_key": f"incoming-{sequence:08d}",
        "classification": "research_confidential" if event_type.startswith("report.") else "customer_confidential",
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


def test_consumer_rejects_wrong_schema_and_isolates_tenant(m08, analyst):
    series = create_series(m08["service"], analyst, "consumer-series").data
    m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="consumer-version",
    )
    consumer = M08EventConsumer(m08["service"])
    payload = {
        "report_version_id": REPORT_A, "signature_id": SIGNATURE_A, "report_content_sha256": "a" * 64,
        "bundle_manifest_sha256": "b" * 64, "signed_file_sha256": "c" * 64,
        "verified_at": wire_time(NOW), "valid_until": wire_time(NOW + timedelta(hours=6)),
    }
    wrong_schema = incoming("report.signature_verified", "M07", "report_version", REPORT_A, 1, payload)
    wrong_schema["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        consumer.consume(wrong_schema)
    wrong_tenant = incoming("report.signature_verified", "M07", "report_version", REPORT_A, 1, payload)
    wrong_tenant["tenant_id"] = "10000000-0000-0000-0000-000000000099"
    consumer.consume(wrong_tenant)
    snapshot = m08["repository"].snapshot()
    assert not any(job.kind == "publish_recommendation_version" for (tenant, _), job in snapshot.jobs.items() if tenant == TENANT)
    assert (TENANT, REPORT_A) not in snapshot.verified_reports
    tampered_hash = incoming("report.signature_verified", "M07", "report_version", REPORT_A, 1, payload)
    tampered_hash["payload"] = {**payload, "report_content_sha256": "f" * 64}
    with pytest.raises(ConflictError):
        consumer.consume(tampered_hash)


def test_consumer_detects_sequence_gap_and_recovers(m08, analyst):
    consumer = M08EventConsumer(m08["service"])
    payload_seq2 = {
        "report_version_id": REPORT_A, "signature_id": SIGNATURE_A, "report_content_sha256": "a" * 64,
        "bundle_manifest_sha256": "b" * 64, "signed_file_sha256": "c" * 64,
        "verified_at": wire_time(NOW), "valid_until": wire_time(NOW + timedelta(hours=6)),
    }
    seq2 = incoming("report.signature_verified", "M07", "report_version", REPORT_A, 2, payload_seq2)
    assert consumer.consume(seq2).status == "retry"
    payload_seq1 = {**payload_seq2, "signature_id": "90000000-0000-0000-0000-000000000099"}
    seq1 = incoming("report.signature_verified", "M07", "report_version", REPORT_A, 1, payload_seq1)
    assert consumer.consume(seq1).status == "applied"
    assert consumer.consume(seq2).status == "applied"


def test_outbox_worker_uses_lease_bounded_retry_and_no_duplicate_publish(m08, analyst):
    _publish_one(m08, analyst, "outbox-series")
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(m08["repository"], bus, max_attempts=2)
    first = publisher.run_once("worker-a")
    assert first.retried == 1

    def make_due(state):
        for event in state.outbox.values():
            event.next_attempt_at = NOW - timedelta(seconds=1)
            event.lease_until = NOW - timedelta(seconds=1)
    m08["repository"].atomic(make_due)
    bus.failure_mode = None
    second = publisher.run_once("worker-b")
    assert second.processed == 1
    assert len(bus.envelopes) == 1
    third = publisher.run_once("worker-c")
    assert third.processed == 0 and len(bus.envelopes) == 1


def test_publish_job_lease_prevents_double_processing_until_due(m08, analyst):
    series = create_series(m08["service"], analyst, "lease-series").data
    version = m08["service"].create_recommendation_version(
        analyst, series["id"], version_body(REPORT_A), expected_version=1, idempotency_key="lease-version",
    ).data
    register_bundle_for(m08, version["id"], REPORT_A)
    set_audience_for_series(m08, series["id"], default_candidates(1))
    M08EventConsumer(m08["service"]).consume(signature_event(REPORT_A, SIGNATURE_A))
    worker = PublicationWorker(m08["service"])
    leased = worker.lease("worker-a")
    assert len(leased) == 1
    assert worker.lease("worker-b") == []


def test_fastapi_router_dispatches_to_the_controller(m08, analyst):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.publication.http import create_fastapi_router
    from modules.publication.domain import ActorContext

    router = create_fastapi_router(m08["service"], lambda request: analyst)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/recommendation-series",
        json={"instrument_ref": {"market": "TWSE", "instrument_code": "2308", "instrument_type": "equity"}, "strategy_scope_ref": "fastapi-route-v1", "research_origin_ref": RESEARCH_ORIGIN_REF},
        headers={"Idempotency-Key": "fastapi-route-001"},
    )
    assert response.status_code == 201
    assert response.json()["state"] == "open"
    assert response.headers["ETag"] == '"1"'

    listed = client.get("/api/v1/publications")
    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_production_composition_refuses_fake_adapters(m08):
    from modules.publication.ports import (
        FakeContentAssetStore, FakeEntitlementAudienceRegistry, FakeEvidenceStore, FakeReportBundleRegistry,
    )
    from modules.publication.repository import InMemoryRepository
    from modules.publication.service import PublicationService

    with pytest.raises(RuntimeError):
        PublicationService(
            InMemoryRepository(), FakeContentAssetStore(), FakeEvidenceStore(),
            FakeReportBundleRegistry(), FakeEntitlementAudienceRegistry(), environment="production",
        )
