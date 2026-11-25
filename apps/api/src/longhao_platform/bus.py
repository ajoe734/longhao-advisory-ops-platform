"""Authenticated command bus and durable-event dispatcher reference behavior."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from longhao_platform.contracts import EventCatalog
from longhao_platform.store import Handler, InMemoryAtomicStore
from longhao_platform.types import Command, CommandResult, DomainEvent, PlatformError, Principal


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    command_name: str
    aggregate_type: str
    producer: str
    allowed_roles: frozenset[str]
    handler: Handler


class CommandBus:
    def __init__(self, store: InMemoryAtomicStore):
        self.store = store
        self._handlers: dict[str, HandlerSpec] = {}

    def register(self, spec: HandlerSpec) -> None:
        if spec.command_name in self._handlers:
            raise PlatformError(500, "DUPLICATE_COMMAND_HANDLER", spec.command_name)
        if not spec.allowed_roles:
            raise PlatformError(500, "COMMAND_WITHOUT_AUTHORIZATION", spec.command_name)
        self._handlers[spec.command_name] = spec

    def execute(self, command: Command, principal: Principal) -> CommandResult:
        spec = self._handlers.get(command.name)
        if spec is None:
            raise PlatformError(404, "COMMAND_NOT_REGISTERED", command.name)
        if not principal.enabled:
            self.store.record_denial(command, principal, "ACTOR_DISABLED")
            raise PlatformError(403, "ACTOR_DISABLED")
        if command.aggregate_type != spec.aggregate_type:
            self.store.record_denial(command, principal, "COMMAND_AGGREGATE_MISMATCH")
            raise PlatformError(422, "COMMAND_AGGREGATE_MISMATCH")
        if principal.roles.isdisjoint(spec.allowed_roles):
            self.store.record_denial(command, principal, "AUTHORIZATION_DENIED")
            raise PlatformError(403, "AUTHORIZATION_DENIED")
        return self.store.execute(command, principal, producer=spec.producer, handler=spec.handler)


Consumer = Callable[[DomainEvent], None]


class EventDispatcher:
    def __init__(self, store: InMemoryAtomicStore, catalog: EventCatalog):
        self.store = store
        self.catalog = catalog
        self._consumers: dict[tuple[str, str], Consumer] = {}

    def register(self, event_type: str, consumer_module: str, consumer: Consumer) -> None:
        definition = self.catalog.get(event_type)
        if definition is None:
            raise PlatformError(500, "EVENT_CONTRACT_NOT_REGISTERED", event_type)
        if consumer_module not in definition.consumers:
            raise PlatformError(500, "EVENT_CONSUMER_MISMATCH", f"{event_type}:{consumer_module}")
        key = (event_type, consumer_module)
        if key in self._consumers:
            raise PlatformError(500, "DUPLICATE_EVENT_CONSUMER", f"{event_type}:{consumer_module}")
        self._consumers[key] = consumer

    def drain(self, *, max_passes: int = 10) -> Mapping[str, object]:
        applied = 0
        failed = 0
        waiting: set[str] = set()
        for _ in range(max_passes):
            progress = False
            pending = self.store.pending_events()
            if not pending:
                break
            for event in pending:
                definition = self.catalog.require(
                    event.event_type,
                    producer=event.producer,
                    aggregate_type=event.aggregate_type,
                )
                missing = [module for module in definition.consumers if (event.event_type, module) not in self._consumers]
                if missing:
                    waiting.update(missing)
                    self.store.mark_event(event.event_id, "waiting_for_modules")
                    continue
                event_failed = False
                for module in definition.consumers:
                    previous = self.store.inbox_hash(event.tenant_id, module, event.event_id)
                    if previous is not None:
                        if previous != event.payload_sha256:
                            self.store.mark_event(event.event_id, "dead_letter", "INBOX_PAYLOAD_CONFLICT")
                            failed += 1
                            event_failed = True
                        continue
                    try:
                        self._consumers[(event.event_type, module)](event)
                        self.store.mark_applied(event, module)
                        applied += 1
                        progress = True
                    except PlatformError as exc:
                        self.store.mark_event(event.event_id, "retry" if exc.retryable else "dead_letter", exc.code)
                        failed += 1
                        event_failed = True
                        break
                    except Exception:
                        self.store.mark_event(event.event_id, "retry", "CONSUMER_FAILED")
                        failed += 1
                        event_failed = True
                        break
                if not event_failed and all(
                    self.store.inbox_hash(event.tenant_id, module, event.event_id) == event.payload_sha256
                    for module in definition.consumers
                ):
                    self.store.mark_event(event.event_id, "published")
            if not progress:
                break
        return {
            "applied": applied,
            "failed": failed,
            "waiting_for_modules": sorted(waiting),
            "pending": sum(row.status != "published" for row in self.store.outbox()),
        }
