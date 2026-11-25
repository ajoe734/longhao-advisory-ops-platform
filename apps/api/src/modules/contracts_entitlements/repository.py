from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    AcceptanceDecision,
    AmendmentRecord,
    AuditEvent,
    CompanyAcceptance,
    Contract,
    ContractService,
    CustomerSignature,
    Entitlement,
    EntitlementDecision,
    IdempotencyReceipt,
    InboxReceipt,
    Job,
    OutboxEvent,
    PackageVersion,
    SignatureSession,
)


@dataclass
class StoreState:
    packages: dict[tuple[str, str], PackageVersion] = field(default_factory=dict)
    package_sequence: dict[tuple[str, str], int] = field(default_factory=dict)
    sessions: dict[tuple[str, str], SignatureSession] = field(default_factory=dict)
    session_by_ref: dict[str, tuple[str, str]] = field(default_factory=dict)
    signatures: dict[tuple[str, str], CustomerSignature] = field(default_factory=dict)
    signatures_by_application: dict[tuple[str, str], str] = field(default_factory=dict)
    provider_tx_index: dict[tuple[str, str], str] = field(default_factory=dict)
    acceptance_decisions: dict[tuple[str, str], AcceptanceDecision] = field(default_factory=dict)
    company_acceptances: dict[tuple[str, str], CompanyAcceptance] = field(default_factory=dict)
    contracts: dict[tuple[str, str], Contract] = field(default_factory=dict)
    contract_by_application: dict[tuple[str, str], str] = field(default_factory=dict)
    contract_services: dict[tuple[str, str], ContractService] = field(default_factory=dict)
    entitlements: dict[tuple[str, str], Entitlement] = field(default_factory=dict)
    entitlement_by_service_feature: dict[tuple[str, str, str], str] = field(default_factory=dict)
    entitlement_decisions: dict[tuple[str, str], EntitlementDecision] = field(default_factory=dict)
    amendments: dict[tuple[str, str], AmendmentRecord] = field(default_factory=dict)
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
