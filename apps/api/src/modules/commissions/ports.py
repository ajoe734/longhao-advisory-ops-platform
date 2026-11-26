from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .domain import DependencyUnavailableError


@dataclass(frozen=True)
class RecognitionFact:
    """M14 finance-confirmed basis: already net of attributable refund allocations."""

    recognition_id: str
    contract_id: str
    customer_id: str
    occurred_at: datetime
    eligible_untaxed_amount: Decimal
    currency: str


@dataclass(frozen=True)
class RefundAllocationFact:
    """M14 refund allocation: a single webhook may be replayed; dedupe is by ``refund_id``."""

    refund_id: str
    original_entry_id: str
    recognition_id: str
    amount: Decimal
    currency: str
    deferred_portion: Decimal = Decimal("0")


class RevenueBasisPort(Protocol):
    is_fake: bool

    def eligible_recognitions(
        self, tenant_id: str, *, period_start, period_end, revenue_cutoff_at: datetime, recognition_ids: tuple[str, ...]
    ) -> list[RecognitionFact]: ...

    def refund_allocation(self, tenant_id: str, refund_id: str) -> RefundAllocationFact | None: ...


class FakeRevenueBasis:
    is_fake = True

    def __init__(self) -> None:
        self._recognitions: dict[tuple[str, str], RecognitionFact] = {}
        self._refunds: dict[tuple[str, str], RefundAllocationFact] = {}
        self.failure_mode: str | None = None

    def add_recognition(self, tenant_id: str, fact: RecognitionFact) -> None:
        self._recognitions[(tenant_id, fact.recognition_id)] = fact

    def add_refund(self, tenant_id: str, fact: RefundAllocationFact) -> None:
        self._refunds[(tenant_id, fact.refund_id)] = fact

    def eligible_recognitions(
        self, tenant_id: str, *, period_start, period_end, revenue_cutoff_at: datetime, recognition_ids: tuple[str, ...]
    ) -> list[RecognitionFact]:
        if self.failure_mode == "timeout":
            raise DependencyUnavailableError("finance basis provider timed out", code="finance_basis_timeout")
        rows = [fact for (tenant, _), fact in self._recognitions.items() if tenant == tenant_id]
        if recognition_ids:
            wanted = set(recognition_ids)
            rows = [row for row in rows if row.recognition_id in wanted]
        rows = [row for row in rows if row.occurred_at.date() >= period_start and row.occurred_at.date() < period_end]
        rows = [row for row in rows if row.occurred_at <= revenue_cutoff_at]
        return sorted(rows, key=lambda row: row.recognition_id)

    def refund_allocation(self, tenant_id: str, refund_id: str) -> RefundAllocationFact | None:
        if self.failure_mode == "timeout":
            raise DependencyUnavailableError("finance basis provider timed out", code="finance_basis_timeout")
        return self._refunds.get((tenant_id, refund_id))


@dataclass(frozen=True)
class ConversionFact:
    """M10 approved sales-assisted renewal evidence."""

    conversion_id: str
    salesperson_id: str
    approved: bool


class SalesConversionPort(Protocol):
    is_fake: bool

    def assisted_conversion(self, tenant_id: str, recognition_id: str) -> ConversionFact | None: ...


class FakeSalesConversion:
    is_fake = True

    def __init__(self) -> None:
        self._conversions: dict[tuple[str, str], ConversionFact] = {}

    def set(self, tenant_id: str, recognition_id: str, fact: ConversionFact | None) -> None:
        self._conversions[(tenant_id, recognition_id)] = fact

    def assisted_conversion(self, tenant_id: str, recognition_id: str) -> ConversionFact | None:
        return self._conversions.get((tenant_id, recognition_id))


@dataclass(frozen=True)
class CohortFact:
    """M11 ownership history + M01/M10 original marketing attribution, by customer."""

    customer_id: str
    customer_year: int
    continuous_auto_renew: bool
    current_owner_id: str | None
    marketing_attribution_id: str | None


class OwnershipCohortPort(Protocol):
    is_fake: bool

    def cohort(self, tenant_id: str, customer_id: str, as_of: datetime) -> CohortFact: ...


class FakeOwnershipCohort:
    is_fake = True

    def __init__(self) -> None:
        self._cohorts: dict[tuple[str, str], CohortFact] = {}

    def set(self, tenant_id: str, fact: CohortFact) -> None:
        self._cohorts[(tenant_id, fact.customer_id)] = fact

    def cohort(self, tenant_id: str, customer_id: str, as_of: datetime) -> CohortFact:
        fact = self._cohorts.get((tenant_id, customer_id))
        if fact is None:
            raise DependencyUnavailableError("ownership cohort snapshot is unavailable", code="cohort_snapshot_unavailable")
        return fact


class EvidencePort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...


class FakeEvidenceStore:
    is_fake = True

    def __init__(self) -> None:
        self._assets: set[tuple[str, str]] = set()

    def add(self, tenant_id: str, asset_id: str) -> None:
        self._assets.add((tenant_id, asset_id))

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._assets
