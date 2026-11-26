from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Callable, Protocol
from urllib.parse import urlparse

from .domain import DependencyUnavailableError, UnauthorizedError, ValidationError, utc_now


@dataclass(frozen=True)
class VerifiedWebhookCall:
    key_id: str
    verified_at_skew_seconds: int


class WebhookSignaturePort(Protocol):
    """HMAC verification for the ``integrationSignature`` security scheme.

    Verifies ``X-Webhook-Signature`` over ``timestamp\\nnonce\\nMETHOD\\npath\\nSHA256(body)``
    and rejects stale timestamps or reused nonces. Never decodes arbitrary bearer claims.
    """

    is_fake: bool

    def verify(
        self,
        *,
        key_id: str,
        timestamp: str,
        nonce: str,
        signature: str,
        method: str,
        path: str,
        raw_body_sha256: str,
    ) -> VerifiedWebhookCall: ...


class FakeWebhookSignatureVerifier:
    is_fake = True

    def __init__(
        self,
        keys: dict[str, bytes] | None = None,
        *,
        max_skew_seconds: int = 300,
        clock: Callable[[], object] = utc_now,
    ) -> None:
        self._keys = dict(keys or {})
        self._max_skew_seconds = max_skew_seconds
        self._clock = clock
        self._seen_nonces: set[tuple[str, str]] = set()

    def register_key(self, key_id: str, secret: bytes) -> None:
        self._keys[key_id] = secret

    def verify(
        self,
        *,
        key_id: str,
        timestamp: str,
        nonce: str,
        signature: str,
        method: str,
        path: str,
        raw_body_sha256: str,
    ) -> VerifiedWebhookCall:
        import hashlib
        import hmac as hmac_module

        secret = self._keys.get(key_id)
        if secret is None:
            raise UnauthorizedError("integration key is not recognized", code="integration_key_unknown")
        if not timestamp.isdigit():
            raise ValidationError("X-Webhook-Timestamp must be unix seconds", field_name="X-Webhook-Timestamp")
        now_unix = int(self._clock().timestamp())
        skew = abs(now_unix - int(timestamp))
        if skew > self._max_skew_seconds:
            raise UnauthorizedError("webhook timestamp is outside the allowed skew", code="webhook_timestamp_skew")
        nonce_key = (key_id, nonce)
        if nonce_key in self._seen_nonces:
            raise UnauthorizedError("webhook nonce was already used", code="webhook_nonce_replayed")
        material = f"{timestamp}\n{nonce}\n{method}\n{path}\n{raw_body_sha256}".encode("utf-8")
        expected = hmac_module.new(secret, material, hashlib.sha256).hexdigest()
        if not hmac_module.compare_digest(expected, signature):
            raise UnauthorizedError("webhook signature is invalid", code="webhook_signature_invalid")
        self._seen_nonces.add(nonce_key)
        return VerifiedWebhookCall(key_id=key_id, verified_at_skew_seconds=skew)


_PRIVATE_NETWORKS_NOTE = "link-local, loopback, multicast, metadata and other non-global addresses are blocked"


class EvidenceTransportPort(Protocol):
    """Low-level transport the SSRF guard drives; it never follows redirects itself."""

    is_fake: bool

    def resolve(self, hostname: str) -> list[str]: ...

    def get(self, url: str) -> "TransportResponse": ...


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes
    redirect_location: str | None = None


class FakeEvidenceTransport:
    is_fake = True

    def __init__(self) -> None:
        self._dns: dict[str, list[str]] = {}
        self._responses: dict[str, TransportResponse] = {}
        self.failure_mode: str | None = None

    def set_dns(self, hostname: str, addresses: list[str]) -> None:
        self._dns[hostname] = addresses

    def set_response(self, url: str, response: TransportResponse) -> None:
        self._responses[url] = response

    def resolve(self, hostname: str) -> list[str]:
        return self._dns.get(hostname, [])

    def get(self, url: str) -> TransportResponse:
        if self.failure_mode == "timeout":
            raise DependencyUnavailableError("evidence source timed out", code="evidence_source_timeout")
        response = self._responses.get(url)
        if response is None:
            raise DependencyUnavailableError("evidence source has no synthetic response", code="evidence_source_unavailable")
        return response


ALLOWED_EVIDENCE_HOSTS = frozenset({"pantheon.internal.longhao.invalid", "agora.internal.longhao.invalid"})


class SsrfBlockedError(ValidationError):
    code = "evidence_url_blocked"


class SsrfSafeEvidenceFetcher:
    """Only allowlisted hosts, only global IPs, redirects re-validated manually (REQ-M05-009)."""

    def __init__(self, transport: EvidenceTransportPort, *, allowed_hosts: frozenset[str] = ALLOWED_EVIDENCE_HOSTS, max_redirects: int = 3) -> None:
        self.transport = transport
        self.allowed_hosts = allowed_hosts
        self.max_redirects = max_redirects

    def fetch(self, url: str) -> bytes:
        current = url
        for _ in range(self.max_redirects + 1):
            self._validate_url(current)
            response = self.transport.get(current)
            if response.redirect_location:
                current = response.redirect_location
                continue
            return response.body
        raise SsrfBlockedError("evidence URL redirected too many times", code="evidence_redirect_loop")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise SsrfBlockedError("evidence URL must use https", code="evidence_url_scheme_blocked")
        hostname = parsed.hostname or ""
        if hostname not in self.allowed_hosts:
            raise SsrfBlockedError("evidence URL host is not allowlisted", code="evidence_url_host_blocked")
        addresses = self.transport.resolve(hostname)
        if not addresses:
            raise SsrfBlockedError("evidence URL host did not resolve", code="evidence_url_unresolved")
        for raw in addresses:
            address = ip_address(raw)
            if not address.is_global:
                raise SsrfBlockedError(f"evidence URL resolves to a non-global address ({_PRIVATE_NETWORKS_NOTE})", code="evidence_url_private_address")
