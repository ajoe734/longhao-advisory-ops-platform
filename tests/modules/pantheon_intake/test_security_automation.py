from __future__ import annotations

from hashlib import sha256
import hmac as hmac_module

import pytest

from modules.pantheon_intake.adapter import resolve_pantheon_adapter_context
from modules.pantheon_intake.automation import CandidateTriageAutomationService, LocalTriageAssistant
from modules.pantheon_intake.domain import ActorContext, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError, ValidationError, wire_time

from .conftest import TENANT, candidate_body, ingest


_KEY_ID = "pantheon-key-1"
_SECRET = b"synthetic-pantheon-hmac-key-32-bytes-minimum"


def _sign(*, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    material = f"{timestamp}\n{nonce}\n{method}\n{path}\n{sha256(body).hexdigest()}".encode("utf-8")
    return hmac_module.new(_SECRET, material, sha256).hexdigest()


def test_webhook_signature_gate_authenticates_the_pantheon_adapter_principal(m05):
    signature = m05["signature"]
    body = b'{"schema_version":"1.0","candidate":{}}'
    now_unix = str(int(m05["clock"]().timestamp()))

    valid_headers = {
        "X-Integration-Key-Id": _KEY_ID,
        "X-Webhook-Timestamp": now_unix,
        "X-Webhook-Nonce": "nonce-0000000000000001",
        "X-Webhook-Signature": _sign(timestamp=now_unix, nonce="nonce-0000000000000001", method="POST", path="/api/v1/research-candidates", body=body),
    }
    context = resolve_pantheon_adapter_context(signature, tenant_id=TENANT, method="POST", path="/api/v1/research-candidates", headers=valid_headers, raw_body=body)
    assert context.principal_type == "integration"
    assert context.roles == frozenset({"pantheon_adapter"})

    # Replaying the exact same nonce must be rejected even with a correct signature.
    with pytest.raises(UnauthorizedError) as replay:
        resolve_pantheon_adapter_context(signature, tenant_id=TENANT, method="POST", path="/api/v1/research-candidates", headers=valid_headers, raw_body=body)
    assert replay.value.code == "webhook_nonce_replayed"

    tampered_body_headers = {**valid_headers, "X-Webhook-Nonce": "nonce-0000000000000002"}
    with pytest.raises(UnauthorizedError) as bad_sig:
        resolve_pantheon_adapter_context(
            signature, tenant_id=TENANT, method="POST", path="/api/v1/research-candidates",
            headers=tampered_body_headers, raw_body=b'{"schema_version":"1.0","candidate":{"tampered":true}}',
        )
    assert bad_sig.value.code == "webhook_signature_invalid"

    stale_timestamp = str(int(m05["clock"]().timestamp()) - 3600)
    stale_headers = {
        "X-Integration-Key-Id": _KEY_ID,
        "X-Webhook-Timestamp": stale_timestamp,
        "X-Webhook-Nonce": "nonce-0000000000000003",
        "X-Webhook-Signature": _sign(timestamp=stale_timestamp, nonce="nonce-0000000000000003", method="POST", path="/api/v1/research-candidates", body=body),
    }
    with pytest.raises(UnauthorizedError) as stale:
        resolve_pantheon_adapter_context(signature, tenant_id=TENANT, method="POST", path="/api/v1/research-candidates", headers=stale_headers, raw_body=body)
    assert stale.value.code == "webhook_timestamp_skew"

    unknown_key_headers = {**valid_headers, "X-Integration-Key-Id": "unknown-key", "X-Webhook-Nonce": "nonce-0000000000000004"}
    with pytest.raises(UnauthorizedError) as unknown_key:
        resolve_pantheon_adapter_context(signature, tenant_id=TENANT, method="POST", path="/api/v1/research-candidates", headers=unknown_key_headers, raw_body=body)
    assert unknown_key.value.code == "integration_key_unknown"

    with pytest.raises(ValidationError) as missing:
        resolve_pantheon_adapter_context(signature, tenant_id=TENANT, method="POST", path="/api/v1/research-candidates", headers={}, raw_body=body)
    assert missing.value.code == "webhook_headers_required"


def test_cross_tenant_staff_cannot_read_candidate(m05, adapter_context, analyst_context):
    response = ingest(m05["service"], adapter_context, "tenant-ingest-001", external_event_id="00000000-0000-0000-0000-000000000301")
    other = ActorContext(
        "10000000-0000-0000-0000-000000000099",
        "20000000-0000-0000-0000-000000000099",
        frozenset({"analyst", "analyst_reviewer"}),
        principal_type="staff",
    )
    with pytest.raises(NotFoundError):
        m05["service"].get_research_candidate(other, response.data["id"])
    assert m05["service"].get_research_candidate(analyst_context, response.data["id"]).data["id"] == response.data["id"]


def test_only_the_right_principal_type_can_call_each_command(m05, adapter_context, analyst_context, reviewer_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "sod-ingest-001", external_event_id="00000000-0000-0000-0000-000000000302")
    candidate_id = response.data["id"]

    # analyst_reviewer cannot ingest: ingestion is integration-only (the Pantheon bridge signature scheme).
    with pytest.raises(ForbiddenError):
        service.ingest_research_candidate(reviewer_context, candidate_body(external_event_id="00000000-0000-0000-0000-000000000303"), idempotency_key="sod-ingest-002")

    # staff (even analyst_reviewer) cannot call the service-only validate command.
    with pytest.raises(ForbiddenError):
        service.validate_research_candidate(
            reviewer_context, candidate_id,
            {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000001"},
            expected_version=1, idempotency_key="sod-validate-001",
        )

    # analyst (read-only role) cannot reject; only analyst_reviewer may.
    with pytest.raises(ForbiddenError):
        service.reject_research_candidate(analyst_context, candidate_id, {"reason_code": "sod_test"}, expected_version=1, idempotency_key="sod-reject-001")

    rejected = service.reject_research_candidate(reviewer_context, candidate_id, {"reason_code": "sod_test"}, expected_version=1, idempotency_key="sod-reject-002")
    assert rejected.data["state"] == "rejected"

    # Every one of the three denials above left an audit trail, not a silent 403.
    denied = [row for row in m05["repository"].snapshot().audit_events if row.reason_code == "forbidden"]
    denied_actions = {row.action for row in denied}
    assert denied_actions == {"m05.ingest_research_candidate", "m05.validate_research_candidate", "m05.reject_research_candidate"}
    assert all(row.result == "rejected" for row in denied)


def test_terminal_candidate_rejects_further_mutation_without_losing_history(m05, adapter_context, reviewer_context):
    service = m05["service"]
    response = ingest(service, adapter_context, "terminal-ingest-001", external_event_id="00000000-0000-0000-0000-000000000304")
    candidate_id = response.data["id"]
    rejected = service.reject_research_candidate(reviewer_context, candidate_id, {"reason_code": "duplicate_of_v1"}, expected_version=1, idempotency_key="terminal-reject-001")
    assert rejected.data["state"] == "rejected"
    with pytest.raises(ConflictError) as excinfo:
        service.reject_research_candidate(reviewer_context, candidate_id, {"reason_code": "second_attempt"}, expected_version=rejected.data["version"], idempotency_key="terminal-reject-002")
    assert excinfo.value.code == "candidate_terminal"
    with pytest.raises(ConflictError):
        service.validate_research_candidate(
            m05["service_context"], candidate_id,
            {"observed_content_sha256": "a" * 64, "evidence_access_confirmed": True, "license_scope_confirmed": True, "source_snapshot_asset_id": "30000000-0000-0000-0000-000000000001"},
            expected_version=rejected.data["version"], idempotency_key="terminal-validate-001",
        )
    stored = m05["repository"].snapshot().candidates[(TENANT, candidate_id)]
    assert stored.content_sha256 == response.data["artifact_content_sha256"]  # original artifact facts untouched
    assert wire_time(stored.received_at) == response.data["received_at"]  # received_at never recomputed
    assert stored.validation_failure_codes == ["rejected"]  # rejection reason preserved, not deleted


def test_minimized_triage_automation_is_untrusted_and_has_no_validate_authority(m05, analyst_context):
    automation = CandidateTriageAutomationService(m05["repository"], LocalTriageAssistant())
    records = [
        {"candidate_id": "60000000-0000-0000-0000-000000000001", "validation_failure_codes": ["evidence_incomplete"], "evidence_refs": ["pantheon:evidence-1:evidence:"]},
        {"candidate_id": "60000000-0000-0000-0000-000000000002", "validation_failure_codes": [], "evidence_refs": []},
    ]
    run = automation.suggest(analyst_context, records)
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert len(run.suggestions) == 2
    assert run.suggestions[0].likely_missing_evidence is True
    assert run.suggestions[1].likely_missing_evidence is False
    snapshot = m05["repository"].snapshot()
    assert snapshot.automation_runs[(TENANT, run.id)] == run
    # No candidate state exists for these synthetic IDs; the automation service never
    # reaches into research.candidate writes, so there is nothing to validate/reject here.
    assert not any(key[1] in {"60000000-0000-0000-0000-000000000001", "60000000-0000-0000-0000-000000000002"} for key in snapshot.candidates)
    with pytest.raises(ValidationError) as raw_pii:
        automation.suggest(analyst_context, [{**records[0], "artifact_text": "forbidden raw content"}])
    assert raw_pii.value.code == "automation_input_not_minimized"


def test_production_automation_refuses_local_fake(m05):
    with pytest.raises(RuntimeError):
        CandidateTriageAutomationService(m05["repository"], LocalTriageAssistant(), environment="production")
