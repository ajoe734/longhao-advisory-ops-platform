from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    AdvisoryReport,
    AuditEvent,
    IdempotencyReceipt,
    InboxReceipt,
    OutboxEvent,
    ReportVersion,
    ReviewDecision,
    Signature,
    SignatureRequest,
    SignatureRevocation,
)


@dataclass
class StoreState:
    reports: dict[tuple[str, str], AdvisoryReport] = field(default_factory=dict)
    versions: dict[tuple[str, str], ReportVersion] = field(default_factory=dict)
    reviews: dict[tuple[str, str], ReviewDecision] = field(default_factory=dict)
    signature_requests: dict[tuple[str, str], SignatureRequest] = field(default_factory=dict)
    signatures: dict[tuple[str, str], Signature] = field(default_factory=dict)
    revocations: dict[tuple[str, str], SignatureRevocation] = field(default_factory=dict)
    superseded_candidates: set[tuple[str, str]] = field(default_factory=set)
    automation_runs: dict[tuple[str, str], Any] = field(default_factory=dict)
    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)


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
