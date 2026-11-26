"""Read-only ports into dependencies M11 does not own.

Per docs/modules/M11-ownership_pool.md §13 these are integration dependencies, not
開工前置: M11 develops against fakes now and only swaps the adapter when the real
provider merges. Paid-service windows ("M02 all contract_service states") are not a
port here — M11 is a registered consumer of ``contract.effective`` /
``contract.terminated`` / ``contract.service_ended`` (events/registry.json) and
keeps its own local projection fed by those events; see repository.py.
"""
from __future__ import annotations

from typing import Protocol


class EvidencePort(Protocol):
    """Read-only check against ``core.object_asset`` (owned outside M11).

    M11 never stores raw HR correspondence or handoff recordings; it only ever
    stores the asset id, and always verifies the asset exists before citing it as
    evidence for a departure or release.
    """

    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...


class FakeEvidencePort:
    is_fake = True

    def __init__(self) -> None:
        self._known: set[tuple[str, str]] = set()

    def register(self, tenant_id: str, asset_id: str) -> None:
        self._known.add((tenant_id, asset_id))

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._known


class RestrictionPort(Protocol):
    """M04's safe compliance-restriction summary ("M13/04 safe restriction flags").

    Returns only a boolean hold flag — never a case id, hit detail or AML reason —
    so a sales-facing pool worklist can never leak why a customer is restricted
    (docs/modules/M11-ownership_pool.md REQ-M11-009).
    """

    is_fake: bool

    def is_restricted(self, tenant_id: str, customer_id: str) -> bool: ...


class FakeRestrictionPort:
    is_fake = True

    def __init__(self) -> None:
        self._restricted: set[tuple[str, str]] = set()

    def restrict(self, tenant_id: str, customer_id: str) -> None:
        self._restricted.add((tenant_id, customer_id))

    def clear(self, tenant_id: str, customer_id: str) -> None:
        self._restricted.discard((tenant_id, customer_id))

    def is_restricted(self, tenant_id: str, customer_id: str) -> bool:
        return (tenant_id, customer_id) in self._restricted
