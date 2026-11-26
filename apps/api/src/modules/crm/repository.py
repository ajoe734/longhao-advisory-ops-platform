from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    ApplicationProjection,
    AuditEvent,
    ContractProjection,
    Conversion,
    CrmWorkItem,
    EntitlementProjection,
    IdempotencyReceipt,
    InboxReceipt,
    MarketingAttribution,
    Opportunity,
    OutboxEvent,
    OwnershipProjection,
    PaymentProjection,
    SalesActivity,
    Suppression,
    SupportCaseProjection,
)


@dataclass
class StoreState:
    marketing_attributions: dict[tuple[str, str], MarketingAttribution] = field(default_factory=dict)
    opportunities: dict[tuple[str, str], Opportunity] = field(default_factory=dict)
    activities: dict[tuple[str, str], SalesActivity] = field(default_factory=dict)
    conversions: dict[tuple[str, str], Conversion] = field(default_factory=dict)
    conversion_by_contract_service: dict[tuple[str, str], str] = field(default_factory=dict)
    work_items: dict[tuple[str, str], CrmWorkItem] = field(default_factory=dict)
    work_item_by_dedupe: dict[tuple[str, str], str] = field(default_factory=dict)
    suppressions: dict[tuple[str, str], Suppression] = field(default_factory=dict)

    # read-only local mirrors of upstream events (never the authority for
    # live authorization/financial decisions; see ports.py)
    applications: dict[tuple[str, str], ApplicationProjection] = field(default_factory=dict)
    contracts: dict[tuple[str, str], ContractProjection] = field(default_factory=dict)
    entitlements: dict[tuple[str, str], EntitlementProjection] = field(default_factory=dict)
    ownership: dict[tuple[str, str], OwnershipProjection] = field(default_factory=dict)
    support_cases: dict[tuple[str, str], SupportCaseProjection] = field(default_factory=dict)
    payments: dict[tuple[str, str], PaymentProjection] = field(default_factory=dict)
    consents: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    kyc_refresh_due: dict[tuple[str, str], Any] = field(default_factory=dict)
    #: (tenant_id, contract_service_id) -> parent contract_id, learned from
    #: ``contract.service_ended``; used to resolve the coarser contract_id
    #: carried by ``finance.refund_confirmed`` back to the contract_service_id
    #: granularity this module stores conversions under.
    contract_service_to_contract: dict[tuple[str, str], str] = field(default_factory=dict)

    audit_events: list[AuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
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
