from __future__ import annotations

from typing import Protocol


class EvidencePort(Protocol):
    """Read-only check against ``core.object_asset`` (owned outside M10).

    M10 never stores raw call recordings, transcripts or complaint text; it
    only ever stores the asset id and a hash, and always verifies the asset
    actually exists before citing it as evidence.
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
