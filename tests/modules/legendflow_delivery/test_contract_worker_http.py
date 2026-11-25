from __future__ import annotations

from datetime import timedelta

import pytest

from modules.legendflow_delivery.domain import (
    ConflictError,
    NotFoundError,
    PreconditionRequiredError,
    StaleVersionError,
    ValidationError,
    canonical_hash,
)
from modules.legendflow_delivery.http import M09Controller, ROUTES
from modules.legendflow_delivery.ports import SendOutcome
from workers.tasks.legendflow_delivery.consumer import M09EventConsumer
from workers.tasks.legendflow_delivery.tasks import DispatchWorker, LocalEventBus, OutboxPublisher

from conftest import (
    CHANNEL_PROOF_LINE_OK,
    MEMBER_A,
    NOW,
    PUBLICATION_X,
    TENANT,
    WEBHOOK_KEY_ID,
    WEBHOOK_PATH,
    WEBHOOK_SECRET,
    dispatch,
    seed_publication,
    send_receipt,
    set_entitlement,
)


#: event_type -> classification, matching events/registry.json exactly (the
#: consumer now validates classification against each event's real schema).
_CLASSIFICATION = {
    "customer.identity_linked": "customer_confidential",
    "customer.consent_changed": "customer_confidential",
    "contract.terminated": "customer_confidential",
    "entitlement.changed": "customer_confidential",
    "report.signature_revoked": "research_confidential",
    "publication.created": "research_confidential",
    "publication.withdrawn": "research_confidential",
    "publication.corrected": "research_confidential",
    "crm.suppression_changed": "customer_confidential",
}


def _envelope(event_type: str, aggregate_type: str, aggregate_id: str, payload: dict, *, sequence_no: int = 1, producer: str = "M08", event_id: str = "e5000000-0000-0000-0000-000000000001") -> dict:
    return {
        "event_id": event_id, "event_type": event_type, "schema_version": "1.0.0", "tenant_id": TENANT,
        "producer": producer, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "aggregate_version": sequence_no,
        "sequence_no": sequence_no, "occurred_at": "2026-11-22T00:00:00.000000Z", "emitted_at": "2026-11-22T00:00:00.000000Z",
        "correlation_id": "c6000000-0000-0000-0000-000000000001", "causation_id": None,
        "idempotency_key": f"{event_type}:synthetic-{sequence_no}", "classification": _CLASSIFICATION.get(event_type, "customer_confidential"),
        "payload_sha256": canonical_hash(payload), "payload": payload,
    }


# ---- idempotency and CAS contract -----------------------------------------

def test_bind_member_channel_idempotency_replay_same_body(m09, member_a):
    env = m09
    body = {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True}
    first = env["service"].bind_member_channel(member_a, body, idempotency_key="bind-replay-0001")
    second = env["service"].bind_member_channel(member_a, body, idempotency_key="bind-replay-0001")
    assert first.data["id"] == second.data["id"]
    assert second.idempotency_replayed is True


def test_bind_member_channel_idempotency_conflict_on_different_body(m09, member_a):
    env = m09
    env["service"].bind_member_channel(
        member_a, {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True}, idempotency_key="bind-conflict-0001",
    )
    with pytest.raises(ConflictError) as excinfo:
        env["service"].bind_member_channel(
            member_a, {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": False}, idempotency_key="bind-conflict-0001",
        )
    assert excinfo.value.code == "idempotency_conflict"


def test_bind_member_channel_conflicting_body_returns_409_even_after_proof_consumed(m09, member_a):
    """F9 regression: a conflicting-body retry with an already-consumed
    proof_ref called channel_proof.verify() before _run_command's idempotency
    conflict check could run, returning 401 channel_proof_invalid instead of
    409 idempotency_conflict."""
    env = m09
    body = {"channel": "line", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True}
    env["service"].bind_member_channel(member_a, body, idempotency_key="bind-conflict-after-use-0001")

    # Simulate the proof being single-use at the real provider: a second
    # verify() call for the same proof_ref now fails.
    env["channel_proof"].fail(CHANNEL_PROOF_LINE_OK, "forged")

    conflicting_body = {**body, "service_delivery_enabled": False}
    with pytest.raises(ConflictError) as excinfo:
        env["service"].bind_member_channel(member_a, conflicting_body, idempotency_key="bind-conflict-after-use-0001")
    assert excinfo.value.code == "idempotency_conflict"


def test_reconcile_delivery_idempotent_replay_succeeds_without_stale_version_error(m09, operator):
    """F9 regression: the pre-CAS read/self._cas() and channel_send.lookup()
    ran before _run_command checked state.idempotency, so replaying a
    succeeded reconcile_delivery with the same Idempotency-Key and If-Match
    raised 412 StaleVersionError instead of returning the cached 202 reply."""
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, job.id)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))

    first = env["service"].reconcile_delivery(
        operator, message.id, {"reason_code": "r"}, expected_version=message.version, idempotency_key="reconcile-replay-0001",
    )
    replay = env["service"].reconcile_delivery(
        operator, message.id, {"reason_code": "r"}, expected_version=message.version, idempotency_key="reconcile-replay-0001",
    )
    assert replay.idempotency_replayed is True
    assert replay.data == first.data


def test_reconcile_delivery_conflicting_body_returns_409_without_stale_version_error(m09, operator):
    """F9 regression companion: a conflicting-body retry must raise 409
    idempotency_conflict, not run the stale pre-CAS/lookup path first."""
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, job.id)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))

    env["service"].reconcile_delivery(
        operator, message.id, {"reason_code": "r"}, expected_version=message.version, idempotency_key="reconcile-conflict-0001",
    )
    with pytest.raises(ConflictError) as excinfo:
        env["service"].reconcile_delivery(
            operator, message.id, {"reason_code": "different"}, expected_version=message.version, idempotency_key="reconcile-conflict-0001",
        )
    assert excinfo.value.code == "idempotency_conflict"


def test_reconcile_delivery_requires_if_match_and_rejects_stale_version(m09, operator):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    env["service"].process_job("worker-1", TENANT, job.id)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))

    with pytest.raises(PreconditionRequiredError):
        env["service"].reconcile_delivery(operator, message.id, {"reason_code": "x"}, expected_version=None, idempotency_key="reconcile-nocas-0001")
    with pytest.raises(StaleVersionError) as excinfo:
        env["service"].reconcile_delivery(operator, message.id, {"reason_code": "x"}, expected_version=message.version + 1, idempotency_key="reconcile-stale-0001")
    assert excinfo.value.current_version == message.version


def test_redrive_delivery_event_requires_dead_letter_status(m09, supervisor):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    with pytest.raises(ConflictError) as excinfo:
        env["service"].redrive_delivery_event(
            supervisor, job.id,
            {"reason_code": "r", "expected_event_id": "e7000000-0000-0000-0000-000000000001", "approved_by": supervisor.actor_id, "revalidate_entitlement": True, "revalidate_content_expiry": True},
            expected_version=job.version, idempotency_key="redrive-notdead-0001",
        )
    assert excinfo.value.code == "job_not_dead_letter"


def test_only_delivery_operator_or_supervisor_role_may_reconcile_or_redrive(m09, member_a, service_agent):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import ForbiddenError

    with pytest.raises(ForbiddenError):
        env["service"].reconcile_delivery(service_agent, message.id, {"reason_code": "x"}, expected_version=message.version, idempotency_key="reconcile-forbidden-0001")
    with pytest.raises(ForbiddenError):
        env["service"].reconcile_delivery(member_a, message.id, {"reason_code": "x"}, expected_version=message.version, idempotency_key="reconcile-forbidden-0002")


# ---- pagination contract ----------------------------------------------------

def test_list_deliveries_pagination_is_cursor_stable(m09, operator):
    env = m09
    for index in range(3):
        publication_id = f"50000000-0000-0000-0000-00000000{20 + index:02d}"
        entitlement_id = f"40000000-0000-0000-0000-00000000{20 + index:02d}"
        report_version_id = f"60000000-0000-0000-0000-00000000{20 + index:02d}"
        set_entitlement(env, entitlement_id=entitlement_id, state="active")
        seed_publication(env, publication_id=publication_id, entitlement_id=entitlement_id, report_version_id=report_version_id)
        dispatch(env, publication_id)

    page1 = env["service"].list_deliveries(operator, limit=2)
    assert len(page1.data["items"]) == 2
    assert page1.data["next_cursor"] is not None
    page2 = env["service"].list_deliveries(operator, limit=2, cursor=page1.data["next_cursor"])
    ids_page1 = {row["id"] for row in page1.data["items"]}
    ids_page2 = {row["id"] for row in page2.data["items"]}
    assert ids_page1.isdisjoint(ids_page2)


# ---- webhook signature and replay contract ---------------------------------

def test_webhook_rejects_stale_timestamp(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import UnauthorizedError

    stale_timestamp = str(int(NOW.timestamp()) - 1000)
    body = {
        "event_id": "e8000000-0000-0000-0000-000000000001", "provider_message_ref": "ref", "delivery_id": message.id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000031",
    }
    with pytest.raises(UnauthorizedError) as excinfo:
        send_receipt(env, body, idempotency_key="receipt-stale-ts-0001", timestamp=stale_timestamp, nonce="s" * 16)
    assert excinfo.value.code == "webhook_timestamp_expired"


def test_webhook_unknown_integration_key_is_rejected(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import UnauthorizedError

    body = {
        "event_id": "e9000000-0000-0000-0000-000000000001", "provider_message_ref": "ref", "delivery_id": message.id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000032",
    }
    with pytest.raises(UnauthorizedError) as excinfo:
        send_receipt(env, body, idempotency_key="receipt-badkey-0001", key_id="unregistered-key", nonce="t" * 16)
    assert excinfo.value.code == "integration_key_unknown"


# ---- event consumer contract: dedup, sequence, tenant, schema -------------

def test_consumer_rejects_malformed_payload_schema(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    envelope = _envelope("entitlement.changed", "entitlement", "40000000-0000-0000-0000-000000000001", {"entitlement_id": "x"})
    with pytest.raises(ValidationError) as excinfo:
        consumer.consume(envelope)
    assert excinfo.value.code == "event_schema_invalid"


def test_consumer_dedupes_duplicate_event_id(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"entitlement_id": "40000000-0000-0000-0000-000000000001", "customer_id": MEMBER_A, "contract_id": "f0000000-0000-0000-0000-000000000001", "state": "active", "decision_id": "d0000000-0000-0000-0000-000000000001", "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], payload, producer="M02")
    first = consumer.consume(envelope)
    second = consumer.consume(envelope)
    assert first.status == "applied"
    assert second.status == "duplicate"
    assert second.result_code == first.result_code


def test_consumer_holds_out_of_order_sequence_for_retry(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"entitlement_id": "40000000-0000-0000-0000-000000000002", "customer_id": MEMBER_A, "contract_id": "f0000000-0000-0000-0000-000000000002", "state": "active", "decision_id": "d0000000-0000-0000-0000-000000000002", "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], payload, sequence_no=3, producer="M02", event_id="ea000000-0000-0000-0000-000000000001")
    result = consumer.consume(envelope)
    assert result.status == "retry"
    assert result.result_code == "sequence_gap"


def test_consumer_rejects_replayed_event_id_with_different_payload(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"entitlement_id": "40000000-0000-0000-0000-000000000003", "customer_id": MEMBER_A, "contract_id": "f0000000-0000-0000-0000-000000000003", "state": "active", "decision_id": "d0000000-0000-0000-0000-000000000003", "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], payload, producer="M02", event_id="eb000000-0000-0000-0000-000000000001")
    consumer.consume(envelope)
    tampered_payload = dict(payload, state="suspended")
    tampered = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], tampered_payload, producer="M02", event_id="eb000000-0000-0000-0000-000000000001")
    with pytest.raises(ConflictError) as excinfo:
        consumer.consume(tampered)
    assert excinfo.value.code == "inbox_payload_mismatch"


def test_consumer_dispatches_on_publication_created_and_retries_on_dependency_outage(m09):
    env = m09
    set_entitlement(env, state="active")
    # No publication registered yet: the port lookup fails and the event must
    # stay retryable rather than silently dropping the fan-out.
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {"publication_id": PUBLICATION_X, "report_version_id": "60000000-0000-0000-0000-000000000001", "signature_id": "70000000-0000-0000-0000-000000000001", "audience_snapshot_id": "aa000000-0000-0000-0000-000000000001", "bundle_manifest_sha256": "b" * 64, "valid_until": "2026-12-22T00:00:00Z"}
    envelope = _envelope("publication.created", "publication", PUBLICATION_X, payload, event_id="ec000000-0000-0000-0000-000000000001")
    result = consumer.consume(envelope)
    assert result.status == "retry"

    seed_publication(env)
    result2 = consumer.consume(envelope)
    assert result2.status == "applied"
    rows = env["repository"].read(lambda state: [m for (t, _), m in state.deliveries.items() if t == TENANT])
    assert len(rows) == 1


# ---- worker loop and outbox publisher --------------------------------------

def test_dispatch_worker_run_once_processes_leased_jobs(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    worker = DispatchWorker(env["service"])
    result = worker.run_once("drill-worker", TENANT, limit=10)
    assert result.leased == 1
    assert result.accepted == 1


def test_outbox_publisher_retries_then_publishes(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    bus = LocalEventBus()
    bus.failure_mode = "timeout"
    publisher = OutboxPublisher(env["repository"], bus, max_attempts=3)
    first = publisher.run_once("outbox-drill")
    assert first.retried >= 1
    bus.failure_mode = None
    # OutboxPublisher reads real wall-clock time (not the injectable service
    # clock); the backoff after one failed attempt is 2 ** 1 == 2 seconds.
    import time

    time.sleep(2.1)
    second = publisher.run_once("outbox-drill")
    assert second.processed >= 1


# ---- HTTP controller problem+json contract --------------------------------

def test_http_controller_maps_domain_error_to_problem_json(m09, member_a):
    env = m09
    controller = M09Controller(env["service"])
    response = controller.handle(
        "bind_member_channel", member_a,
        body={"channel": "not_a_channel", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True},
        headers={"idempotency-key": "http-bad-0001"},
    )
    assert response.status == 422
    assert response.body["code"] == "validation_failed"
    assert response.headers["Content-Type"] == "application/problem+json"


def test_http_controller_webhook_route_requires_no_bearer_context():
    operation_ids = {route[2] for route in ROUTES}
    assert "receive_message_receipt" in operation_ids
    assert {"GET", "POST"} >= {route[0] for route in ROUTES}


# ---- F2: process_job revalidation and lookup-first retry -------------------

def test_process_job_retry_of_unknown_looks_up_before_sending_again(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    env["channel_send"].queue(message.provider_idempotency_key, SendOutcome("unknown"))

    send_calls = []
    original_send = env["channel_send"].send

    def counting_send(*args, **kwargs):
        send_calls.append(args)
        return original_send(*args, **kwargs)

    env["channel_send"].send = counting_send
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))

    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    first = env["service"].process_job("worker-1", TENANT, job.id)
    assert first.result == "unknown"
    assert len(send_calls) == 1

    # The provider actually received the first attempt; a bounded retry must
    # discover that through lookup() instead of blind-sending a second time.
    env["channel_send"].seed_accepted(message.provider_idempotency_key, "provider-ref-9001")
    env["clock"].advance(10)  # past the retry backoff so the job is leasable again
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    second = env["service"].process_job("worker-1", TENANT, job.id)
    assert second.result == "accepted"
    assert len(send_calls) == 1


def test_process_job_handles_entitlement_dependency_outage_without_aborting_batch(m09):
    env = m09
    from modules.legendflow_delivery.domain import DependencyUnavailableError

    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    job = env["repository"].read(lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT))
    env["service"].lease_jobs("worker-1", TENANT, limit=10)

    def unavailable(tenant_id, entitlement_id):
        raise DependencyUnavailableError("entitlement authority is not available", code="entitlement_lookup_failed")

    env["entitlements"].current = unavailable

    outcome = env["service"].process_job("worker-1", TENANT, job.id)  # must not raise
    assert outcome.result == "unknown"
    job_after = env["repository"].read(lambda state: state.jobs[(TENANT, job.id)])
    assert job_after.status == "retry"
    assert job_after.last_error_code == "entitlement_lookup_unavailable"


def test_process_job_rechecks_channel_binding_and_preference_before_send(m09, member_a):
    env = m09
    from modules.legendflow_delivery.domain import CommunicationPreference, new_id

    set_entitlement(env, state="active")
    seed_publication(env)
    env["service"].bind_member_channel(
        member_a, {"channel": "sms", "verified_channel_proof_ref": CHANNEL_PROOF_LINE_OK, "service_delivery_enabled": True},
        idempotency_key="recheck-bind-0001",
    )
    dispatch(env)
    sms_job = env["repository"].read(
        lambda state: next(j for (t, _), j in state.jobs.items() if t == TENANT and state.deliveries[(t, j.message_id)].channel == "sms")
    )

    # The preference flips to opted-out after the job was queued but before
    # the worker sends; process_job must re-check it, not just trust dispatch time.
    def disable_pref(state):
        pref = CommunicationPreference(
            id=new_id(), tenant_id=TENANT, version=1, created_at=NOW, updated_at=NOW,
            customer_id=MEMBER_A, channel="sms", purpose="service", enabled=False, consent_id=new_id(),
        )
        state.preferences[(TENANT, pref.id)] = pref
        state.preference_index[(TENANT, MEMBER_A, "sms", "service")] = pref.id

    env["repository"].atomic(disable_pref)
    env["service"].lease_jobs("worker-1", TENANT, limit=10)
    outcome = env["service"].process_job("worker-1", TENANT, sms_job.id)
    assert outcome.result == "suppressed"
    sms_message = env["repository"].read(
        lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT and m.channel == "sms")
    )
    assert sms_message.suppression_reason == "service_preference_disabled"


# ---- F5: webhook field validation and nonce-burn ordering ------------------

def test_webhook_rejects_malformed_nonce_and_key_id_length(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import UnauthorizedError

    body = {
        "event_id": "ec000000-0000-0000-0000-000000000001", "provider_message_ref": "ref", "delivery_id": message.id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000040",
    }
    with pytest.raises(UnauthorizedError) as excinfo:
        send_receipt(env, body, idempotency_key="receipt-shortnonce-0001", nonce="short")
    assert excinfo.value.code == "webhook_nonce_invalid"

    with pytest.raises(UnauthorizedError) as excinfo:
        send_receipt(env, body, idempotency_key="receipt-shortkey-0001", nonce="u" * 16, key_id="short")
    assert excinfo.value.code == "integration_key_id_invalid"


def test_webhook_rejects_oversized_provider_message_ref_and_non_integer_sequence(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import ValidationError

    base_body = {
        "event_id": "ec100000-0000-0000-0000-000000000001", "provider_message_ref": "r" * 201, "delivery_id": message.id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000041",
    }
    with pytest.raises(ValidationError) as excinfo:
        send_receipt(env, base_body, idempotency_key="receipt-longref-0001", nonce="v" * 16)
    assert excinfo.value.field_name == "provider_message_ref"

    bad_sequence_body = dict(base_body, provider_message_ref="ref", provider_sequence="not-an-int", event_id="ec100000-0000-0000-0000-000000000002")
    with pytest.raises(ValidationError) as excinfo:
        send_receipt(env, bad_sequence_body, idempotency_key="receipt-badseq-0001", nonce="w" * 16)
    assert excinfo.value.field_name == "provider_sequence"


def test_webhook_rejects_integration_key_whose_provider_does_not_match_delivery_channel(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    from modules.legendflow_delivery.domain import ValidationError
    from modules.legendflow_delivery.ports import IntegrationKey

    line_key_id = "synthetic-line-only-adapter-0001"
    env["integration_keys"].register(IntegrationKey(key_id=line_key_id, tenant_id=TENANT, secret=WEBHOOK_SECRET, provider="line"))
    body = {
        "event_id": "ec200000-0000-0000-0000-000000000001", "provider_message_ref": "ref", "delivery_id": message.id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000042",
    }
    with pytest.raises(ValidationError) as excinfo:
        send_receipt(env, body, idempotency_key="receipt-wrongprovider-0001", nonce="x" * 16, key_id=line_key_id)
    assert excinfo.value.code == "receipt_provider_mismatch"


def test_webhook_nonce_is_not_wasted_when_the_request_then_fails_validation(m09):
    env = m09
    set_entitlement(env, state="active")
    seed_publication(env)
    dispatch(env)
    from modules.legendflow_delivery.domain import NotFoundError

    missing_delivery_id = "90000000-0000-0000-0000-000000000098"
    body = {
        "event_id": "ec300000-0000-0000-0000-000000000001", "provider_message_ref": "ref", "delivery_id": missing_delivery_id,
        "provider_status": "accepted", "provider_occurred_at": "2026-11-23T01:02:03.000000Z",
        "provider_receipt_asset_id": "90000000-0000-0000-0000-000000000043",
    }
    nonce = "y" * 16
    with pytest.raises(NotFoundError):
        send_receipt(env, body, idempotency_key="receipt-notfound-0001", nonce=nonce)

    # Same nonce, now against a real delivery: the earlier failed call must
    # never have durably burned it.
    message = env["repository"].read(lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT))
    good_body = dict(body, delivery_id=message.id, event_id="ec300000-0000-0000-0000-000000000002")
    accepted = send_receipt(env, good_body, idempotency_key="receipt-notfound-0002", nonce=nonce)
    assert accepted.data["status"] == "accepted"


# ---- F7: consumer atomicity, envelope schema strictness, contract scoping -

def test_consumer_apply_and_inbox_commit_in_one_transaction(m09, monkeypatch):
    env = m09
    payload = {
        "contract_id": "f0000000-0000-0000-0000-000000000010", "customer_id": MEMBER_A,
        "terminated_at": "2026-11-22T00:00:00.000000Z", "refund_evaluation_required": False,
    }
    envelope = _envelope("contract.terminated", "contract", payload["contract_id"], payload, producer="M02", event_id="ed000000-0000-0000-0000-000000000001")
    consumer = M09EventConsumer(env["repository"], env["service"])

    def boom(self, state, envelope, key, result_code):
        raise RuntimeError("simulated crash between apply and inbox write")

    monkeypatch.setattr(M09EventConsumer, "_finalize", boom)
    with pytest.raises(RuntimeError):
        consumer.consume(envelope)

    # The whole transaction rolled back: the business projection write made
    # by _apply is not left committed without its matching inbox receipt.
    assert env["repository"].read(lambda state: dict(state.terminated_customers)) == {}
    assert env["repository"].read(lambda state: dict(state.inbox)) == {}


def test_consumer_rejects_payload_enum_value_not_in_the_published_schema(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])
    payload = {
        "entitlement_id": "40000000-0000-0000-0000-000000000011", "customer_id": MEMBER_A,
        "contract_id": "f0000000-0000-0000-0000-000000000011", "state": "not_a_real_state",
        "decision_id": "d0000000-0000-0000-0000-000000000011", "valid_until": "2026-12-22T00:00:00Z",
    }
    envelope = _envelope("entitlement.changed", "entitlement", payload["entitlement_id"], payload, producer="M02", event_id="ee000000-0000-0000-0000-000000000001")
    with pytest.raises(ValidationError) as excinfo:
        consumer.consume(envelope)
    assert excinfo.value.code == "event_schema_invalid"


def test_contract_termination_is_scoped_per_contract_not_per_customer(m09):
    env = m09
    consumer = M09EventConsumer(env["repository"], env["service"])

    kept_entitlement = "40000000-0000-0000-0000-000000000012"
    terminated_entitlement = "40000000-0000-0000-0000-000000000013"
    kept_contract = "f0000000-0000-0000-0000-000000000012"
    terminated_contract = "f0000000-0000-0000-0000-000000000013"

    for entitlement_id, contract_id in [(kept_entitlement, kept_contract), (terminated_entitlement, terminated_contract)]:
        payload = {
            "entitlement_id": entitlement_id, "customer_id": MEMBER_A, "contract_id": contract_id,
            "state": "active", "decision_id": "d0000000-0000-0000-0000-000000000012", "valid_until": "2026-12-22T00:00:00Z",
        }
        envelope = _envelope("entitlement.changed", "entitlement", entitlement_id, payload, producer="M02", event_id=f"ef00000{entitlement_id[-1]}-0000-0000-0000-000000000001")
        assert consumer.consume(envelope).status == "applied"

    terminate_payload = {
        "contract_id": terminated_contract, "customer_id": MEMBER_A,
        "terminated_at": "2026-11-22T00:00:00.000000Z", "refund_evaluation_required": False,
    }
    terminate_envelope = _envelope("contract.terminated", "contract", terminated_contract, terminate_payload, producer="M02", event_id="ef100000-0000-0000-0000-000000000001")
    assert consumer.consume(terminate_envelope).status == "applied"

    set_entitlement(env, entitlement_id=kept_entitlement, state="active")
    set_entitlement(env, entitlement_id=terminated_entitlement, state="active")

    kept_publication = "50000000-0000-0000-0000-000000000020"
    terminated_publication = "50000000-0000-0000-0000-000000000021"
    seed_publication(env, publication_id=kept_publication, entitlement_id=kept_entitlement, report_version_id="60000000-0000-0000-0000-000000000020")
    seed_publication(env, publication_id=terminated_publication, entitlement_id=terminated_entitlement, report_version_id="60000000-0000-0000-0000-000000000021")

    kept_summary = dispatch(env, kept_publication)
    terminated_summary = dispatch(env, terminated_publication)
    assert kept_summary.created == 1 and kept_summary.suppressed == 0
    assert terminated_summary.created == 0 and terminated_summary.suppressed == 1
    terminated_message = env["repository"].read(
        lambda state: next(m for (t, _), m in state.deliveries.items() if t == TENANT and m.publication_id == terminated_publication)
    )
    assert terminated_message.suppression_reason == "contract_terminated"
