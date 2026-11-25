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
)
from modules.report_signature.repository import InMemoryRepository
from modules.report_signature.service import ReportSignatureService

from conftest import (
    OTHER_TENANT,
    REVOCATION_EVIDENCE_ASSET_ID,
    TENANT,
    callback_body,
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
