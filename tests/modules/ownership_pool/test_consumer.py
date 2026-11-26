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
