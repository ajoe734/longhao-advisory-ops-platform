from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    AccountingExport,
    ControlException,
    EvidenceExport,
    IdempotencyReceipt,
    InboxReceipt,
    Invoice,
    Job,
    Journal,
    LegalHold,
    ModuleAuditEvent,
    OutboxEvent,
    Payment,
    ReconciliationItem,
    Refund,
    RevenueRecognition,
    RevenueRun,
    ServiceSchedule,
    Settlement,
    SettlementImport,
)


@dataclass
class ContractSnapshot:
    contract_id: str
    customer_id: str | None = None
    effective_at: datetime | None = None
    service_starts_at: datetime | None = None
    service_ends_at: datetime | None = None
    terminated_at: datetime | None = None
    refund_evaluation_required: bool = False
    accepted: bool = False
    ended_services: dict[str, datetime] = field(default_factory=dict)


@dataclass
class StoreState:
    invoices: dict[tuple[str, str], Invoice] = field(default_factory=dict)
    invoice_number_index: dict[tuple[str, str], str] = field(default_factory=dict)
    payments: dict[tuple[str, str], Payment] = field(default_factory=dict)
    payment_provider_index: dict[tuple[str, str, str], str] = field(default_factory=dict)
    webhook_receipts: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    settlements: dict[tuple[str, str], Settlement] = field(default_factory=dict)
    settlement_provider_index: dict[tuple[str, str], str] = field(default_factory=dict)
    settlement_imports: dict[tuple[str, str], SettlementImport] = field(default_factory=dict)
    reconciliation_items: dict[tuple[str, str], ReconciliationItem] = field(default_factory=dict)
    service_schedules: dict[tuple[str, str], ServiceSchedule] = field(default_factory=dict)
    revenue_runs: dict[tuple[str, str], RevenueRun] = field(default_factory=dict)
    revenue_run_key_index: dict[tuple[str, str], str] = field(default_factory=dict)
    revenue_recognitions: dict[tuple[str, str], RevenueRecognition] = field(default_factory=dict)
    recognition_key_index: dict[tuple[str, str], str] = field(default_factory=dict)
    refunds: dict[tuple[str, str], Refund] = field(default_factory=dict)
    refund_idempotency_index: dict[tuple[str, str], str] = field(default_factory=dict)
    journals: dict[tuple[str, str], Journal] = field(default_factory=dict)
    journal_number_index: dict[tuple[str, str], str] = field(default_factory=dict)
    journal_source_index: dict[tuple[str, str, str], str] = field(default_factory=dict)
    accounting_exports: dict[tuple[str, str], AccountingExport] = field(default_factory=dict)
    export_hash_index: dict[tuple[str, str, str], str] = field(default_factory=dict)
    exported_journal_index: dict[tuple[str, str], str] = field(default_factory=dict)
    control_exceptions: dict[tuple[str, str], ControlException] = field(default_factory=dict)
    legal_holds: dict[tuple[str, str], LegalHold] = field(default_factory=dict)
    evidence_exports: dict[tuple[str, str], EvidenceExport] = field(default_factory=dict)
    audit_events: list[ModuleAuditEvent] = field(default_factory=list)
    outbox: dict[tuple[str, str], OutboxEvent] = field(default_factory=dict)
    inbox: dict[tuple[str, str, str, str], InboxReceipt] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str, str], IdempotencyReceipt] = field(default_factory=dict)
    jobs: dict[tuple[str, str], Job] = field(default_factory=dict)
    contracts: dict[tuple[str, str], ContractSnapshot] = field(default_factory=dict)
    approved_commission_runs: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    commission_adjustment_receipts: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    automation_runs: dict[tuple[str, str], Any] = field(default_factory=dict)
    automation_reviews: dict[tuple[str, str], Any] = field(default_factory=dict)
    control_exceptions_counter: int = 0


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
