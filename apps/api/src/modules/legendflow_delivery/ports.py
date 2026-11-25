from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import DependencyUnavailableError, UnauthorizedError


# ---- M08 publication content (read-only; M08 is the system of record) -----

@dataclass(frozen=True)
class PublicationSnapshot:
    publication_id: str
    report_version_id: str
    signature_id: str
    recommendation_series_id: str
    recommendation_version_id: str
    action: str
    approved_text: str
    body_sha256: str
    report_version_ids: tuple[str, ...]
    published_at: datetime
    valid_until: datetime


class PublicationPort(Protocol):
    is_fake: bool

    def get(self, tenant_id: str, publication_id: str) -> PublicationSnapshot: ...


class FakePublicationPort:
    is_fake = True

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], PublicationSnapshot] = {}

    def register(self, tenant_id: str, snapshot: PublicationSnapshot) -> None:
        self._snapshots[(tenant_id, snapshot.publication_id)] = snapshot

    def get(self, tenant_id: str, publication_id: str) -> PublicationSnapshot:
        snapshot = self._snapshots.get((tenant_id, publication_id))
        if snapshot is None:
            raise DependencyUnavailableError("publication snapshot is not available", code="publication_lookup_failed")
        return snapshot


# ---- M08 publication.recipient (read-only evidence of eligibility) --------

@dataclass(frozen=True)
class RecipientSnapshot:
    recipient_id: str
    customer_id: str
    entitlement_id: str
    entitlement_version: int
    eligibility_reason: str


class PublicationRecipientPort(Protocol):
    is_fake: bool

    def recipients(self, tenant_id: str, publication_id: str) -> list[RecipientSnapshot]: ...


class FakePublicationRecipientPort:
    is_fake = True

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], list[RecipientSnapshot]] = {}

    def set(self, tenant_id: str, publication_id: str, rows: list[RecipientSnapshot]) -> None:
        self._rows[(tenant_id, publication_id)] = rows

    def recipients(self, tenant_id: str, publication_id: str) -> list[RecipientSnapshot]:
        return list(self._rows.get((tenant_id, publication_id), []))


# ---- M02 contract.entitlement (live authority; never cached for gating) ---

@dataclass(frozen=True)
class EntitlementStatus:
    entitlement_id: str
    customer_id: str
    state: str
    decision_id: str
    valid_until: datetime


class EntitlementPort(Protocol):
    is_fake: bool

    def current(self, tenant_id: str, entitlement_id: str) -> EntitlementStatus: ...


class FakeEntitlementPort:
    is_fake = True

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], EntitlementStatus] = {}

    def set(self, tenant_id: str, status: EntitlementStatus) -> None:
        self._rows[(tenant_id, status.entitlement_id)] = status

    def current(self, tenant_id: str, entitlement_id: str) -> EntitlementStatus:
        status = self._rows.get((tenant_id, entitlement_id))
        if status is None:
            raise DependencyUnavailableError("entitlement authority is not available", code="entitlement_lookup_failed")
        return status


# ---- M07 report signing / revocation (read-only signed evidence) ---------

@dataclass(frozen=True)
class ReportAccessGrant:
    download_url: str
    content_sha256: str


class ReportAccessPort(Protocol):
    is_fake: bool

    def sign(self, tenant_id: str, report_version_id: str, customer_id: str) -> ReportAccessGrant: ...


class FakeReportAccessPort:
    is_fake = True

    def __init__(self) -> None:
        self._content: dict[tuple[str, str], str] = {}

    def register(self, tenant_id: str, report_version_id: str, content_sha256: str) -> None:
        self._content[(tenant_id, report_version_id)] = content_sha256

    def sign(self, tenant_id: str, report_version_id: str, customer_id: str) -> ReportAccessGrant:
        content_sha256 = self._content.get((tenant_id, report_version_id))
        if content_sha256 is None:
            raise DependencyUnavailableError("report store is not available", code="report_store_unavailable")
        token = f"synthetic-{report_version_id}-{customer_id}"
        return ReportAccessGrant(f"https://reports.example.invalid/proxy/{token}", content_sha256)


# ---- member/LINE/SMS/email channel proof and send providers --------------

@dataclass(frozen=True)
class VerifiedChannelProof:
    masked_destination: str
    destination_fingerprint: str
    proof_asset_id: str
    verified_at: datetime
    external_identity_id: str | None = None


class ChannelProofPort(Protocol):
    is_fake: bool

    def verify(self, channel: str, proof_ref: str) -> VerifiedChannelProof: ...


class FakeChannelProofPort:
    is_fake = True

    def __init__(self) -> None:
        self._proofs: dict[str, VerifiedChannelProof | str] = {}

    def register(self, proof_ref: str, proof: VerifiedChannelProof) -> None:
        self._proofs[proof_ref] = proof

    def fail(self, proof_ref: str, mode: str) -> None:
        if mode not in {"timeout", "forged"}:
            raise ValueError("unsupported synthetic failure mode")
        self._proofs[proof_ref] = mode

    def verify(self, channel: str, proof_ref: str) -> VerifiedChannelProof:
        value = self._proofs.get(proof_ref)
        if value == "timeout":
            raise DependencyUnavailableError("channel provider timed out", code="channel_provider_timeout")
        if value == "forged" or value is None:
            raise UnauthorizedError("channel proof is invalid", code="channel_proof_invalid")
        assert isinstance(value, VerifiedChannelProof)
        return value


@dataclass(frozen=True)
class SendOutcome:
    outcome: str  # accepted / rejected / timeout / unknown
    provider_message_id: str | None = None
    error_code: str | None = None


class ChannelSendPort(Protocol):
    is_fake: bool

    def send(self, channel: str, destination_fingerprint: str, idempotency_key: str, content_sha256: str) -> SendOutcome: ...

    def lookup(self, channel: str, idempotency_key: str) -> SendOutcome: ...


class FakeChannelSendPort:
    """Synthetic provider; injectable per idempotency_key for success/timeout/unknown/duplicate drills."""

    is_fake = True

    def __init__(self) -> None:
        self._results: dict[str, SendOutcome] = {}
        self._queued_outcomes: dict[str, list[SendOutcome]] = {}

    def queue(self, idempotency_key: str, *outcomes: SendOutcome) -> None:
        self._queued_outcomes[idempotency_key] = list(outcomes)

    def seed_accepted(self, idempotency_key: str, provider_message_id: str) -> None:
        """Simulate a provider that actually received a request our client saw as a timeout."""
        self._results[idempotency_key] = SendOutcome("accepted", provider_message_id=provider_message_id)

    def send(self, channel: str, destination_fingerprint: str, idempotency_key: str, content_sha256: str) -> SendOutcome:
        queued = self._queued_outcomes.get(idempotency_key)
        outcome = queued.pop(0) if queued else SendOutcome("accepted", provider_message_id=f"synthetic-{idempotency_key}")
        if outcome.outcome == "accepted":
            self._results[idempotency_key] = outcome
        return outcome

    def lookup(self, channel: str, idempotency_key: str) -> SendOutcome:
        return self._results.get(idempotency_key, SendOutcome("unknown"))


# ---- webhook integration key resolution (tenant comes from the key, never
#      from a client-controlled header) ------------------------------------

@dataclass(frozen=True)
class IntegrationKey:
    key_id: str
    tenant_id: str
    secret: bytes
    provider: str


class IntegrationKeyPort(Protocol):
    is_fake: bool

    def resolve(self, key_id: str) -> IntegrationKey: ...


class FakeIntegrationKeyPort:
    is_fake = True

    def __init__(self) -> None:
        self._keys: dict[str, IntegrationKey] = {}

    def register(self, key: IntegrationKey) -> None:
        self._keys[key.key_id] = key

    def resolve(self, key_id: str) -> IntegrationKey:
        key = self._keys.get(key_id)
        if key is None:
            raise UnauthorizedError("integration key is not recognized", code="integration_key_unknown")
        return key
