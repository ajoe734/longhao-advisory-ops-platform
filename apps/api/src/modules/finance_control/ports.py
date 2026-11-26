from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .domain import DependencyUnavailableError


@dataclass(frozen=True)
class RefundProviderResult:
    status: str  # confirmed | failed | unknown
    provider_refund_ref: str | None
    occurred_at: datetime | None


class RefundProviderPort(Protocol):
    is_fake: bool

    def submit(self, idempotency_key: str, amount: Decimal, currency: str, reference: str) -> RefundProviderResult: ...

    def query(self, idempotency_key: str) -> RefundProviderResult | None: ...


class FakeRefundProvider:
    """Deterministic refund provider stand-in; outcomes are injected by tests.

    ``not_submitted`` is modeled as ``query`` returning ``None``. ``submit`` is
    rejected a second time for the same idempotency key so execute_refund must
    query first instead of re-dispatching money.
    """

    is_fake = True

    def __init__(self) -> None:
        self._outcomes: dict[str, RefundProviderResult | str] = {}
        self._submitted: set[str] = set()
        self.submit_calls: list[str] = []

    def register(self, idempotency_key: str, outcome: RefundProviderResult | str) -> None:
        self._outcomes[idempotency_key] = outcome

    def submit(self, idempotency_key: str, amount: Decimal, currency: str, reference: str) -> RefundProviderResult:
        self.submit_calls.append(idempotency_key)
        if idempotency_key in self._submitted:
            raise DependencyUnavailableError(
                "refund provider idempotency key was already submitted; query before resubmitting",
                code="refund_provider_duplicate_submit",
            )
        self._submitted.add(idempotency_key)
        outcome = self._outcomes.get(idempotency_key)
        if outcome == "timeout":
            return RefundProviderResult("unknown", None, None)
        if outcome is None:
            raise DependencyUnavailableError("refund provider timed out", code="refund_provider_timeout")
        assert isinstance(outcome, RefundProviderResult)
        return outcome

    def query(self, idempotency_key: str) -> RefundProviderResult | None:
        if idempotency_key not in self._submitted:
            return None
        outcome = self._outcomes.get(idempotency_key)
        if outcome in (None, "timeout"):
            return RefundProviderResult("unknown", None, None)
        assert isinstance(outcome, RefundProviderResult)
        return outcome


@dataclass(frozen=True)
class SettlementLine:
    provider_transaction_ref: str
    gross_amount: Decimal
    fee_amount: Decimal
    net_amount: Decimal
    currency: str
    bank_line_ref: str


class SettlementStatementPort(Protocol):
    is_fake: bool

    def parse(self, tenant_id: str, asset_id: str, sha256: str) -> list[SettlementLine]: ...


class FakeSettlementStatements:
    is_fake = True

    def __init__(self) -> None:
        self._statements: dict[tuple[str, str, str], list[SettlementLine]] = {}

    def register(self, tenant_id: str, asset_id: str, sha256: str, lines: list[SettlementLine]) -> None:
        self._statements[(tenant_id, asset_id, sha256)] = lines

    def parse(self, tenant_id: str, asset_id: str, sha256: str) -> list[SettlementLine]:
        key = (tenant_id, asset_id, sha256)
        if key not in self._statements:
            raise DependencyUnavailableError("settlement statement asset is not available", code="statement_not_found")
        return list(self._statements[key])


class EvidenceAssetPort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...


class FakeEvidenceAssets:
    is_fake = True

    def __init__(self) -> None:
        self._assets: set[tuple[str, str]] = set()

    def add(self, tenant_id: str, asset_id: str) -> None:
        self._assets.add((tenant_id, asset_id))

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._assets


class TaxMappingPort(Protocol):
    is_fake: bool

    def approved(self, tenant_id: str, tax_code: str, tax_mapping_version: str) -> bool: ...


class FakeTaxMappings:
    is_fake = True

    def __init__(self) -> None:
        self._approved: set[tuple[str, str, str]] = set()

    def approve(self, tenant_id: str, tax_code: str, tax_mapping_version: str) -> None:
        self._approved.add((tenant_id, tax_code, tax_mapping_version))

    def approved(self, tenant_id: str, tax_code: str, tax_mapping_version: str) -> bool:
        return (tenant_id, tax_code, tax_mapping_version) in self._approved
