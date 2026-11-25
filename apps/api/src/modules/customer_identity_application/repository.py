from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable, TypeVar
from typing import Any

from .domain import (
    Application,
    AuditEvent,
    Consent,
    Customer,
    ExternalIdentity,
    IdempotencyReceipt,
    InboxReceipt,
    Job,
    MergeCase,
    OutboxEvent,
)


@dataclass
class StoreState:
    customers: dict[tuple[str, str], Customer] = field(default_factory=dict)
    identities: dict[tuple[str, str], ExternalIdentity] = field(default_factory=dict)
    identity_index: dict[tuple[str, str, str], str] = field(default_factory=dict)
    contact_index: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    applications: dict[tuple[str, str], Application] = field(default_factory=dict)
    consents: dict[tuple[str, str], Consent] = field(default_factory=dict)
    merge_cases: dict[tuple[str, str], MergeCase] = field(default_factory=dict)
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
    jobs: dict[tuple[str, str], Job] = field(default_factory=dict)
    automation_runs: dict[tuple[str, str], Any] = field(default_factory=dict)
    automation_reviews: dict[tuple[str, str], Any] = field(default_factory=dict)


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
