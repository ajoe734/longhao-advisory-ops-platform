from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    Assignment,
    AuditEvent,
    ContractWindow,
    EmployeeDeparture,
    IdempotencyReceipt,
    InboxReceipt,
    Job,
    OutboxEvent,
    PolicyVersion,
    PoolEntry,
    PoolEvaluation,
)


@dataclass
class StoreState:
    policies: dict[tuple[str, str], PolicyVersion] = field(default_factory=dict)
    policy_by_code: dict[tuple[str, str], str] = field(default_factory=dict)
    assignments: dict[tuple[str, str], Assignment] = field(default_factory=dict)
    current_assignment: dict[tuple[str, str], str] = field(default_factory=dict)
    pool_entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    pool_entry_by_assignment: dict[tuple[str, str], str] = field(default_factory=dict)
    departures: dict[tuple[str, str], EmployeeDeparture] = field(default_factory=dict)
    evaluations: dict[tuple[str, str], PoolEvaluation] = field(default_factory=dict)

    # read-only local mirrors of upstream events (never the authority for live
    # authorization/financial decisions; see ports.py)
    contracts: dict[tuple[str, str], ContractWindow] = field(default_factory=dict)
    service_ends: dict[tuple[str, str], tuple[str, Any]] = field(default_factory=dict)

    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
    jobs: dict[tuple[str, str], Job] = field(default_factory=dict)


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
