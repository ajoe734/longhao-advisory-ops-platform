from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .domain import DependencyUnavailableError, ForbiddenError, new_id, utc_now


@dataclass(frozen=True)
class SignatureSessionHandle:
    provider: str
    provider_transaction_id: str
    redirect_url: str
    expires_at: datetime


class SignatureProviderPort(Protocol):
    is_fake: bool

    def create_session(self, *, report_version_id: str, bundle_sha256: str, signer_subject_ref: str) -> SignatureSessionHandle: ...

    def lookup(self, provider_transaction_id: str) -> str: ...


class FakeSignatureProvider:
    """Synthetic signing-provider adapter. Never a real credential or bytes."""

    is_fake = True

    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self.failure_mode: str | None = None

    def create_session(self, *, report_version_id: str, bundle_sha256: str, signer_subject_ref: str) -> SignatureSessionHandle:
        if self.failure_mode == "timeout":
            raise DependencyUnavailableError("signature provider timed out", code="signature_provider_timeout")
        transaction_id = new_id()
        self._sessions[transaction_id] = "pending"
        return SignatureSessionHandle(
            provider="synthetic-signature-provider",
            provider_transaction_id=transaction_id,
            redirect_url=f"https://signature-provider.invalid/sessions/{transaction_id}",
            expires_at=utc_now() + timedelta(hours=1),
        )

    def lookup(self, provider_transaction_id: str) -> str:
        return self._sessions.get(provider_transaction_id, "not_found")


@dataclass(frozen=True)
class QualifiedCredential:
    credential_id: str
    signer_id: str
    scope: str
    expires_at: datetime
    subject_ref: str


class SignerQualificationPort(Protocol):
    is_fake: bool

    def verify(self, tenant_id: str, signer_id: str, credential_id: str) -> QualifiedCredential: ...


class FakeQualificationRegistry:
    """Synthetic analyst-signer credential directory; no real MFA material."""

    is_fake = True

    def __init__(self) -> None:
        self._credentials: dict[tuple[str, str, str], QualifiedCredential] = {}

    def register(self, tenant_id: str, signer_id: str, credential_id: str, credential: QualifiedCredential) -> None:
        self._credentials[(tenant_id, signer_id, credential_id)] = credential

    def verify(self, tenant_id: str, signer_id: str, credential_id: str) -> QualifiedCredential:
        credential = self._credentials.get((tenant_id, signer_id, credential_id))
        if credential is None:
            raise ForbiddenError("signer credential is unknown or does not match", code="qualification_invalid")
        if credential.expires_at <= utc_now() or credential.scope != "analyst_report":
            raise ForbiddenError("signer credential is expired or out of scope", code="qualification_invalid")
        return credential


class ObjectAssetPort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...

    def sha256_of(self, tenant_id: str, asset_id: str) -> str | None: ...

    def write(self, tenant_id: str, payload: dict) -> tuple[str, str]: ...


class FakeObjectAssetStore:
    """Synthetic CAS stand-in for the M14 immutable-object module."""

    is_fake = True

    def __init__(self) -> None:
        self._assets: dict[tuple[str, str], str | None] = {}

    def add(self, tenant_id: str, asset_id: str, sha256: str | None = None) -> None:
        self._assets[(tenant_id, asset_id)] = sha256

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._assets

    def sha256_of(self, tenant_id: str, asset_id: str) -> str | None:
        return self._assets.get((tenant_id, asset_id))

    def write(self, tenant_id: str, payload: dict) -> tuple[str, str]:
        from .domain import canonical_hash

        asset_id = new_id()
        digest = canonical_hash(payload)
        self._assets[(tenant_id, asset_id)] = digest
        return asset_id, digest


class ReviewCaseLookupPort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, review_case_id: str) -> bool: ...


class FakeReviewCaseDirectory:
    """Synthetic stand-in for M06 review case lookups."""

    is_fake = True

    def __init__(self) -> None:
        self._cases: set[tuple[str, str]] = set()

    def add(self, tenant_id: str, review_case_id: str) -> None:
        self._cases.add((tenant_id, review_case_id))

    def exists(self, tenant_id: str, review_case_id: str) -> bool:
        return (tenant_id, review_case_id) in self._cases


class CandidateLineagePort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, candidate_id: str) -> bool: ...


class FakeCandidateLineage:
    """Synthetic stand-in for M05 research-candidate lookups."""

    is_fake = True

    def __init__(self) -> None:
        self._candidates: set[tuple[str, str]] = set()

    def add(self, tenant_id: str, candidate_id: str) -> None:
        self._candidates.add((tenant_id, candidate_id))

    def exists(self, tenant_id: str, candidate_id: str) -> bool:
        return (tenant_id, candidate_id) in self._candidates
