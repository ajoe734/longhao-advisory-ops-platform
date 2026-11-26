from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    AuditEvent,
    CommissionAdjustment,
    CommissionEntry,
    CommissionPolicy,
    CommissionRun,
    IdempotencyReceipt,
    InboxReceipt,
    Job,
    OutboxEvent,
    PayoutBatch,
    PayoutLine,
    PendingAdjustmentDraft,
)


@dataclass
class StoreState:
    policies: dict[tuple[str, str], CommissionPolicy] = field(default_factory=dict)
    policy_version_counters: dict[tuple[str, str], int] = field(default_factory=dict)
    runs: dict[tuple[str, str], CommissionRun] = field(default_factory=dict)
    run_keys: dict[tuple[str, str], str] = field(default_factory=dict)
    entries: dict[tuple[str, str], CommissionEntry] = field(default_factory=dict)
    adjustments: dict[tuple[str, str], CommissionAdjustment] = field(default_factory=dict)
    adjustment_dedupe: dict[tuple[str, str], str] = field(default_factory=dict)
    pending_adjustment_drafts: dict[tuple[str, str], PendingAdjustmentDraft] = field(default_factory=dict)
    payout_batches: dict[tuple[str, str], PayoutBatch] = field(default_factory=dict)
    payout_lines: dict[tuple[str, str], PayoutLine] = field(default_factory=dict)
    payout_line_by_entry: dict[tuple[str, str], str] = field(default_factory=dict)
    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
    jobs: dict[tuple[str, str], Job] = field(default_factory=dict)


T = TypeVar("T")


class InMemoryRepository:
    """Copy-on-write transaction adapter used by the module, worker and UAT tests.

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
