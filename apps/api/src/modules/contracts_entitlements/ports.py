from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .domain import (
    ApplicationSnapshot,
    ClearanceStatus,
    DependencyUnavailableError,
    KycStatus,
    NotFoundError,
    SuitabilityStatus,
    UnauthorizedError,
    new_id,
    utc_now,
)


class ApplicationSourcePort(Protocol):
    """Read-only mirror of ``customer.application`` (M01-owned; M02 has SELECT only)."""

    is_fake: bool

    def get(self, tenant_id: str, application_id: str) -> ApplicationSnapshot: ...


class KycPort(Protocol):
    """Read-only mirror of ``kyc.submission`` (M03-owned)."""

    is_fake: bool

    def current(self, tenant_id: str, application_id: str) -> KycStatus | None: ...


class SuitabilityPort(Protocol):
    """Read-only mirror of ``kyc.suitability_assessment`` (M03-owned)."""

    is_fake: bool

    def current(self, tenant_id: str, application_id: str) -> SuitabilityStatus | None: ...


class ClearancePort(Protocol):
    """Read-only mirror of ``aml.business_clearance`` safe projection (M04-owned)."""

    is_fake: bool

    def current(self, tenant_id: str, application_id: str) -> ClearanceStatus | None: ...


class PaymentPort(Protocol):
    """Read-only mirror of ``finance.payment`` (M14-owned)."""

    is_fake: bool

    def condition_ref(self, tenant_id: str, application_id: str) -> str | None: ...


class EvidencePort(Protocol):
    is_fake: bool

    def exists(self, tenant_id: str, asset_id: str) -> bool: ...


@dataclass(frozen=True)
class SignatureSessionDraft:
    provider_session_ref: str
    redirect_url: str
    expires_at: datetime


class SignatureProviderPort(Protocol):
    """Creates the customer-facing e-signing redirect session. NOT_CONNECTED to a real provider."""

    is_fake: bool

    def create_session(self, tenant_id: str, application_id: str, package_version_id: str) -> SignatureSessionDraft: ...


class WebhookVerifierPort(Protocol):
    """Verifies ``X-Webhook-Signature`` HMAC per the published integrationSignature scheme.

    ``X-Integration-Key-Id`` identifies which tenant's provisioned signing key is
    in play; the webhook body never carries a trusted ``tenant_id`` itself.
    """

    is_fake: bool

    def resolve_tenant(self, key_id: str) -> str: ...

    def verify(self, *, timestamp: str, nonce: str, method: str, path: str, raw_body: bytes, signature: str, key_id: str) -> bool: ...


# ---- fakes: synthetic adapters only; production must reject them ----


class FakeApplicationSource:
    is_fake = True

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ApplicationSnapshot] = {}

    def register(self, snapshot: ApplicationSnapshot) -> None:
        self._rows[(snapshot.tenant_id, snapshot.id)] = snapshot

    def set_version(self, tenant_id: str, application_id: str, version: int) -> None:
        current = self._rows[(tenant_id, application_id)]
        self._rows[(tenant_id, application_id)] = ApplicationSnapshot(
            current.id, current.tenant_id, version, current.customer_id, current.package_version_id,
            current.service_selections, current.intent, current.preceding_contract_id, current.state,
        )

    def set_state(self, tenant_id: str, application_id: str, state: str) -> None:
        current = self._rows[(tenant_id, application_id)]
        self._rows[(tenant_id, application_id)] = ApplicationSnapshot(
            current.id, current.tenant_id, current.version, current.customer_id, current.package_version_id,
            current.service_selections, current.intent, current.preceding_contract_id, state,
        )

    def get(self, tenant_id: str, application_id: str) -> ApplicationSnapshot:
        row = self._rows.get((tenant_id, application_id))
        if row is None:
            raise NotFoundError("application was not found")
        return row


class FakeGateDirectory:
    """Single fake backing KYC, suitability and business-clearance read ports."""

    is_fake = True

    def __init__(self) -> None:
        self._kyc: dict[tuple[str, str], KycStatus] = {}
        self._suitability: dict[tuple[str, str], SuitabilityStatus] = {}
        self._clearance: dict[tuple[str, str], ClearanceStatus] = {}

    def set_kyc(self, tenant_id: str, application_id: str, status: KycStatus) -> None:
        self._kyc[(tenant_id, application_id)] = status

    def set_suitability(self, tenant_id: str, application_id: str, status: SuitabilityStatus) -> None:
        self._suitability[(tenant_id, application_id)] = status

    def set_clearance(self, tenant_id: str, application_id: str, status: ClearanceStatus) -> None:
        self._clearance[(tenant_id, application_id)] = status

    def current(self, tenant_id: str, application_id: str) -> KycStatus | None:
        return self._kyc.get((tenant_id, application_id))

    # Protocol-shaped dispatch: instances are handed out per-port but share state.
    class _KycView:
        is_fake = True

        def __init__(self, parent: "FakeGateDirectory") -> None:
            self._parent = parent

        def current(self, tenant_id: str, application_id: str) -> KycStatus | None:
            return self._parent._kyc.get((tenant_id, application_id))

    class _SuitabilityView:
        is_fake = True

        def __init__(self, parent: "FakeGateDirectory") -> None:
            self._parent = parent

        def current(self, tenant_id: str, application_id: str) -> SuitabilityStatus | None:
            return self._parent._suitability.get((tenant_id, application_id))

    class _ClearanceView:
        is_fake = True

        def __init__(self, parent: "FakeGateDirectory") -> None:
            self._parent = parent

        def current(self, tenant_id: str, application_id: str) -> ClearanceStatus | None:
            return self._parent._clearance.get((tenant_id, application_id))

    def kyc_port(self) -> "FakeGateDirectory._KycView":
        return self._KycView(self)

    def suitability_port(self) -> "FakeGateDirectory._SuitabilityView":
        return self._SuitabilityView(self)

    def clearance_port(self) -> "FakeGateDirectory._ClearanceView":
        return self._ClearanceView(self)


class FakePaymentDirectory:
    is_fake = True

    def __init__(self) -> None:
        self._conditions: dict[tuple[str, str], str] = {}

    def set_condition(self, tenant_id: str, application_id: str, condition_ref: str) -> None:
        self._conditions[(tenant_id, application_id)] = condition_ref

    def condition_ref(self, tenant_id: str, application_id: str) -> str | None:
        return self._conditions.get((tenant_id, application_id))


class FakeSignatureProvider:
    is_fake = True

    def __init__(self) -> None:
        self.sessions: dict[str, tuple[str, str, str]] = {}

    def create_session(self, tenant_id: str, application_id: str, package_version_id: str) -> SignatureSessionDraft:
        ref = f"synthetic-session-{new_id()}"
        self.sessions[ref] = (tenant_id, application_id, package_version_id)
        return SignatureSessionDraft(ref, f"https://signing.example.invalid/sessions/{ref}", utc_now() + timedelta(hours=1))


class FakeWebhookVerifier:
    is_fake = True

    def __init__(self) -> None:
        self.accept = True
        self.tenant_by_key: dict[str, str] = {}

    def register_key(self, key_id: str, tenant_id: str) -> None:
        self.tenant_by_key[key_id] = tenant_id

    def resolve_tenant(self, key_id: str) -> str:
        tenant_id = self.tenant_by_key.get(key_id)
        if tenant_id is None:
            raise UnauthorizedError("integration key is not provisioned", code="webhook_signature_invalid")
        return tenant_id

    def verify(self, *, timestamp: str, nonce: str, method: str, path: str, raw_body: bytes, signature: str, key_id: str) -> bool:
        if signature == "timeout":
            raise DependencyUnavailableError("signature verifier timed out", code="signature_verifier_timeout")
        return self.accept and signature == "valid"


class FakeEvidenceStore:
    is_fake = True

    def __init__(self) -> None:
        self._assets: set[tuple[str, str]] = set()

    def add(self, tenant_id: str, asset_id: str) -> None:
        self._assets.add((tenant_id, asset_id))

    def exists(self, tenant_id: str, asset_id: str) -> bool:
        return (tenant_id, asset_id) in self._assets
