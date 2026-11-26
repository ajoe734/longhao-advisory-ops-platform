from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Callable, TypeVar

from .domain import (
    AuditEvent,
    CaseInteraction,
    CaseResolution,
    IdempotencyReceipt,
    InboxReceipt,
    Job,
    OutboxEvent,
    ResponseApproval,
    ResponseAttempt,
    ResponseDispatch,
    ResponseDraft,
    ResponseReceipt,
    ServiceCase,
    SlaEvent,
)


@dataclass
class StoreState:
    cases: dict[tuple[str, str], ServiceCase] = field(default_factory=dict)
    case_number_index: dict[tuple[str, str], str] = field(default_factory=dict)
    interactions: dict[tuple[str, str], CaseInteraction] = field(default_factory=dict)
    sla_events: dict[tuple[str, str], SlaEvent] = field(default_factory=dict)
    response_drafts: dict[tuple[str, str], ResponseDraft] = field(default_factory=dict)
    response_approvals: dict[tuple[str, str], ResponseApproval] = field(default_factory=dict)
    response_dispatches: dict[tuple[str, str], ResponseDispatch] = field(default_factory=dict)
    response_attempts: dict[tuple[str, str], ResponseAttempt] = field(default_factory=dict)
    response_receipts: dict[tuple[str, str], ResponseReceipt] = field(default_factory=dict)
    case_resolutions: dict[tuple[str, str], CaseResolution] = field(default_factory=dict)
    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
    jobs: dict[tuple[str, str], Job] = field(default_factory=dict)
    webhook_nonces: dict[tuple[str, str, str], datetime] = field(default_factory=dict)


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
