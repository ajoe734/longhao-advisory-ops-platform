from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .domain import new_id


class ObjectAssetPort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...

    def store(self, tenant_id: str, content: str) -> str: ...


class KnowledgeBasePort(Protocol):
    is_fake: bool

    def approved(self, tenant_id: str, knowledge_version: str, source_ref: str) -> bool: ...


class ChannelBindingPort(Protocol):
    is_fake: bool

    def verified(self, tenant_id: str, customer_id: str, channel_binding_id: str, channel: str) -> bool: ...


class SuppressionRequestPort(Protocol):
    is_fake: bool

    def request(self, tenant_id: str, customer_id: str, scope: str, reason_code: str) -> str: ...


class FakeObjectAssetStore:
    """Synthetic object asset registry. No production evidence is accepted."""

    is_fake = True

    def __init__(self) -> None:
        self._assets: dict[tuple[str, str], str] = {}

    def add(self, tenant_id: str, asset_id: str, content: str = "") -> None:
        self._assets[(tenant_id, asset_id)] = content

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._assets

    def store(self, tenant_id: str, content: str) -> str:
        asset_id = new_id()
        self._assets[(tenant_id, asset_id)] = content
        return asset_id

    def read(self, tenant_id: str, asset_id: str) -> str | None:
        return self._assets.get((tenant_id, asset_id))


class FakeKnowledgeBase:
    is_fake = True

    def __init__(self) -> None:
        self._approved: set[tuple[str, str, str]] = set()

    def approve(self, tenant_id: str, knowledge_version: str, source_ref: str) -> None:
        self._approved.add((tenant_id, knowledge_version, source_ref))

    def approved(self, tenant_id: str, knowledge_version: str, source_ref: str) -> bool:
        return (tenant_id, knowledge_version, source_ref) in self._approved


class FakeChannelBindings:
    is_fake = True

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], tuple[str, str]] = {}

    def add(self, tenant_id: str, binding_id: str, customer_id: str, channel: str) -> None:
        self._bindings[(tenant_id, binding_id)] = (customer_id, channel)

    def verified(self, tenant_id: str, customer_id: str, channel_binding_id: str, channel: str) -> bool:
        return self._bindings.get((tenant_id, channel_binding_id)) == (customer_id, channel)


@dataclass
class SuppressionRequestRecord:
    tenant_id: str
    customer_id: str
    scope: str
    reason_code: str
    suppression_id: str


class FakeSuppressionRequests:
    """Local stand-in for the M10 suppression intake; marked NOT_CONNECTED upstream."""

    is_fake = True

    def __init__(self) -> None:
        self.requests: list[SuppressionRequestRecord] = []

    def request(self, tenant_id: str, customer_id: str, scope: str, reason_code: str) -> str:
        suppression_id = new_id()
        self.requests.append(SuppressionRequestRecord(tenant_id, customer_id, scope, reason_code, suppression_id))
        return suppression_id
