"""Per-aggregate ordering rule for event consumers (events/README.md).

``sequence_no`` is contiguous within a tenant/aggregate stream across *all* of
the producer's event types for that aggregate. Only a consumer that subscribes
to the full stream may demand contiguous sequence numbers; a partial subscriber
will always see gaps (the events it does not subscribe to) and must instead
accept forward jumps while still rejecting stale or replayed sequence numbers.
"""
from __future__ import annotations

import json
from functools import lru_cache

from longhao_platform.contracts import ROOT


@lru_cache(maxsize=1)
def _streams() -> tuple[dict[tuple[str, str], frozenset[str]], dict[tuple[str, str, str], frozenset[str]]]:
    rows = json.loads((ROOT / "events/registry.json").read_text(encoding="utf-8"))
    streams: dict[tuple[str, str], set[str]] = {}
    subscriptions: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        streams.setdefault((row["producer"], row["aggregate_type"]), set()).add(row["event_type"])
        for consumer in row["consumers"]:
            subscriptions.setdefault((consumer, row["producer"], row["aggregate_type"]), set()).add(row["event_type"])
    return (
        {key: frozenset(value) for key, value in streams.items()},
        {key: frozenset(value) for key, value in subscriptions.items()},
    )


def requires_contiguous_sequence(consumer: str, producer: str, aggregate_type: str) -> bool:
    """True only when ``consumer`` subscribes to every event type of that aggregate stream."""
    streams, subscriptions = _streams()
    stream = streams.get((producer, aggregate_type))
    subscribed = subscriptions.get((consumer, producer, aggregate_type))
    return bool(stream) and subscribed == stream


def is_sequence_gap(consumer: str, envelope: dict, last_sequence: int) -> bool:
    """A blocking gap: a full-stream subscriber received a sequence beyond the next one."""
    if envelope["sequence_no"] <= last_sequence + 1:
        return False
    return requires_contiguous_sequence(consumer, envelope["producer"], envelope["aggregate_type"])
