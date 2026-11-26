"""Contract/CAS/idempotency behavior, the HTTP boundary, and worker tasks.

Covers everything the UAT file does not: the generic command-envelope
contract (idempotency replay vs conflict, CAS 412/428), problem+json error
shapes through ``M10Controller``, the event consumer's sequence-gap/dead
inbox-payload-mismatch handling, and the outbox publisher's
timeout/unknown/dead-letter recovery drill (mirrors
``workers/tasks/legendflow_delivery``'s pattern for M09).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from conftest import make_envelope

from modules.crm.domain import (
    ConflictError,
    PreconditionRequiredError,
    StaleVersionError,
    ValidationError,
)
from modules.crm.http import M10Controller
from crm.tasks import LocalEventBus, OutboxPublisher, RetryableTaskError, UnknownExternalResult


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


# ---- idempotency / CAS contract ------------------------------------------


def test_idempotency_key_replay_returns_identical_response(service, actors):
    body = {
        "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
        "reason_code": "renewal_due", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
    }
    first = service.create_opportunity(actors.sales(), body, idempotency_key="contract-replay-00000001")
    second = service.create_opportunity(actors.sales(), body, idempotency_key="contract-replay-00000001")
    assert first.data == second.data
    assert first.idempotency_replayed is False
    assert second.idempotency_replayed is True


def test_idempotency_key_reused_with_different_payload_conflicts(service, actors):
    body_a = {
        "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
        "reason_code": "renewal_due", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
    }
    body_b = {**body_a, "service_code": "advisory-pro"}
    service.create_opportunity(actors.sales(), body_a, idempotency_key="contract-conflict-00000001")
    with pytest.raises(ConflictError) as excinfo:
        service.create_opportunity(actors.sales(), body_b, idempotency_key="contract-conflict-00000001")
    assert excinfo.value.code == "idempotency_conflict"


def test_missing_if_match_is_428(service, actors):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "renewal_due", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="contract-428-00000001",
    )
    with pytest.raises(PreconditionRequiredError):
        service.set_next_action(
            actors.sales(), opp.data["id"], {
                "owner_id": actors.sales_id, "action_type": "call", "due_at": _iso(_now() + timedelta(days=1)), "reason_code": "x",
            }, expected_version=None, idempotency_key="contract-428-00000002",
        )


def test_stale_if_match_is_412(service, actors):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "renewal_due", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="contract-412-00000001",
    )
    service.set_next_action(
        actors.sales(), opp.data["id"], {
            "owner_id": actors.sales_id, "action_type": "call", "due_at": _iso(_now() + timedelta(days=1)), "reason_code": "x",
        }, expected_version=1, idempotency_key="contract-412-00000002",
    )
    with pytest.raises(StaleVersionError) as excinfo:
        service.set_next_action(
            actors.sales(), opp.data["id"], {
                "owner_id": actors.sales_id, "action_type": "call", "due_at": _iso(_now() + timedelta(days=1)), "reason_code": "x",
            }, expected_version=1, idempotency_key="contract-412-00000003",
        )
    assert excinfo.value.current_version == 2


def test_unknown_opportunity_is_404(service, actors):
    from modules.crm.domain import NotFoundError

    with pytest.raises(NotFoundError):
        service.get_opportunity(actors.sales(), _uuid())


# ---- HTTP boundary (framework-neutral controller) -------------------------


def test_http_controller_maps_domain_error_to_problem_json(service, actors):
    controller = M10Controller(service)
    response = controller.handle(
        "create_opportunity", actors.sales(),
        body={"customer_id": "not-a-uuid", "type": "renewal", "service_code": "x", "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now())},
        headers={"idempotency-key": "http-400-000000001"},
    )
    assert response.status == 422
    assert response.body["code"] == "validation_failed"
    assert response.body["correlation_id"]
    assert response.headers["Content-Type"] == "application/problem+json"


def test_http_controller_echoes_idempotency_replayed_header(service, actors):
    controller = M10Controller(service)
    body = {"customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic", "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5))}
    headers = {"idempotency-key": "http-replay-000000001"}
    first = controller.handle("create_opportunity", actors.sales(), body=body, headers=headers)
    second = controller.handle("create_opportunity", actors.sales(), body=body, headers=headers)
    assert first.headers["Idempotency-Replayed"] == "false"
    assert second.headers["Idempotency-Replayed"] == "true"
    assert first.body == second.body


def test_http_controller_rejects_malformed_if_match(service, actors):
    controller = M10Controller(service)
    response = controller.handle(
        "set_next_action", actors.sales(), path={"opportunity_id": _uuid()},
        body={"owner_id": actors.sales_id, "action_type": "call", "due_at": _iso(_now()), "reason_code": "x"},
        headers={"idempotency-key": "http-ifmatch-00000001", "if-match": "not-an-etag"},
    )
    assert response.status == 422
    assert response.body["code"] == "validation_failed"


def test_http_controller_unknown_operation_id(service, actors):
    controller = M10Controller(service)
    response = controller.handle("not_a_real_operation", actors.sales())
    assert response.status == 422
    assert response.body["code"] == "operation_not_found"


# ---- event consumer contract ----------------------------------------------


def test_consumer_rejects_payload_hash_mismatch_on_replay(consumer, actors):
    from modules.crm.domain import canonical_hash

    envelope = make_envelope(
        event_type="customer.created", tenant_id=actors.tenant_id, producer="M01", aggregate_type="customer",
        aggregate_id=_uuid(), payload={"customer_id": _uuid(), "marketing_attribution_id": None},
    )
    assert consumer.consume(envelope).status == "applied"
    # Same event_id, internally-consistent hash, but a genuinely different
    # payload -- the redelivery itself was tampered with (or corrupted),
    # not merely a hash/content mismatch within one envelope.
    retampered_payload = {**envelope["payload"], "marketing_attribution_id": _uuid()}
    tampered = {**envelope, "payload": retampered_payload, "payload_sha256": canonical_hash(retampered_payload)}
    with pytest.raises(ConflictError) as excinfo:
        consumer.consume(tampered)
    assert excinfo.value.code == "inbox_payload_mismatch"


def test_consumer_holds_sequence_gap_for_retry(consumer, actors):
    application_id = _uuid()
    gap_envelope = make_envelope(
        event_type="application.company_decided", tenant_id=actors.tenant_id, producer="M02", aggregate_type="application",
        aggregate_id=application_id, sequence_no=2,
        payload={"application_id": application_id, "acceptance_id": _uuid(), "decision": "accept", "contract_id": _uuid()},
    )
    result = consumer.consume(gap_envelope)
    assert result.status == "retry"
    assert result.result_code == "sequence_gap"

    predecessor = make_envelope(
        event_type="application.company_decided", tenant_id=actors.tenant_id, producer="M02", aggregate_type="application",
        aggregate_id=application_id, sequence_no=1,
        payload={"application_id": application_id, "acceptance_id": _uuid(), "decision": "request_changes", "contract_id": None},
    )
    assert consumer.consume(predecessor).status == "applied"
    assert consumer.consume(gap_envelope).status == "applied"


def test_consumer_rejects_unsupported_event_type(consumer, actors):
    envelope = {
        "event_id": _uuid(), "event_type": "not.a.real.event", "schema_version": "1.0.0", "tenant_id": actors.tenant_id,
        "producer": "M99", "aggregate_type": "thing", "aggregate_id": _uuid(), "aggregate_version": 1, "sequence_no": 1,
        "occurred_at": _iso(_now()), "emitted_at": _iso(_now()), "correlation_id": _uuid(), "causation_id": None,
        "idempotency_key": "whatever", "classification": "customer_confidential", "payload_sha256": "0" * 64, "payload": {},
    }
    with pytest.raises(ValidationError) as excinfo:
        consumer.consume(envelope)
    assert excinfo.value.code == "unsupported_event_type"


# ---- outbox publisher (lease / retry / dead-letter) ------------------------


def test_outbox_publisher_resolves_unknown_result_via_lookup(repository, service, actors):
    service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="outbox-unknown-00000001",
    )

    class FlakyBus(LocalEventBus):
        def __init__(self) -> None:
            super().__init__()
            self._first_call = True

        def publish(self, envelope):
            if self._first_call:
                self._first_call = False
                self.envelopes[envelope["event_id"]] = envelope  # provider actually received it
                raise UnknownExternalResult(envelope["event_id"])
            return super().publish(envelope)

    bus = FlakyBus()
    publisher = OutboxPublisher(repository, bus)
    result = publisher.run_once("drill-worker")
    assert result.processed == 1
    assert result.retried == 0
    assert result.dead_lettered == 0


def test_refund_confirmed_reverses_an_approved_conversion(consumer, service, actors, evidence):
    opp = service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="refund-opp-00000001",
    )
    ev = _uuid()
    evidence.register(actors.tenant_id, ev)
    activity = service.record_sales_activity(
        actors.sales(), opp.data["id"], {
            "activity_type": "completed_call", "occurred_at": _iso(_now()), "customer_response_evidence_id": ev,
            "activity_evidence_asset_id": ev, "needs_summary": "x", "next_action": "y",
            "next_action_due_at": _iso(_now() + timedelta(days=1)), "outcome": "converted",
        }, expected_version=1, idempotency_key="refund-activity-00000001",
    )
    contract_service_id = _uuid()
    claim = service.claim_conversion(
        actors.sales(), opp.data["id"], {
            "application_id": _uuid(), "contract_id": contract_service_id, "finance_transaction_ref": _uuid(),
            "sales_activity_ids": [activity.data["id"]], "conversion_type": "renewal",
            "claimed_salesperson_id": actors.sales_id, "reason_code": "x",
        }, expected_version=2, idempotency_key="refund-claim-00000001",
    )
    decision = service.decide_conversion(
        actors.supervisor(), claim.data["id"], {
            "decision": "approve", "reason_code": "x", "approved_salesperson_id": actors.sales_id,
            "causality_evidence_asset_id": ev,
        }, expected_version=1, idempotency_key="refund-decide-00000001",
    )
    assert decision.data["state"] == "approved"
    outbox_before = len(service.repository.snapshot().outbox)

    refund_envelope = make_envelope(
        event_type="finance.refund_confirmed", tenant_id=actors.tenant_id, producer="M14", aggregate_type="refund",
        aggregate_id=_uuid(),
        payload={"refund_id": _uuid(), "contract_id": contract_service_id, "original_payment_id": _uuid(), "allocation_snapshot_id": _uuid(), "confirmed_at": _iso(_now())},
    )
    result = consumer.consume(refund_envelope)
    assert result.status == "applied"
    assert result.result_code == "conversions_reversed:1"

    snapshot = service.repository.snapshot()
    stored = snapshot.conversions[(actors.tenant_id, claim.data["id"])]
    assert stored.decision_status == "reversed"
    assert stored.sales_actor_id == actors.sales_id, "reversal preserves who the clawback applies to"

    outbox_after = snapshot.outbox
    assert len(outbox_after) == outbox_before + 1
    reversed_events = [event for event in outbox_after.values() if event.event_type == "crm.conversion_decided" and event.payload["decision"] == "reversed"]
    assert len(reversed_events) == 1
    assert reversed_events[0].payload["contract_id"] == contract_service_id

    # Replaying the same refund event must not reverse it a second time or
    # emit a second crm.conversion_decided(reversed) event.
    assert consumer.consume(refund_envelope).status == "duplicate"
    assert len(service.repository.snapshot().outbox) == outbox_before + 1


def test_refund_confirmed_for_unknown_contract_is_a_no_op(consumer, actors):
    refund_envelope = make_envelope(
        event_type="finance.refund_confirmed", tenant_id=actors.tenant_id, producer="M14", aggregate_type="refund",
        aggregate_id=_uuid(),
        payload={"refund_id": _uuid(), "contract_id": _uuid(), "original_payment_id": _uuid(), "allocation_snapshot_id": _uuid(), "confirmed_at": _iso(_now())},
    )
    result = consumer.consume(refund_envelope)
    assert result.status == "applied"
    assert result.result_code == "no_approved_conversion_for_contract"


def test_outbox_publisher_dead_letters_after_max_attempts(repository, service, actors):
    service.create_opportunity(
        actors.sales(), {
            "customer_id": _uuid(), "type": "renewal", "service_code": "advisory-basic",
            "reason_code": "x", "owner_id": actors.sales_id, "due_at": _iso(_now() + timedelta(days=5)),
        }, idempotency_key="outbox-deadletter-00000001",
    )

    class AlwaysTimeout(LocalEventBus):
        def publish(self, envelope):
            raise RetryableTaskError("synthetic_timeout")

    publisher = OutboxPublisher(repository, AlwaysTimeout(), max_attempts=3)
    for attempt in range(3):
        result = publisher.run_once("drill-worker")
        if attempt < 2:
            assert result.retried == 1
            assert result.dead_lettered == 0
            # The real backoff delay (2**attempt_count seconds) has not
            # elapsed between these two immediately-successive calls; clear
            # it to simulate the scheduler's next tick actually arriving,
            # rather than sleeping in a unit test.
            _clear_next_attempt(repository)
        else:
            assert result.dead_lettered == 1

    outbox = repository.snapshot().outbox
    assert any(event.status == "dead_letter" for event in outbox.values())


def _clear_next_attempt(repository) -> None:
    def clear(state):
        for event in state.outbox.values():
            event.next_attempt_at = None

    repository.atomic(lambda state: (clear(state), None)[1])
