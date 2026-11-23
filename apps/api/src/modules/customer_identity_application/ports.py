from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import DependencyUnavailableError, UnauthorizedError


@dataclass(frozen=True)
class VerifiedIdentity:
    provider: str
    subject: str
    issuer: str
    audience: str
    verified_at: datetime
    expires_at: datetime
    assurance_level: str
    display_name: str
    email: str | None = None
    phone: str | None = None
    nonce_validated: bool = True


class IdentityProviderPort(Protocol):
    is_fake: bool

    def verify(self, proof_ref: str) -> VerifiedIdentity: ...


class EvidencePort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...

    def customer_authored(self, tenant_id: str, asset_id: str, customer_id: str) -> bool: ...

    def merge_allowed(self, tenant_id: str, customer_ids: tuple[str, str]) -> bool: ...


@dataclass(frozen=True)
class SourceSnapshot:
    source: str
    status: str
    last_success_at: datetime | None
    items: list[dict] | None
    error_code: str | None = None


class CustomerSourcePort(Protocol):
    is_fake: bool

    def snapshots(self, tenant_id: str, customer_id: str) -> list[SourceSnapshot]: ...


class FakeIdentityProvider:
    """Synthetic proof lookup. No token or real customer data is accepted."""

    is_fake = True

    def __init__(self, expected_issuers: set[str], expected_audience: str) -> None:
        self.expected_issuers = expected_issuers
        self.expected_audience = expected_audience
        self._proofs: dict[str, VerifiedIdentity | str] = {}

    def register(self, proof_ref: str, identity: VerifiedIdentity) -> None:
        self._proofs[proof_ref] = identity

    def fail(self, proof_ref: str, mode: str) -> None:
        if mode not in {"timeout", "forged"}:
            raise ValueError("unsupported synthetic failure mode")
        self._proofs[proof_ref] = mode

    def verify(self, proof_ref: str) -> VerifiedIdentity:
        value = self._proofs.get(proof_ref)
        if value == "timeout":
            raise DependencyUnavailableError("identity provider timed out", code="identity_provider_timeout")
        if value == "forged" or value is None:
            raise UnauthorizedError("identity proof is invalid", code="identity_proof_invalid")
        assert isinstance(value, VerifiedIdentity)
        if (
            value.issuer not in self.expected_issuers
            or value.audience != self.expected_audience
            or not value.nonce_validated
        ):
            raise UnauthorizedError("identity proof issuer, audience or nonce is invalid", code="identity_proof_invalid")
        return value


class FakeEvidenceStore:
    is_fake = True

    def __init__(self) -> None:
        self._assets: set[tuple[str, str]] = set()
        self._customer_authors: dict[tuple[str, str], str] = {}
        self.blocked_merges: set[tuple[str, str, str]] = set()

    def add(self, tenant_id: str, asset_id: str, customer_id: str | None = None) -> None:
        self._assets.add((tenant_id, asset_id))
        if customer_id:
            self._customer_authors[(tenant_id, asset_id)] = customer_id

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._assets

    def customer_authored(self, tenant_id: str, asset_id: str, customer_id: str) -> bool:
        return self._customer_authors.get((tenant_id, asset_id)) == customer_id

    def merge_allowed(self, tenant_id: str, customer_ids: tuple[str, str]) -> bool:
        return (tenant_id, *customer_ids) not in self.blocked_merges


class FakeCustomerSources:
    is_fake = True

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], list[SourceSnapshot]] = {}

    def set(self, tenant_id: str, customer_id: str, snapshots: list[SourceSnapshot]) -> None:
        self._snapshots[(tenant_id, customer_id)] = snapshots

    def snapshots(self, tenant_id: str, customer_id: str) -> list[SourceSnapshot]:
        return list(self._snapshots.get((tenant_id, customer_id), []))
