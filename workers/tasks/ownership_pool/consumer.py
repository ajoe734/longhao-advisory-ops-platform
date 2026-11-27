"""M11 inbound event consumer.

M11 is a registered consumer (events/registry.json) of ``contract.effective`` /
``contract.terminated`` / ``contract.service_ended`` and keeps a local read-model
of paid-service windows fed by them (``StoreState.contracts`` /
``service_ends``) — this is exactly the "M11需重新查全部付費服務" guidance on
``contract.service_ended``: one early-ended service never concludes a full
interruption by itself, so the consumer only ever records facts; the lapse
decision itself lives in ``OwnershipPoolService._evaluate_customer``.

``ownership.employee_departed`` lists M11 itself as a consumer. The real side
effects (closing assignments, opening pool entries) already happen synchronously
inside ``record_employee_departure``'s own transaction, so this consumer's handler
for that event type is a defensive, idempotent confirmation pass only — it must
never double-apply the departure.

A first ``contract.effective`` for a customer who has no maintenance-ownership row
at all establishes the "官網自助" baseline from docs/modules/M11-ownership_pool.md
UC-M11-A: the customer starts out owned by the company pool, available for later
redevelopment, with no sales assignment and therefore no sales award.
"""
from __future__ import annotations

from longhao_platform.event_ordering import is_sequence_gap

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from modules.ownership_pool.domain import (
    Assignment,
    AuditEvent,
    ConflictError,
    ContractWindow,
    InboxReceipt,
    NotFoundError,
    OutboxEvent,
    PoolEntry,
    ValidationError,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
    wire_time,
)
from modules.ownership_pool.ports import RestrictionPort
from modules.ownership_pool.repository import InMemoryRepository, StoreState
from modules.ownership_pool.service import DEFAULT_POLICY_CODE


_CONTRACTS: dict[str, tuple[str, str, set[str], str]] = {
    "contract.effective": (
        "M02", "contract",
        {"contract_id", "customer_id", "effective_at", "service_starts_at", "service_ends_at"},
        "customer_confidential",
    ),
    "contract.terminated": (
        "M02", "contract",
        {"contract_id", "customer_id", "terminated_at", "refund_evaluation_required"},
        "customer_confidential",
    ),
    "contract.service_ended": (
        "M02", "contract_service",
        {"contract_service_id", "contract_id", "customer_id", "ended_at"},
        "customer_confidential",
    ),
    "ownership.employee_departed": (
        "M11", "employee",
        {"employee_id", "departure_id", "effective_at"},
        "internal",
    ),
}


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    result_code: str


class M11EventConsumer:
    def __init__(self, repository: InMemoryRepository, restriction: RestrictionPort, *, clock=utc_now) -> None:
        self.repository = repository
        self.restriction = restriction
        self._clock = clock

    def consume(self, envelope: dict[str, Any]) -> ConsumeResult:
        self._validate_envelope(envelope)
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        key = (tenant_id, "M11", envelope["producer"], event_id)

        def transaction(state: StoreState) -> ConsumeResult:
            existing = state.inbox.get(key)
            if existing:
                if existing.payload_sha256 != envelope["payload_sha256"]:
                    raise ConflictError("event_id was replayed with a different payload", code="inbox_payload_mismatch")
                if existing.status != "retry":
                    return ConsumeResult("duplicate", existing.result_code)

            last_sequence = max(
                (
                    receipt.sequence_no for receipt in state.inbox.values()
                    if receipt.tenant_id == tenant_id
                    and receipt.producer == envelope["producer"]
                    and receipt.aggregate_type == envelope["aggregate_type"]
                    and receipt.aggregate_id == envelope["aggregate_id"]
                    and receipt.status == "applied"
                ),
                default=0,
            )
            if is_sequence_gap("M11", envelope, last_sequence):
                state.inbox[key] = self._receipt(envelope, "retry", "sequence_gap", None)
                return ConsumeResult("retry", "sequence_gap")
            if envelope["sequence_no"] <= last_sequence:
                state.inbox[key] = self._receipt(envelope, "applied", "stale_sequence_ignored", self._clock())
                return ConsumeResult("applied", "stale_sequence_ignored")

            result_code = self._apply(state, envelope)
            applied_at = self._clock()
            state.inbox[key] = self._receipt(envelope, "applied", result_code, applied_at)
            state.audit_events.append(
                AuditEvent(
                    id=new_id(),
                    tenant_id=tenant_id,
                    actor_id="00000000-0000-0000-0000-000000000001",
                    purpose="event_projection",
                    action=f"m11.consume.{envelope['event_type']}",
                    aggregate_type=envelope["aggregate_type"],
                    aggregate_id=envelope["aggregate_id"],
                    aggregate_version=envelope["aggregate_version"],
                    correlation_id=envelope["correlation_id"],
                    result=result_code,
                    reason_code=None,
                    recorded_at=applied_at,
                )
            )
            return ConsumeResult("applied", result_code)

        return self.repository.atomic(transaction)

    def _apply(self, state: StoreState, envelope: dict[str, Any]) -> str:
        event_type = envelope["event_type"]
        payload = envelope["payload"]
        tenant_id = envelope["tenant_id"]
        if event_type == "contract.effective":
            contract_id = payload["contract_id"]
            window = ContractWindow(
                tenant_id=tenant_id,
                contract_id=contract_id,
                customer_id=payload["customer_id"],
                service_starts_at=self._time(payload["service_starts_at"]),
                service_ends_at=self._time(payload["service_ends_at"]),
            )
            previous = state.contracts.get((tenant_id, contract_id))
            if previous is not None:
                window.terminated_at = previous.terminated_at
                window.service_ended_ats = dict(previous.service_ended_ats)
            state.contracts[(tenant_id, contract_id)] = window
            self._ensure_pool_baseline(
                state, tenant_id, payload["customer_id"],
                correlation_id=envelope["correlation_id"], causation_id=envelope["event_id"],
            )
            return "contract_window_recorded"
        if event_type == "contract.terminated":
            window = state.contracts.get((tenant_id, payload["contract_id"]))
            if not window:
                raise NotFoundError("contract referenced by event was not found")
            window.terminated_at = self._time(payload["terminated_at"])
            return "contract_termination_recorded"
        if event_type == "contract.service_ended":
            ended_at = self._time(payload["ended_at"])
            state.service_ends[(tenant_id, payload["contract_service_id"])] = (
                payload["contract_id"], ended_at
            )
            window = state.contracts.get((tenant_id, payload["contract_id"]))
            if window:
                # Keyed by contract_service_id so a later event for one child
                # service can never overwrite/discard an earlier event for a
                # different one under the same contract_id.
                window.service_ended_ats[payload["contract_service_id"]] = ended_at
            return "service_end_recorded"
        if event_type == "ownership.employee_departed":
            # Synchronous side effects already ran inside record_employee_departure;
            # this is a defensive, idempotent confirmation only.
            return "self_departure_confirmation_noop"
        raise ValidationError("event type is not consumed by M11", code="unsupported_event_type")

    def _ensure_pool_baseline(
        self, state: StoreState, tenant_id: str, customer_id: str, *, correlation_id: str, causation_id: str | None,
    ) -> None:
        if (tenant_id, customer_id) in state.current_assignment:
            return
        policies = [
            policy for (tenant, _), policy in state.policies.items()
            if tenant == tenant_id and policy.policy_code == DEFAULT_POLICY_CODE
        ]
        if not policies:
            return
        policy = max(policies, key=lambda item: item.policy_version)
        now = self._clock()
        # Re-check live compliance restriction the same way
        # OwnershipPoolService._open_pool_entry does, so a customer who is
        # already restricted before their first paid-service event is ever
        # consumed does not materialize as an "available" pool entry.
        restricted = self.restriction.is_restricted(tenant_id, customer_id)
        assignment = Assignment(
            id=new_id(),
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
            customer_id=customer_id,
            owner_type="company_pool",
            valid_from=now,
            reason_code="unassigned_web",
            policy_version_id=policy.id,
            assigned_by="00000000-0000-0000-0000-000000000001",
        )
        state.assignments[(tenant_id, assignment.id)] = assignment
        state.current_assignment[(tenant_id, customer_id)] = assignment.id
        entry = PoolEntry(
            id=new_id(),
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
            customer_id=customer_id,
            assignment_id=assignment.id,
            entry_reason="unassigned_web",
            policy_version_id=policy.id,
            policy_version=policy.wire_code,
            entered_at=now,
            evaluation_snapshot={"reason": "first_contract_without_owner"},
            state="suppressed" if restricted else "available",
            suppressed_reason="compliance_hold" if restricted else None,
        )
        state.pool_entries[(tenant_id, entry.id)] = entry
        state.pool_entry_by_assignment[(tenant_id, assignment.id)] = entry.id

        M11EventConsumer._emit_outbox(
            state, tenant_id, "ownership.assignment_changed", "maintenance_assignment", assignment.id, 1,
            correlation_id, causation_id, now,
            {"assignment_id": assignment.id, "customer_id": customer_id, "salesperson_id": None, "effective_at": wire_time(now), "state": "active"},
        )
        M11EventConsumer._emit_outbox(
            state, tenant_id, "ownership.pool_entered", "pool_entry", entry.id, 1, correlation_id, causation_id, now,
            {"pool_entry_id": entry.id, "customer_id": customer_id, "entry_reason": "unassigned_web", "policy_version": policy.wire_code},
        )

    @staticmethod
    def _emit_outbox(
        state: StoreState, tenant_id: str, event_type: str, aggregate_type: str, aggregate_id: str,
        aggregate_version: int, correlation_id: str, causation_id: str | None, now, payload: dict[str, Any],
    ) -> None:
        event = OutboxEvent(
            event_id=new_id(),
            event_type=event_type,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            sequence_no=1,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=f"{event_type}:{aggregate_id}:{aggregate_version}",
            classification="customer_confidential",
            payload=payload,
            occurred_at=now,
            emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    @staticmethod
    def _validate_envelope(envelope: dict[str, Any]) -> None:
        required = {
            "event_id", "event_type", "schema_version", "tenant_id", "producer", "aggregate_type",
            "aggregate_id", "aggregate_version", "sequence_no", "occurred_at", "emitted_at", "correlation_id",
            "causation_id", "idempotency_key", "classification", "payload_sha256", "payload",
        }
        if set(envelope) != required:
            raise ValidationError("event envelope fields do not match schema", code="event_schema_invalid")
        contract = _CONTRACTS.get(envelope["event_type"])
        if not contract:
            raise ValidationError("event type is not consumed by M11", code="unsupported_event_type")
        producer, aggregate_type, payload_fields, classification = contract
        if (
            envelope["schema_version"] != "1.0.0"
            or envelope["producer"] != producer
            or envelope["aggregate_type"] != aggregate_type
            or envelope["classification"] != classification
        ):
            raise ValidationError("event contract metadata is invalid", code="event_schema_invalid")
        try:
            require_uuid(envelope["event_id"], "event_id")
            require_uuid(envelope["tenant_id"], "tenant_id")
            require_uuid(envelope["aggregate_id"], "aggregate_id")
            require_uuid(envelope["correlation_id"], "correlation_id")
            if envelope["causation_id"] is not None:
                require_uuid(envelope["causation_id"], "causation_id")
        except ValidationError as exc:
            raise ValidationError("event envelope identifiers must be UUIDs", code="event_schema_invalid", field_name=exc.field_name) from exc
        if set(envelope["payload"]) != payload_fields:
            raise ValidationError("event payload fields do not match schema", code="event_schema_invalid")
        if canonical_hash(envelope["payload"]) != envelope["payload_sha256"]:
            raise ConflictError("event payload hash is invalid", code="event_payload_hash_invalid")
        if not isinstance(envelope["sequence_no"], int) or envelope["sequence_no"] < 1:
            raise ValidationError("sequence_no must be positive", field_name="sequence_no")

    @staticmethod
    def _receipt(envelope: dict[str, Any], status: str, code: str, applied_at: datetime | None) -> InboxReceipt:
        return InboxReceipt(
            tenant_id=envelope["tenant_id"],
            consumer="M11",
            producer=envelope["producer"],
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            payload_sha256=envelope["payload_sha256"],
            aggregate_type=envelope["aggregate_type"],
            aggregate_id=envelope["aggregate_id"],
            sequence_no=envelope["sequence_no"],
            status=status,
            result_code=code,
            applied_at=applied_at,
        )

    @staticmethod
    def _time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValidationError("event time must include timezone", code="event_schema_invalid")
        return parsed.astimezone(timezone.utc)
