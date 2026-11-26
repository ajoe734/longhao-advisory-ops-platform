"""M12 commission policy, calculation, adjustment and payout application service.

Every state-changing operation runs through one repository callback so domain
state, audit, outbox and the idempotency receipt commit atomically. Heavy
calculation (``create_commission_run``) and payout assembly
(``create_commission_payout_batch``) only enqueue a job and return 202; the
worker in ``workers/tasks/commissions`` performs the work and flips state,
matching the OpenAPI contract's "202 means persisted receipt only" rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any, Callable

from . import calculation
from .domain import (
    ActorContext,
    CommissionAdjustment,
    CommissionEntry,
    CommissionPolicy,
    CommissionRun,
    ConflictError,
    DomainError,
    ForbiddenError,
    IdempotencyReceipt,
    Job,
    NotFoundError,
    OutboxEvent,
    PayoutBatch,
    PreconditionRequiredError,
    StaleVersionError,
    ValidationError,
    canonical_hash,
    decimal_str,
    money,
    new_id,
    require_date,
    require_decimal,
    require_rfc3339,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import EvidencePort, OwnershipCohortPort, RevenueBasisPort, SalesConversionPort
from .repository import InMemoryRepository, StoreState


_KEY_RE = __import__("re").compile(r"^[A-Za-z0-9._:-]{8,128}$")
_ALLOCATION = {"unresolved", "marketing_decay_only", "whole_pool_decay"}
_AGE_ANCHOR = {"unresolved", "first_paid_effective_start"}
_LINEAGE = {"unresolved", "sticky_assisted_share", "transaction_only_share"}
_YEAR3_RULE = {"unresolved", "zero_unless_continuous_auto", "approved_custom_rule"}
_ROUNDING_MODES = {"ROUND_HALF_UP", "ROUND_HALF_EVEN"}
SERVICE_ACTOR_ID = "00000000-0000-0000-0000-000000000001"


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
    aggregate_type: str | None
    aggregate_id: str | None
    aggregate_version: int | None
    error: DomainError | None = None


class CommissionService:
    def __init__(
        self,
        repository: InMemoryRepository,
        revenue: RevenueBasisPort,
        conversions: SalesConversionPort,
        ownership: OwnershipCohortPort,
        evidence: EvidencePort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False) for adapter in (repository, revenue, conversions, ownership, evidence)
        ):
            raise RuntimeError("fake M12 adapters are forbidden in production")
        self.repository = repository
        self.revenue = revenue
        self.conversions = conversions
        self.ownership = ownership
        self.evidence = evidence
        self._clock = clock

    # ---- policy -----------------------------------------------------------

    def create_commission_policy(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"commission_operator"})
        allowed = {
            "policy_code", "effective_from", "effective_until", "base_pool_rate", "marketing_year_1",
            "marketing_year_2", "marketing_year_3_auto", "assisted_marketing_share", "assisted_sales_share",
            "allocation_interpretation", "age_anchor", "service_lineage_rule", "year3_assisted_marketing_rule",
            "pool_policy_version", "recognition_policy_version", "rounding_mode", "rounding_scale", "notes",
        }
        required = allowed - {"effective_until", "notes"}
        self._body(body, allowed, required)
        policy_code = str(body["policy_code"])
        if not 1 <= len(policy_code) <= 80:
            raise ValidationError("policy_code must be 1 to 80 characters", field_name="policy_code")
        if body["marketing_year_1"] != "1.0" or body["marketing_year_2"] != "0.5" or body["marketing_year_3_auto"] != "0.25":
            raise ValidationError("marketing age coefficients are fixed by approved policy", field_name="marketing_year_1")
        if body["assisted_marketing_share"] != "0.3" or body["assisted_sales_share"] != "0.7":
            raise ValidationError("assisted pool split is fixed at 30/70", field_name="assisted_marketing_share")
        if body["allocation_interpretation"] not in _ALLOCATION:
            raise ValidationError("allocation_interpretation is not supported", field_name="allocation_interpretation")
        if body["age_anchor"] not in _AGE_ANCHOR:
            raise ValidationError("age_anchor is not supported", field_name="age_anchor")
        if body["service_lineage_rule"] not in _LINEAGE:
            raise ValidationError("service_lineage_rule is not supported", field_name="service_lineage_rule")
        if body["year3_assisted_marketing_rule"] not in _YEAR3_RULE:
            raise ValidationError("year3_assisted_marketing_rule is not supported", field_name="year3_assisted_marketing_rule")
        if body["rounding_mode"] not in _ROUNDING_MODES:
            raise ValidationError("rounding_mode is not supported", field_name="rounding_mode")
        scale = body["rounding_scale"]
        if not isinstance(scale, int) or not 0 <= scale <= 6:
            raise ValidationError("rounding_scale must be between 0 and 6", field_name="rounding_scale")
        base_rate = body.get("base_pool_rate")
        parsed_rate = require_decimal(base_rate, "base_pool_rate") if base_rate is not None else None
        if parsed_rate is not None and parsed_rate > 1:
            raise ValidationError("base_pool_rate must be between 0 and 1", field_name="base_pool_rate")
        effective_from = require_rfc3339(body["effective_from"], "effective_from")
        effective_until = require_rfc3339(body["effective_until"], "effective_until") if body.get("effective_until") else None
        for name in ("pool_policy_version", "recognition_policy_version"):
            if not 1 <= len(str(body[name])) <= 80:
                raise ValidationError(f"{name} must be 1 to 80 characters", field_name=name)
        notes = str(body.get("notes", ""))
        if len(notes) > 4000:
            raise ValidationError("notes exceeds 4000 characters", field_name="notes")
        correlation_id = self._correlation(correlation_id)

        def mutate(state: StoreState) -> _Outcome:
            next_version = state.policy_version_counters.get((context.tenant_id, policy_code), 0) + 1
            state.policy_version_counters[(context.tenant_id, policy_code)] = next_version
            policy = CommissionPolicy(
                id=new_id(),
                tenant_id=context.tenant_id,
                policy_code=policy_code,
                policy_version=next_version,
                effective_from=effective_from,
                effective_until=effective_until,
                base_pool_rate=parsed_rate,
                allocation_interpretation=body["allocation_interpretation"],
                age_anchor=body["age_anchor"],
                service_lineage_rule=body["service_lineage_rule"],
                year3_assisted_marketing_rule=body["year3_assisted_marketing_rule"],
                assisted_marketing_share=Decimal("0.3"),
                assisted_sales_share=Decimal("0.7"),
                pool_policy_version=str(body["pool_policy_version"]),
                recognition_policy_version=str(body["recognition_policy_version"]),
                rounding_mode=body["rounding_mode"],
                rounding_scale=scale,
                notes=notes,
                full_policy_asset_id=new_id(),
                proposed_by=context.actor_id,
            )
            state.policies[(context.tenant_id, policy.id)] = policy
            return self._policy_outcome(policy, context, 201)

        return self._run_command(context, "create_commission_policy", idempotency_key, body, correlation_id, mutate)

    def approve_commission_policy(
        self,
        context: ActorContext,
        policy_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"commission_approver"})
        policy_id = require_uuid(policy_id, "policy_id")
        self._body(
            body,
            {"decision", "policy_sha256", "approved_base_pool_rate", "parameter_decision_evidence_asset_id", "effective_from", "reason_code"},
            {"decision", "policy_sha256", "approved_base_pool_rate", "parameter_decision_evidence_asset_id", "effective_from", "reason_code"},
        )
        if body["decision"] not in {"approve", "reject"}:
            raise ValidationError("decision is not supported", field_name="decision")
        approved_rate = require_decimal(body["approved_base_pool_rate"], "approved_base_pool_rate")
        if approved_rate > 1:
            raise ValidationError("approved_base_pool_rate must be between 0 and 1", field_name="approved_base_pool_rate")
        evidence_id = require_uuid(body["parameter_decision_evidence_asset_id"], "parameter_decision_evidence_asset_id")
        if not self.evidence.exists(context.tenant_id, evidence_id):
            raise ValidationError("parameter decision evidence does not exist", code="evidence_not_found", field_name="parameter_decision_evidence_asset_id")
        effective_from = require_rfc3339(body["effective_from"], "effective_from")
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            policy = self._stored_policy(state, context.tenant_id, policy_id)
            self._cas(policy.version, expected_version)
            if policy.content_hash() != body["policy_sha256"]:
                raise ConflictError("policy content changed since this approval was drafted", code="policy_content_mismatch")
            if policy.state != "draft":
                raise ConflictError("policy decision was already recorded", code="policy_already_decided")
            if policy.proposed_by == context.actor_id:
                raise ForbiddenError("maker cannot approve the same policy", code="maker_checker_violation")
            policy.version += 1
            policy.updated_at = now
            if body["decision"] == "reject":
                policy.state = "retired"
                return self._policy_outcome(policy, context, 200)
            if policy.allocation_interpretation == "unresolved" or policy.service_lineage_rule == "unresolved":
                raise ConflictError(
                    "allocation_interpretation and service_lineage_rule must be resolved before approval",
                    code="policy_unresolved_rules",
                )
            policy.base_pool_rate = approved_rate
            policy.effective_from = effective_from
            policy.approved_by = context.actor_id
            policy.approved_at = now
            policy.state = "approved"
            return self._policy_outcome(policy, context, 200)

        return self._run_command(context, "approve_commission_policy", idempotency_key, {"policy_id": policy_id, **body}, correlation_id, mutate)

    # ---- preview / run ------------------------------------------------------

    def preview_commissions(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"commission_operator"})
        policy_id, period_start, period_end, revenue_cutoff_at, recognition_ids = self._parse_run_request(body)
        correlation_id = self._correlation(correlation_id)

        def mutate(state: StoreState) -> _Outcome:
            policy = self._stored_policy(state, context.tenant_id, policy_id)
            lines, unapproved, control_total, currency, _years = self._compute_lines(
                context, policy, period_start, period_end, revenue_cutoff_at, recognition_ids
            )
            response = {
                "preview_id": new_id(),
                "policy_id": policy.id,
                "lines": lines,
                "live_payable": False,
                "unapproved_assumptions": unapproved,
                "control_total": money(control_total, currency),
            }
            return _Outcome(response, 201, None, None, None)

        return self._run_command(context, "preview_commissions", idempotency_key, body, correlation_id, mutate)

    def create_commission_run(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"commission_operator"})
        policy_id, period_start, period_end, revenue_cutoff_at, recognition_ids = self._parse_run_request(body)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            policy = self._stored_policy(state, context.tenant_id, policy_id)
            if not policy.live_eligible():
                raise ValidationError(
                    "commission policy is not approved for live calculation",
                    code="policy_not_approved",
                )
            run_key = canonical_hash(
                {"policy_id": policy_id, "period_start": str(period_start), "period_end": str(period_end),
                 "revenue_cutoff_at": wire_time(revenue_cutoff_at), "recognition_ids": sorted(recognition_ids)}
            )
            existing_id = state.run_keys.get((context.tenant_id, run_key))
            if existing_id:
                run = state.runs[(context.tenant_id, existing_id)]
                return self._run_outcome(state, run, context, 202)
            run = CommissionRun(
                id=new_id(),
                tenant_id=context.tenant_id,
                policy_id=policy.id,
                period_start=period_start,
                period_end=period_end,
                revenue_cutoff_at=revenue_cutoff_at,
                run_key=run_key,
                recognition_ids=tuple(sorted(recognition_ids)),
                prepared_by=context.actor_id,
                policy_sha256=policy.content_hash(),
                started_at=now,
            )
            state.runs[(context.tenant_id, run.id)] = run
            state.run_keys[(context.tenant_id, run_key)] = run.id
            job = Job(id=new_id(), tenant_id=context.tenant_id, kind="calculate_commission_run", payload={"run_id": run.id})
            state.jobs[(context.tenant_id, job.id)] = job
            return self._run_outcome(state, run, context, 202)

        return self._run_command(context, "create_commission_run", idempotency_key, body, correlation_id, mutate)

    def get_commission_run(self, context: ActorContext, run_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {"commission_operator", "commission_approver"})
        run_id = require_uuid(run_id, "run_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            run = self._stored_run(state, context.tenant_id, run_id)
            outcome = self._run_outcome(state, run, context, 200)
            self._audit(state, context, "get_commission_run", "commission_run", run.id, run.version, correlation_id, "succeeded")
            return ServiceResponse(outcome.value, outcome.status, self._etag(run.version))

        return self.repository.atomic(query)

    def list_commission_entries(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"commission_operator", "commission_approver", "sales"})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in {"calculated", "approved", "exported", "adjusted"}:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()
        sales_only = context.roles.isdisjoint({"commission_operator", "commission_approver"})

        def query(state: StoreState) -> ServiceResponse:
            rows = [
                entry for (tenant, _), entry in state.entries.items()
                if tenant == context.tenant_id
                and entry.created_at <= snapshot_at
                and (not sales_only or entry.beneficiary_actor_id == context.actor_id)
                and (not cursor or entry.id > cursor)
            ]
            rows.sort(key=lambda entry: entry.id)
            projected = []
            for entry in rows:
                computed_state = self._entry_state(state, entry)
                if state_filter and computed_state != state_filter:
                    continue
                projected.append((entry, computed_state))
            page = projected[:limit]
            self._audit(state, context, "list_commission_entries", "commission_entry", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [entry.to_api(self._entry_actions(computed_state), state=computed_state, original_entry_id=None) for entry, computed_state in page],
                    "next_cursor": page[-1][0].id if len(projected) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(projected),
                }
            )

        return self.repository.atomic(query)

    def approve_commission_run(
        self,
        context: ActorContext,
        run_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"commission_approver"})
        run_id = require_uuid(run_id, "run_id")
        decision_evidence = self._validate_decision(body)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            run = self._stored_run(state, context.tenant_id, run_id)
            self._cas(run.version, expected_version)
            if run.prepared_by == context.actor_id:
                raise ForbiddenError("maker cannot approve the same run", code="segregation_conflict")
            if run.state != "calculated":
                raise ConflictError("run is not ready for approval", code="run_not_calculated")
            run.version += 1
            run.updated_at = now
            if body["decision"] == "approve":
                run.state = "approved"
                run.approved_by = context.actor_id
                run.approved_at = now
                self._emit(
                    state,
                    event_type="commission.run_approved",
                    aggregate_type="commission_run",
                    tenant_id=context.tenant_id,
                    aggregate_id=run.id,
                    aggregate_version=run.version,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:run-approved",
                    payload={
                        "run_id": run.id,
                        "policy_id": run.policy_id,
                        "approval_id": new_id(),
                        "ledger_manifest_sha256": canonical_hash({"run_id": run.id, "approved_by": context.actor_id}),
                    },
                    now=now,
                )
            else:
                run.state = "rejected"
            return self._run_outcome(state, run, context, 200)

        request = {"run_id": run_id, **body}
        return self._run_command(context, "approve_commission_run", idempotency_key, request, correlation_id, mutate)

    # ---- adjustment ---------------------------------------------------------

    def create_commission_adjustment(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"commission_operator"})
        self._body(
            body,
            {"original_entry_id", "refund_id", "attributable_recognition_id", "adjustment_amount", "reason_code", "evidence_asset_id"},
            {"original_entry_id", "refund_id", "attributable_recognition_id", "adjustment_amount", "reason_code", "evidence_asset_id"},
        )
        original_entry_id = require_uuid(body["original_entry_id"], "original_entry_id")
        refund_id = require_uuid(body["refund_id"], "refund_id")
        recognition_id = require_uuid(body["attributable_recognition_id"], "attributable_recognition_id")
        evidence_id = require_uuid(body["evidence_asset_id"], "evidence_asset_id")
        if not self.evidence.exists(context.tenant_id, evidence_id):
            raise ValidationError("adjustment evidence does not exist", code="evidence_not_found", field_name="evidence_asset_id")
        money_body = body["adjustment_amount"]
        if not isinstance(money_body, dict) or set(money_body) != {"amount", "currency"}:
            raise ValidationError("adjustment_amount must be a Money object", field_name="adjustment_amount")
        amount = require_decimal(money_body["amount"], "adjustment_amount.amount", allow_negative=True)
        currency = str(money_body["currency"])
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        dedupe_key = canonical_hash({"original_entry_id": original_entry_id, "refund_id": refund_id})

        def mutate(state: StoreState) -> _Outcome:
            existing_id = state.adjustment_dedupe.get((context.tenant_id, dedupe_key))
            if existing_id:
                existing = state.adjustments[(context.tenant_id, existing_id)]
                return self._adjustment_outcome(existing, context, 201)
            original = self._stored_entry(state, context.tenant_id, original_entry_id)
            if original.recognition_id != recognition_id:
                raise ValidationError(
                    "attributable_recognition_id does not match the original entry",
                    field_name="attributable_recognition_id",
                )
            draft = state.pending_adjustment_drafts.get((context.tenant_id, dedupe_key))
            if draft is not None and draft.prepared_by != context.actor_id:
                prepared_by, approved_by = draft.prepared_by, context.actor_id
            else:
                prepared_by, approved_by = context.actor_id, context.actor_id
            exceeds = abs(amount) > original.award_amount
            paid_line_id = state.payout_line_by_entry.get((context.tenant_id, original.id))
            already_paid = paid_line_id is not None and state.payout_lines[(context.tenant_id, paid_line_id)].accepted is True
            adjustment = CommissionAdjustment(
                id=new_id(),
                tenant_id=context.tenant_id,
                original_entry_id=original.id,
                refund_id=refund_id,
                amount=amount,
                currency=currency,
                reason_code=reason_code,
                dedupe_key=dedupe_key,
                recovery_method="manual_review" if already_paid else "ledger_offset",
                prepared_by=prepared_by,
                approved_by=approved_by,
                original_recipient_id=original.beneficiary_actor_id,
                exceeds_original_award=exceeds,
                created_at=now,
                updated_at=now,
            )
            state.adjustments[(context.tenant_id, adjustment.id)] = adjustment
            state.adjustment_dedupe[(context.tenant_id, dedupe_key)] = adjustment.id
            self._emit(
                state,
                event_type="commission.adjustment_recorded",
                aggregate_type="commission_adjustment",
                tenant_id=context.tenant_id,
                aggregate_id=adjustment.id,
                aggregate_version=1,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:adjustment-recorded",
                payload={
                    "adjustment_id": adjustment.id,
                    "original_entry_id": adjustment.original_entry_id,
                    "refund_id": adjustment.refund_id,
                    "original_recipient_id": adjustment.original_recipient_id,
                },
                now=now,
            )
            return self._adjustment_outcome(adjustment, context, 201)

        return self._run_command(context, "create_commission_adjustment", idempotency_key, body, correlation_id, mutate)

    # ---- payout batch -------------------------------------------------------

    def create_commission_payout_batch(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"payroll_operator"})
        self._body(body, {"approved_run_ids", "payout_period", "payroll_mapping_version"}, {"approved_run_ids", "payout_period", "payroll_mapping_version"})
        run_ids = body["approved_run_ids"]
        if not isinstance(run_ids, list) or not 1 <= len(run_ids) <= 100:
            raise ValidationError("approved_run_ids must contain 1 to 100 items", field_name="approved_run_ids")
        run_ids = [require_uuid(value, "approved_run_ids") for value in run_ids]
        if not re.fullmatch(r"\d{4}-\d{2}", str(body["payout_period"])):
            raise ValidationError("payout_period must match YYYY-MM", field_name="payout_period")
        mapping_version = str(body["payroll_mapping_version"])
        if not 1 <= len(mapping_version) <= 80:
            raise ValidationError("payroll_mapping_version must contain 1 to 80 characters", field_name="payroll_mapping_version")
        correlation_id = self._correlation(correlation_id)

        def mutate(state: StoreState) -> _Outcome:
            currency = "TWD"
            for run_id in run_ids:
                run = self._stored_run(state, context.tenant_id, run_id)
                if run.state != "approved":
                    raise ValidationError("referenced run is not approved", code="run_not_approved", field_name="approved_run_ids")
                currency = run.currency
                for (tenant, _), entry in state.entries.items():
                    if tenant != context.tenant_id or entry.run_id != run_id:
                        continue
                    existing_line_id = state.payout_line_by_entry.get((context.tenant_id, entry.id))
                    if existing_line_id:
                        existing_batch_id = state.payout_lines[(context.tenant_id, existing_line_id)].batch_id
                        existing_batch = state.payout_batches.get((context.tenant_id, existing_batch_id))
                        if existing_batch is not None and existing_batch.status != "reversed":
                            raise ConflictError("entry already belongs to an active payout batch", code="entry_already_batched")
            batch = PayoutBatch(
                id=new_id(),
                tenant_id=context.tenant_id,
                run_ids=tuple(sorted(set(run_ids))),
                payout_period=str(body["payout_period"]),
                payroll_mapping_version=mapping_version,
                prepared_by=context.actor_id,
                currency=currency,
                control_sha256=canonical_hash({"run_ids": sorted(set(run_ids)), "payout_period": body["payout_period"], "stage": "preliminary"}),
            )
            state.payout_batches[(context.tenant_id, batch.id)] = batch
            job = Job(id=new_id(), tenant_id=context.tenant_id, kind="assemble_payout_batch", payload={"batch_id": batch.id})
            state.jobs[(context.tenant_id, job.id)] = job
            return self._payout_outcome(batch, context, 202)

        return self._run_command(context, "create_commission_payout_batch", idempotency_key, body, correlation_id, mutate)

    def get_commission_payout_batch(self, context: ActorContext, batch_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {"payroll_operator", "commission_approver"})
        batch_id = require_uuid(batch_id, "batch_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            batch = self._stored_batch(state, context.tenant_id, batch_id)
            self._audit(state, context, "get_commission_payout_batch", "payout_batch", batch.id, batch.version, correlation_id, "succeeded")
            return ServiceResponse(batch.to_api(self._payout_actions(batch, context)), etag=self._etag(batch.version))

        return self.repository.atomic(query)

    def approve_commission_payout_batch(
        self,
        context: ActorContext,
        batch_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"commission_approver"})
        batch_id = require_uuid(batch_id, "batch_id")
        self._validate_decision(body)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            batch = self._stored_batch(state, context.tenant_id, batch_id)
            self._cas(batch.version, expected_version)
            if batch.prepared_by == context.actor_id:
                raise ForbiddenError("maker cannot approve the same payout batch", code="segregation_conflict")
            if batch.status != "approval_pending":
                raise ConflictError("payout batch is not ready for approval", code="batch_not_ready")
            batch.version += 1
            batch.updated_at = now
            if body["decision"] == "approve":
                control_total = self._payable_total(state, context.tenant_id, batch.run_ids)
                if control_total != batch.total_amount:
                    raise ConflictError("payout batch total no longer matches approved run control totals", code="control_total_mismatch")
                batch.status = "approved"
                batch.approved_by = context.actor_id
            else:
                batch.status = "reversed"
            return self._payout_outcome(batch, context, 200)

        request = {"batch_id": batch_id, **body}
        return self._run_command(context, "approve_commission_payout_batch", idempotency_key, request, correlation_id, mutate)

    def acknowledge_commission_payout_batch(
        self, context: ActorContext, batch_id: str, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._authorize(context, {"payroll_adapter"})
        batch_id = require_uuid(batch_id, "batch_id")
        self._body(
            body,
            {"event_id", "external_batch_ref", "control_sha256", "status", "accepted_entry_ids", "rejected_entry_ids", "acknowledgement_evidence_id"},
            {"event_id", "external_batch_ref", "control_sha256", "status", "accepted_entry_ids", "rejected_entry_ids", "acknowledgement_evidence_id"},
        )
        event_id = require_uuid(body["event_id"], "event_id")
        if body["status"] not in {"accepted", "partial", "rejected"}:
            raise ValidationError("status is not supported", field_name="status")
        accepted_ids = [require_uuid(v, "accepted_entry_ids") for v in body["accepted_entry_ids"]]
        rejected_ids = [require_uuid(v, "rejected_entry_ids") for v in body["rejected_entry_ids"]]
        evidence_id = require_uuid(body["acknowledgement_evidence_id"], "acknowledgement_evidence_id")
        if not self.evidence.exists(context.tenant_id, evidence_id):
            raise ValidationError("acknowledgement evidence does not exist", code="evidence_not_found", field_name="acknowledgement_evidence_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            batch = self._stored_batch(state, context.tenant_id, batch_id)
            if event_id in batch.ack_event_ids:
                return self._payout_outcome(batch, context, 202)
            if batch.status != "approved":
                raise ConflictError("payout batch is not approved", code="batch_not_approved")
            if body["control_sha256"] != batch.control_sha256:
                raise ValidationError("control_sha256 does not match the assembled batch", code="control_total_mismatch", field_name="control_sha256")
            batch.version += 1
            batch.updated_at = now
            batch.ack_event_ids = batch.ack_event_ids + (event_id,)
            batch.payroll_export_ref = str(body["external_batch_ref"])
            for entry_id in accepted_ids:
                line_id = state.payout_line_by_entry.get((context.tenant_id, entry_id))
                if line_id:
                    state.payout_lines[(context.tenant_id, line_id)].accepted = True
            for entry_id in rejected_ids:
                line_id = state.payout_line_by_entry.get((context.tenant_id, entry_id))
                if line_id:
                    state.payout_lines[(context.tenant_id, line_id)].accepted = False
            if body["status"] == "rejected":
                batch.status = "reversed"
            else:
                batch.status = "paid"
                batch.paid_at = now
                batch.export_asset_id = evidence_id
            return self._payout_outcome(batch, context, 202)

        request = {"batch_id": batch_id, **body}
        return self._run_command(context, "acknowledge_commission_payout_batch", idempotency_key, request, correlation_id, mutate)

    # ---- job entry points used by workers/tasks/commissions -----------------

    def run_calculation_job(self, tenant_id: str, run_id: str) -> str:
        """Executed by the worker lease loop; returns a short result code."""

        def mutate(state: StoreState) -> str:
            run = state.runs.get((tenant_id, run_id))
            if run is None:
                raise NotFoundError("commission run was not found")
            if run.state != "queued":
                return "already_processed"
            policy = state.policies[(tenant_id, run.policy_id)]
            context = ActorContext(tenant_id, SERVICE_ACTOR_ID, frozenset({"platform_worker"}), principal_type="service", actor_type="service")
            lines, unapproved, _control_total, currency, customer_years = self._compute_lines(
                context, policy, run.period_start, run.period_end, run.revenue_cutoff_at, set(run.recognition_ids)
            )
            run.currency = currency
            now = self._clock()
            for line in lines:
                for beneficiary_type, amount_key, actor_key in (
                    ("marketing", "marketing_award", "marketing_attribution_id"),
                    ("sales", "sales_award", "salesperson_id"),
                    ("company", "company_retained", None),
                ):
                    entry = CommissionEntry(
                        id=new_id(),
                        tenant_id=tenant_id,
                        run_id=run.id,
                        recognition_id=line["recognition_id"],
                        beneficiary_type=beneficiary_type,
                        beneficiary_actor_id=line.get(actor_key) if actor_key else None,
                        award_amount=Decimal(line[amount_key]["amount"]),
                        base_pool_amount=Decimal(line["base_pool"]["amount"]),
                        eligible_untaxed_amount=Decimal(line["basis"]["amount"]),
                        currency=currency,
                        customer_year=customer_years[line["recognition_id"]],
                        age_coefficient=Decimal(line["marketing_age_coefficient"]),
                        calculation_snapshot=line,
                        conversion_id=line["conversion_id"],
                        attribution_id=line["marketing_attribution_id"],
                        created_at=now,
                        updated_at=now,
                    )
                    state.entries[(tenant_id, entry.id)] = entry
            run.state = "calculated"
            run.completed_at = now
            run.updated_at = now
            run.input_sha256 = canonical_hash({"run_id": run.id, "lines": len(lines), "unapproved": unapproved})
            self._emit(
                state,
                event_type="commission.run_calculated",
                aggregate_type="commission_run",
                tenant_id=tenant_id,
                aggregate_id=run.id,
                aggregate_version=run.version,
                correlation_id=new_id(),
                idempotency_key=f"run-calculated:{run.id}",
                payload={
                    "run_id": run.id,
                    "policy_id": run.policy_id,
                    "entry_count": len(lines) * 3,
                    "calculation_manifest_sha256": run.input_sha256,
                },
                now=now,
            )
            return "calculated"

        return self.repository.atomic(mutate)

    def assemble_payout_batch_job(self, tenant_id: str, batch_id: str) -> str:
        def mutate(state: StoreState) -> str:
            batch = state.payout_batches.get((tenant_id, batch_id))
            if batch is None:
                raise NotFoundError("payout batch was not found")
            if batch.status != "draft":
                return "already_assembled"
            lines: list[tuple[str, Decimal]] = []
            now = self._clock()
            for (tenant, entry_id), entry in state.entries.items():
                if tenant != tenant_id or entry.run_id not in batch.run_ids or entry.beneficiary_type not in {"marketing", "sales"}:
                    continue
                net_amount = entry.award_amount + sum(
                    (adj.amount for adj in state.adjustments.values() if adj.original_entry_id == entry.id), Decimal("0")
                )
                if net_amount <= 0:
                    continue
                adjustment_ids = tuple(
                    adj.id for adj in state.adjustments.values() if adj.original_entry_id == entry.id
                )
                from .domain import PayoutLine

                line = PayoutLine(id=new_id(), tenant_id=tenant_id, batch_id=batch.id, entry_id=entry.id, amount=net_amount, adjustment_ids=adjustment_ids, created_at=now, updated_at=now)
                state.payout_lines[(tenant_id, line.id)] = line
                state.payout_line_by_entry[(tenant_id, entry.id)] = line.id
                lines.append((entry.id, net_amount))
            lines.sort(key=lambda row: row[0])
            total = sum((amount for _, amount in lines), Decimal("0"))
            batch.total_amount = total
            batch.line_count = len(lines)
            batch.control_sha256 = canonical_hash({"lines": [(eid, decimal_str(amount)) for eid, amount in lines], "total": decimal_str(total)})
            batch.status = "approval_pending"
            batch.updated_at = now
            return "assembled"

        return self.repository.atomic(mutate)

    # ---- calculation helpers -------------------------------------------------

    def _compute_lines(
        self,
        context: ActorContext,
        policy: CommissionPolicy,
        period_start: date,
        period_end: date,
        revenue_cutoff_at: datetime,
        recognition_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[str], Decimal, str, dict[str, int]]:
        facts = self.revenue.eligible_recognitions(
            context.tenant_id, period_start=period_start, period_end=period_end, revenue_cutoff_at=revenue_cutoff_at,
            recognition_ids=tuple(sorted(recognition_ids)),
        )
        lines: list[dict[str, Any]] = []
        unapproved: set[str] = set(f"policy:{name}" for name in policy.unresolved_parameters())
        control_total = Decimal("0")
        currency = "TWD"
        customer_years: dict[str, int] = {}
        for fact in facts:
            cohort = self.ownership.cohort(context.tenant_id, fact.customer_id, revenue_cutoff_at)
            conversion = self.conversions.assisted_conversion(context.tenant_id, fact.recognition_id)
            split = calculation.compute_split(fact, cohort, conversion, policy)
            unapproved.update(split.unapproved_assumptions)
            lines.append(calculation.line_snapshot(fact, cohort, conversion, policy, split))
            control_total += split.base_pool
            currency = fact.currency
            customer_years[fact.recognition_id] = cohort.customer_year
        return lines, sorted(unapproved), control_total, currency, customer_years

    def _parse_run_request(self, body: dict[str, Any]) -> tuple[str, date, date, datetime, set[str]]:
        self._body(body, {"policy_id", "period_start", "period_end", "revenue_cutoff_at", "recognition_ids", "assumption_overrides_asset_id"}, {"policy_id", "period_start", "period_end", "revenue_cutoff_at"})
        policy_id = require_uuid(body["policy_id"], "policy_id")
        period_start = require_date(body["period_start"], "period_start")
        period_end = require_date(body["period_end"], "period_end")
        if period_end <= period_start:
            raise ValidationError("period_end must be after period_start", field_name="period_end")
        revenue_cutoff_at = require_rfc3339(body["revenue_cutoff_at"], "revenue_cutoff_at")
        recognition_ids = body.get("recognition_ids") or []
        if not isinstance(recognition_ids, list) or len(recognition_ids) > 10000:
            raise ValidationError("recognition_ids must contain at most 10000 items", field_name="recognition_ids")
        recognition_ids = {require_uuid(value, "recognition_ids") for value in recognition_ids}
        return policy_id, period_start, period_end, revenue_cutoff_at, recognition_ids

    # ---- command transaction helpers ----------------------------------------

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
                return _Outcome(receipt.response_value, receipt.response_status, None, None, None, receipt.error), True
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

    def _emit(
        self,
        state: StoreState,
        *,
        event_type: str,
        aggregate_type: str,
        tenant_id: str,
        aggregate_id: str,
        aggregate_version: int,
        correlation_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        now: datetime,
        causation_id: str | None = None,
    ) -> None:
        sequence = 1 + max(
            (event.sequence_no for (tenant, _), event in state.outbox.items() if tenant == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(), event_type=event_type, tenant_id=tenant_id, aggregate_type=aggregate_type,
            aggregate_id=aggregate_id, aggregate_version=aggregate_version, sequence_no=sequence,
            correlation_id=correlation_id, causation_id=causation_id, idempotency_key=idempotency_key,
            classification="internal", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    # ---- authorization and validation ---------------------------------------

    def _authorize(self, context: ActorContext, roles: set[str]) -> None:
        if context.roles.isdisjoint(roles):
            raise ForbiddenError("actor cannot perform this M12 operation")

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

    def _validate_decision(self, body: dict[str, Any]) -> list[str]:
        self._body(body, {"decision", "reason_code", "note", "evidence_asset_ids"}, {"decision", "reason_code"})
        if body["decision"] not in {"approve", "reject", "request_changes"}:
            raise ValidationError("decision is not supported", field_name="decision")
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        evidence_ids = body.get("evidence_asset_ids", [])
        if not isinstance(evidence_ids, list) or len(evidence_ids) > 30:
            raise ValidationError("evidence_asset_ids must be an array up to 30 items", field_name="evidence_asset_ids")
        return [require_uuid(value, "evidence_asset_ids") for value in evidence_ids]

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _etag(version: int) -> str:
        return f'"{version}"'

    @staticmethod
    def _stored_policy(state: StoreState, tenant_id: str, policy_id: str) -> CommissionPolicy:
        policy = state.policies.get((tenant_id, policy_id))
        if not policy:
            raise NotFoundError("commission policy was not found")
        return policy

    @staticmethod
    def _stored_run(state: StoreState, tenant_id: str, run_id: str) -> CommissionRun:
        run = state.runs.get((tenant_id, run_id))
        if not run:
            raise NotFoundError("commission run was not found")
        return run

    @staticmethod
    def _stored_entry(state: StoreState, tenant_id: str, entry_id: str) -> CommissionEntry:
        entry = state.entries.get((tenant_id, entry_id))
        if not entry:
            raise NotFoundError("commission entry was not found")
        return entry

    @staticmethod
    def _stored_batch(state: StoreState, tenant_id: str, batch_id: str) -> PayoutBatch:
        batch = state.payout_batches.get((tenant_id, batch_id))
        if not batch:
            raise NotFoundError("payout batch was not found")
        return batch

    def _run_totals(self, state: StoreState, run: CommissionRun) -> tuple[dict[str, Decimal], int, int]:
        """Returns (totals, raw entry-row count, distinct exception line count).

        ``basis``/``pool`` are only accumulated once per recognition line (via
        the marketing row) since all three beneficiary rows for a line carry
        the same basis/base_pool snapshot values.
        """

        totals = {"basis": Decimal("0"), "pool": Decimal("0"), "marketing": Decimal("0"), "sales": Decimal("0"), "company": Decimal("0")}
        entry_count = 0
        exception_recognition_ids: set[str] = set()
        for (tenant, _), entry in state.entries.items():
            if tenant != run.tenant_id or entry.run_id != run.id:
                continue
            entry_count += 1
            if entry.beneficiary_type == "marketing":
                totals["basis"] += entry.eligible_untaxed_amount
                totals["pool"] += entry.base_pool_amount
            totals[entry.beneficiary_type] += entry.award_amount
            trace_codes = entry.calculation_snapshot.get("rule_trace_codes") or ()
            if any("fallback" in code for code in trace_codes):
                exception_recognition_ids.add(entry.recognition_id)
        return totals, entry_count, len(exception_recognition_ids)

    def _payable_total(self, state: StoreState, tenant_id: str, run_ids: tuple[str, ...]) -> Decimal:
        total = Decimal("0")
        for (tenant, entry_id), entry in state.entries.items():
            if tenant != tenant_id or entry.run_id not in run_ids or entry.beneficiary_type not in {"marketing", "sales"}:
                continue
            total += entry.award_amount + sum(
                (adj.amount for adj in state.adjustments.values() if adj.original_entry_id == entry.id), Decimal("0")
            )
        return total

    def _entry_state(self, state: StoreState, entry: CommissionEntry) -> str:
        run = state.runs.get((entry.tenant_id, entry.run_id))
        has_adjustment = any(adj.original_entry_id == entry.id for adj in state.adjustments.values())
        if has_adjustment:
            return "adjusted"
        line_id = state.payout_line_by_entry.get((entry.tenant_id, entry.id))
        if line_id:
            batch_id = state.payout_lines[(entry.tenant_id, line_id)].batch_id
            batch = state.payout_batches.get((entry.tenant_id, batch_id))
            if batch is not None and batch.status in {"exported", "paid"}:
                return "exported"
        if run is not None and run.state == "approved":
            return "approved"
        return "calculated"

    @staticmethod
    def _entry_actions(_state: str) -> list[str]:
        return []

    def _policy_outcome(self, policy: CommissionPolicy, context: ActorContext, status: int) -> _Outcome:
        actions: list[str] = []
        if policy.state == "draft":
            actions.append("approve_commission_policy")
        return _Outcome(policy.to_api(actions), status, "commission_policy", policy.id, policy.version)

    def _run_outcome(self, state: StoreState, run: CommissionRun, context: ActorContext, status: int) -> _Outcome:
        totals, entry_count, exception_count = self._run_totals(state, run)
        actions: list[str] = []
        if run.state == "calculated":
            actions.append("approve_commission_run")
        return _Outcome(run.to_api(actions, totals, entry_count, exception_count), status, "commission_run", run.id, run.version)

    def _adjustment_outcome(self, adjustment: CommissionAdjustment, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(adjustment.to_api([]), status, "commission_adjustment", adjustment.id, adjustment.version)

    def _payout_actions(self, batch: PayoutBatch, context: ActorContext) -> list[str]:
        if batch.status == "approval_pending":
            return ["approve_commission_payout_batch"]
        return []

    def _payout_outcome(self, batch: PayoutBatch, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(batch.to_api(self._payout_actions(batch, context)), status, "payout_batch", batch.id, batch.version)

    def _audit(
        self, state: StoreState, context: ActorContext, action: str, aggregate_type: str | None, aggregate_id: str | None,
        aggregate_version: int | None, correlation_id: str, result: str, reason_code: str | None = None,
    ) -> None:
        from .domain import AuditEvent

        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=f"m12.{action}", aggregate_type=aggregate_type or "none", aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, None, None, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
