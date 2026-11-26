from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    AuditEvent,
    CandidateEvidence,
    IdempotencyReceipt,
    InboxReceipt,
    Job,
    OutboxEvent,
    ResearchCandidate,
)


@dataclass
class StoreState:
    candidates: dict[tuple[str, str], ResearchCandidate] = field(default_factory=dict)
    # (tenant, producer, external_event_id) -> candidate_id ; business-level source dedup.
    candidates_by_event: dict[tuple[str, str, str], str] = field(default_factory=dict)
    # (tenant, producer, external_event_id) -> sha256 of the normalized ingest payload that
    # first claimed this event id; a resend with a different fingerprint is a payload conflict,
    # not a duplicate, even though both carry the same external_event_id.
    candidate_event_fingerprints: dict[tuple[str, str, str], str] = field(default_factory=dict)
    # (tenant, producer, external_candidate_id) -> candidate_id of the current (non-superseded) row.
    candidate_lineage_head: dict[tuple[str, str, str], str] = field(default_factory=dict)
    evidence: dict[tuple[str, str], CandidateEvidence] = field(default_factory=dict)
    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
    jobs: dict[tuple[str, str], Job] = field(default_factory=dict)
    automation_runs: dict[tuple[str, str], Any] = field(default_factory=dict)
    safety_receipts: dict[tuple[str, str], Any] = field(default_factory=dict)


T = TypeVar("T")


class InMemoryRepository:
    """Copy-on-write transaction adapter used by module and UAT tests.

    The callback's state, audit, outbox, inbox and idempotency changes become
    visible together. An exception rolls the complete transaction back.
    """

    is_fake = True

    def __init__(self) -> None:
        self._state = StoreState()
        self._lock = RLock()

    def atomic(self, callback: Callable[[StoreState], T]) -> T:
        with self._lock:
            working = deepcopy(self._state)
            result = callback(working)
            self._state = working
            return deepcopy(result)

    def read(self, callback: Callable[[StoreState], T]) -> T:
        with self._lock:
            return deepcopy(callback(self._state))

    def snapshot(self) -> StoreState:
        return self.read(lambda state: state)

    def replace_for_test(self, state: StoreState) -> None:
        with self._lock:
            self._state = deepcopy(state)
