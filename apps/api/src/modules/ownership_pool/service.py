"""M11 command/query application service.

Every state-changing operation runs through one repository callback, so domain
state, audit, outbox and the idempotency receipt commit atomically — the same
discipline as modules/customer_identity_application/service.py (M01) and
modules/crm/service.py (M10). ``ownership.assignment`` rows always keep the
customer's timeline gap-free: at most one row per customer has ``valid_until is
None`` at any time, and it is either a ``sales`` row (a real maintenance owner) or
a ``company_pool`` row (available for redevelopment). Only ``sales`` rows are ever
rendered as a ``MaintenanceAssignment`` API resource.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

from .domain import (
    ASSIGNMENT_STATES,
    Assignment,
    AuditEvent,
    ActorContext,
    ConflictError,
    DomainError,
    EmployeeDeparture,
    ForbiddenError,
    IdempotencyReceipt,
    NotFoundError,
    OutboxEvent,
    POOL_ENTRY_STATES,
    PolicyVersion,
    PoolEntry,
    PoolEvaluation,
    PreconditionRequiredError,
    StaleVersionError,
    ValidationError,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import EvidencePort, RestrictionPort
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

DEFAULT_POLICY_CODE = "company_pool_lapse"


@dataclass(frozen=True)
class ServiceResponse:
    data: dict[str, Any]
    status: int = 200
    etag: str | None = None
    idempotency_replayed: bool = False


@dataclass
class _Outcome:
    value: dict[str, Any]
    status: int
    aggregate_type: str
    aggregate_id: str | None
    aggregate_version: int | None
    error: DomainError | None = None


class OwnershipPoolService:
    def __init__(
        self,
        repository: InMemoryRepository,
        evidence: EvidencePort,
        restriction: RestrictionPort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False) for adapter in (repository, evidence, restriction)
        ):
            raise RuntimeError("fake M11 adapters are forbidden in production")
        self.repository = repository
        self.evidence = evidence
        self.restriction = restriction
        self._clock = clock

    # ---- internal policy administration ----------------------------------
    # Not a published operationId: api/openapi.yaml's M11 surface has no policy
    # lifecycle endpoint, but ownership.policy_version rows are a hard FK
    # dependency of every assignment/pool_entry row. Ops tooling and tests use
    # this directly, mirroring customer_identity_application.service's
    # documented precedent for necessary-but-unpublished commands.

    def propose_policy_version(
        self,
        context: ActorContext,
        *,
        policy_code: str = DEFAULT_POLICY_CODE,
        lapse_days: int = 7,
        departure_immediate: bool = True,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"sales_supervisor"})
        if not isinstance(lapse_days, int) or lapse_days <= 0:
            raise ValidationError("lapse_days must be a positive integer", field_name="lapse_days")
        now = self._clock()

        def mutate(state: StoreState) -> PolicyVersion:
            existing_versions = [
                policy.policy_version for (tenant, _), policy in state.policies.items()
                if tenant == context.tenant_id and policy.policy_code == policy_code
            ]
            policy = PolicyVersion(
                id=new_id(),
                tenant_id=context.tenant_id,
                created_at=now,
                updated_at=now,
                policy_code=policy_code,
                policy_version=max(existing_versions, default=0) + 1,
                proposed_by=context.actor_id,
                lapse_days=lapse_days,
                departure_immediate=departure_immediate,
            )
            state.policies[(context.tenant_id, policy.id)] = policy
            state.policy_by_code[(context.tenant_id, policy.wire_code)] = policy.id
            return policy

        policy = self.repository.atomic(mutate)
        return ServiceResponse(self._policy_api(policy), 201, self._etag(policy.version))

    def approve_policy_version(
        self,
        context: ActorContext,
        policy_id: str,
        *,
        expected_version: int | None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"sales_supervisor"})
        policy_id = require_uuid(policy_id, "policy_id")
        now = self._clock()

        def mutate(state: StoreState) -> PolicyVersion:
            policy = state.policies.get((context.tenant_id, policy_id))
            if not policy:
                raise NotFoundError("policy version was not found")
            self._cas(policy.version, expected_version)
            if policy.approval_status != "draft":
                raise ConflictError("only a draft policy can be approved", code="policy_not_draft")
            if policy.proposed_by == context.actor_id:
                raise ForbiddenError("approver must differ from proposer", code="approval_requires_separate_actor")
            policy.approval_status = "approved"
            policy.approved_by = context.actor_id
            policy.approved_at = now
            policy.updated_at = now
            policy.version += 1
            return policy

        policy = self.repository.atomic(mutate)
        return ServiceResponse(self._policy_api(policy), 200, self._etag(policy.version))

    @staticmethod
    def _policy_api(policy: PolicyVersion) -> dict[str, Any]:
        return {
            "id": policy.id,
            "policy_version": policy.wire_code,
            "approval_status": policy.approval_status,
            "lapse_days": policy.lapse_days,
            "departure_immediate": policy.departure_immediate,
        }

    # ---- published commands -----------------------------------------------

    def list_company_pool(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"sales", "sales_supervisor"})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in POOL_ENTRY_STATES:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(store: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in store.pool_entries.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not state_filter or item.state == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(store, context, "list_company_pool", "pool_entry", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._pool_entry_actions(item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_ownership_history(
        self,
        context: ActorContext,
        customer_id: str,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"sales", "sales_supervisor"})
        customer_id = require_uuid(customer_id, "customer_id")
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in ASSIGNMENT_STATES:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(store: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in store.assignments.items()
                    if tenant == context.tenant_id
                    and item.customer_id == customer_id
                    and item.owner_type == "sales"
                    and item.created_at <= snapshot_at
                    and (not state_filter or item.wire_state == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(store, context, "get_ownership_history", "customer", customer_id, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._assignment_actions(item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def assign_maintenance_owner(
        self,
        context: ActorContext,
        customer_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        customer_id = require_uuid(customer_id, "customer_id")
        self._authorize_staff(context, {"sales_supervisor"})
        self._body(body, {"salesperson_id", "effective_from", "reason_code", "previous_assignment_id", "handoff_evidence_asset_id"}, {"salesperson_id", "effective_from", "reason_code"})
        salesperson_id = require_uuid(body["salesperson_id"], "salesperson_id")
        effective_from = self._parse_time(body["effective_from"], "effective_from")
        reason_code = self._reason_code(body["reason_code"])
        previous_assignment_id = require_uuid(body["previous_assignment_id"], "previous_assignment_id") if body.get("previous_assignment_id") else None
        handoff_evidence_asset_id = require_uuid(body["handoff_evidence_asset_id"], "handoff_evidence_asset_id") if body.get("handoff_evidence_asset_id") else None
        if handoff_evidence_asset_id and not self.evidence.exists(context.tenant_id, handoff_evidence_asset_id):
            raise ValidationError("handoff evidence does not exist", code="evidence_not_found", field_name="handoff_evidence_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            current_id = state.current_assignment.get((context.tenant_id, customer_id))
            current = state.assignments.get((context.tenant_id, current_id)) if current_id else None
            self._cas(current.version if current else 0, expected_version)
            if previous_assignment_id and (not current or current.id != previous_assignment_id):
                raise ValidationError("previous_assignment_id does not match the current assignment", field_name="previous_assignment_id")
            policy = self._require_tenant_policy(state, context.tenant_id)
            if current:
                current.valid_until = effective_from
                current.end_state = "superseded"
                current.updated_at = now
                current.version += 1
                self._close_dangling_pool_entry(state, context.tenant_id, current.id, salesperson_id, effective_from)
                self._emit_assignment_changed(state, context, current, correlation_id, now)

            assignment = Assignment(
                id=new_id(),
                tenant_id=context.tenant_id,
                created_at=now,
                updated_at=now,
                customer_id=customer_id,
                owner_type="sales",
                owner_actor_id=salesperson_id,
                valid_from=effective_from,
                reason_code=reason_code,
                policy_version_id=policy.id,
                assigned_by=context.actor_id,
                predecessor_id=current.id if current else None,
            )
            state.assignments[(context.tenant_id, assignment.id)] = assignment
            state.current_assignment[(context.tenant_id, customer_id)] = assignment.id
            self._emit_assignment_changed(state, context, assignment, correlation_id, now)
            return self._assignment_outcome(assignment, context, 201)

        return self._run_command(context, "assign_maintenance_owner", idempotency_key, {"customer_id": customer_id, **body}, correlation_id, mutate)

    def claim_pool_customer(
        self,
        context: ActorContext,
        pool_entry_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        pool_entry_id = require_uuid(pool_entry_id, "pool_entry_id")
        self._authorize_staff(context, {"sales"})
        self._body(body, {"salesperson_id", "first_contact_due_at", "reason_code"}, {"salesperson_id", "first_contact_due_at", "reason_code"})
        salesperson_id = require_uuid(body["salesperson_id"], "salesperson_id")
        if salesperson_id != context.actor_id:
            raise ForbiddenError("sales staff can only claim for themselves", code="claim_requires_self")
        first_contact_due_at = self._parse_time(body["first_contact_due_at"], "first_contact_due_at")
        reason_code = self._reason_code(body["reason_code"])
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            entry = self._stored_pool_entry(state, context.tenant_id, pool_entry_id)
            self._cas(entry.version, expected_version)
            if entry.state != "available":
                raise ConflictError(f"pool entry is not available ({entry.state})", code="pool_entry_not_available")
            predecessor = state.assignments[(context.tenant_id, entry.assignment_id)]
            predecessor.valid_until = now
            predecessor.updated_at = now
            predecessor.version += 1

            assignment = Assignment(
                id=new_id(),
                tenant_id=context.tenant_id,
                created_at=now,
                updated_at=now,
                customer_id=entry.customer_id,
                owner_type="sales",
                owner_actor_id=salesperson_id,
                valid_from=now,
                reason_code=reason_code,
                policy_version_id=entry.policy_version_id,
                assigned_by=context.actor_id,
                predecessor_id=predecessor.id,
                source_pool_entry_id=entry.id,
            )
            state.assignments[(context.tenant_id, assignment.id)] = assignment
            state.current_assignment[(context.tenant_id, entry.customer_id)] = assignment.id

            entry.state = "claimed"
            entry.claimed_by = salesperson_id
            entry.claimed_at = now
            entry.first_contact_due_at = first_contact_due_at
            entry.updated_at = now
            entry.version += 1

            self._emit_assignment_changed(state, context, assignment, correlation_id, now)
            return self._assignment_outcome(assignment, context, 201)

        return self._run_command(context, "claim_pool_customer", idempotency_key, {"pool_entry_id": pool_entry_id, **body}, correlation_id, mutate)

    def release_maintenance_owner(
        self,
        context: ActorContext,
        assignment_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        assignment_id = require_uuid(assignment_id, "assignment_id")
        self._authorize_staff(context, {"sales_supervisor"})
        self._body(body, {"reason_code", "note", "evidence_asset_ids"}, {"reason_code"})
        reason_code = self._reason_code(body["reason_code"])
        note = self._note(body.get("note"))
        evidence_asset_ids = self._evidence_ids(context.tenant_id, body.get("evidence_asset_ids"))
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            assignment = self._stored_assignment(state, context.tenant_id, assignment_id)
            self._cas(assignment.version, expected_version)
            if assignment.owner_type != "sales":
                raise ValidationError("only a sales-owned assignment can be released", code="not_sales_owned")
            if assignment.valid_until is not None:
                raise ConflictError("assignment has already ended", code="assignment_already_ended")
            policy = self._require_tenant_policy(state, context.tenant_id)

            assignment.valid_until = now
            assignment.end_state = "released"
            assignment.updated_at = now
            assignment.version += 1
            self._emit_assignment_changed(state, context, assignment, correlation_id, now)

            entry = self._open_pool_entry(
                state, context, assignment, policy, "manual_release", now,
                evaluation_snapshot={"reason_code": reason_code, "note": note, "evidence_asset_ids": evidence_asset_ids},
                correlation_id=correlation_id,
            )
            return self._pool_entry_outcome(entry, context, 201)

        return self._run_command(context, "release_maintenance_owner", idempotency_key, {"assignment_id": assignment_id, **body}, correlation_id, mutate)

    def record_employee_departure(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"hr_operator"})
        self._body(body, {"employee_id", "effective_at", "hr_evidence_asset_id", "reason_code"}, {"employee_id", "effective_at", "hr_evidence_asset_id", "reason_code"})
        employee_id = require_uuid(body["employee_id"], "employee_id")
        effective_at = self._parse_time(body["effective_at"], "effective_at")
        hr_evidence_asset_id = require_uuid(body["hr_evidence_asset_id"], "hr_evidence_asset_id")
        if not self.evidence.exists(context.tenant_id, hr_evidence_asset_id):
            raise ValidationError("HR departure evidence does not exist", code="evidence_not_found", field_name="hr_evidence_asset_id")
        reason_code = self._reason_code(body["reason_code"])
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            affected = [
                assignment for (tenant, _), assignment in state.assignments.items()
                if tenant == context.tenant_id
                and assignment.owner_type == "sales"
                and assignment.owner_actor_id == employee_id
                and assignment.valid_until is None
            ]
            policy = self._require_tenant_policy(state, context.tenant_id) if affected else None
            pool_entries_created = 0
            for assignment in sorted(affected, key=lambda item: item.id):
                assignment.valid_until = effective_at
                assignment.end_state = "departure"
                assignment.updated_at = now
                assignment.version += 1
                self._emit_assignment_changed(state, context, assignment, correlation_id, now)
                self._open_pool_entry(
                    state, context, assignment, policy, "employee_departure", effective_at,
                    evaluation_snapshot={"departed_employee_id": employee_id, "reason_code": reason_code},
                    correlation_id=correlation_id,
                )
                pool_entries_created += 1

            departure_event = self._emit(
                state,
                event_type="ownership.employee_departed",
                aggregate_type="employee",
                aggregate_id=employee_id,
                aggregate_version=1,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:employee-departed",
                payload={"employee_id": employee_id, "departure_id": "", "effective_at": wire_time(effective_at)},
                now=now,
                tenant_id=context.tenant_id,
            )
            departure = EmployeeDeparture(
                id=new_id(),
                tenant_id=context.tenant_id,
                employee_id=employee_id,
                effective_at=effective_at,
                hr_evidence_asset_id=hr_evidence_asset_id,
                reason_code=reason_code,
                recorded_at=now,
                assignments_closed=len(affected),
                pool_entries_created=pool_entries_created,
                access_revocation_event_id=departure_event.event_id,
            )
            departure_event.payload["departure_id"] = departure.id
            state.departures[(context.tenant_id, departure.id)] = departure
            return _Outcome(departure.to_api(), 202, "employee", employee_id, 1)

        return self._run_command(context, "record_employee_departure", idempotency_key, body, correlation_id, mutate)

    def evaluate_company_pool(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_service(context, {"ownership_service"})
        self._body(body, {"as_of", "policy_version", "dry_run", "customer_ids"}, {"as_of", "policy_version", "dry_run"})
        as_of = self._parse_time(body["as_of"], "as_of")
        policy_version = str(body["policy_version"])
        if not isinstance(body["dry_run"], bool):
            raise ValidationError("dry_run must be a boolean", field_name="dry_run")
        dry_run = body["dry_run"]
        customer_ids = body.get("customer_ids")
        if customer_ids is not None:
            if not isinstance(customer_ids, list) or len(customer_ids) > 1000:
                raise ValidationError("customer_ids must contain at most 1000 items", field_name="customer_ids")
            customer_ids = [require_uuid(value, "customer_ids") for value in customer_ids]
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            policy_id = state.policy_by_code.get((context.tenant_id, policy_version))
            policy = state.policies.get((context.tenant_id, policy_id)) if policy_id else None
            if not policy:
                raise NotFoundError("policy version was not found")
            if not dry_run and policy.approval_status != "approved":
                raise ValidationError("live evaluation requires an approved policy", code="policy_not_approved")
            rule_status = "approved" if policy.approval_status == "approved" else "proposed"

            targets = customer_ids
            if targets is None:
                targets = sorted({
                    assignment.customer_id for (tenant, _), assignment in state.assignments.items()
                    if tenant == context.tenant_id and assignment.owner_type == "sales" and assignment.valid_until is None
                })

            evaluated: list[dict[str, Any]] = []
            for customer_id in targets:
                outcome = self._evaluate_customer(state, context.tenant_id, customer_id, as_of, policy)
                evaluated.append(outcome)
                if not dry_run and outcome["outcome"] == "eligible":
                    assignment_id = state.current_assignment.get((context.tenant_id, customer_id))
                    assignment = state.assignments.get((context.tenant_id, assignment_id)) if assignment_id else None
                    if assignment and assignment.owner_type == "sales" and assignment.valid_until is None:
                        assignment.valid_until = now
                        # MaintenanceAssignment.state has no dedicated "lapsed" value;
                        # "released" is the closest fit (the relationship ended, just
                        # automatically rather than by a supervisor's manual release).
                        # The real reason survives as PoolEntry.entry_reason below.
                        assignment.end_state = "released"
                        assignment.updated_at = now
                        assignment.version += 1
                        self._emit_assignment_changed(state, context, assignment, correlation_id, now)
                        lapse_started_at = self._parse_time(outcome["lapse_started_at"], "lapse_started_at")
                        self._open_pool_entry(
                            state, context, assignment, policy, "paid_service_lapse", now,
                            evaluation_snapshot=outcome,
                            correlation_id=correlation_id,
                            all_paid_services_ended_at=lapse_started_at,
                        )

            evaluation = PoolEvaluation(
                id=new_id(),
                tenant_id=context.tenant_id,
                policy_version_id=policy.id,
                policy_version=policy_version,
                dry_run=dry_run,
                rule_status=rule_status,
                as_of=as_of,
                created_at=now,
                evaluated=evaluated,
            )
            state.evaluations[(context.tenant_id, evaluation.id)] = evaluation
            self._audit(state, context, "evaluate_company_pool", "pool_evaluation", evaluation.id, 1, correlation_id, "succeeded")
            return _Outcome(evaluation.to_api(), 202, "pool_evaluation", evaluation.id, 1)

        return self._run_command(context, "evaluate_company_pool", idempotency_key, body, correlation_id, mutate)

    # ---- lapse evaluation ---------------------------------------------------

    def _evaluate_customer(self, state: StoreState, tenant_id: str, customer_id: str, as_of: datetime, policy: PolicyVersion) -> dict[str, Any]:
        windows = [window for (tenant, _), window in state.contracts.items() if tenant == tenant_id and window.customer_id == customer_id]
        if not windows:
            return {"customer_id": customer_id, "outcome": "not_applicable", "reason": "no_known_paid_service"}
        ends = [window.effective_end() for window in windows]
        if any(end > as_of for end in ends):
            return {"customer_id": customer_id, "outcome": "ineligible", "reason": "active_paid_service_present", "all_paid_services_lapsed": False}
        lapse_started_at = max(ends)
        if self.restriction.is_restricted(tenant_id, customer_id):
            return {
                "customer_id": customer_id, "outcome": "blocked", "reason": "compliance_hold",
                "all_paid_services_lapsed": True, "lapse_started_at": wire_time(lapse_started_at),
            }
        if as_of - lapse_started_at < timedelta(days=policy.lapse_days):
            return {
                "customer_id": customer_id, "outcome": "ineligible", "reason": "lapse_window_not_elapsed",
                "all_paid_services_lapsed": True, "lapse_started_at": wire_time(lapse_started_at),
            }
        return {
            "customer_id": customer_id, "outcome": "eligible",
            "all_paid_services_lapsed": True, "lapse_started_at": wire_time(lapse_started_at),
        }

    # ---- shared pool-entry / assignment transitions -------------------------

    def _open_pool_entry(
        self,
        state: StoreState,
        context: ActorContext,
        predecessor: Assignment,
        policy: PolicyVersion,
        entry_reason: str,
        entered_at: datetime,
        *,
        evaluation_snapshot: dict[str, Any],
        correlation_id: str,
        all_paid_services_ended_at: datetime | None = None,
    ) -> PoolEntry:
        now = self._clock()
        successor = Assignment(
            id=new_id(),
            tenant_id=context.tenant_id,
            created_at=now,
            updated_at=now,
            customer_id=predecessor.customer_id,
            owner_type="company_pool",
            valid_from=entered_at,
            reason_code=entry_reason,
            policy_version_id=policy.id,
            assigned_by=context.actor_id,
            predecessor_id=predecessor.id,
        )
        state.assignments[(context.tenant_id, successor.id)] = successor
        state.current_assignment[(context.tenant_id, predecessor.customer_id)] = successor.id
        self._emit_assignment_changed(state, context, successor, correlation_id, now)

        restricted = self.restriction.is_restricted(context.tenant_id, predecessor.customer_id)
        entry = PoolEntry(
            id=new_id(),
            tenant_id=context.tenant_id,
            created_at=now,
            updated_at=now,
            customer_id=predecessor.customer_id,
            assignment_id=successor.id,
            entry_reason=entry_reason,
            policy_version_id=policy.id,
            policy_version=policy.wire_code,
            entered_at=entered_at,
            evaluation_snapshot=evaluation_snapshot,
            all_paid_services_ended_at=all_paid_services_ended_at,
            state="suppressed" if restricted else "available",
            suppressed_reason="compliance_hold" if restricted else None,
        )
        state.pool_entries[(context.tenant_id, entry.id)] = entry
        state.pool_entry_by_assignment[(context.tenant_id, successor.id)] = entry.id
        self._emit(
            state,
            event_type="ownership.pool_entered",
            aggregate_type="pool_entry",
            aggregate_id=entry.id,
            aggregate_version=1,
            correlation_id=correlation_id,
            idempotency_key=f"pool-entered:{entry.id}",
            payload={
                "pool_entry_id": entry.id,
                "customer_id": entry.customer_id,
                "entry_reason": entry.entry_reason,
                "policy_version": entry.policy_version,
            },
            now=now,
            tenant_id=context.tenant_id,
        )
        return entry

    def _close_dangling_pool_entry(self, state: StoreState, tenant_id: str, assignment_id: str, claimed_by: str, claimed_at: datetime) -> None:
        entry_id = state.pool_entry_by_assignment.get((tenant_id, assignment_id))
        if not entry_id:
            return
        entry = state.pool_entries[(tenant_id, entry_id)]
        if entry.state == "available":
            entry.state = "claimed"
            entry.claimed_by = claimed_by
            entry.claimed_at = claimed_at
            entry.updated_at = claimed_at
            entry.version += 1

    def _require_tenant_policy(self, state: StoreState, tenant_id: str) -> PolicyVersion:
        candidates = [
            policy for (tenant, _), policy in state.policies.items()
            if tenant == tenant_id and policy.policy_code == DEFAULT_POLICY_CODE
        ]
        if not candidates:
            raise ValidationError("no company pool policy has been configured", code="pool_policy_not_configured")
        return max(candidates, key=lambda policy: policy.policy_version)

    # ---- command transaction helpers ---------------------------------------

    def _run_command(
        self,
        context: ActorContext,
        operation: str,
        key: str,
        request: dict[str, Any],
        correlation_id: str,
        mutate: Callable[[StoreState], _Outcome],
    ) -> ServiceResponse:
        if not _KEY_RE.fullmatch(key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, operation, key)

        def transaction(state: StoreState) -> tuple[_Outcome, bool]:
            receipt = state.idempotency.get(receipt_key)
            if receipt:
                if receipt.request_sha256 != fingerprint:
                    raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
                return _Outcome(receipt.response_value, receipt.response_status, "replay", None, None, receipt.error), True
            outcome = mutate(state)
            self._audit(
                state, context, operation, outcome.aggregate_type, outcome.aggregate_id, outcome.aggregate_version,
                correlation_id, outcome.error.code if outcome.error else "succeeded", outcome.error.code if outcome.error else None,
            )
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                operation=operation,
                key=key,
                request_sha256=fingerprint,
                response_status=outcome.status,
                response_value=outcome.value,
                error=outcome.error,
                completed_at=self._clock(),
            )
            return outcome, False

        try:
            outcome, replayed = self.repository.atomic(transaction)
        except DomainError as exc:
            self._audit_failure(context, operation, correlation_id, exc.code)
            raise
        if outcome.error:
            raise outcome.error
        etag = self._etag(outcome.aggregate_version) if outcome.aggregate_version else None
        return ServiceResponse(outcome.value, outcome.status, etag, replayed)

    def _emit_assignment_changed(self, state: StoreState, context: ActorContext, assignment: Assignment, correlation_id: str, now: datetime) -> None:
        self._emit(
            state,
            event_type="ownership.assignment_changed",
            aggregate_type="maintenance_assignment",
            aggregate_id=assignment.id,
            aggregate_version=assignment.version,
            correlation_id=correlation_id,
            idempotency_key=f"assignment-changed:{assignment.id}:{assignment.version}",
            payload={
                "assignment_id": assignment.id,
                "customer_id": assignment.customer_id,
                "salesperson_id": assignment.owner_actor_id,
                "effective_at": wire_time(assignment.valid_until or assignment.valid_from),
                "state": assignment.wire_state,
            },
            now=now,
            tenant_id=context.tenant_id,
        )

    def _emit(
        self,
        state: StoreState,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        correlation_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        now: datetime,
        tenant_id: str,
        causation_id: str | None = None,
    ) -> OutboxEvent:
        sequence = 1 + max(
            (
                event.sequence_no for (tenant, _), event in state.outbox.items()
                if tenant == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id
            ),
            default=0,
        )
        classification = "internal" if event_type == "ownership.employee_departed" else "customer_confidential"
        event = OutboxEvent(
            event_id=new_id(),
            event_type=event_type,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            sequence_no=sequence,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            classification=classification,
            payload=payload,
            occurred_at=now,
            emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event
        return event

    # ---- authorization, mapping and validation -----------------------------

    def _authorize_staff(self, context: ActorContext, roles: set[str]) -> None:
        if self._staff(context, roles):
            return
        raise ForbiddenError("staff role is not allowed for this operation")

    def _authorize_service(self, context: ActorContext, roles: set[str]) -> None:
        if context.principal_type == "service" and bool(context.roles & roles):
            return
        raise ForbiddenError("service identity is not allowed for this operation")

    @staticmethod
    def _staff(context: ActorContext, roles: set[str]) -> bool:
        return context.principal_type == "staff" and bool(context.roles & roles)

    @staticmethod
    def _body(body: dict[str, Any], allowed: set[str], required: set[str], min_properties: int = 0) -> None:
        if not isinstance(body, dict):
            raise ValidationError("request body must be an object")
        extra = set(body) - allowed
        missing = required - set(body)
        if extra:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(extra))}", field_name=sorted(extra)[0])
        if missing:
            raise ValidationError(f"missing fields: {', '.join(sorted(missing))}", field_name=sorted(missing)[0])
        if len(body) < min_properties:
            raise ValidationError("request body has too few properties")

    @staticmethod
    def _reason_code(value: Any) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        return value

    @staticmethod
    def _note(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 4000:
            raise ValidationError("note must be a string up to 4000 characters", field_name="note")
        return value

    def _evidence_ids(self, tenant_id: str, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 30:
            raise ValidationError("evidence_asset_ids must contain at most 30 items", field_name="evidence_asset_ids")
        ids = [require_uuid(item, "evidence_asset_ids") for item in value]
        if any(not self.evidence.exists(tenant_id, item) for item in ids):
            raise ValidationError("evidence asset does not exist", code="evidence_not_found", field_name="evidence_asset_ids")
        return ids

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _parse_time(value: Any, field_name: str) -> datetime:
        if not isinstance(value, str):
            raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        except (ValueError, AttributeError) as exc:
            raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _etag(version: int) -> str:
        return f'"{version}"'

    @staticmethod
    def _stored_assignment(state: StoreState, tenant_id: str, assignment_id: str) -> Assignment:
        assignment = state.assignments.get((tenant_id, assignment_id))
        if not assignment:
            raise NotFoundError("assignment was not found")
        return assignment

    @staticmethod
    def _stored_pool_entry(state: StoreState, tenant_id: str, pool_entry_id: str) -> PoolEntry:
        entry = state.pool_entries.get((tenant_id, pool_entry_id))
        if not entry:
            raise NotFoundError("pool entry was not found")
        return entry

    def _pool_entry_actions(self, entry: PoolEntry, context: ActorContext) -> list[str]:
        if entry.state == "available" and self._staff(context, {"sales"}):
            return ["claim_pool_customer"]
        return []

    def _assignment_actions(self, assignment: Assignment, context: ActorContext) -> list[str]:
        if assignment.wire_state == "active" and self._staff(context, {"sales_supervisor"}):
            return ["release_maintenance_owner", "assign_maintenance_owner"]
        return []

    def _assignment_outcome(self, assignment: Assignment, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(assignment.to_api(self._assignment_actions(assignment, context)), status, "maintenance_assignment", assignment.id, assignment.version)

    def _pool_entry_outcome(self, entry: PoolEntry, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(entry.to_api(self._pool_entry_actions(entry, context)), status, "pool_entry", entry.id, entry.version)

    def _audit(
        self,
        state: StoreState,
        context: ActorContext,
        action: str,
        aggregate_type: str,
        aggregate_id: str | None,
        aggregate_version: int | None,
        correlation_id: str,
        result: str,
        reason_code: str | None = None,
    ) -> None:
        state.audit_events.append(
            AuditEvent(
                id=new_id(),
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                purpose=context.purpose,
                action=f"m11.{action}",
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                correlation_id=correlation_id,
                result=result,
                reason_code=reason_code,
                recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security", None, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
