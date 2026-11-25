from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    AuditEvent,
    IdempotencyReceipt,
    InboxReceipt,
    KycSubmission,
    OutboxEvent,
    RefreshCase,
    SignedApplicationProjection,
    SubmissionDocument,
    SuitabilityAssessment,
)


@dataclass
class StoreState:
    submissions: dict[tuple[str, str], KycSubmission] = field(default_factory=dict)
    documents: dict[tuple[str, str], SubmissionDocument] = field(default_factory=dict)
    assessments: dict[tuple[str, str], SuitabilityAssessment] = field(default_factory=dict)
    refresh_cases: dict[tuple[str, str], RefreshCase] = field(default_factory=dict)
    signed_applications: dict[tuple[str, str], SignedApplicationProjection] = field(default_factory=dict)
    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
    automation_runs: dict[tuple[str, str], Any] = field(default_factory=dict)
    automation_reviews: dict[tuple[str, str], Any] = field(default_factory=dict)
    suitability_overrides: dict[tuple[str, str], Any] = field(default_factory=dict)


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
