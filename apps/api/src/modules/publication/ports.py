from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import DependencyUnavailableError, NotFoundError, canonical_hash, new_id


@dataclass(frozen=True)
class StoredAsset:
    asset_id: str
    sha256: str


class ContentAssetPort(Protocol):
    """Stores the reviewed channel text for a recommendation version.

    Content is written once at creation time and never mutated afterwards;
    the returned ``sha256`` is the immutable hash later cross-checked against
    the signed bundle.
    """

    is_fake: bool

    def store(self, tenant_id: str, content: str) -> StoredAsset: ...


class FakeContentAssetStore:
    is_fake = True

    def __init__(self) -> None:
        self._assets: dict[str, str] = {}

    def store(self, tenant_id: str, content: str) -> StoredAsset:
        asset_id = new_id()
        digest = canonical_hash({"tenant_id": tenant_id, "content": content})
        self._assets[asset_id] = digest
        return StoredAsset(asset_id, digest)


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


@dataclass(frozen=True)
class SignedBundle:
    consistent: bool
    bundle_manifest_sha256: str
    report_content_sha256: str


class ReportBundlePort(Protocol):
    """Cross-checks that the bundle actually signed by M07 still matches the

    recommendation channel content M08 recorded at review time. This is the
    control that blocks a post-signature content swap (REQ-M08-005).
    """

    is_fake: bool

    def verify(
        self,
        tenant_id: str,
        report_version_id: str,
        recommendation_version_id: str,
        expected_content_sha256: str,
    ) -> SignedBundle: ...


class FakeReportBundleRegistry:
    """Local M07 adapter stand-in that accepts, but never derives, signature output.

    The manifest digest is authority data emitted by M07 after verification.
    Recomputing a digest from a subset of report fields would allow this fake to
    disagree with the signed package while still making a publication test pass.
    """

    is_fake = True

    def __init__(self) -> None:
        self._manifests: dict[tuple[str, str], dict[str, str]] = {}
        self._covered_content: dict[tuple[str, str, str], str] = {}
        self._failures: dict[tuple[str, str, str], str] = {}

    def register(
        self,
        tenant_id: str,
        report_version_id: str,
        recommendation_version_id: str,
        *,
        content_sha256: str,
        report_content_sha256: str,
        bundle_manifest_sha256: str,
    ) -> str:
        if not isinstance(bundle_manifest_sha256, str) or len(bundle_manifest_sha256) != 64:
            raise ValueError("bundle_manifest_sha256 must be supplied from an M07 signature result")
        manifest_key = (tenant_id, report_version_id)
        manifest = self._manifests.get(manifest_key)
        if manifest is None:
            manifest = {
                "report_content_sha256": report_content_sha256,
                "bundle_manifest_sha256": bundle_manifest_sha256,
            }
            self._manifests[manifest_key] = manifest
        elif (
            manifest["report_content_sha256"] != report_content_sha256
            or manifest["bundle_manifest_sha256"] != bundle_manifest_sha256
        ):
            raise ValueError("a report version must retain the one M07-signed manifest digest")
        self._covered_content[(tenant_id, report_version_id, recommendation_version_id)] = content_sha256
        return manifest["bundle_manifest_sha256"]

    def fail(self, tenant_id: str, report_version_id: str, recommendation_version_id: str, mode: str) -> None:
        self._failures[(tenant_id, report_version_id, recommendation_version_id)] = mode

    def verify(
        self,
        tenant_id: str,
        report_version_id: str,
        recommendation_version_id: str,
        expected_content_sha256: str,
    ) -> SignedBundle:
        key = (tenant_id, report_version_id, recommendation_version_id)
        failure = self._failures.get(key)
        if failure == "timeout":
            raise DependencyUnavailableError("report bundle registry timed out", code="report_bundle_timeout")
        manifest = self._manifests.get((tenant_id, report_version_id))
        covered_content = self._covered_content.get(key)
        if manifest is None or covered_content is None:
            raise NotFoundError("signed bundle was not registered for this recommendation version")
        return SignedBundle(
            consistent=covered_content == expected_content_sha256,
            bundle_manifest_sha256=manifest["bundle_manifest_sha256"],
            report_content_sha256=manifest["report_content_sha256"],
        )


@dataclass(frozen=True)
class AudienceCandidate:
    customer_id: str
    entitlement_id: str
    contract_id: str
    entitlement_version: int
    eligible: bool
    reason_code: str


class EntitlementAudiencePort(Protocol):
    """Resolves the candidate audience for an instrument/strategy scope.

    A timeout must leave the publish attempt retryable; it must never widen
    to "send to everyone" (REQ-M08-006/007).
    """

    is_fake: bool

    def resolve(self, tenant_id: str, instrument_ref: dict, as_of: datetime) -> tuple[AudienceCandidate, ...]: ...


class FakeEntitlementAudienceRegistry:
    is_fake = True

    def __init__(self) -> None:
        self._candidates: dict[str, tuple[AudienceCandidate, ...]] = {}
        self.failure_mode: str | None = None

    @staticmethod
    def _key(tenant_id: str, instrument_ref: dict) -> str:
        return canonical_hash({"tenant_id": tenant_id, "instrument_ref": instrument_ref})

    def set_candidates(self, tenant_id: str, instrument_ref: dict, candidates: tuple[AudienceCandidate, ...]) -> None:
        self._candidates[self._key(tenant_id, instrument_ref)] = candidates

    def resolve(self, tenant_id: str, instrument_ref: dict, as_of: datetime) -> tuple[AudienceCandidate, ...]:
        if self.failure_mode == "timeout":
            raise DependencyUnavailableError("entitlement resolution timed out", code="entitlement_resolution_timeout")
        return self._candidates.get(self._key(tenant_id, instrument_ref), ())
