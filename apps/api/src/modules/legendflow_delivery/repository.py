from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, TypeVar

from .domain import (
    AuditEvent,
    ChannelBinding,
    CommunicationPreference,
    Delivery,
    DeliveryAttempt,
    DeliveryReceiptRecord,
    DispatchJob,
    EntitlementProjection,
    IdempotencyReceipt,
    InboxReceipt,
    OutboxEvent,
    PublicationProjection,
    WebhookNonceReceipt,
)


@dataclass
class StoreState:
    deliveries: dict[tuple[str, str], Delivery] = field(default_factory=dict)
    delivery_by_idempotency_key: dict[tuple[str, str], str] = field(default_factory=dict)
    attempts: dict[tuple[str, str], DeliveryAttempt] = field(default_factory=dict)
    receipts: dict[tuple[str, str], DeliveryReceiptRecord] = field(default_factory=dict)
    receipt_index: dict[tuple[str, str, str], str] = field(default_factory=dict)
    channel_bindings: dict[tuple[str, str], ChannelBinding] = field(default_factory=dict)
    binding_index: dict[tuple[str, str, str, str], str] = field(default_factory=dict)
    preferences: dict[tuple[str, str], CommunicationPreference] = field(default_factory=dict)
    preference_index: dict[tuple[str, str, str, str], str] = field(default_factory=dict)
    jobs: dict[tuple[str, str], DispatchJob] = field(default_factory=dict)
    job_by_message: dict[tuple[str, str], str] = field(default_factory=dict)
    publications: dict[tuple[str, str], PublicationProjection] = field(default_factory=dict)
    entitlements: dict[tuple[str, str], EntitlementProjection] = field(default_factory=dict)
    suppressions: dict[tuple[str, str, str], bool] = field(default_factory=dict)
    consents: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    #: (tenant_id, customer_id, contract_id) -> terminated_at; keyed per contract so
    #: terminating one contract never blocks dispatch for the customer's other contracts.
    terminated_customers: dict[tuple[str, str, str], Any] = field(default_factory=dict)
    #: (tenant_id, replacement_publication_id) -> original_publication_id, recorded when
    #: publication.corrected arrives so replacement deliveries can be linked either order.
    correction_links: dict[tuple[str, str], str] = field(default_factory=dict)
    revoked_signatures: dict[tuple[str, str], str] = field(default_factory=dict)
    identity_links: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    webhook_nonces: dict[tuple[str, str], WebhookNonceReceipt] = field(default_factory=dict)
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
