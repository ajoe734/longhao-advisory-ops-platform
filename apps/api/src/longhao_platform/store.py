"""In-process reference implementation of command, audit, outbox and inbox semantics.

It is intentionally a local/test store. Production modules use the matching PostgreSQL
tables in ``core`` and keep their business writes in the same database transaction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from longhao_platform.contracts import EventCatalog
from longhao_platform.types import (
    AggregateRecord,
    AuditEvent,
    Command,
    CommandResult,
    DomainEvent,
    HandlerResult,
    PlatformError,
    Principal,
    utc_now,
)


IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
AUDIT_CLASSIFICATIONS = {"internal", "personal", "financial", "restricted_aml"}


def _json_bytes(value: Any) -> bytes:
    _reject_floats(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlatformError(422, "NON_JSON_PAYLOAD", "Command and event payloads must be JSON values.") from exc


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise PlatformError(422, "NON_CANONICAL_NUMBER", "Use decimal strings; binary floating-point values are forbidden.")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_floats(item)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _clone_json(value: Any) -> Any:
    return json.loads(_json_bytes(value))


def _rfc3339(value: Any) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class _Receipt:
    fingerprint: str
    result: CommandResult


Handler = Callable[[Mapping[str, Any] | None, Command, Principal], HandlerResult]


class InMemoryAtomicStore:
    """Deterministic store used by local runs and cross-module tests.

    Handlers are pure: the store calls them before publishing any mutation. Aggregate,
    audit event, outbox rows and the idempotency receipt are then installed together
    while one lock is held. A raised exception therefore leaves no partial commit.
    """

    def __init__(self, catalog: EventCatalog):
        self.catalog = catalog
        self._lock = threading.RLock()
        self._aggregates: dict[tuple[UUID, str, UUID], AggregateRecord] = {}
        self._receipts: dict[tuple[UUID, UUID, str, str], _Receipt] = {}
        self._audits: list[AuditEvent] = []
        self._outbox: dict[UUID, DomainEvent] = {}
        self._inbox: dict[tuple[UUID, str, UUID], str] = {}

    def execute(self, command: Command, principal: Principal, *, producer: str, handler: Handler) -> CommandResult:
        if command.expected_version < 0:
            raise PlatformError(422, "INVALID_EXPECTED_VERSION")
        if not IDEMPOTENCY_PATTERN.fullmatch(command.idempotency_key):
            raise PlatformError(428, "IDEMPOTENCY_KEY_REQUIRED")
        fingerprint = _sha256(
            {
                "name": command.name,
                "aggregate_type": command.aggregate_type,
                "aggregate_id": str(command.aggregate_id),
                "expected_version": command.expected_version,
                "payload": command.payload,
            }
        )
        receipt_key = (principal.tenant_id, principal.actor_id, command.name, command.idempotency_key)
        aggregate_key = (principal.tenant_id, command.aggregate_type, command.aggregate_id)
        with self._lock:
            previous = self._receipts.get(receipt_key)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise PlatformError(409, "IDEMPOTENCY_PAYLOAD_CONFLICT")
                prior = previous.result
                return CommandResult(prior.aggregate_id, prior.version, copy.deepcopy(prior.response), prior.event_ids, True)

            current = self._aggregates.get(aggregate_key)
            current_version = 0 if current is None else current.version
            if command.expected_version != current_version:
                raise PlatformError(412, "STALE_AGGREGATE_VERSION", f"Expected {command.expected_version}; current version is {current_version}.")

            current_state = None if current is None else copy.deepcopy(current.state)
            outcome = handler(current_state, command, principal)
            if not isinstance(outcome, HandlerResult):
                raise PlatformError(500, "INVALID_HANDLER_RESULT")
            if outcome.classification not in AUDIT_CLASSIFICATIONS:
                raise PlatformError(500, "INVALID_AUDIT_CLASSIFICATION")
            new_state = _clone_json(dict(outcome.state))
            response = _clone_json(dict(outcome.response))
            new_version = current_version + 1
            now = utc_now()
            events: list[DomainEvent] = []
            prior_sequence = max(
                (
                    event.sequence_no
                    for event in self._outbox.values()
                    if event.tenant_id == principal.tenant_id
                    and event.producer == producer
                    and event.aggregate_type == command.aggregate_type
                    and event.aggregate_id == command.aggregate_id
                ),
                default=0,
            )
            for sequence, pending in enumerate(outcome.events, start=1):
                definition = self.catalog.require(
                    pending.event_type,
                    producer=producer,
                    aggregate_type=command.aggregate_type,
                )
                payload = _clone_json(dict(pending.payload))
                event = DomainEvent(
                        tenant_id=principal.tenant_id,
                        event_id=uuid4(),
                        event_type=definition.event_type,
                        schema_version=definition.schema_version,
                        producer=producer,
                        aggregate_type=command.aggregate_type,
                        aggregate_id=command.aggregate_id,
                        aggregate_version=new_version,
                        sequence_no=prior_sequence + sequence,
                        idempotency_key=f"{command.idempotency_key}:{sequence}",
                        classification=definition.classification,
                        payload_sha256=_sha256(payload),
                        payload=payload,
                        correlation_id=command.correlation_id,
                        causation_id=command.causation_id,
                        occurred_at=now,
                        emitted_at=now,
                    )
                self.catalog.validate(
                    {
                        "event_id": str(event.event_id),
                        "event_type": event.event_type,
                        "schema_version": event.schema_version,
                        "tenant_id": str(event.tenant_id),
                        "producer": event.producer,
                        "aggregate_type": event.aggregate_type,
                        "aggregate_id": str(event.aggregate_id),
                        "aggregate_version": event.aggregate_version,
                        "sequence_no": event.sequence_no,
                        "occurred_at": _rfc3339(event.occurred_at),
                        "emitted_at": _rfc3339(event.emitted_at),
                        "correlation_id": str(event.correlation_id),
                        "causation_id": str(event.causation_id) if event.causation_id else None,
                        "idempotency_key": event.idempotency_key,
                        "classification": event.classification,
                        "payload_sha256": event.payload_sha256,
                        "payload": event.payload,
                    }
                )
                events.append(event)

            audit = self._build_audit(
                principal=principal,
                action=command.name,
                resource_type=command.aggregate_type,
                resource_id=command.aggregate_id,
                correlation_id=command.correlation_id,
                outcome="success",
                classification=outcome.classification,
                changed_fields=outcome.changed_fields,
                occurred_at=now,
            )
            result = CommandResult(
                aggregate_id=command.aggregate_id,
                version=new_version,
                response=response,
                event_ids=tuple(event.event_id for event in events),
            )

            # Commit point: these structures are the local equivalent of one SQL transaction.
            self._aggregates[aggregate_key] = AggregateRecord(
                principal.tenant_id, command.aggregate_type, command.aggregate_id, new_version, new_state
            )
            self._audits.append(audit)
            for event in events:
                self._outbox[event.event_id] = event
            self._receipts[receipt_key] = _Receipt(fingerprint, result)
            return copy.deepcopy(result)

    def record_denial(self, command: Command, principal: Principal, code: str) -> None:
        with self._lock:
            self._audits.append(
                self._build_audit(
                    principal=principal,
                    action=command.name,
                    resource_type=command.aggregate_type,
                    resource_id=command.aggregate_id,
                    correlation_id=command.correlation_id,
                    outcome="denied",
                    classification="internal",
                    changed_fields=(code,),
                    occurred_at=utc_now(),
                )
            )

    def _build_audit(
        self,
        *,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: UUID,
        correlation_id: UUID,
        outcome: str,
        classification: str,
        changed_fields: tuple[str, ...],
        occurred_at: Any,
    ) -> AuditEvent:
        prior_hash = next((row.event_hash for row in reversed(self._audits) if row.tenant_id == principal.tenant_id), None)
        changes = {field: "changed" for field in changed_fields}
        event_id = uuid4()
        material = {
            "tenant_id": str(principal.tenant_id),
            "event_id": str(event_id),
            "actor_id": str(principal.actor_id),
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id),
            "correlation_id": str(correlation_id),
            "outcome": outcome,
            "classification": classification,
            "redacted_changes": changes,
            "occurred_at": occurred_at.isoformat(),
            "prior_hash": prior_hash,
        }
        return AuditEvent(
            tenant_id=principal.tenant_id,
            event_id=event_id,
            actor_id=principal.actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            correlation_id=correlation_id,
            outcome=outcome,
            classification=classification,
            redacted_changes=changes,
            occurred_at=occurred_at,
            prior_hash=prior_hash,
            event_hash=_sha256(material),
        )

    def aggregate(self, tenant_id: UUID, aggregate_type: str, aggregate_id: UUID) -> AggregateRecord | None:
        with self._lock:
            return copy.deepcopy(self._aggregates.get((tenant_id, aggregate_type, aggregate_id)))

    def audits(self, tenant_id: UUID | None = None) -> tuple[AuditEvent, ...]:
        with self._lock:
            rows = self._audits if tenant_id is None else [row for row in self._audits if row.tenant_id == tenant_id]
            return tuple(copy.deepcopy(rows))

    def outbox(self, tenant_id: UUID | None = None) -> tuple[DomainEvent, ...]:
        with self._lock:
            rows = self._outbox.values()
            if tenant_id is not None:
                rows = (row for row in rows if row.tenant_id == tenant_id)
            return tuple(copy.deepcopy(list(rows)))

    def pending_events(self) -> tuple[DomainEvent, ...]:
        with self._lock:
            return tuple(copy.deepcopy(row) for row in self._outbox.values() if row.status in {"pending", "retry", "waiting_for_modules"})

    def inbox_hash(self, tenant_id: UUID, consumer: str, event_id: UUID) -> str | None:
        with self._lock:
            return self._inbox.get((tenant_id, consumer, event_id))

    def mark_applied(self, event: DomainEvent, consumer: str) -> None:
        with self._lock:
            key = (event.tenant_id, consumer, event.event_id)
            previous = self._inbox.get(key)
            if previous is not None and previous != event.payload_sha256:
                raise PlatformError(409, "INBOX_PAYLOAD_CONFLICT")
            self._inbox[key] = event.payload_sha256

    def mark_event(self, event_id: UUID, status: str, error_code: str | None = None) -> None:
        with self._lock:
            event = self._outbox[event_id]
            event.status = status
            event.last_error_code = error_code
            if error_code is not None:
                event.attempt_count += 1
