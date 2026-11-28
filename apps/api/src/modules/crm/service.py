from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

from .domain import (
    ActorContext,
    ApplicationProjection,
    AuditEvent,
    ClaimRecord,
    ConflictError,
    ContractProjection,
    Conversion,
    CONVERSION_KINDS,
    CrmWorkItem,
    DomainError,
    EntitlementProjection,
    ForbiddenError,
    FUNNEL_STAGES,
    IdempotencyReceipt,
    MarketingAttribution,
    NEXT_ACTION_TYPES,
    NotFoundError,
    Opportunity,
    OPPORTUNITY_KIND_FROM_DB,
    OPPORTUNITY_KIND_TO_DB,
    OPPORTUNITY_OPEN_STATES,
    OPPORTUNITY_STATE_FROM_DB,
    OPPORTUNITY_STATE_TO_DB,
    OutboxEvent,
    OwnershipProjection,
    PaymentProjection,
    PreconditionRequiredError,
    SALES_ACTIVITY_OUTCOMES,
    SALES_ACTIVITY_TYPES,
    SUPPRESSION_REASON_CATEGORIES,
    SUPPRESSION_SCOPES,
    SalesActivity,
    StaleVersionError,
    Suppression,
    SupportCaseProjection,
    ValidationError,
    WORK_ITEM_OPEN_DB_STATUSES,
    WORKLISTS,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import EvidencePort
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
#: suppression scopes that block a new *sales-initiated* outreach action;
#: a service-necessary follow-up (``resolve_issue``) is never blocked.
#: ``marketing_contact`` (REQ-M10-006's marketing opt-out) is included:
#: the requirement text blocks "行銷／升級催促" outreach on opt-out, not
#: only the narrower ``sales_contact``/``all_nonessential_contact`` scopes.
_SALES_PUSH_BLOCKING_SCOPES = {"marketing_contact", "sales_contact", "all_nonessential_contact"}
_RENEWAL_WINDOW = timedelta(days=30)
_PENDING_REVIEW_GRACE = timedelta(days=2)


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


@dataclass(frozen=True)
class SweepSummary:
    created: int
    updated: int
    skipped: int


class CrmService:
    """M10 command/query application service.

    Every state-changing operation runs through one repository callback so
    domain state, audit, outbox and the idempotency receipt commit together.
    Worklist generation (REQ-M10-001/005/012) and upstream event projection
    updates are exposed as plain methods so both HTTP commands and the worker
    (``workers/tasks/crm``) share one business-rule implementation.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        evidence: EvidencePort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(getattr(adapter, "is_fake", False) for adapter in (repository, evidence)):
            raise RuntimeError("fake M10 adapters are forbidden in production")
        self.repository = repository
        self.evidence = evidence
        self._clock = clock

    # ---- create_opportunity ----------------------------------------------

    def create_opportunity(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"sales"}, operation="create_opportunity", correlation_id=correlation_id)
        self._body(body, {"customer_id", "type", "service_code", "contract_id", "reason_code", "owner_id", "due_at"}, {"customer_id", "type", "service_code", "reason_code", "owner_id", "due_at"})
        customer_id = require_uuid(body["customer_id"], "customer_id")
        if body["type"] not in OPPORTUNITY_KIND_TO_DB:
            raise ValidationError("type is not supported", field_name="type")
        kind = OPPORTUNITY_KIND_TO_DB[body["type"]]
        service_code = self._text(body["service_code"], "service_code", 80)
        reason_code = self._text(body["reason_code"], "reason_code", 80)
        owner_id = require_uuid(body["owner_id"], "owner_id")
        due_at = self._parse_time(body["due_at"], "due_at")
        contract_service_id = require_uuid(body["contract_id"], "contract_id") if body.get("contract_id") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            suitable, suitability_reason = self._upgrade_suitability(state, context.tenant_id, customer_id, kind)
            opportunity = Opportunity(
                id=new_id(),
                tenant_id=context.tenant_id,
                version=1,
                created_at=now,
                updated_at=now,
                customer_id=customer_id,
                kind=kind,
                target_service_code=service_code,
                due_at=due_at,
                status="open" if suitable else "cancelled",
                owner_actor_id=owner_id,
                contract_service_id=contract_service_id,
                open_reason_code=reason_code if suitable else suitability_reason,
            )
            state.opportunities[(context.tenant_id, opportunity.id)] = opportunity
            self._emit_opportunity_changed(state, opportunity, correlation_id)
            return self._opportunity_outcome(opportunity, context, 201)

        return self._run_command(context, "create_opportunity", idempotency_key, body, correlation_id, mutate)

    def _upgrade_suitability(self, state: StoreState, tenant_id: str, customer_id: str, kind: str) -> tuple[bool, str | None]:
        if kind != "upgrade":
            return True, None
        for entitlement in state.entitlements.values():
            if entitlement.tenant_id == tenant_id and entitlement.customer_id == customer_id and entitlement.state == "suspended":
                return False, "upgrade_requires_suitability_reassessment"
        return True, None

    # ---- list_opportunities / get_opportunity ------------------------------

    def list_opportunities(
        self,
        context: ActorContext,
        *,
        state_filter: str | None,
        cursor: str | None,
        limit: int,
        as_of: datetime | None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"sales", "sales_supervisor"}, operation="list_opportunities", correlation_id=correlation_id)
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = [item for (tenant, _), item in state.opportunities.items() if tenant == context.tenant_id and item.created_at <= snapshot_at]
            if "sales" in context.roles and "sales_supervisor" not in context.roles:
                rows = [item for item in rows if item.owner_actor_id == context.actor_id]
            if state_filter and state_filter not in OPPORTUNITY_STATE_TO_DB:
                raise ValidationError("state filter is not supported", field_name="state")
            if state_filter:
                rows = [item for item in rows if item.status == OPPORTUNITY_STATE_TO_DB[state_filter]]
            rows = sorted((item for item in rows if not cursor or item.id > cursor), key=lambda item: item.id)
            page = rows[:limit]
            self._audit(state, context, "list_opportunities", "opportunity", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._opportunity_actions(item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_opportunity(self, context: ActorContext, opportunity_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {"sales", "sales_supervisor"}, operation="get_opportunity", correlation_id=correlation_id)
        opportunity_id = require_uuid(opportunity_id, "opportunity_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            opportunity = self._stored_opportunity(state, context.tenant_id, opportunity_id)
            self._authorize_opportunity_owner(context, opportunity)
            self._audit(state, context, "get_opportunity", "opportunity", opportunity.id, opportunity.version, correlation_id, "succeeded")
            return ServiceResponse(opportunity.to_api(self._opportunity_actions(opportunity, context)), etag=self._etag(opportunity.version))

        try:
            return self.repository.atomic(query)
        except ForbiddenError as exc:
            # Unlike record_sales_activity/set_next_action (routed through
            # _run_command, which already audits a DomainError raised inside
            # its own transaction), this plain query has no such wrapper of
            # its own -- the ownership denial would otherwise leave no audit
            # trail at all.
            self._audit_failure(context, "get_opportunity", correlation_id, exc.code)
            raise

    # ---- record_sales_activity --------------------------------------------

    def record_sales_activity(
        self,
        context: ActorContext,
        opportunity_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"sales"}, operation="record_sales_activity", correlation_id=correlation_id)
        opportunity_id = require_uuid(opportunity_id, "opportunity_id")
        self._body(
            body,
            {"activity_type", "occurred_at", "customer_response_evidence_id", "activity_evidence_asset_id", "needs_summary", "next_action", "next_action_due_at", "outcome"},
            {"activity_type", "occurred_at", "activity_evidence_asset_id", "needs_summary", "next_action", "next_action_due_at", "outcome"},
        )
        if body["activity_type"] not in SALES_ACTIVITY_TYPES:
            raise ValidationError("activity_type is not supported", field_name="activity_type")
        if body["outcome"] not in SALES_ACTIVITY_OUTCOMES:
            raise ValidationError("outcome is not supported", field_name="outcome")
        occurred_at = self._parse_time(body["occurred_at"], "occurred_at")
        next_action_due_at = self._parse_time(body["next_action_due_at"], "next_action_due_at")
        evidence_asset_id = require_uuid(body["activity_evidence_asset_id"], "activity_evidence_asset_id")
        customer_response_evidence_id = (
            require_uuid(body["customer_response_evidence_id"], "customer_response_evidence_id")
            if body.get("customer_response_evidence_id")
            else None
        )
        needs_summary = self._text(body["needs_summary"], "needs_summary", 2000)
        next_action_text = self._text(body["next_action"], "next_action", 500)
        if not self.evidence.exists(context.tenant_id, evidence_asset_id):
            raise ValidationError("activity evidence does not exist", code="evidence_not_found", field_name="activity_evidence_asset_id")
        if customer_response_evidence_id and not self.evidence.exists(context.tenant_id, customer_response_evidence_id):
            raise ValidationError("customer response evidence does not exist", code="evidence_not_found", field_name="customer_response_evidence_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            opportunity = self._stored_opportunity(state, context.tenant_id, opportunity_id)
            self._authorize_opportunity_owner(context, opportunity)
            self._cas(opportunity.version, expected_version)
            two_way = body["activity_type"] not in {"proposal", "follow_up"} and customer_response_evidence_id is not None
            upstream_tx_at = self._earliest_confirmed_transaction_at(state, context.tenant_id, opportunity.customer_id, opportunity.created_at)
            late_entry = self._has_claimed_conversion(state, context.tenant_id, opportunity.id) or (upstream_tx_at is not None and upstream_tx_at <= now)
            qualifies = two_way and not late_entry
            activity = SalesActivity(
                id=new_id(),
                tenant_id=context.tenant_id,
                version=1,
                created_at=now,
                updated_at=now,
                customer_id=opportunity.customer_id,
                opportunity_id=opportunity.id,
                actor_id=context.actor_id,
                activity_type=body["activity_type"],
                occurred_at=occurred_at,
                evidence_asset_id=evidence_asset_id,
                two_way=two_way,
                outcome_code=body["outcome"],
                qualifies_for_claim=qualifies,
                customer_response_evidence_id=customer_response_evidence_id,
                needs_summary=needs_summary,
                late_entry=late_entry,
            )
            state.activities[(context.tenant_id, activity.id)] = activity
            worklist = self._worklist_for_kind(opportunity.kind)
            self._upsert_work_item(
                state,
                context.tenant_id,
                customer_id=opportunity.customer_id,
                opportunity_id=opportunity.id,
                worklist=worklist,
                next_action=next_action_text,
                due_at=next_action_due_at,
                reason_codes=[body["outcome"]],
                evidence_refs=[evidence_asset_id],
                owner_actor_id=opportunity.owner_actor_id or context.actor_id,
                suggestion_origin="human",
                now=now,
            )
            # The path's CAS'd aggregate is the opportunity; bump its version
            # so a subsequent CAS'd command on the same opportunity_id (another
            # activity, set_next_action, claim_conversion) must observe this write.
            opportunity.version += 1
            opportunity.updated_at = now
            return self._activity_outcome(activity, opportunity, context, 201)

        return self._run_command(context, "record_sales_activity", idempotency_key, {"opportunity_id": opportunity_id, **body}, correlation_id, mutate)

    @staticmethod
    def _has_claimed_conversion(state: StoreState, tenant_id: str, opportunity_id: str) -> bool:
        """REQ-M10-010: a transaction is "already known" from the moment any
        conversion claim exists for this opportunity, not only once a
        supervisor has approved it -- a backdated activity submitted while a
        claim is still ``proposed`` (pre-decision) is exactly the
        "post-transaction, pre-decision backfill" case this flags."""
        return any(
            tenant == tenant_id and conversion.opportunity_id == opportunity_id
            for (tenant, _), conversion in state.conversions.items()
        )

    @staticmethod
    def _earliest_confirmed_transaction_at(state: StoreState, tenant_id: str, customer_id: str, since: datetime) -> datetime | None:
        """REQ-M10-010: the earliest moment this customer's contract became
        effective or a payment was confirmed, considered only from the
        opportunity's own ``created_at`` onward -- an unrelated transaction
        that predates this opportunity must never retroactively flag it.
        A sales activity recorded at or after this moment is a post-transaction
        backfill, whatever ``occurred_at`` the salesperson claims for it."""
        candidates = [
            contract.effective_at
            for (tenant, _), contract in state.contracts.items()
            if tenant == tenant_id and contract.customer_id == customer_id and contract.effective_at is not None and contract.effective_at >= since
        ]
        candidates += [
            payment.confirmed_at
            for (tenant, _), payment in state.payments.items()
            if tenant == tenant_id and payment.customer_id == customer_id and payment.confirmed_at is not None and payment.confirmed_at >= since
        ]
        return min(candidates) if candidates else None

    # ---- set_next_action ----------------------------------------------------

    def set_next_action(
        self,
        context: ActorContext,
        opportunity_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"sales", "sales_supervisor"}, operation="set_next_action", correlation_id=correlation_id)
        opportunity_id = require_uuid(opportunity_id, "opportunity_id")
        self._body(body, {"owner_id", "action_type", "due_at", "reason_code", "note"}, {"owner_id", "action_type", "due_at", "reason_code"})
        owner_id = require_uuid(body["owner_id"], "owner_id")
        if body["action_type"] not in NEXT_ACTION_TYPES:
            raise ValidationError("action_type is not supported", field_name="action_type")
        due_at = self._parse_time(body["due_at"], "due_at")
        reason_code = self._text(body["reason_code"], "reason_code", 80)
        note = self._text(body["note"], "note", 2000) if body.get("note") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            opportunity = self._stored_opportunity(state, context.tenant_id, opportunity_id)
            self._authorize_opportunity_owner(context, opportunity)
            self._cas(opportunity.version, expected_version)
            if body["action_type"] != "resolve_issue":
                blocked, suppression_reason = self._sales_push_blocked(state, context.tenant_id, opportunity.customer_id, now)
                if blocked:
                    raise ConflictError("an active suppression blocks this sales outreach", code=f"suppression_blocks_action:{suppression_reason}")
                if opportunity.kind == "upgrade" and opportunity.status == "cancelled" and body["action_type"] != "upgrade_assessment":
                    raise ValidationError("upgrade opportunity requires suitability reassessment first", code="upgrade_requires_reassessment")
            worklist = self._worklist_for_kind(opportunity.kind)
            work_item = self._upsert_work_item(
                state,
                context.tenant_id,
                customer_id=opportunity.customer_id,
                opportunity_id=opportunity.id,
                worklist=worklist,
                next_action=body["action_type"],
                due_at=due_at,
                reason_codes=[reason_code],
                evidence_refs=[],
                owner_actor_id=owner_id,
                suggestion_origin="human",
                now=now,
                note=note,
                force_status="assigned",
            )
            suppression_applied = self._customer_suppressed(state, context.tenant_id, opportunity.customer_id, _SALES_PUSH_BLOCKING_SCOPES, now)
            # Bump the path's CAS'd aggregate (the opportunity) so a
            # subsequent CAS'd command on the same opportunity_id must
            # observe this write.
            opportunity.version += 1
            opportunity.updated_at = now
            return self._work_item_outcome(work_item, opportunity, context, 201, suppression_applied)

        return self._run_command(context, "set_next_action", idempotency_key, {"opportunity_id": opportunity_id, **body}, correlation_id, mutate)

    # ---- claim_conversion ---------------------------------------------------

    def claim_conversion(
        self,
        context: ActorContext,
        opportunity_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"sales"}, operation="claim_conversion", correlation_id=correlation_id)
        opportunity_id = require_uuid(opportunity_id, "opportunity_id")
        self._body(
            body,
            {"application_id", "contract_id", "finance_transaction_ref", "sales_activity_ids", "conversion_type", "claimed_salesperson_id", "reason_code"},
            {"application_id", "contract_id", "finance_transaction_ref", "sales_activity_ids", "conversion_type", "claimed_salesperson_id", "reason_code"},
        )
        application_id = require_uuid(body["application_id"], "application_id")
        contract_service_id = require_uuid(body["contract_id"], "contract_id")
        finance_transaction_ref = require_uuid(body["finance_transaction_ref"], "finance_transaction_ref")
        if body["conversion_type"] not in CONVERSION_KINDS:
            raise ValidationError("conversion_type is not supported", field_name="conversion_type")
        claimed_salesperson_id = require_uuid(body["claimed_salesperson_id"], "claimed_salesperson_id")
        if "sales" in context.roles and claimed_salesperson_id != context.actor_id:
            raise ForbiddenError("a salesperson may only claim conversion credit for themselves", code="self_claim_required")
        activity_ids = body["sales_activity_ids"]
        if not isinstance(activity_ids, list) or not 1 <= len(activity_ids) <= 50:
            raise ValidationError("sales_activity_ids must contain 1 to 50 items", field_name="sales_activity_ids")
        activity_ids = [require_uuid(value, "sales_activity_ids") for value in activity_ids]
        reason_code = self._text(body["reason_code"], "reason_code", 80)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            opportunity = self._stored_opportunity(state, context.tenant_id, opportunity_id)
            self._cas(opportunity.version, expected_version)
            for activity_id in activity_ids:
                activity = state.activities.get((context.tenant_id, activity_id))
                if activity is None or activity.opportunity_id != opportunity.id:
                    raise ValidationError("sales_activity_ids must reference this opportunity's own activities", field_name="sales_activity_ids")
            existing_id = state.conversion_by_contract_service.get((context.tenant_id, contract_service_id))
            claim = ClaimRecord(claimed_salesperson_id, tuple(activity_ids), now, reason_code)
            if existing_id:
                conversion = state.conversions[(context.tenant_id, existing_id)]
                # Any decision already made here (approved/rejected/disputed/
                # reversed) is a supervisor's considered call; a re-claim must
                # never silently reset it back to "proposed" and wipe
                # decided_by/decision_at -- only an undecided ("proposed")
                # claim can still accept a competing claim (UAT-M10-009).
                if conversion.decision_status != "proposed":
                    error = ConflictError("this transaction's conversion has already been decided", code="conversion_already_decided")
                    # A rejected re-claim must leave the opportunity untouched
                    # -- bumping its version here would commit (repository.py
                    # commits the mutated working state even on a returned
                    # _Outcome.error) and silently invalidate any CAS/ETag a
                    # caller is holding on the terminal/decided aggregate.
                    return _Outcome({}, error.status, "conversion", conversion.id, conversion.version, error)
                conversion.claim_history = (*conversion.claim_history, claim)
                conversion.evidence_activity_ids = tuple(sorted(set(conversion.evidence_activity_ids) | set(activity_ids)))
                conversion.version += 1
                conversion.updated_at = now
                # The path's CAS'd aggregate is the opportunity; bump its
                # version so a subsequent CAS'd command on the same
                # opportunity_id must observe this write (same pattern as
                # record_sales_activity/set_next_action) -- only on success.
                opportunity.version += 1
                opportunity.updated_at = now
                return self._conversion_outcome(conversion, context, 201)
            if opportunity.status in {"converted", "lost", "cancelled"}:
                # A brand-new conversion attempt (a different
                # contract_service_id) against an opportunity already closed
                # out by a prior transaction -- unlike the existing_id branch
                # above, there is no decided conversion row to carry the more
                # specific "already decided" code. Leave the opportunity
                # untouched (see the existing_id branch's error above).
                error = ConflictError("opportunity is already in a terminal state", code="opportunity_terminal")
                return _Outcome({}, error.status, "opportunity", opportunity.id, opportunity.version, error)
            conversion = Conversion(
                id=new_id(),
                tenant_id=context.tenant_id,
                version=1,
                created_at=now,
                updated_at=now,
                customer_id=opportunity.customer_id,
                opportunity_id=opportunity.id,
                contract_service_id=contract_service_id,
                application_id=application_id,
                conversion_kind=body["conversion_type"],
                finance_transaction_ref=finance_transaction_ref,
                claimed_salesperson_id=claimed_salesperson_id,
                claim_history=(claim,),
                evidence_activity_ids=tuple(activity_ids),
            )
            state.conversions[(context.tenant_id, conversion.id)] = conversion
            state.conversion_by_contract_service[(context.tenant_id, contract_service_id)] = conversion.id
            opportunity.version += 1
            opportunity.updated_at = now
            return self._conversion_outcome(conversion, context, 201)

        return self._run_command(context, "claim_conversion", idempotency_key, {"opportunity_id": opportunity_id, **body}, correlation_id, mutate)

    # ---- decide_conversion ---------------------------------------------------

    def decide_conversion(
        self,
        context: ActorContext,
        conversion_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"sales_supervisor"}, operation="decide_conversion", correlation_id=correlation_id)
        conversion_id = require_uuid(conversion_id, "conversion_id")
        self._body(body, {"decision", "reason_code", "approved_salesperson_id", "qualifying_activity_ids", "causality_evidence_asset_id"}, {"decision", "reason_code", "causality_evidence_asset_id"})
        if body["decision"] not in {"approve", "reject", "dispute"}:
            raise ValidationError("decision is not supported", field_name="decision")
        reason_code = self._text(body["reason_code"], "reason_code", 80)
        causality_evidence_asset_id = require_uuid(body["causality_evidence_asset_id"], "causality_evidence_asset_id")
        if not self.evidence.exists(context.tenant_id, causality_evidence_asset_id):
            raise ValidationError("causality evidence does not exist", code="evidence_not_found", field_name="causality_evidence_asset_id")
        approved_salesperson_id = require_uuid(body["approved_salesperson_id"], "approved_salesperson_id") if body.get("approved_salesperson_id") else None
        requested_activity_ids = body.get("qualifying_activity_ids") or []
        if not isinstance(requested_activity_ids, list) or len(requested_activity_ids) > 50:
            raise ValidationError("qualifying_activity_ids must be an array up to 50 items", field_name="qualifying_activity_ids")
        requested_activity_ids = [require_uuid(value, "qualifying_activity_ids") for value in requested_activity_ids]
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            conversion = self._stored_conversion(state, context.tenant_id, conversion_id)
            self._cas(conversion.version, expected_version)
            claimants = {claim.salesperson_id for claim in conversion.claim_history}
            if context.actor_id in claimants:
                raise ForbiddenError("the reviewing supervisor may not have claimed this conversion themselves", code="maker_checker_violation")
            if conversion.decision_status in {"approved", "reversed"}:
                raise ConflictError("conversion has already been decided", code="conversion_already_decided")
            opportunity = self._stored_opportunity(state, context.tenant_id, conversion.opportunity_id)

            if body["decision"] == "dispute":
                conversion.decision_status = "disputed"
                conversion.decided_by = context.actor_id
                conversion.decision_at = now
                conversion.version += 1
                conversion.updated_at = now
                self._emit_conversion_decided(state, conversion, "disputed", None, causality_evidence_asset_id, correlation_id)
                return self._conversion_outcome(conversion, context, 200)

            if body["decision"] == "reject":
                conversion.decision_status = "rejected"
                conversion.decided_by = context.actor_id
                conversion.decision_at = now
                conversion.sales_actor_id = None
                conversion.version += 1
                conversion.updated_at = now
                self._emit_conversion_decided(state, conversion, "rejected", None, causality_evidence_asset_id, correlation_id)
                return self._conversion_outcome(conversion, context, 200)

            candidate_ids = requested_activity_ids or list(conversion.evidence_activity_ids)
            # Independent temporal-causality check (REQ-M10-010): never trust
            # the ``qualifies_for_claim``/``late_entry`` flags alone at
            # decision time. The claim time (``conversion.created_at``) is
            # sales-controlled and not authoritative on its own -- an
            # upstream system of record (contract effective / payment
            # confirmed) may show the transaction closed even earlier, so the
            # cited activity's ``occurred_at`` must precede whichever of the
            # two came first. Separately, an activity actually *recorded*
            # after that upstream close is a backfill regardless of what
            # ``occurred_at`` it claims, unless a supervisor has explicitly
            # approved it as a late entry.
            upstream_tx_at = self._earliest_confirmed_transaction_at(state, context.tenant_id, opportunity.customer_id, opportunity.created_at)
            boundary_at = min(conversion.created_at, upstream_tx_at) if upstream_tx_at is not None else conversion.created_at
            qualifying_ids = [
                activity_id for activity_id in candidate_ids
                if (activity := state.activities.get((context.tenant_id, activity_id))) is not None
                and activity.opportunity_id == conversion.opportunity_id
                and activity.qualifies_for_claim
                and activity.occurred_at < boundary_at
                and (upstream_tx_at is None or activity.created_at <= upstream_tx_at or activity.late_entry_approved_at is not None)
            ]
            if approved_salesperson_id is not None and not qualifying_ids:
                error = ValidationError(
                    "no cited activity has sufficient two-way evidence for sales attribution", code="evidence_insufficient"
                )
                return _Outcome({}, error.status, "conversion", conversion.id, conversion.version, error)
            conversion.decision_status = "approved"
            conversion.decided_by = context.actor_id
            conversion.decision_at = now
            conversion.sales_actor_id = approved_salesperson_id
            conversion.policy_version = "m10-conversion-policy-v1"
            if qualifying_ids:
                conversion.evidence_activity_ids = tuple(sorted(set(conversion.evidence_activity_ids) | set(qualifying_ids)))
            conversion.version += 1
            conversion.updated_at = now
            opportunity.status = "converted"
            opportunity.converted_at = now
            opportunity.version += 1
            opportunity.updated_at = now
            # the follow-up work this opportunity generated is finished once it converts
            for item in list(state.work_items.values()):
                if item.tenant_id == context.tenant_id and item.opportunity_id == opportunity.id:
                    self._complete_work_item(item, now)
            self._emit_opportunity_changed(state, opportunity, correlation_id)
            self._emit_conversion_decided(state, conversion, "approved", approved_salesperson_id, causality_evidence_asset_id, correlation_id)
            return self._conversion_outcome(conversion, context, 200)

        return self._run_command(context, "decide_conversion", idempotency_key, {"conversion_id": conversion_id, **body}, correlation_id, mutate)

    # ---- create_crm_suppression ----------------------------------------------

    def create_crm_suppression(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"service_agent", "sales_supervisor"}, operation="create_crm_suppression", correlation_id=correlation_id)
        self._body(body, {"customer_id", "scope", "reason_category", "reason_ref", "expires_at"}, {"customer_id", "scope", "reason_category", "reason_ref", "expires_at"})
        customer_id = require_uuid(body["customer_id"], "customer_id")
        if body["scope"] not in SUPPRESSION_SCOPES:
            raise ValidationError("scope is not supported", field_name="scope")
        if body["reason_category"] not in SUPPRESSION_REASON_CATEGORIES:
            raise ValidationError("reason_category is not supported", field_name="reason_category")
        reason_ref = require_uuid(body["reason_ref"], "reason_ref")
        if not self.evidence.exists(context.tenant_id, reason_ref):
            raise ValidationError("suppression reason reference does not exist", code="evidence_not_found", field_name="reason_ref")
        expires_at = self._parse_time(body["expires_at"], "expires_at") if body.get("expires_at") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            suppression = Suppression(
                id=new_id(),
                tenant_id=context.tenant_id,
                version=1,
                created_at=now,
                updated_at=now,
                customer_id=customer_id,
                scope=body["scope"],
                reason_code=body["reason_category"],
                actor_id=context.actor_id,
                valid_from=now,
                reason_ref=reason_ref,
                valid_until=expires_at,
            )
            state.suppressions[(context.tenant_id, suppression.id)] = suppression
            self._emit_suppression_changed(state, suppression, correlation_id, active=True)
            return _Outcome(suppression.to_api([], as_of=now), 201, "suppression", suppression.id, suppression.version)

        return self._run_command(context, "create_crm_suppression", idempotency_key, body, correlation_id, mutate)

    # ---- get_funnel_metrics ----------------------------------------------------

    def get_funnel_metrics(self, context: ActorContext, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {"sales_supervisor", "marketing"}, operation="get_funnel_metrics", correlation_id=correlation_id)
        correlation_id = self._correlation(correlation_id)
        snapshot_at = self._clock()

        def query(state: StoreState) -> ServiceResponse:
            counts: dict[tuple[str, str], set[str]] = {}
            earliest: datetime | None = None
            latest: datetime | None = None

            def mark(stage: str, service_code: str, key: str, moment: datetime) -> None:
                nonlocal earliest, latest
                counts.setdefault((stage, service_code), set()).add(key)
                earliest = moment if earliest is None else min(earliest, moment)
                latest = moment if latest is None else max(latest, moment)

            for (tenant, _), application in state.applications.items():
                if tenant != context.tenant_id:
                    continue
                service_code = application.package_version_id
                mark("application_started", service_code, application.application_id, snapshot_at)
                if application.customer_signed:
                    mark("customer_signed", service_code, application.application_id, snapshot_at)
                if application.company_decision == "accept":
                    mark("accepted", service_code, application.application_id, snapshot_at)
                payment = state.payments.get((tenant, application.application_id))
                if payment and payment.status == "confirmed":
                    mark("paid", service_code, application.application_id, snapshot_at)
            for (tenant, _), entitlement in state.entitlements.items():
                if tenant != context.tenant_id:
                    continue
                contract = state.contracts.get((tenant, entitlement.contract_id))
                if entitlement.state == "active" and (contract is None or contract.terminated_at is None):
                    mark("active", entitlement.contract_id, entitlement.customer_id, snapshot_at)
            for (tenant, _), opportunity in state.opportunities.items():
                if tenant != context.tenant_id or opportunity.status not in OPPORTUNITY_OPEN_STATES:
                    continue
                if opportunity.kind == "renewal":
                    mark("renewal", opportunity.target_service_code, opportunity.customer_id, snapshot_at)
                if opportunity.kind == "upgrade":
                    mark("upgrade", opportunity.target_service_code, opportunity.customer_id, snapshot_at)
            for (tenant, _), contract in state.contracts.items():
                if tenant != context.tenant_id:
                    continue
                if contract.terminated_at is not None:
                    mark("churn", "all", contract.customer_id, contract.terminated_at)

            today = snapshot_at.date()
            items = [
                {
                    "stage": stage,
                    "count": len(members),
                    "cohort_key": f"{today.isoformat()[:7]}",
                    "service_code": service_code if service_code else "unknown",
                    "period_start": today.isoformat(),
                    "period_end": today.isoformat(),
                }
                for (stage, service_code), members in sorted(counts.items())
                if stage in FUNNEL_STAGES
            ]
            self._audit(state, context, "get_funnel_metrics", "funnel", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": items,
                    "as_of": wire_time(snapshot_at),
                    "attribution_policy_version": "m10-conversion-policy-v1",
                    "data_complete_through": wire_time(latest or snapshot_at),
                }
            )

        return self.repository.atomic(query)

    # ---- list_crm_worklists ----------------------------------------------------

    def list_crm_worklists(
        self,
        context: ActorContext,
        *,
        state_filter: str | None,
        cursor: str | None,
        limit: int,
        as_of: datetime | None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"sales", "sales_supervisor", "marketing"}, operation="list_crm_worklists", correlation_id=correlation_id)
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = [item for (tenant, _), item in state.work_items.items() if tenant == context.tenant_id and item.created_at <= snapshot_at]
            if "marketing" in context.roles and "sales" not in context.roles and "sales_supervisor" not in context.roles:
                rows = [item for item in rows if item.worklist == "company_pool"]
            elif "sales" in context.roles and "sales_supervisor" not in context.roles:
                rows = [item for item in rows if item.owner_actor_id == context.actor_id or item.owner_actor_id is None]
            suppressed_map = {
                item.id: self._customer_suppressed(state, context.tenant_id, item.customer_id, _SALES_PUSH_BLOCKING_SCOPES, snapshot_at)
                for item in rows
            }
            projected = [(item, item.to_api(self._work_item_actions(item, context), suppression_applied=suppressed_map[item.id])) for item in rows]
            if state_filter:
                projected = [(item, api) for item, api in projected if api["state"] == state_filter]
            projected.sort(key=lambda pair: pair[0].id)
            total_estimate = len(projected)
            # Filter by cursor before slicing to the page limit (as
            # list_opportunities does); checking the *unfiltered* projected
            # length against limit here previously produced a spurious
            # next_cursor on the last page, which then crashed the
            # following page's ``page[-1]["id"]`` with an IndexError.
            filtered = [api for item, api in projected if not cursor or item.id > cursor]
            page = filtered[:limit]
            self._audit(state, context, "list_crm_worklists", "work_item", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": page,
                    "next_cursor": page[-1]["id"] if len(filtered) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": total_estimate,
                }
            )

        return self.repository.atomic(query)

    # ---- worklist sweep (worker entry point) -------------------------------

    def sweep_worklists(self, tenant_id: str, *, as_of: datetime | None = None, correlation_id: str | None = None) -> SweepSummary:
        """Idempotent periodic recompute (UAT-M10-001/005/012).

        Every candidate work item is upserted by ``dedupe_key`` so a re-run
        over the same inputs never creates a duplicate; an already
        ``in_progress``/``done``/``cancelled`` item keeps its owner and result
        and only has its reason/evidence refreshed.
        """
        now = as_of or self._clock()
        correlation_id = self._correlation(correlation_id)
        created = updated = skipped = 0

        def transaction(state: StoreState) -> tuple[int, int, int]:
            nonlocal created, updated, skipped
            for (tenant, _), contract in list(state.contracts.items()):
                if tenant != tenant_id or contract.terminated_at is not None or contract.service_ends_at is None:
                    continue
                if now <= contract.service_ends_at <= now + _RENEWAL_WINDOW:
                    before = len(state.work_items)
                    self._upsert_work_item(
                        state, tenant_id, customer_id=contract.customer_id, opportunity_id=None, worklist="renewal",
                        next_action="renewal_offer", due_at=contract.service_ends_at - timedelta(days=14),
                        reason_codes=["renewal_due"], evidence_refs=[], owner_actor_id=None, suggestion_origin="rule", now=now,
                    )
                    if len(state.work_items) > before:
                        created += 1
                    else:
                        updated += 1
            for (tenant, _), application in list(state.applications.items()):
                if tenant != tenant_id or not application.customer_signed:
                    continue
                if application.company_decision is None:
                    before = len(state.work_items)
                    self._upsert_work_item(
                        state, tenant_id, customer_id=application.customer_id, opportunity_id=None, worklist="pending_review",
                        next_action="complete_documents", due_at=now + _PENDING_REVIEW_GRACE,
                        reason_codes=["awaiting_company_decision"], evidence_refs=[], owner_actor_id=None, suggestion_origin="rule", now=now,
                    )
                    if len(state.work_items) > before:
                        created += 1
                    else:
                        updated += 1
            return created, updated, skipped

        self.repository.atomic(transaction)
        return SweepSummary(created, updated, skipped)

    # ---- upstream projection updates (worker/consumer entry points) -------

    def record_customer_created(self, state: StoreState, tenant_id: str, customer_id: str, marketing_attribution_id: str | None, now: datetime) -> None:
        attribution = MarketingAttribution(
            id=marketing_attribution_id or new_id(), tenant_id=tenant_id, version=1, created_at=now, updated_at=now,
            customer_id=customer_id, source_type="unknown" if marketing_attribution_id is None else "web",
            acquired_at=now, evidence_sha256="0" * 64,
        )
        state.marketing_attributions[(tenant_id, attribution.id)] = attribution

    def record_application_created(self, state: StoreState, tenant_id: str, application_id: str, customer_id: str, package_version_id: str, intent: str) -> None:
        state.applications[(tenant_id, application_id)] = ApplicationProjection(
            tenant_id=tenant_id, application_id=application_id, customer_id=customer_id, package_version_id=package_version_id, intent=intent,
        )

    def record_application_customer_signed(self, state: StoreState, tenant_id: str, application_id: str) -> None:
        application = state.applications.get((tenant_id, application_id))
        if application is not None:
            application.customer_signed = True

    def record_application_company_decided(self, state: StoreState, tenant_id: str, application_id: str, decision: str, contract_id: str | None) -> None:
        application = state.applications.get((tenant_id, application_id))
        if application is not None:
            application.company_decision = decision
            application.contract_id = contract_id
            # The "awaiting company decision" follow-up no longer applies once no
            # signed application of this customer is still undecided.
            undecided = any(
                row.customer_signed and row.company_decision is None
                for (tenant, _), row in state.applications.items()
                if tenant == tenant_id and row.customer_id == application.customer_id
            )
            if not undecided:
                self._close_work_item(state, tenant_id, application.customer_id, "pending_review")

    def record_contract_effective(self, state: StoreState, tenant_id: str, contract_id: str, customer_id: str, effective_at: datetime, service_ends_at: datetime) -> None:
        state.contracts[(tenant_id, contract_id)] = ContractProjection(
            tenant_id=tenant_id, contract_id=contract_id, customer_id=customer_id, effective_at=effective_at, service_ends_at=service_ends_at,
        )

    def record_contract_terminated(self, state: StoreState, tenant_id: str, contract_id: str, customer_id: str, terminated_at: datetime) -> None:
        contract = state.contracts.get((tenant_id, contract_id))
        if contract is None:
            contract = ContractProjection(tenant_id=tenant_id, contract_id=contract_id, customer_id=customer_id)
            state.contracts[(tenant_id, contract_id)] = contract
        contract.terminated_at = terminated_at

    def record_entitlement_changed(self, state: StoreState, tenant_id: str, entitlement_id: str, customer_id: str, contract_id: str, decision_state: str, valid_until: datetime) -> None:
        state.entitlements[(tenant_id, entitlement_id)] = EntitlementProjection(
            tenant_id=tenant_id, entitlement_id=entitlement_id, customer_id=customer_id, contract_id=contract_id, state=decision_state, valid_until=valid_until,
        )
        if decision_state == "suspended":
            now = self._clock()
            self._upsert_work_item(
                state, tenant_id, customer_id=customer_id, opportunity_id=None, worklist="retention",
                next_action="resolve_issue", due_at=now + timedelta(days=3), reason_codes=["service_restricted"],
                evidence_refs=[entitlement_id], owner_actor_id=None, suggestion_origin="rule", now=now,
            )

    def record_ownership_assignment_changed(self, state: StoreState, tenant_id: str, customer_id: str, salesperson_id: str | None, decision_state: str, now: datetime) -> None:
        state.ownership[(tenant_id, customer_id)] = OwnershipProjection(
            tenant_id=tenant_id, customer_id=customer_id, salesperson_id=salesperson_id, state=decision_state, updated_at=now,
        )
        if salesperson_id and decision_state == "active":
            # a customer with an active maintenance owner is no longer waiting in the company pool
            self._close_work_item(state, tenant_id, customer_id, "company_pool")

    def record_ownership_pool_entered(self, state: StoreState, tenant_id: str, customer_id: str, entry_reason: str, now: datetime) -> None:
        projection = state.ownership.get((tenant_id, customer_id))
        if projection is None:
            projection = OwnershipProjection(tenant_id=tenant_id, customer_id=customer_id, salesperson_id=None, state="pool", updated_at=now)
            state.ownership[(tenant_id, customer_id)] = projection
        projection.in_company_pool = True
        self._upsert_work_item(
            state, tenant_id, customer_id=customer_id, opportunity_id=None, worklist="company_pool",
            next_action="call", due_at=now + timedelta(days=14), reason_codes=[entry_reason], evidence_refs=[],
            owner_actor_id=None, suggestion_origin="rule", now=now,
        )

    #: support.case_opened categories/priorities that count as an "unresolved
    #: major complaint" for REQ-M10-006 (customer must not be pushed sales
    #: outreach while this is open).
    _MAJOR_COMPLAINT_PRIORITIES = {"high", "urgent"}

    def record_support_case_opened(
        self, state: StoreState, tenant_id: str, case_id: str, customer_id: str, category: str, priority: str,
        now: datetime, correlation_id: str,
    ) -> None:
        state.support_cases[(tenant_id, case_id)] = SupportCaseProjection(tenant_id=tenant_id, case_id=case_id, customer_id=customer_id, category=category, priority=priority, open=True)
        if category == "complaint" and priority in self._MAJOR_COMPLAINT_PRIORITIES:
            suppression = Suppression(
                id=new_id(), tenant_id=tenant_id, version=1, created_at=now, updated_at=now, customer_id=customer_id,
                scope="sales_contact", reason_code="open_complaint", actor_id="00000000-0000-0000-0000-000000000001",
                valid_from=now, reason_ref=case_id,
            )
            state.suppressions[(tenant_id, suppression.id)] = suppression
            self._emit_suppression_changed(state, suppression, correlation_id, active=True)

    def record_support_case_resolved(self, state: StoreState, tenant_id: str, case_id: str, now: datetime, correlation_id: str) -> None:
        case = state.support_cases.get((tenant_id, case_id))
        if case is not None:
            case.open = False
        for (tenant, _), suppression in state.suppressions.items():
            if tenant == tenant_id and suppression.scope == "sales_contact" and suppression.reason_ref == case_id and suppression.active(now):
                suppression.valid_until = now
                suppression.version += 1
                suppression.updated_at = now
                self._emit_suppression_changed(state, suppression, correlation_id, active=False)

    def record_payment_confirmed(self, state: StoreState, tenant_id: str, application_id: str, confirmed_at: datetime) -> None:
        application = state.applications.get((tenant_id, application_id))
        customer_id = application.customer_id if application is not None else None
        payment = state.payments.get((tenant_id, application_id))
        if payment is None:
            payment = PaymentProjection(tenant_id=tenant_id, application_id=application_id, status="confirmed", customer_id=customer_id, confirmed_at=confirmed_at)
            state.payments[(tenant_id, application_id)] = payment
        else:
            payment.status = "confirmed"
            payment.customer_id = customer_id or payment.customer_id
            payment.confirmed_at = confirmed_at
        if customer_id is not None and not any(
            row.status == "failed" for (tenant, _), row in state.payments.items()
            if tenant == tenant_id and row.customer_id == customer_id
        ):
            self._close_work_item(state, tenant_id, customer_id, "unpaid")

    def record_payment_failed(self, state: StoreState, tenant_id: str, application_id: str, customer_id: str, reason_code: str, now: datetime) -> None:
        state.payments[(tenant_id, application_id)] = PaymentProjection(tenant_id=tenant_id, application_id=application_id, status="failed", customer_id=customer_id, reason_code=reason_code)
        self._upsert_work_item(
            state, tenant_id, customer_id=customer_id, opportunity_id=None, worklist="unpaid", next_action="complete_documents",
            due_at=now + timedelta(days=1), reason_codes=[reason_code], evidence_refs=[], owner_actor_id=None, suggestion_origin="rule", now=now,
        )

    def record_kyc_refresh_required(self, state: StoreState, tenant_id: str, customer_id: str, refresh_case_id: str, due_at: datetime, now: datetime) -> None:
        self._upsert_work_item(
            state, tenant_id, customer_id=customer_id, opportunity_id=None, worklist="incomplete_application", next_action="complete_documents",
            due_at=due_at, reason_codes=["kyc_refresh_required"], evidence_refs=[refresh_case_id], owner_actor_id=None, suggestion_origin="rule", now=now,
        )

    def record_consent_changed(
        self, state: StoreState, tenant_id: str, customer_id: str, consent_id: str, purpose: str, granted: bool,
        *, correlation_id: str,
    ) -> None:
        state.consents[(tenant_id, consent_id)] = {"customer_id": customer_id, "purpose": purpose, "granted": granted}
        if purpose != "marketing":
            return
        now = self._clock()
        if not granted:
            suppression = Suppression(
                id=new_id(), tenant_id=tenant_id, version=1, created_at=now, updated_at=now, customer_id=customer_id,
                scope="marketing_contact", reason_code="opt_out", actor_id="00000000-0000-0000-0000-000000000001", valid_from=now, reason_ref=consent_id,
            )
            state.suppressions[(tenant_id, suppression.id)] = suppression
            self._emit_suppression_changed(state, suppression, correlation_id, active=True)
            return
        # Re-grant (``granted=True``): lift any still-active marketing_contact
        # suppression for this customer -- it must not keep blocking sales
        # push forever once the customer has opted back in.
        for (tenant, _), suppression in state.suppressions.items():
            if tenant == tenant_id and suppression.customer_id == customer_id and suppression.scope == "marketing_contact" and suppression.active(now):
                suppression.valid_until = now
                suppression.version += 1
                suppression.updated_at = now
                self._emit_suppression_changed(state, suppression, correlation_id, active=False)

    def record_contract_service_ended(self, state: StoreState, tenant_id: str, contract_service_id: str, contract_id: str) -> None:
        state.contract_service_to_contract[(tenant_id, contract_service_id)] = contract_id

    def reverse_conversion_for_refund(self, state: StoreState, tenant_id: str, contract_id: str, now: datetime, correlation_id: str) -> str:
        """Claws back approved conversion(s) attribution on ``finance.refund_confirmed``.

        ``finance.refund_confirmed`` carries M02's parent ``contract_id``,
        which is coarser than the ``contract_service_id`` this module keys
        conversions by. Candidates are resolved via
        ``contract_service_to_contract`` (learned from
        ``contract.service_ended``); until that mapping is known for a given
        contract_service_id, this falls back to treating the refund's
        contract_id as matching contract_service_id directly (true for a
        contract with exactly one service line) -- see
        ``database/proposals/M10/contract-changes.md``.
        """
        candidate_ids = {
            service_id for (tenant, service_id), mapped in state.contract_service_to_contract.items()
            if tenant == tenant_id and mapped == contract_id
        }
        candidate_ids.add(contract_id)
        reversed_count = 0
        for service_id in candidate_ids:
            conversion_id = state.conversion_by_contract_service.get((tenant_id, service_id))
            if conversion_id is None:
                continue
            conversion = state.conversions[(tenant_id, conversion_id)]
            if conversion.decision_status != "approved":
                continue
            conversion.decision_status = "reversed"
            conversion.version += 1
            conversion.updated_at = now
            self._emit_conversion_decided(
                state, conversion, "reversed", conversion.sales_actor_id,
                conversion.claim_history[-1].activity_ids[0] if conversion.claim_history else conversion.id, correlation_id,
            )
            reversed_count += 1
        return f"conversions_reversed:{reversed_count}" if reversed_count else "no_approved_conversion_for_contract"

    # ---- work item upsert helper (UAT-M10-012) -----------------------------

    def _close_work_item(self, state: StoreState, tenant_id: str, customer_id: str, worklist: str) -> None:
        """Complete a rule-generated work item whose triggering condition has been resolved.

        Items someone already started (in_progress) keep their owner's status;
        completed and cancelled items are left as they are.
        """
        existing_id = state.work_item_by_dedupe.get((tenant_id, f"{customer_id}:{worklist}"))
        item = state.work_items.get((tenant_id, existing_id)) if existing_id else None
        if item is not None:
            self._complete_work_item(item, self._clock())

    @staticmethod
    def _complete_work_item(item: CrmWorkItem, now: datetime) -> None:
        if item.status not in WORK_ITEM_OPEN_DB_STATUSES:
            return
        item.status = "done"
        item.version += 1
        item.updated_at = now

    def _upsert_work_item(
        self,
        state: StoreState,
        tenant_id: str,
        *,
        customer_id: str,
        opportunity_id: str | None,
        worklist: str,
        next_action: str,
        due_at: datetime,
        reason_codes: list[str],
        evidence_refs: list[str],
        owner_actor_id: str | None,
        suggestion_origin: str,
        now: datetime,
        note: str | None = None,
        force_status: str | None = None,
    ) -> CrmWorkItem:
        if worklist not in WORKLISTS:
            raise ValidationError("worklist is not supported", code="worklist_not_supported")
        dedupe_key = f"{customer_id}:{worklist}"
        existing_id = state.work_item_by_dedupe.get((tenant_id, dedupe_key))
        if existing_id:
            item = state.work_items[(tenant_id, existing_id)]
            merged_reasons = list(item.reason_codes)
            for code in reason_codes:
                if code not in merged_reasons:
                    merged_reasons.append(code)
            item.reason_codes = tuple(merged_reasons[:20])
            merged_evidence = list(item.evidence_refs)
            for ref in evidence_refs:
                if ref not in merged_evidence:
                    merged_evidence.append(ref)
            item.evidence_refs = tuple(merged_evidence[:20])
            if item.status not in {"in_progress", "done", "cancelled"}:
                item.due_at = min(item.due_at, due_at)
                item.next_action = next_action
                if note is not None:
                    item.note = note
                if owner_actor_id is not None:
                    item.owner_actor_id = owner_actor_id
                if force_status:
                    item.status = force_status
            item.version += 1
            item.updated_at = now
            return item
        item = CrmWorkItem(
            id=new_id(), tenant_id=tenant_id, version=1, created_at=now, updated_at=now, customer_id=customer_id,
            worklist=worklist, dedupe_key=dedupe_key, next_action=next_action, due_at=due_at,
            reason_codes=tuple(reason_codes[:20]), status=force_status or "open", opportunity_id=opportunity_id,
            owner_actor_id=owner_actor_id, evidence_refs=tuple(evidence_refs[:20]), suggestion_origin=suggestion_origin, note=note,
        )
        state.work_items[(tenant_id, item.id)] = item
        state.work_item_by_dedupe[(tenant_id, dedupe_key)] = item.id
        return item

    @staticmethod
    def _worklist_for_kind(kind: str) -> str:
        return {"new": "incomplete_application", "renewal": "renewal", "upgrade": "upgrade", "retention": "retention", "reactivation": "reactivation"}.get(kind, "retention")

    # ---- suppression helpers -----------------------------------------------

    @staticmethod
    def _customer_suppressed(state: StoreState, tenant_id: str, customer_id: str, scopes: set[str], as_of: datetime) -> bool:
        return any(
            tenant == tenant_id and suppression.customer_id == customer_id and suppression.scope in scopes and suppression.active(as_of)
            for (tenant, _), suppression in state.suppressions.items()
        )

    def _sales_push_blocked(self, state: StoreState, tenant_id: str, customer_id: str, as_of: datetime) -> tuple[bool, str | None]:
        for (tenant, _), suppression in state.suppressions.items():
            if tenant == tenant_id and suppression.customer_id == customer_id and suppression.scope in _SALES_PUSH_BLOCKING_SCOPES and suppression.active(as_of):
                return True, suppression.reason_code
        return False, None

    # ---- command transaction helpers (shared idempotency/CAS/audit) -------

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
                tenant_id=context.tenant_id, actor_id=context.actor_id, operation=operation, key=key,
                request_sha256=fingerprint, response_status=outcome.status, response_value=outcome.value,
                error=outcome.error, completed_at=self._clock(),
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

    # ---- event emission -----------------------------------------------------

    def _emit_opportunity_changed(self, state: StoreState, opportunity: Opportunity, correlation_id: str) -> None:
        self._emit(
            state, event_type="crm.opportunity_changed", tenant_id=opportunity.tenant_id, aggregate_type="opportunity",
            aggregate_id=opportunity.id, aggregate_version=opportunity.version, correlation_id=correlation_id,
            idempotency_key=f"opportunity:{opportunity.id}:v{opportunity.version}",
            payload={
                "opportunity_id": opportunity.id, "customer_id": opportunity.customer_id,
                "state": OPPORTUNITY_STATE_FROM_DB[opportunity.status],
                "owner_id": opportunity.owner_actor_id,
            },
        )

    def _emit_conversion_decided(self, state: StoreState, conversion: Conversion, decision: str, approved_salesperson_id: str | None, evidence_snapshot_id: str, correlation_id: str) -> None:
        self._emit(
            state, event_type="crm.conversion_decided", tenant_id=conversion.tenant_id, aggregate_type="conversion",
            aggregate_id=conversion.id, aggregate_version=conversion.version, correlation_id=correlation_id,
            idempotency_key=f"conversion:{conversion.id}:v{conversion.version}",
            payload={
                "conversion_id": conversion.id, "contract_id": conversion.contract_service_id, "decision": decision,
                "approved_salesperson_id": approved_salesperson_id, "evidence_snapshot_id": evidence_snapshot_id,
            },
        )

    def _emit_suppression_changed(self, state: StoreState, suppression: Suppression, correlation_id: str, *, active: bool) -> None:
        self._emit(
            state, event_type="crm.suppression_changed", tenant_id=suppression.tenant_id, aggregate_type="suppression",
            aggregate_id=suppression.id, aggregate_version=suppression.version, correlation_id=correlation_id,
            idempotency_key=f"suppression:{suppression.id}:v{suppression.version}",
            payload={"suppression_id": suppression.id, "customer_id": suppression.customer_id, "scope": suppression.scope, "active": active},
        )

    def _emit(
        self, state: StoreState, *, event_type: str, tenant_id: str, aggregate_type: str, aggregate_id: str,
        aggregate_version: int, correlation_id: str, idempotency_key: str, payload: dict[str, Any],
    ) -> None:
        now = self._clock()
        sequence = 1 + max(
            (event.sequence_no for event in state.outbox.values() if event.tenant_id == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id),
            default=0,
        )
        event_id = new_id()
        event = OutboxEvent(
            event_id=event_id, event_type=event_type, tenant_id=tenant_id, aggregate_type=aggregate_type,
            aggregate_id=aggregate_id, aggregate_version=aggregate_version, sequence_no=sequence,
            correlation_id=correlation_id, causation_id=None, idempotency_key=idempotency_key,
            classification="customer_confidential", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event_id)] = event

    # ---- authorization, mapping and validation ----------------------------

    def _authorize(self, context: ActorContext, roles: set[str], *, operation: str, correlation_id: str | None) -> None:
        if context.principal_type == "staff" and context.roles & roles:
            return
        if context.principal_type == "service" and ({"m10:*"} & context.scopes or roles & context.scopes):
            return
        # A pre-command role denial happens before ``_run_command`` runs (it
        # is every public method's first statement), so without this it
        # would never reach ``_run_command``'s own DomainError audit catch
        # and the rejection would leave no audit trail at all.
        self._audit_failure(context, operation, correlation_id, "forbidden")
        raise ForbiddenError("actor cannot perform this operation")

    @staticmethod
    def _authorize_opportunity_owner(context: ActorContext, opportunity: Opportunity) -> None:
        if "sales_supervisor" in context.roles:
            return
        if "sales" in context.roles and opportunity.owner_actor_id == context.actor_id:
            return
        if context.principal_type == "service":
            return
        raise ForbiddenError("actor cannot access this opportunity", code="opportunity_not_owned")

    @staticmethod
    def _body(body: dict[str, Any], allowed: set[str], required: set[str]) -> None:
        if not isinstance(body, dict):
            raise ValidationError("request body must be an object")
        extra = set(body) - allowed
        missing = required - set(body)
        if extra:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(extra))}", field_name=sorted(extra)[0])
        if missing:
            raise ValidationError(f"missing fields: {', '.join(sorted(missing))}", field_name=sorted(missing)[0])

    @staticmethod
    def _text(value: Any, field_name: str, max_length: int) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= max_length:
            raise ValidationError(f"{field_name} must be a string up to {max_length} characters", field_name=field_name)
        return value

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _parse_time(value: str, field_name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
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
    def _stored_opportunity(state: StoreState, tenant_id: str, opportunity_id: str) -> Opportunity:
        opportunity = state.opportunities.get((tenant_id, opportunity_id))
        if not opportunity:
            raise NotFoundError("opportunity was not found")
        return opportunity

    @staticmethod
    def _stored_conversion(state: StoreState, tenant_id: str, conversion_id: str) -> Conversion:
        conversion = state.conversions.get((tenant_id, conversion_id))
        if not conversion:
            raise NotFoundError("conversion was not found")
        return conversion

    def _opportunity_actions(self, opportunity: Opportunity, context: ActorContext) -> list[str]:
        """Mirror each command's real ``_authorize``/``_authorize_opportunity_owner``
        gate exactly, per-action -- not one blanket "owns" predicate for all
        three actions. ``record_sales_activity``/``claim_conversion`` are
        ``_authorize(context, {"sales"})`` only (``sales_supervisor`` always
        gets 403 from them); ``set_next_action`` is
        ``_authorize(context, {"sales", "sales_supervisor"})``."""
        owner_sales = "sales" in context.roles and opportunity.owner_actor_id == context.actor_id
        supervisor = "sales_supervisor" in context.roles
        if opportunity.status in {"converted", "lost"}:
            return []
        if opportunity.status == "cancelled":
            # Suitability-blocked upgrade (REQ-M10-007/UAT-M10-007): the only
            # permitted path forward is a reassessment via set_next_action's
            # upgrade_assessment action, never a direct sales push.
            return ["set_next_action"] if owner_sales or supervisor else []
        actions: list[str] = []
        if owner_sales:
            actions.extend(["record_sales_activity", "claim_conversion"])
        if owner_sales or supervisor:
            actions.append("set_next_action")
        return sorted(actions)

    @staticmethod
    def _work_item_actions(item: CrmWorkItem, context: ActorContext) -> list[str]:
        if item.status in {"done", "cancelled"}:
            return []
        can_set_next_action = "sales_supervisor" in context.roles or (
            "sales" in context.roles and (item.owner_actor_id == context.actor_id or item.owner_actor_id is None)
        )
        return ["set_next_action"] if can_set_next_action else []

    def _opportunity_outcome(self, opportunity: Opportunity, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(opportunity.to_api(self._opportunity_actions(opportunity, context)), status, "opportunity", opportunity.id, opportunity.version)

    def _activity_outcome(self, activity: SalesActivity, opportunity: Opportunity, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(activity.to_api([]), status, "opportunity", opportunity.id, opportunity.version)

    def _conversion_outcome(self, conversion: Conversion, context: ActorContext, status: int) -> _Outcome:
        claimants = {claim.salesperson_id for claim in conversion.claim_history}
        actions = (
            ["decide_conversion"]
            if "sales_supervisor" in context.roles
            and conversion.decision_status in {"proposed", "disputed"}
            and context.actor_id not in claimants
            else []
        )
        return _Outcome(conversion.to_api(actions), status, "conversion", conversion.id, conversion.version)

    def _work_item_outcome(self, item: CrmWorkItem, opportunity: Opportunity, context: ActorContext, status: int, suppression_applied: bool) -> _Outcome:
        return _Outcome(item.to_api(self._work_item_actions(item, context), suppression_applied=suppression_applied), status, "opportunity", opportunity.id, opportunity.version)

    def _audit(
        self, state: StoreState, context: ActorContext, action: str, aggregate_type: str, aggregate_id: str | None,
        aggregate_version: int | None, correlation_id: str, result: str, reason_code: str | None = None,
    ) -> None:
        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=f"m10.{action}", aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security", None, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
