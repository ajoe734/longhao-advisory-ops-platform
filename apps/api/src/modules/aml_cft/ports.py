from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .domain import DependencyUnavailableError


@dataclass(frozen=True)
class ScreeningAcceptance:
    provider: str
    provider_ref: str
    provider_request_id: str


class ScreeningProviderPort(Protocol):
    is_fake: bool

    def start(self, tenant_id: str, request_sha256: str, scope: list[str]) -> ScreeningAcceptance: ...


class EvidencePort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...


class ApplicationAccessPort(Protocol):
    is_fake: bool

    def customer_for(self, tenant_id: str, application_id: str) -> str | None: ...


class FakeScreeningProvider:
    """Synthetic list-screening adapter. No real sanctions/PEP list is queried."""

    is_fake = True

    def __init__(self, provider_name: str = "synthetic-watchlist") -> None:
        self.provider_name = provider_name
        self._next = 0
        self.failure_mode: str | None = None

    def start(self, tenant_id: str, request_sha256: str, scope: list[str]) -> ScreeningAcceptance:
        if self.failure_mode == "timeout":
            raise DependencyUnavailableError(
                "screening provider timed out", code="screening_provider_timeout"
            )
        self._next += 1
        provider_request_id = f"{tenant_id[:8]}-{request_sha256[:12]}-{self._next}"
        return ScreeningAcceptance(
            provider=self.provider_name,
            provider_ref=f"{self.provider_name}:{'+'.join(sorted(scope))}",
            provider_request_id=provider_request_id,
        )


class FakeEvidenceStore:
    is_fake = True

    def __init__(self) -> None:
        self._assets: set[tuple[str, str]] = set()

    def add(self, tenant_id: str, asset_id: str) -> None:
        self._assets.add((tenant_id, asset_id))

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._assets


class FakeApplicationAccess:
    is_fake = True

    def __init__(self) -> None:
        self._owners: dict[tuple[str, str], str] = {}

    def register(self, tenant_id: str, application_id: str, customer_id: str) -> None:
        self._owners[(tenant_id, application_id)] = customer_id

    def customer_for(self, tenant_id: str, application_id: str) -> str | None:
        return self._owners.get((tenant_id, application_id))
