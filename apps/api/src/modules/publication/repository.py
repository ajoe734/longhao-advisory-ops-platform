from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable, TypeVar

from .domain import (
    AudienceSnapshot,
    AuditEvent,
    EntitlementRecord,
    IdempotencyReceipt,
    InboxReceipt,
    Job,
    OutboxEvent,
    Publication,
    RecommendationSeries,
    RecommendationVersion,
    Recipient,
    RevokedSignature,
    VerifiedReport,
)


@dataclass
class StoreState:
    series: dict[tuple[str, str], RecommendationSeries] = field(default_factory=dict)
    series_by_code: dict[tuple[str, str], str] = field(default_factory=dict)
    versions: dict[tuple[str, str], RecommendationVersion] = field(default_factory=dict)
    publications: dict[tuple[str, str], Publication] = field(default_factory=dict)
    publications_by_version: dict[tuple[str, str], str] = field(default_factory=dict)
    snapshots: dict[tuple[str, str], AudienceSnapshot] = field(default_factory=dict)
    recipients: dict[tuple[str, str], Recipient] = field(default_factory=dict)
    verified_reports: dict[tuple[str, str], VerifiedReport] = field(default_factory=dict)
    revoked_signatures: dict[tuple[str, str], RevokedSignature] = field(default_factory=dict)
    entitlements: dict[tuple[str, str], EntitlementRecord] = field(default_factory=dict)
    jobs: dict[tuple[str, str], Job] = field(default_factory=dict)
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
