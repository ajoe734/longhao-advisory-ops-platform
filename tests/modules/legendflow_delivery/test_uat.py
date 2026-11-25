from __future__ import annotations

import pytest

from modules.legendflow_delivery.domain import ChannelBinding, CommunicationPreference, ForbiddenError, UnauthorizedError, resolve_delivery_permission
from modules.legendflow_delivery.ports import SendOutcome

from conftest import (
    ENTITLEMENT_A1,
    MEMBER_A,
    MEMBER_B,
    NOW,
    PUBLICATION_X,
    REPORT_VERSION_X,
    TENANT,
    CHANNEL_PROOF_FORGED,
    CHANNEL_PROOF_LINE_OK,
    dispatch,
    seed_publication,
    send_receipt,
    set_entitlement,
)


def _only_message(env, publication_id: str = PUBLICATION_X, channel: str = "legendflow"):
    rows = env["repository"].read(
        lambda state: [m for (t, _), m in state.deliveries.items() if t == TENANT and m.publication_id == publication_id and m.channel == channel]
    )
    assert len(rows) == 1
    return rows[0]


def test_uat_m09_001_feed_shows_only_entitled_publication(m09, member_a):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env, publication_id=PUBLICATION_X, recipient_id="30000000-0000-0000-0000-000000000001", customer_id=MEMBER_A, entitlement_id=ENTITLEMENT_A1)
    dispatch(env, PUBLICATION_X)

    publication_y = "50000000-0000-0000-0000-000000000002"
    report_version_y = "60000000-0000-0000-0000-000000000002"
    # Member A has no recipient row at all for publication Y (not entitled to service Y);
    # it is only ever dispatched to member B.
    seed_publication(env, publication_id=publication_y, recipient_id="30000000-0000-0000-0000-000000000099", customer_id=MEMBER_B, entitlement_id="40000000-0000-0000-0000-000000000099", report_version_id=report_version_y)
    set_entitlement(env, entitlement_id="40000000-0000-0000-0000-000000000099", customer_id=MEMBER_B, state="active")
    dispatch(env, publication_y)

    page = env["service"].get_member_feed(member_a)
    ids = {item["publication_id"] for item in page.data["items"]}
    assert ids == {PUBLICATION_X}
    assert publication_y not in ids
    # Y cannot be read directly either: no eligible delivery/entitlement backs it for member A,
    # even though A can correctly guess Y's report_version_id.
    with pytest.raises(ForbiddenError):
        env["service"].request_member_report_access(member_a, report_version_y)


def test_uat_m09_002_report_access_requires_current_entitlement(m09, member_a):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)

    granted = env["service"].request_member_report_access(member_a, REPORT_VERSION_X)
    assert granted.data["content_sha256"] == "a" * 64

    # Entitlement is revoked after the earlier grant; a new request must be denied,
    # and the earlier short-lived ticket is not reusable through this API either.
    set_entitlement(env, state="revoked")
    with pytest.raises(ForbiddenError):
        env["service"].request_member_report_access(member_a, REPORT_VERSION_X)


def test_uat_m09_003_unverified_channel_cannot_bind(m09, member_a):
    env = m09
    with pytest.raises(UnauthorizedError):
        env["service"].bind_member_channel(
            member_a, {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_FORGED, "service_delivery_enabled": True},
            idempotency_key="bind-attempt-0001",
        )
    rows = env["repository"].read(lambda state: [b for (t, _), b in state.channel_bindings.items() if t == TENANT])
    assert rows == []

    ok = env["service"].bind_member_channel(
        member_a, {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True},
        idempotency_key="bind-attempt-0002",
    )
    assert ok.data["state"] == "verified"


def test_uat_m09_004_dispatch_rechecks_entitlement_and_suppresses(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    summary = dispatch(env)
    assert summary.created == 1

    # Entitlement flips to revoked after queueing but before the worker sends.
    set_entitlement(env, state="revoked", decision_id="d1000000-0000-0000-0000-000000000099")
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    outcome = env["service"].process_job("worker-1", TENANT, job.id)
    assert outcome.result == "suppressed"

    message = _only_message(env)
    assert message.status == "suppressed"
    assert message.last_entitlement_decision_id == "d1000000-0000-0000-0000-000000000099"
    attempts = env["repository"].read(lambda state: [a for (t, _), a in state.attempts.items() if t == TENANT and a.message_id == message.id])
    assert any(a.outcome == "rejected" and a.error_code == "entitlement_revoked" for a in attempts)


def test_uat_m09_005_accepted_does_not_fabricate_delivery(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    outcome = env["service"].process_job("worker-1", TENANT, job.id)
    assert outcome.result == "accepted"

    message = _only_message(env)
    assert message.status == "accepted"
    assert message.accepted_at is not None
    assert message.sent_at is None
    assert message.delivered_at is None
    assert message.read_at is None


def test_uat_m09_006_unknown_read_state_is_not_fabricated_as_unread(m09, member_a):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    env["service"].bind_member_channel(
        member_a, {"channel": "sms", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True},
        idempotency_key="bind-sms-0001",
    )
    dispatch(env)
    sms_job = env["repository"].read(
        lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT and state.deliveries[(t, j.message_id)].channel == "sms")
    )
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, sms_job.id)

    sms_message = _only_message(env, channel="sms")
    assert sms_message.read_at is None
    assert sms_message.read_capability != "supported"


def test_uat_m09_007_timeout_is_resolved_before_retry_as_one_logical_message(m09, operator):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = _only_message(env)
    env["channel_send"].queue(message.provider_idempotency_key, SendOutcome("unknown"))

    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    first = env["service"].process_job("worker-1", TENANT, job.id)
    assert first.result == "unknown"
    message = _only_message(env)
    assert message.status == "unknown"

    # The provider actually accepted it; reconcile must find that before any resend.
    env["channel_send"].seed_accepted(message.provider_idempotency_key, "provider-ref-0001")
    reconciled = env["service"].reconcile_delivery(
        operator, message.id, {"reason_code": "worker_restart_timeout"}, expected_version=message.version, idempotency_key="reconcile-0001",
    )
    assert reconciled.data["state"] == "accepted"

    # Exactly one logical message exists throughout, regardless of retries.
    rows = env["repository"].read(lambda state: [m for (t, _), m in state.deliveries.items() if t == TENANT and m.channel == "legendflow"])
    assert len(rows) == 1


def test_uat_m09_008_out_of_order_receipts_never_regress(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, job.id)
    message = _only_message(env)
    assert message.status == "accepted"

    read_at = NOW
    delivered_at = NOW.replace(second=1)
    send_receipt(
        env,
        {
            "event_id": "e1000000-0000-0000-0000-000000000001", "provider_message_ref": message.provider_message_ref or "ref",
            "delivery_id": message.id, "provider_sequence": 2, "provider_status": "read",
            "provider_occurred_at": read_at.isoformat().replace("+00:00", "Z"), "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000011",
        },
        idempotency_key="receipt-read-0001", nonce="q" * 16,
    )
    after_read = _only_message(env)
    assert after_read.read_at is not None
    assert after_read.delivered_at is None  # never fabricated

    send_receipt(
        env,
        {
            "event_id": "e1000000-0000-0000-0000-000000000002", "provider_message_ref": message.provider_message_ref or "ref",
            "delivery_id": message.id, "provider_sequence": 1, "provider_status": "delivered",
            "provider_occurred_at": delivered_at.isoformat().replace("+00:00", "Z"), "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000012",
        },
        idempotency_key="receipt-delivered-0001", nonce="w" * 16,
    )
    final = _only_message(env)
    assert final.status == "delivered"
    assert final.delivered_at is not None
    assert final.read_at is not None  # not regressed by the later-processed delivered receipt


def test_uat_m09_009_expired_dead_letter_cannot_be_redriven(m09, supervisor):
    env = m09
    set_entitlement(env, state="active")
    from datetime import timedelta
    # Long enough to survive the five bounded retries below; the redrive
    # denial check later advances the clock well past this.
    seed_publication(env, valid_until=NOW + timedelta(seconds=3000))
    dispatch(env)
    message = _only_message(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))

    env["channel_send"].queue(message.provider_idempotency_key, *([SendOutcome("rejected", error_code="synthetic_rejected")] * 5))
    for attempt in range(5):
        if attempt > 0:
            env["clock"].advance(400)
        leased = env["service"].lease_jobs("worker-1", TENANT, limit=10)
        assert leased, f"no job due to lease on attempt {attempt}"
        env["service"].process_job("worker-1", TENANT, leased[0].id)

    job = env["repository"].read(lambda state: state.jobs[(TENANT, job.id)])
    assert job.status == "dead_letter"
    assert job.dead_letter_event_id is not None

    env["clock"].advance(10_000)  # now well past the message's expiry
    from modules.legendflow_delivery.domain import ConflictError
    with pytest.raises(ConflictError) as excinfo:
        env["service"].redrive_delivery_event(
            supervisor, job.id,
            {"reason_code": "operator_requested", "expected_event_id": job.dead_letter_event_id, "approved_by": supervisor.actor_id, "revalidate_entitlement": True, "revalidate_content_expiry": True},
            expected_version=job.version, idempotency_key="redrive-0001",
        )
    assert excinfo.value.code == "content_expired_cannot_redrive"
    attempts_after = env["repository"].read(lambda state: [a for (t, _), a in state.attempts.items() if t == TENANT and a.message_id == message.id])
    assert len(attempts_after) == 5  # no new provider call was made by the denied redrive


def test_uat_m09_010_correction_preserves_original_content(m09, member_a, operator):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, job.id)
    original = _only_message(env)
    original_hash = original.rendered_sha256

    from workers.tasks.legendflow_delivery.consumer import M09EventConsumer
    from modules.legendflow_delivery.domain import canonical_hash

    replacement_publication_id = "50000000-0000-0000-0000-000000000003"
    payload = {"publication_id": PUBLICATION_X, "replacement_publication_id": replacement_publication_id, "replacement_report_version_id": "60000000-0000-0000-0000-000000000003"}
    envelope = {
        "event_id": "e2000000-0000-0000-0000-000000000001", "event_type": "publication.corrected", "schema_version": "1.0.0",
        "tenant_id": TENANT, "producer": "M08", "aggregate_type": "publication", "aggregate_id": PUBLICATION_X,
        "aggregate_version": 2, "sequence_no": 1, "occurred_at": "2026-11-23T01:02:03.000000Z", "emitted_at": "2026-11-23T01:02:03.000000Z",
        "correlation_id": "c3000000-0000-0000-0000-000000000001", "causation_id": None, "idempotency_key": "publication.corrected:test-1",
        "classification": "research_confidential", "payload_sha256": canonical_hash(payload), "payload": payload,
    }
    consumer = M09EventConsumer(env["repository"], env["service"])
    result = consumer.consume(envelope)
    assert result.status == "applied"

    page = env["service"].get_member_feed(member_a)
    item = next(row for row in page.data["items"] if row["publication_id"] == PUBLICATION_X)
    assert item["state"] == "corrected"
    assert item["correction_publication_id"] == replacement_publication_id

    # The original message row is untouched: same hash, same attempt history still queryable.
    unchanged = _only_message(env)
    assert unchanged.rendered_sha256 == original_hash
    listed = env["service"].list_deliveries(operator)
    assert any(row["id"] == original.id and row["content_sha256"] == original_hash for row in listed.data["items"])


def test_apply_receipt_allows_failed_bounce_after_sent_but_never_after_delivered():
    """F5 regression: a provider bounce can legitimately arrive after the
    carrier handoff ("sent") but before a delivery is confirmed; only a
    confirmed "delivered" message must never regress to "failed"."""
    from modules.legendflow_delivery.domain import Delivery

    def make(status: str) -> Delivery:
        return Delivery(
            id="x", tenant_id=TENANT, version=1, created_at=NOW, updated_at=NOW, publication_id=PUBLICATION_X,
            recipient_id="r", customer_id=MEMBER_A, entitlement_id=ENTITLEMENT_A1, channel="email",
            rendered_sha256="a" * 64, rendered_asset_id=REPORT_VERSION_X, provider_idempotency_key="k",
            expires_at=NOW, status=status,
        )

    sent = make("sent")
    assert sent.apply_receipt("failed", NOW) is True
    assert sent.status == "failed"

    delivered = make("delivered")
    assert delivered.apply_receipt("failed", NOW) is False
    assert delivered.status == "delivered"


def test_uat_m09_011_marketing_opt_out_never_suppresses_service_notice():
    binding = ChannelBinding(
        id="x", tenant_id=TENANT, version=1, created_at=NOW, updated_at=NOW, customer_id=MEMBER_A, channel="sms",
        provider="sms_gateway", destination_hmac="f" * 64, masked_destination="***1234", proof_asset_id="p", verified_at=NOW,
    )
    marketing_optout = CommunicationPreference(id="p1", tenant_id=TENANT, version=1, created_at=NOW, updated_at=NOW, customer_id=MEMBER_A, channel="sms", purpose="marketing", enabled=False, consent_id="c1")

    marketing_allowed, marketing_reason = resolve_delivery_permission(purpose="marketing", binding=binding, preference=marketing_optout)
    service_allowed, service_reason = resolve_delivery_permission(purpose="service", binding=binding, preference=None)

    assert marketing_allowed is False
    assert marketing_reason == "marketing_preference_not_granted"
    assert service_allowed is True
    assert service_reason == "service_delivery_allowed"


def test_uat_m09_012_webhook_requires_valid_signature_and_dedupes_legitimate_replay(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, job.id)
    message = _only_message(env)
    before_version = message.version

    body = {
        "event_id": "e4000000-0000-0000-0000-000000000001", "provider_message_ref": message.provider_message_ref or "ref",
        "delivery_id": message.id, "provider_status": "delivered", "provider_occurred_at": "2026-11-23T01:02:04.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000021",
    }
    import json as _json
    from conftest import WEBHOOK_SECRET, WEBHOOK_PATH, sign_webhook

    raw_body = _json.dumps(body, sort_keys=True).encode("utf-8")
    with pytest.raises(UnauthorizedError):
        env["service"].receive_message_receipt(
            body, idempotency_key="receipt-bad-sig-0001", key_id="synthetic-line-adapter-key-0001",
            timestamp=str(int(env["clock"]().timestamp())), nonce="z" * 16, signature="0" * 64,
            method="POST", path=WEBHOOK_PATH, raw_body=raw_body,
        )
    unchanged = _only_message(env)
    assert unchanged.version == before_version

    accepted = send_receipt(env, body, idempotency_key="receipt-good-0001", nonce="y" * 16)
    assert accepted.data["status"] == "accepted"

    # Legitimate provider retry: same provider_event_id, fresh nonce, different Idempotency-Key.
    replay = send_receipt(env, body, idempotency_key="receipt-good-0002", nonce="x" * 16)
    assert replay.data["status"] == "duplicate"
    assert replay.data["receipt_id"] == accepted.data["receipt_id"]


def test_uat_m09_002_denied_report_access_is_still_audited(m09, member_a):
    """F1 regression: atomic() discards the whole working copy on raise, so a
    denial audit row appended on the same path that then raises would never
    persist. The denial must be recorded by a dedicated follow-up write."""
    env = m09
    before = env["repository"].read(lambda state: len(state.audit_events))
    with pytest.raises(ForbiddenError):
        env["service"].request_member_report_access(member_a, "60000000-0000-0000-0000-000000000099")
    after = env["repository"].read(lambda state: len(state.audit_events))
    assert after - before >= 1


def test_uat_m09_010_correction_links_replacement_delivery_to_original_either_order(m09):
    """F3 regression: replaces_delivery_id must end up set regardless of
    whether the replacement publication is dispatched before or after the
    publication.corrected event that links it back to the original."""
    env = m09
    from workers.tasks.legendflow_delivery.consumer import M09EventConsumer
    from modules.legendflow_delivery.domain import canonical_hash

    def correct(original_publication_id: str, replacement_publication_id: str, replacement_report_version_id: str, event_id: str) -> None:
        payload = {"publication_id": original_publication_id, "replacement_publication_id": replacement_publication_id, "replacement_report_version_id": replacement_report_version_id}
        envelope = {
            "event_id": event_id, "event_type": "publication.corrected", "schema_version": "1.0.0",
            "tenant_id": TENANT, "producer": "M08", "aggregate_type": "publication", "aggregate_id": original_publication_id,
            "aggregate_version": 2, "sequence_no": 1, "occurred_at": "2026-11-23T01:02:03.000000Z", "emitted_at": "2026-11-23T01:02:03.000000Z",
            "correlation_id": "c3100000-0000-0000-0000-000000000001", "causation_id": None, "idempotency_key": f"publication.corrected:{event_id}",
            "classification": "research_confidential", "payload_sha256": canonical_hash(payload), "payload": payload,
        }
        result = M09EventConsumer(env["repository"], env["service"]).consume(envelope)
        assert result.status == "applied"

    # Order A: correction event arrives before the replacement is dispatched.
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    original_a = _only_message(env)
    replacement_a = "50000000-0000-0000-0000-000000000005"
    correct(PUBLICATION_X, replacement_a, "60000000-0000-0000-0000-000000000005", "e2100000-0000-0000-0000-000000000001")
    seed_publication(env, publication_id=replacement_a, report_version_id="60000000-0000-0000-0000-000000000005", body_sha256="c" * 64)
    dispatch(env, replacement_a)
    linked_a = env["repository"].read(
        lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT and m.publication_id == replacement_a)
    )
    assert linked_a.replaces_delivery_id == original_a.id

    # Order B: replacement is dispatched first; the correction event must
    # backfill replaces_delivery_id onto the already-created delivery.
    original_publication_b = "50000000-0000-0000-0000-000000000006"
    seed_publication(env, publication_id=original_publication_b, report_version_id="60000000-0000-0000-0000-000000000006", recipient_id="30000000-0000-0000-0000-000000000006", customer_id=MEMBER_B, entitlement_id="40000000-0000-0000-0000-000000000006", body_sha256="d" * 64)
    set_entitlement(env, entitlement_id="40000000-0000-0000-0000-000000000006", customer_id=MEMBER_B, state="active")
    dispatch(env, original_publication_b)
    original_b = env["repository"].read(
        lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT and m.publication_id == original_publication_b)
    )
    replacement_b = "50000000-0000-0000-0000-000000000007"
    seed_publication(env, publication_id=replacement_b, report_version_id="60000000-0000-0000-0000-000000000007", recipient_id="30000000-0000-0000-0000-000000000006", customer_id=MEMBER_B, entitlement_id="40000000-0000-0000-0000-000000000006", body_sha256="e" * 64)
    dispatch(env, replacement_b)  # dispatched BEFORE the correction event
    correct(original_publication_b, replacement_b, "60000000-0000-0000-0000-000000000007", "e2100000-0000-0000-0000-000000000002")
    linked_b = env["repository"].read(
        lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT and m.publication_id == replacement_b)
    )
    assert linked_b.replaces_delivery_id == original_b.id


def test_uat_m09_010_withdrawal_cancels_queued_jobs_and_process_job_independently_refuses_to_send(m09):
    """F3/F2 regression: withdrawal must stop a still-queued job from ever
    dispatching retracted content, both through the consumer's immediate
    cancellation and through process_job's own live re-check."""
    env = m09
    from workers.tasks.legendflow_delivery.consumer import M09EventConsumer
    from modules.legendflow_delivery.domain import canonical_hash

    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = _only_message(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    assert job.status == "pending"

    payload = {"publication_id": PUBLICATION_X, "withdrawal_record_id": "90000000-0000-0000-0000-000000000099", "effective_at": "2026-11-23T01:02:03.000000Z", "notify_recipients": True}
    envelope = {
        "event_id": "e2200000-0000-0000-0000-000000000001", "event_type": "publication.withdrawn", "schema_version": "1.0.0",
        "tenant_id": TENANT, "producer": "M08", "aggregate_type": "publication", "aggregate_id": PUBLICATION_X,
        "aggregate_version": 2, "sequence_no": 1, "occurred_at": "2026-11-23T01:02:03.000000Z", "emitted_at": "2026-11-23T01:02:03.000000Z",
        "correlation_id": "c3200000-0000-0000-0000-000000000001", "causation_id": None, "idempotency_key": "publication.withdrawn:test-1",
        "classification": "research_confidential", "payload_sha256": canonical_hash(payload), "payload": payload,
    }
    result = M09EventConsumer(env["repository"], env["service"]).consume(envelope)
    assert result.status == "applied"
    assert "cancelled=1" in result.result_code

    job_after = env["repository"].read(lambda state: state.jobs[(TENANT, job.id)])
    assert job_after.status == "cancelled"
    message_after = _only_message(env)
    assert message_after.status == "suppressed"
    assert message_after.suppression_reason == "publication_withdrawn"

    # Defense in depth: even a job that stayed "leased" past the cancellation
    # must never dispatch, because process_job re-checks withdrawal live.
    seed_publication(env, publication_id="50000000-0000-0000-0000-000000000008", recipient_id="30000000-0000-0000-0000-000000000009", customer_id=MEMBER_B, entitlement_id="40000000-0000-0000-0000-000000000009", report_version_id="60000000-0000-0000-0000-000000000008")
    set_entitlement(env, entitlement_id="40000000-0000-0000-0000-000000000009", customer_id=MEMBER_B, state="active")
    dispatch(env, "50000000-0000-0000-0000-000000000008")
    other_job = env["repository"].read(
        lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT and state.deliveries[(t, j.message_id)].publication_id == "50000000-0000-0000-0000-000000000008")
    )

    def mark_withdrawn(state):
        state.publications[(TENANT, "50000000-0000-0000-0000-000000000008")].state = "withdrawn"

    env["repository"].atomic(mark_withdrawn)
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    outcome = env["service"].process_job("worker-1", TENANT, other_job.id)
    assert outcome.result == "suppressed"
    other_message = env["repository"].read(
        lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT and m.publication_id == "50000000-0000-0000-0000-000000000008")
    )
    assert other_message.suppression_reason == "publication_withdrawn"


def test_uat_m09_011_set_communication_preference_end_to_end(m09, member_a, operator):
    """F4 regression: set_communication_preference had zero calls across the
    whole suite; it must be exercised through the real API, must reject a
    consent whose own purpose does not match, must block marketing opt-in
    while a CRM suppression is active, and a service opt-out must actually
    change which channels dispatch_publication fans out to."""
    env = m09
    from modules.legendflow_delivery.domain import ValidationError

    set_entitlement(env, state="active")
    seed_publication(env)
    env["service"].bind_member_channel(
        member_a, {"channel": "sms", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True},
        idempotency_key="pref-bind-0001",
    )

    privacy_consent_id = "c4000000-0000-0000-0000-000000000001"
    marketing_consent_id = "c4000000-0000-0000-0000-000000000002"

    def grant_consent(consent_id: str, purpose: str, granted: bool) -> None:
        def mutate(state):
            state.consents[(TENANT, consent_id)] = {"customer_id": MEMBER_A, "purpose": purpose, "granted": granted}

        env["repository"].atomic(mutate)

    grant_consent(privacy_consent_id, "privacy", True)
    grant_consent(marketing_consent_id, "marketing", True)

    # A consent recorded for a different purpose must never unlock marketing,
    # even though it is granted.
    with pytest.raises(ValidationError) as excinfo:
        env["service"].set_communication_preference(
            member_a, {"channel": "sms", "purpose": "marketing", "enabled": True, "consent_id": privacy_consent_id},
            idempotency_key="pref-marketing-wrong-consent-0001",
        )
    assert excinfo.value.code == "consent_required_for_marketing_optin"

    # A CRM suppression (crm.suppression_changed, read from state.suppressions)
    # blocks a marketing opt-in even with a correctly-purposed, granted consent.
    def suppress() -> None:
        def mutate(state):
            state.suppressions[(TENANT, MEMBER_A, "marketing_contact")] = True

        env["repository"].atomic(mutate)

    suppress()
    with pytest.raises(ValidationError) as excinfo:
        env["service"].set_communication_preference(
            member_a, {"channel": "sms", "purpose": "marketing", "enabled": True, "consent_id": marketing_consent_id},
            idempotency_key="pref-marketing-suppressed-0001",
        )
    assert excinfo.value.code == "marketing_opt_in_blocked_by_suppression"

    # A real service-channel opt-out through the API must suppress that
    # channel at the next dispatch, with an audit row recorded for the call.
    before_audit = env["repository"].read(lambda state: len(state.audit_events))
    result = env["service"].set_communication_preference(
        member_a, {"channel": "sms", "purpose": "service", "enabled": False, "consent_id": privacy_consent_id},
        idempotency_key="pref-service-optout-0001",
    )
    assert result.data["enabled"] is False
    after_audit = env["repository"].read(lambda state: len(state.audit_events))
    assert after_audit > before_audit

    dispatch(env)
    sms_messages = env["repository"].read(
        lambda state: [m for (t, _), m in state.deliveries.items() if t == TENANT and m.customer_id == MEMBER_A and m.channel == "sms"]
    )
    assert sms_messages == []
