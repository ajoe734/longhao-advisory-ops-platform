from __future__ import annotations

import pytest

from modules.pantheon_intake.adapter import UnsupportedSchemaVersion
from modules.pantheon_intake.domain import ActorContext, ConflictError, ForbiddenError, PreconditionRequiredError
from modules.pantheon_intake.ports import TransportResponse

from .conftest import ALLOWED_HOST, DISALLOWED_HOST, GLOBAL_IP, TENANT, candidate_body, ingest


def test_uat_m05_001_persona_autonomous_creates_immutable_candidate_without_ticket(m05, adapter_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "ingest-001", external_event_id="00000000-0000-0000-0000-000000000101")
    assert response.status == 202
    assert response.data["state"] == "received"
    assert response.data["review_case_id"] is None  # no review待辦 assigned yet; assigned on first validation
    candidate_id = response.data["id"]

    service_context = m05["service_context"]
    validated = service.validate_research_candidate(
        service_context, candidate_id,
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000099"},
        expected_version=response.data["version"], idempotency_key="validate-001",
    )
    assert validated.data["review_case_id"] is not None
    assert validated.data["state"] == "accepted"


def test_uat_m05_002_analyst_initiated_uses_the_same_channel(m05, adapter_context):
    service = m05["service"]
    response = ingest(
        service, adapter_context, "ingest-002",
        external_event_id="00000000-0000-0000-0000-000000000102",
        origin="analyst_initiated",
    )
    assert response.data["origin"] == "analyst_initiated"
    # Same pipeline: still requires evidence/review, no shortcut around validation.
    assert response.data["review_case_id"] is None
    assert response.data["state"] == "received"


def test_uat_m05_003_resend_creates_a_single_case(m05, adapter_context):
    service = m05["service"]
    event_id = "00000000-0000-0000-0000-000000000103"
    first = ingest(service, adapter_context, "ingest-003a", external_event_id=event_id)
    second = ingest(service, adapter_context, "ingest-003b", external_event_id=event_id)
    assert first.data["id"] == second.data["id"]
    snapshot = m05["repository"].snapshot()
    assert len(snapshot.candidates) == 1
    assert len([event for event in snapshot.outbox.values() if event.event_type == "research.candidate_received"]) == 1


def test_uat_m05_004_same_version_different_hash_quarantines_without_overwrite(m05, adapter_context):
    service = m05["service"]
    first = ingest(service, adapter_context, "ingest-004a", external_event_id="00000000-0000-0000-0000-000000000104", content_sha256="a" * 64)
    with pytest.raises(ConflictError) as excinfo:
        ingest(service, adapter_context, "ingest-004b", external_event_id="00000000-0000-0000-0000-000000000105", content_sha256="c" * 64)
    assert excinfo.value.code == "artifact_hash_conflict"
    stored = m05["repository"].snapshot().candidates[(TENANT, first.data["id"])]
    assert stored.content_sha256 == "a" * 64  # original not overwritten
    assert stored.status == "quarantined"
    assert "artifact_hash_conflict" in stored.validation_failure_codes
    assert len(stored.conflicting_receipts) == 1


def test_uat_m05_003b_resend_with_different_payload_is_a_conflict_not_a_duplicate(m05, adapter_context):
    service = m05["service"]
    event_id = "00000000-0000-0000-0000-000000000113"
    first = ingest(service, adapter_context, "ingest-003c", external_event_id=event_id, content_sha256="a" * 64)
    with pytest.raises(ConflictError) as excinfo:
        ingest(service, adapter_context, "ingest-003d", external_event_id=event_id, content_sha256="f" * 64)
    assert excinfo.value.code == "event_payload_conflict"
    snapshot = m05["repository"].snapshot()
    assert len(snapshot.candidates) == 1
    stored = snapshot.candidates[(TENANT, first.data["id"])]
    assert stored.content_sha256 == "a" * 64  # original not overwritten by the conflicting resend


def test_available_at_after_occurred_at_is_quarantined_not_backfilled(m05, adapter_context):
    service = m05["service"]
    response = ingest(
        service, adapter_context, "ingest-lookahead-001",
        external_event_id="00000000-0000-0000-0000-000000000114",
        source_created_at="2026-11-01T00:00:00Z",
        source_available_at="2026-11-15T00:00:00Z",  # claims to have been available two weeks after the research occurred
    )
    assert "source_available_at_look_ahead" in response.data["validation_failure_codes"]
    service_context = m05["service_context"]
    validated = service.validate_research_candidate(
        service_context, response.data["id"],
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000099"},
        expected_version=response.data["version"], idempotency_key="validate-lookahead-001",
    )
    assert validated.data["state"] == "quarantined"
    assert "source_available_at_look_ahead" in validated.data["validation_failure_codes"]


def test_uat_m05_004b_hash_conflict_against_rejected_head_stays_rejected(m05, adapter_context, reviewer_context):
    service = m05["service"]
    first = ingest(service, adapter_context, "ingest-004c", external_event_id="00000000-0000-0000-0000-000000000115", content_sha256="a" * 64)
    rejected = service.reject_research_candidate(
        reviewer_context, first.data["id"], {"reason_code": "duplicate_of_v1"},
        expected_version=first.data["version"], idempotency_key="reject-004c",
    )
    assert rejected.data["state"] == "rejected"
    with pytest.raises(ConflictError) as excinfo:
        ingest(service, adapter_context, "ingest-004d", external_event_id="00000000-0000-0000-0000-000000000116", content_sha256="c" * 64)
    assert excinfo.value.code == "artifact_hash_conflict"
    stored = m05["repository"].snapshot().candidates[(TENANT, first.data["id"])]
    assert stored.status == "rejected"  # terminal rejection is never reopened into quarantine
    assert len(stored.conflicting_receipts) == 1  # the conflicting receipt is still recorded for traceability


def test_uat_m05_004c_hash_conflict_against_ready_head_notifies_quarantine(m05, adapter_context):
    service = m05["service"]
    first = ingest(service, adapter_context, "ingest-004e", external_event_id="00000000-0000-0000-0000-000000000117", content_sha256="a" * 64)
    service_context = m05["service_context"]
    validated = service.validate_research_candidate(
        service_context, first.data["id"],
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000099"},
        expected_version=first.data["version"], idempotency_key="validate-004e",
    )
    assert validated.data["state"] == "accepted"  # now "ready" and eligible for M06's signature queue
    with pytest.raises(ConflictError):
        ingest(service, adapter_context, "ingest-004f", external_event_id="00000000-0000-0000-0000-000000000118", content_sha256="c" * 64)
    stored = m05["repository"].snapshot().candidates[(TENANT, first.data["id"])]
    assert stored.status == "quarantined"  # pulled back out of the signature-eligible state
    quarantine_events = [
        event for event in m05["repository"].snapshot().outbox.values()
        if event.event_type == "research.candidate_validated"
        and event.aggregate_id == first.data["id"]
        and event.payload["validation_result"] == "quarantined"
    ]
    assert len(quarantine_events) == 1  # M06 is told the candidate must not enter the signature queue


def test_uat_m05_005_missing_evidence_stays_quarantined(m05, adapter_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "ingest-005", external_event_id="00000000-0000-0000-0000-000000000106", evidence_checksum=None)
    assert "evidence_incomplete" in response.data["validation_failure_codes"]
    service_context = m05["service_context"]
    validated = service.validate_research_candidate(
        service_context, response.data["id"],
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000099"},
        expected_version=response.data["version"], idempotency_key="validate-005",
    )
    assert validated.data["state"] == "quarantined"
    assert "evidence_incomplete" in validated.data["validation_failure_codes"]
    # Raw receipt (the original submitted ref) remains traceable even without a checksum.
    assert validated.data["evidence_bundle_refs"][0]["external_id"] == "evidence-1"


def test_uat_m05_006_external_approval_never_reaches_customers(m05, adapter_context, analyst_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "ingest-006", external_event_id="00000000-0000-0000-0000-000000000107")
    service_context = m05["service_context"]
    validated = service.validate_research_candidate(
        service_context, response.data["id"],
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000099"},
        expected_version=response.data["version"], idempotency_key="validate-006",
    )
    assert validated.data["state"] == "accepted"  # accepted into review, never "published"
    stored = m05["repository"].snapshot().candidates[(TENANT, response.data["id"])]
    assert stored.customer_distribution_allowed is False  # internal_only license: no auto customer publish
    customer_context = ActorContext(TENANT, "20000000-0000-0000-0000-000000000099", frozenset(), principal_type="staff")
    with pytest.raises(ForbiddenError):
        service.get_research_candidate(customer_context, response.data["id"])


def test_uat_m05_007_research_time_is_not_backfilled_as_trade_time(m05, adapter_context):
    service = m05["service"]
    response = ingest(
        service, adapter_context, "ingest-007",
        external_event_id="00000000-0000-0000-0000-000000000108",
        source_created_at="2026-11-01T00:00:00Z",
        source_committed_at="2026-10-31T00:00:00Z",  # report "committed" before the trade occurred
    )
    stored = m05["repository"].snapshot().candidates[(TENANT, response.data["id"])]
    assert stored.source_occurred_at.isoformat() != stored.source_committed_at.isoformat()
    assert stored.received_at != stored.source_occurred_at
    assert "source_time_inconsistent" in response.data["validation_failure_codes"]


def test_uat_m05_008_unknown_schema_version_is_rejected(m05):
    adapter = m05["adapter"]
    with pytest.raises(UnsupportedSchemaVersion):
        adapter.translate(TENANT, {"schema_version": "999", "candidate": {}})
    receipts = [row for row in m05["repository"].snapshot().safety_receipts.values() if row.tenant_id == TENANT]
    assert any(row.reason_code == "unsupported_schema" and row.received_schema_version == "999" for row in receipts)


def test_uat_m05_009_ssrf_blocked_hosts_and_addresses_are_rejected(m05):
    fetcher = m05["fetcher"]
    transport = m05["transport"]
    with pytest.raises(Exception) as excinfo:
        fetcher.fetch(f"https://{DISALLOWED_HOST}/evidence.json")
    assert excinfo.value.code == "evidence_url_host_blocked"

    transport.set_dns(ALLOWED_HOST, ["169.254.169.254"])  # metadata address behind an allowlisted hostname
    with pytest.raises(Exception) as excinfo:
        fetcher.fetch(f"https://{ALLOWED_HOST}/evidence.json")
    assert excinfo.value.code == "evidence_url_private_address"

    transport.set_dns(ALLOWED_HOST, [GLOBAL_IP])
    transport.set_response(
        f"https://{ALLOWED_HOST}/redirect",
        TransportResponse(status=302, body=b"", redirect_location=f"https://{DISALLOWED_HOST}/evidence.json"),
    )
    with pytest.raises(Exception) as excinfo:
        fetcher.fetch(f"https://{ALLOWED_HOST}/redirect")
    assert excinfo.value.code == "evidence_url_host_blocked"


def test_uat_m05_010_unlicensed_source_cannot_reach_customers(m05, adapter_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "ingest-010", external_event_id="00000000-0000-0000-0000-000000000109", license_scope_ref="internal_only")
    service_context = m05["service_context"]
    validated = service.validate_research_candidate(
        service_context, response.data["id"],
        {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000099"},
        expected_version=response.data["version"], idempotency_key="validate-010",
    )
    assert validated.data["state"] == "accepted"  # still enters review
    assert "customer_distribution_denied" in validated.data["validation_failure_codes"]
    stored = m05["repository"].snapshot().candidates[(TENANT, response.data["id"])]
    assert stored.customer_distribution_allowed is False


def test_uat_m05_011_new_version_marks_old_review_stale(m05, adapter_context):
    service = m05["service"]
    v1 = ingest(service, adapter_context, "ingest-011a", external_event_id="00000000-0000-0000-0000-000000000110", version="1", content_sha256="a" * 64)
    v2 = ingest(service, adapter_context, "ingest-011b", external_event_id="00000000-0000-0000-0000-000000000111", version="2", content_sha256="d" * 64)
    assert v1.data["id"] != v2.data["id"]
    assert v2.data["state"] == "received"  # v2 is not auto-signed; it starts its own review
    snapshot = m05["repository"].snapshot()
    stored_v1 = snapshot.candidates[(TENANT, v1.data["id"])]
    assert stored_v1.status == "superseded"
    superseded_events = [event for event in snapshot.outbox.values() if event.event_type == "research.candidate_superseded"]
    assert len(superseded_events) == 1
    payload = superseded_events[0].payload
    assert payload["candidate_id"] == v1.data["id"]
    assert payload["replacement_candidate_id"] == v2.data["id"]
    assert payload["previous_artifact_sha256"] == "a" * 64
    assert payload["replacement_artifact_sha256"] == "d" * 64


def test_uat_m05_012_pantheon_outage_does_not_discard_the_candidate(m05, adapter_context):
    service = m05["service"]
    worker = m05["worker"]
    response = ingest(service, adapter_context, "ingest-012", external_event_id="00000000-0000-0000-0000-000000000112")
    candidate_id = response.data["id"]
    original_received_at = m05["repository"].snapshot().candidates[(TENANT, candidate_id)].received_at

    consumer = m05["consumer"]
    envelope = next(event.envelope() for event in m05["repository"].snapshot().outbox.values() if event.aggregate_id == candidate_id)
    consumer.consume(envelope)
    # No URL registered -> evidence source is unavailable -> retry, then dead letter.
    # TaskRunner schedules backoff against the real wall clock, so force each job past
    # its next_attempt_at instead of waiting out ``min(300, 2**attempt)`` seconds for real.
    def _clear_backoff() -> None:
        def mutate(state):
            for job in state.jobs.values():
                if job.tenant_id == TENANT and job.payload.get("candidate_id") == candidate_id:
                    job.next_attempt_at = None

        m05["repository"].atomic(mutate)

    for _ in range(6):
        _clear_backoff()
        worker.run_once("drill-worker")
    dead_letters = m05["task_runner"].dead_letter_jobs(TENANT)
    assert any(job.payload["candidate_id"] == candidate_id for job in dead_letters)
    still_there = m05["repository"].snapshot().candidates[(TENANT, candidate_id)]
    assert still_there.id == candidate_id
    assert still_there.received_at == original_received_at
    assert still_there.status == "received"

    job = next(job for job in dead_letters if job.payload["candidate_id"] == candidate_id)
    m05["task_runner"].redrive(TENANT, job.id, "evidence source URL registered after outage")
    m05["evidence_source"].set_url(TENANT, candidate_id, f"https://{ALLOWED_HOST}/evidence.json")
    m05["transport"].set_response(f"https://{ALLOWED_HOST}/evidence.json", TransportResponse(status=200, body=b"synthetic-snapshot-bytes"))
    worker.run_once("drill-worker")
    final = m05["repository"].snapshot().candidates[(TENANT, candidate_id)]
    assert final.received_at == original_received_at
    validated_events = [
        event for event in m05["repository"].snapshot().outbox.values()
        if event.event_type == "research.candidate_validated" and event.aggregate_id == candidate_id
    ]
    assert len(validated_events) == 1  # one success does not rebuild/duplicate the case
