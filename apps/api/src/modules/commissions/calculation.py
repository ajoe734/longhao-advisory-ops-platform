"""Pure decimal commission-split engine shared by preview and the run worker.

No I/O and no randomness: every result is reproducible from ``(policy, fact,
cohort, conversion)``. Conservation (``marketing + sales + company ==
base_pool``) is guaranteed by construction: the two rounded shares are
quantized first and the company share always absorbs the remainder, so
minor-unit rounding never leaks or invents money (REQ-M12-010).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .domain import ValidationError, apply_rounding, decimal_str, money
from .ports import CohortFact, ConversionFact, RecognitionFact


@dataclass(frozen=True)
class CommissionSplit:
    basis: Decimal
    base_pool_rate: Decimal
    base_pool: Decimal
    marketing_share: Decimal
    marketing_age_coefficient: Decimal
    sales_share: Decimal
    marketing_award: Decimal
    sales_award: Decimal
    company_retained: Decimal
    assisted: bool
    customer_year: int
    trace_codes: tuple[str, ...]
    unapproved_assumptions: tuple[str, ...]


def age_coefficient(customer_year: int, continuous_auto_renew: bool, policy) -> tuple[Decimal, tuple[str, ...]]:
    if customer_year <= 0:
        raise ValidationError("customer_year must be at least 1", field_name="customer_year")
    if customer_year == 1:
        return policy.marketing_year_1, ("age:year1",)
    if customer_year == 2:
        return policy.marketing_year_2, ("age:year2",)
    # year >= 3
    if policy.year3_assisted_marketing_rule == "approved_custom_rule":
        # No steward-approved custom table exists yet; simulation may still run
        # with the deterministic fallback, flagged as an unapproved assumption.
        if continuous_auto_renew:
            return policy.marketing_year_3_auto, ("age:year3_custom_fallback_auto",)
        return Decimal("0"), ("age:year3_custom_fallback_manual",)
    if continuous_auto_renew:
        return policy.marketing_year_3_auto, ("age:year3_continuous_auto",)
    return Decimal("0"), ("age:year3_manual_restart_zero",)


def compute_split(
    fact: RecognitionFact,
    cohort: CohortFact,
    conversion: ConversionFact | None,
    policy,
) -> CommissionSplit:
    basis = fact.eligible_untaxed_amount
    unapproved: list[str] = []
    if policy.base_pool_rate is None:
        # Simulation must still be possible with an unapproved/null rate
        # (REQ-M12-001); live is independently blocked by
        # ``CommissionPolicy.live_eligible()`` before this function runs.
        unapproved.append("base_pool_rate:unresolved_simulated_as_zero")
        pool_rate = Decimal("0")
    else:
        pool_rate = policy.base_pool_rate
    base_pool = basis * pool_rate
    coefficient, age_trace = age_coefficient(cohort.customer_year, cohort.continuous_auto_renew, policy)
    if policy.year3_assisted_marketing_rule == "approved_custom_rule" and cohort.customer_year >= 3:
        unapproved.append("year3_assisted_marketing_rule:approved_custom_rule_not_configured")
    if policy.age_anchor == "unresolved":
        unapproved.append("age_anchor:unresolved")

    assisted = bool(conversion and conversion.approved)
    trace = list(age_trace)
    if not assisted:
        marketing_award = apply_rounding(base_pool * coefficient, policy.rounding_mode, policy.rounding_scale)
        sales_award = Decimal("0")
        marketing_share = coefficient
        sales_share = Decimal("0")
        trace.append("split:organic_no_sales_share")
    else:
        trace.append("split:assisted_30_70")
        marketing_pool = base_pool * policy.assisted_marketing_share
        sales_pool = base_pool * policy.assisted_sales_share
        if policy.allocation_interpretation == "unresolved":
            unapproved.append("allocation_interpretation:unresolved")
        if policy.allocation_interpretation == "whole_pool_decay":
            marketing_award = apply_rounding(marketing_pool * coefficient, policy.rounding_mode, policy.rounding_scale)
            sales_award = apply_rounding(sales_pool * coefficient, policy.rounding_mode, policy.rounding_scale)
            trace.append("decay:whole_pool_decay")
        else:
            # marketing_decay_only (and the unresolved fallback used only for
            # simulation): sales keeps full assisted credit for the active
            # intervention; only the passive marketing slice decays by cohort age.
            marketing_award = apply_rounding(marketing_pool * coefficient, policy.rounding_mode, policy.rounding_scale)
            sales_award = apply_rounding(sales_pool, policy.rounding_mode, policy.rounding_scale)
            trace.append("decay:marketing_decay_only")
        marketing_share = policy.assisted_marketing_share
        sales_share = policy.assisted_sales_share

    rounded_base_pool = apply_rounding(base_pool, policy.rounding_mode, policy.rounding_scale)
    company_retained = rounded_base_pool - marketing_award - sales_award
    if company_retained < 0:
        # Rounding never produces a negative remainder for any supported scale,
        # but guard explicitly rather than silently clipping a real shortfall.
        raise ValidationError(
            "rounded marketing and sales shares exceed the base pool", code="commission_conservation_violated"
        )
    return CommissionSplit(
        basis=basis,
        base_pool_rate=pool_rate,
        base_pool=rounded_base_pool,
        marketing_share=marketing_share,
        marketing_age_coefficient=coefficient,
        sales_share=sales_share,
        marketing_award=marketing_award,
        sales_award=sales_award,
        company_retained=company_retained,
        assisted=assisted,
        customer_year=cohort.customer_year,
        trace_codes=tuple(trace),
        unapproved_assumptions=tuple(unapproved),
    )


def line_snapshot(fact: RecognitionFact, cohort: CohortFact, conversion: ConversionFact | None, policy, split: CommissionSplit) -> dict[str, Any]:
    return {
        "recognition_id": fact.recognition_id,
        "contract_id": fact.contract_id,
        "customer_id": fact.customer_id,
        "basis": money(split.basis, fact.currency),
        "base_pool_rate": decimal_str(split.base_pool_rate),
        "base_pool": money(split.base_pool, fact.currency),
        "marketing_share": decimal_str(split.marketing_share),
        "marketing_age_coefficient": decimal_str(split.marketing_age_coefficient),
        "sales_share": decimal_str(split.sales_share),
        "marketing_award": money(split.marketing_award, fact.currency),
        "sales_award": money(split.sales_award, fact.currency),
        "company_retained": money(split.company_retained, fact.currency),
        "conversion_id": conversion.conversion_id if conversion else None,
        "marketing_attribution_id": cohort.marketing_attribution_id,
        "salesperson_id": conversion.salesperson_id if conversion else None,
        "policy_id": policy.id,
        "rule_trace_codes": list(split.trace_codes),
    }
