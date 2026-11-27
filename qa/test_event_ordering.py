"""Ordering rule from events/README.md: only full-stream subscribers demand contiguity."""
from __future__ import annotations

import json

import pytest

from longhao_platform.contracts import ROOT
from longhao_platform.event_ordering import is_sequence_gap, requires_contiguous_sequence


@pytest.mark.parametrize("consumer,producer,aggregate", [
    ("M02", "M01", "application"),        # application.created is the whole M01 application stream
    ("M09", "M08", "publication"),        # created + withdrawn + corrected
    ("M02", "M04", "business_clearance"),
    ("M12", "M14", "revenue_recognition"),
])
def test_full_stream_subscribers_require_contiguous_sequences(consumer, producer, aggregate):
    assert requires_contiguous_sequence(consumer, producer, aggregate)
    envelope = {"sequence_no": 3, "producer": producer, "aggregate_type": aggregate}
    assert is_sequence_gap(consumer, envelope, last_sequence=1)
    assert not is_sequence_gap(consumer, {**envelope, "sequence_no": 2}, last_sequence=1)


@pytest.mark.parametrize("consumer,producer,aggregate", [
    ("M09", "M01", "customer"),           # identity_linked only; customer.created is seq 1
    ("M03", "M02", "application"),        # customer_signed only
    ("M08", "M07", "report_version"),     # signature_verified follows version_frozen
    ("M06", "M05", "research_candidate"),
    ("M13", "M08", "publication"),
])
def test_partial_subscribers_accept_forward_gaps(consumer, producer, aggregate):
    assert not requires_contiguous_sequence(consumer, producer, aggregate)
    assert not is_sequence_gap(consumer, {"sequence_no": 7, "producer": producer, "aggregate_type": aggregate}, last_sequence=1)


def test_unknown_streams_never_block():
    assert not requires_contiguous_sequence("M01", "M99", "nothing")


def test_every_registry_subscription_is_classified():
    rows = json.loads((ROOT / "events/registry.json").read_text(encoding="utf-8"))
    for row in rows:
        for consumer in row["consumers"]:
            assert isinstance(requires_contiguous_sequence(consumer, row["producer"], row["aggregate_type"]), bool)
