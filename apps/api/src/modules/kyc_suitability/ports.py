from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import DependencyUnavailableError, NotFoundError


@dataclass(frozen=True)
class SignedApplication:
    application_id: str
    customer_id: str
    customer_signature_id: str
    signed_at: datetime
    version: int = 1


class ApplicationGatewayPort(Protocol):
    """M01/M02 adjacency: confirms customer_signed before KYC can start and
    supplies the application's current version for path-aggregate CAS."""

    is_fake: bool

    def get_signed_application(self, tenant_id: str, application_id: str) -> SignedApplication: ...


class CustomerGatewayPort(Protocol):
    """M01 adjacency: supplies the customer aggregate's current version for
    path-aggregate CAS on ``request_due_diligence_refresh``."""

    is_fake: bool

    def get_customer_version(self, tenant_id: str, customer_id: str) -> int: ...


class FakeCustomerGateway:
    is_fake = True

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], int] = {}

    def register(self, tenant_id: str, customer_id: str, version: int = 1) -> None:
        self._versions[(tenant_id, customer_id)] = version

    def get_customer_version(self, tenant_id: str, customer_id: str) -> int:
        version = self._versions.get((tenant_id, customer_id))
        if version is None:
            raise NotFoundError("customer was not found")
        return version


@dataclass(frozen=True)
class ScanOutcome:
    status: str  # clean | rejected
    rejection_code: str | None = None


class DocumentScannerPort(Protocol):
    """Isolation-zone malware/MIME scanner. Never exposes a public URL."""

    is_fake: bool

    def scan(self, asset_id: str, sha256: str, media_type: str) -> ScanOutcome: ...


@dataclass(frozen=True)
class VerificationOutcome:
    status: str  # cleared | flagged


class ExternalVerificationPort(Protocol):
    """Due-diligence refresh dependency (identity/background recheck)."""

    is_fake: bool

    def check(self, tenant_id: str, customer_id: str) -> VerificationOutcome: ...


class FakeApplicationGateway:
    is_fake = True

    def __init__(self) -> None:
        self._signed: dict[tuple[str, str], SignedApplication] = {}

    def register(
        self,
        tenant_id: str,
        application_id: str,
        customer_id: str,
        customer_signature_id: str,
        signed_at: datetime,
        *,
        version: int = 1,
    ) -> None:
        self._signed[(tenant_id, application_id)] = SignedApplication(
            application_id, customer_id, customer_signature_id, signed_at, version
        )

    def get_signed_application(self, tenant_id: str, application_id: str) -> SignedApplication:
        found = self._signed.get((tenant_id, application_id))
        if found is None:
            raise NotFoundError("application was not found or is not customer_signed")
        return found


class RepositoryApplicationGateway:
    """Reads the local projection fed by the application.customer_signed consumer.

    Still a development/test-time stand-in: a production composition root must
    supply a gateway backed by a real M01/M02 call, not this eventually
    consistent local projection.
    """

    is_fake = True

    def __init__(self, repository) -> None:  # InMemoryRepository; untyped to avoid a cycle
        self.repository = repository

    def get_signed_application(self, tenant_id: str, application_id: str) -> SignedApplication:
        def read(state):
            return state.signed_applications.get((tenant_id, application_id))

        projection = self.repository.read(read)
        if projection is None:
            raise NotFoundError("application was not found or is not customer_signed")
        return SignedApplication(
            projection.application_id, projection.customer_id, projection.customer_signature_id, projection.signed_at
        )


class FakeDocumentScanner:
    is_fake = True

    def __init__(self) -> None:
        self._outcomes: dict[str, ScanOutcome | str] = {}

    def register(self, asset_id: str, outcome: ScanOutcome) -> None:
        self._outcomes[asset_id] = outcome

    def fail(self, asset_id: str, mode: str) -> None:
        if mode != "timeout":
            raise ValueError("unsupported synthetic failure mode")
        self._outcomes[asset_id] = mode

    def scan(self, asset_id: str, sha256: str, media_type: str) -> ScanOutcome:
        value = self._outcomes.get(asset_id, ScanOutcome("clean"))
        if value == "timeout":
            raise DependencyUnavailableError("document scanner timed out", code="document_scanner_timeout")
        assert isinstance(value, ScanOutcome)
        return value


class FakeExternalVerification:
    is_fake = True

    def __init__(self) -> None:
        self._outcomes: dict[tuple[str, str], VerificationOutcome | str] = {}

    def register(self, tenant_id: str, customer_id: str, outcome: VerificationOutcome) -> None:
        self._outcomes[(tenant_id, customer_id)] = outcome

    def fail(self, tenant_id: str, customer_id: str, mode: str) -> None:
        if mode != "timeout":
            raise ValueError("unsupported synthetic failure mode")
        self._outcomes[(tenant_id, customer_id)] = mode

    def check(self, tenant_id: str, customer_id: str) -> VerificationOutcome:
        value = self._outcomes.get((tenant_id, customer_id), VerificationOutcome("cleared"))
        if value == "timeout":
            raise DependencyUnavailableError("external verification timed out", code="external_verification_timeout")
        assert isinstance(value, VerificationOutcome)
        return value
