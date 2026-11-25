"""Stable contracts shared by the runtime and independently owned modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformError(Exception):
    """A safe error that may cross the HTTP boundary."""

    def __init__(self, status: int, code: str, detail: str = "", *, retryable: bool = False):
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Principal:
    actor_id: UUID
    tenant_id: UUID
    subject: str
    roles: frozenset[str]
    actor_type: str = "human"
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    aggregate_type: str
    aggregate_id: UUID
    expected_version: int
    idempotency_key: str
    payload: Mapping[str, Any]
    correlation_id: UUID = field(default_factory=uuid4)
    causation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PendingEvent:
    event_type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class HandlerResult:
    state: Mapping[str, Any]
    events: tuple[PendingEvent, ...]
    response: Mapping[str, Any] = field(default_factory=dict)
    classification: str = "internal"
    changed_fields: tuple[str, ...] = ()


@dataclass(slots=True)
class AggregateRecord:
    tenant_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    version: int
    state: dict[str, Any]


@dataclass(slots=True)
class DomainEvent:
    tenant_id: UUID
    event_id: UUID
    event_type: str
    schema_version: str
    producer: str
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    sequence_no: int
    idempotency_key: str
    classification: str
    payload_sha256: str
    payload: dict[str, Any]
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: datetime
    emitted_at: datetime
    status: str = "pending"
    attempt_count: int = 0
    last_error_code: str | None = None


@dataclass(slots=True)
class AuditEvent:
    tenant_id: UUID
    event_id: UUID
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    correlation_id: UUID
    outcome: str
    classification: str
    redacted_changes: dict[str, str]
    occurred_at: datetime
    prior_hash: str | None
    event_hash: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    aggregate_id: UUID
    version: int
    response: Mapping[str, Any]
    event_ids: tuple[UUID, ...]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    headers: Mapping[str, str]
    query: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    body: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: Mapping[str, Any] | str | bytes
    headers: Mapping[str, str] = field(default_factory=dict)
