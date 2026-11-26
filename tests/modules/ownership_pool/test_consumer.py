from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from modules.ownership_pool.domain import ConflictError, NotFoundError, ValidationError, canonical_hash, wire_time

from conftest import (
    NOW,
    SALES_A,
    TENANT,
    _envelope,
    contract_effective_envelope,
    contract_service_ended_envelope,
    contract_terminated_envelope,
    seed_policy,
)


CUSTOMER = "60000000-0000-0000-0000-000000000001"
CONTRACT = "70000000-0000-0000-0000-000000000001"


def test_contract_effective_establishes_company_pool_baseline(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    result = m11["consumer"].consume(envelope)
    assert result.status == "applied"
    assert result.result_code == "contract_window_recorded"
    snapshot = m11["repository"].snapshot()
    assignment_id = snapshot.current_assignment[(TENANT, CUSTOMER)]
    assert snapshot.assignments[(TENANT, assignment_id)].owner_type == "company_pool"
    entries = [entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER]
    assert len(entries) == 1
    assert entries[0].entry_reason == "unassigned_web"


def test_contract_effective_propagates_inbound_correlation_and_causation_ids(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    m11["consumer"].consume(envelope)
    snapshot = m11["repository"].snapshot()
    baseline_events = [
        event for event in snapshot.outbox.values()
        if event.event_type in {"ownership.assignment_changed", "ownership.pool_entered"}
    ]
    assert len(baseline_events) == 2
    for event in baseline_events:
        assert event.correlation_id == envelope["correlation_id"]
        assert event.causation_id == envelope["event_id"]


def test_contract_effective_for_a_restricted_customer_creates_a_suppressed_baseline(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    m11["restriction"].restrict(TENANT, CUSTOMER)
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    m11["consumer"].consume(envelope)
    snapshot = m11["repository"].snapshot()
    entry = next(entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER)
    assert entry.state == "suppressed"
    assert entry.suppressed_reason == "compliance_hold"


def test_envelope_with_mismatched_classification_is_rejected(m11):
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    envelope["classification"] = "internal"
    with pytest.raises(ValidationError) as excinfo:
        m11["consumer"].consume(envelope)
    assert excinfo.value.code == "event_schema_invalid"


def test_envelope_with_non_uuid_correlation_id_is_rejected(m11):
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    envelope["correlation_id"] = "not-a-uuid"
    with pytest.raises(ValidationError) as excinfo:
        m11["consumer"].consume(envelope)
    assert excinfo.value.code == "event_schema_invalid"


def test_contract_effective_without_policy_records_window_but_skips_pool_baseline(m11):
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    result = m11["consumer"].consume(envelope)
    assert result.status == "applied"
    snapshot = m11["repository"].snapshot()
    assert (TENANT, CONTRACT) in snapshot.contracts
    assert (TENANT, CUSTOMER) not in snapshot.current_assignment


def test_duplicate_event_id_is_a_no_op(m11, supervisor):
    seed_policy(m11["service"], supervisor, supervisor)
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    first = m11["consumer"].consume(envelope)
    second = m11["consumer"].consume(envelope)
    assert first.status == "applied"
    assert second.status == "duplicate"
    snapshot = m11["repository"].snapshot()
    entries = [entry for entry in snapshot.pool_entries.values() if entry.customer_id == CUSTOMER]
    assert len(entries) == 1  # not recreated


def test_replayed_event_id_with_different_payload_is_rejected(m11):
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    m11["consumer"].consume(envelope)
    tampered = dict(envelope)
    tampered_payload = dict(envelope["payload"])
    tampered_payload["service_ends_at"] = tampered_payload["service_ends_at"].replace("2027", "2099")
    tampered["payload"] = tampered_payload
    tampered["payload_sha256"] = canonical_hash(tampered_payload)  # internally consistent, but differs from what was stored
    with pytest.raises(ConflictError) as excinfo:
        m11["consumer"].consume(tampered)
    assert excinfo.value.code == "inbox_payload_mismatch"


def test_unsupported_event_type_is_rejected(m11):
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    envelope["event_type"] = "contract.renewed"
    with pytest.raises(ValidationError) as excinfo:
        m11["consumer"].consume(envelope)
    assert excinfo.value.code == "unsupported_event_type"


def test_sequence_gap_is_held_for_retry(m11):
    envelope = contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365), sequence_no=5)
    result = m11["consumer"].consume(envelope)
    assert result.status == "retry"
    assert result.result_code == "sequence_gap"
    snapshot = m11["repository"].snapshot()
    assert (TENANT, CONTRACT) not in snapshot.contracts


def test_terminated_event_for_unknown_contract_is_not_found(m11):
    envelope = contract_terminated_envelope(CUSTOMER, CONTRACT, terminated_at=NOW, sequence_no=1)
    with pytest.raises(NotFoundError):
        m11["consumer"].consume(envelope)


def test_service_ended_tracks_each_contract_service_independently(m11):
    service_a = "70000000-0000-0000-0000-0000000000a1"
    service_b = "70000000-0000-0000-0000-0000000000a2"
    effective = m11["consumer"].consume(
        contract_effective_envelope(CUSTOMER, CONTRACT, starts_at=NOW, ends_at=NOW + timedelta(days=365))
    )
    assert effective.status == "applied"

    # Two different contract_service_ids under the same contract_id, with the
    # *later*-processed event actually ending *earlier*: the historical bug
    # overwrote a single scalar last-write-wins, so this ordering would have
    # silently discarded service_a's later end time in favor of service_b's
    # earlier one.
    first = m11["consumer"].consume(
        contract_service_ended_envelope(CUSTOMER, CONTRACT, service_a, ended_at=NOW + timedelta(days=10))
    )
    assert first.status == "applied" and first.result_code == "service_end_recorded"
    second = m11["consumer"].consume(
        contract_service_ended_envelope(CUSTOMER, CONTRACT, service_b, ended_at=NOW + timedelta(days=5))
    )
    assert second.status == "applied" and second.result_code == "service_end_recorded"

    snapshot = m11["repository"].snapshot()
    window = snapshot.contracts[(TENANT, CONTRACT)]
    assert window.service_ended_ats == {
        service_a: NOW + timedelta(days=10),
        service_b: NOW + timedelta(days=5),
    }
    # effective_end() takes the latest known per-service end, not whichever
    # event happened to be processed last.
    assert window.effective_end() == NOW + timedelta(days=10)
    assert (TENANT, service_a) in snapshot.service_ends
    assert (TENANT, service_b) in snapshot.service_ends


def test_self_consumed_employee_departed_is_a_defensive_noop(m11):
    envelope = _envelope(
        event_type="ownership.employee_departed",
        producer="M11",
        aggregate_type="employee",
        aggregate_id=SALES_A,
        aggregate_version=1,
        sequence_no=1,
        payload={"employee_id": SALES_A, "departure_id": str(uuid4()), "effective_at": wire_time(NOW)},
    )
    result = m11["consumer"].consume(envelope)
    assert result.status == "applied"
    assert result.result_code == "self_departure_confirmation_noop"
