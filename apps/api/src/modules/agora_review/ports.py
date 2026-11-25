from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .domain import DependencyUnavailableError, ForbiddenError, utc_now


@dataclass(frozen=True)
class ConversationAck:
    external_conversation_id: str
    status: str


@dataclass(frozen=True)
class RevisionAck:
    job_ref: str
    status: str


@dataclass(frozen=True)
class RevisionStatus:
    status: str
    result_artifact_ref: dict[str, Any] | None = None
    result_content_sha256: str | None = None


@dataclass(frozen=True)
class HealthStatus:
    status: str
    last_success_at: datetime | None


class ServantPort(Protocol):
    """Agora/Servant external adapter boundary. Never holds signature/publish capability."""

    is_fake: bool

    def open_conversation(self, idempotency_key: str, payload: dict[str, Any]) -> ConversationAck: ...

    def submit_revision(self, idempotency_key: str, payload: dict[str, Any]) -> RevisionAck: ...

    def get_revision_status(self, idempotency_key: str) -> RevisionStatus: ...

    def health_check(self) -> HealthStatus: ...


class FakeServant:
    """Deterministic local adapter; never reaches a real network or holds real credentials."""

    is_fake = True

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationAck] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._conversation_failure: dict[str, str] = {}
        self._job_failure: dict[str, str] = {}
        self.received_conversation_payloads: dict[str, dict[str, Any]] = {}
        self.received_revision_payloads: dict[str, dict[str, Any]] = {}
        self.healthy = True
        self._last_success_at: datetime | None = None

    def fail_conversation(self, idempotency_key: str, mode: str) -> None:
        if mode not in {"timeout", "denied"}:
            raise ValueError("unsupported synthetic conversation failure mode")
        self._conversation_failure[idempotency_key] = mode

    def fail_job(self, idempotency_key: str, mode: str) -> None:
        if mode not in {"timeout_after_register", "timeout_before_register", "denied"}:
            raise ValueError("unsupported synthetic job failure mode")
        self._job_failure[idempotency_key] = mode

    def open_conversation(self, idempotency_key: str, payload: dict[str, Any]) -> ConversationAck:
        self.received_conversation_payloads[idempotency_key] = payload
        existing = self._conversations.get(idempotency_key)
        if existing is not None:
            return existing
        mode = self._conversation_failure.pop(idempotency_key, None)
        if mode == "timeout":
            raise DependencyUnavailableError("servant conversation open timed out", code="servant_conversation_timeout")
        if mode == "denied":
            raise ForbiddenError("servant adapter denied the conversation", code="servant_conversation_denied")
        ack = ConversationAck(external_conversation_id=f"conv-{idempotency_key}", status="active")
        self._conversations[idempotency_key] = ack
        self._last_success_at = utc_now()
        return ack

    def submit_revision(self, idempotency_key: str, payload: dict[str, Any]) -> RevisionAck:
        self.received_revision_payloads[idempotency_key] = payload
        existing = self._jobs.get(idempotency_key)
        if existing is not None:
            return RevisionAck(job_ref=idempotency_key, status="accepted")
        mode = self._job_failure.pop(idempotency_key, None)
        if mode == "timeout_after_register":
            self._jobs[idempotency_key] = {"status": "running"}
            raise DependencyUnavailableError("servant revision submit timed out", code="servant_revision_timeout")
        if mode == "timeout_before_register":
            raise DependencyUnavailableError("servant revision submit timed out", code="servant_revision_timeout")
        if mode == "denied":
            raise ForbiddenError("servant adapter denied the revision job", code="servant_revision_denied")
        self._jobs[idempotency_key] = {"status": "running"}
        self._last_success_at = utc_now()
        return RevisionAck(job_ref=idempotency_key, status="accepted")

    def get_revision_status(self, idempotency_key: str) -> RevisionStatus:
        job = self._jobs.get(idempotency_key)
        if job is None:
            return RevisionStatus(status="unknown")
        return RevisionStatus(job["status"], job.get("result_artifact_ref"), job.get("result_content_sha256"))

    def submitted_job_count(self, idempotency_key: str) -> int:
        return 1 if idempotency_key in self._jobs else 0

    def health_check(self) -> HealthStatus:
        if not self.healthy:
            return HealthStatus(status="degraded", last_success_at=self._last_success_at)
        self._last_success_at = utc_now()
        return HealthStatus(status="available", last_success_at=self._last_success_at)


class ArtifactDiffPort(Protocol):
    is_fake: bool

    def diff(self, before_sha256: str, after_sha256: str) -> tuple[str, ...]: ...


class FakeArtifactDiff:
    """Local diff oracle; real content never leaves Agora/Pantheon through this platform."""

    is_fake = True

    def __init__(self) -> None:
        self._diffs: dict[tuple[str, str], tuple[str, ...]] = {}

    def register(self, before_sha256: str, after_sha256: str, changes: tuple[str, ...]) -> None:
        self._diffs[(before_sha256, after_sha256)] = changes

    def diff(self, before_sha256: str, after_sha256: str) -> tuple[str, ...]:
        if before_sha256 == after_sha256:
            return ()
        return self._diffs.get((before_sha256, after_sha256), ())


@dataclass(frozen=True)
class WebhookRequest:
    """Everything needed to verify the ``X-Webhook-*``/``X-Integration-Key-Id`` envelope.

    ``raw_body`` must be the exact bytes the caller signed; re-serialized JSON can
    reorder keys or whitespace and silently break a legitimate signature.
    """

    signature: str
    timestamp: str
    nonce: str
    integration_key_id: str
    raw_body: bytes
    method: str = "POST"
    path: str = "/api/v1/webhooks/servant-revisions"


class WebhookSecretPort(Protocol):
    """Resolves the shared HMAC secret for a given ``X-Integration-Key-Id``."""

    is_fake: bool

    def secret_for(self, integration_key_id: str) -> bytes | None: ...


class StaticWebhookSecretStore:
    """Fixed-key fake adapter for tests; never backed by a real secret manager."""

    is_fake = True

    def __init__(self, secrets: dict[str, bytes]) -> None:
        self._secrets = dict(secrets)

    def secret_for(self, integration_key_id: str) -> bytes | None:
        return self._secrets.get(integration_key_id)


def servant_webhook_signature(
    secret: bytes, *, timestamp: str, nonce: str, method: str, path: str, raw_body: bytes
) -> str:
    body_hash = hashlib.sha256(raw_body).hexdigest()
    material = f"{timestamp}\n{nonce}\n{method}\n{path}\n{body_hash}".encode("utf-8")
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def verify_servant_webhook(
    secret: bytes,
    *,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    raw_body: bytes,
    provided_signature: str,
    now: datetime | None = None,
    skew_seconds: int = 300,
) -> None:
    """Pure HMAC check per the OpenAPI ``integrationSignature`` contract; no network access."""

    now = now or utc_now()
    try:
        signed_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (ValueError, OSError) as exc:
        raise ForbiddenError("webhook timestamp is invalid", code="webhook_timestamp_invalid") from exc
    if abs((now - signed_at).total_seconds()) > skew_seconds:
        raise ForbiddenError("webhook timestamp is outside the allowed skew", code="webhook_timestamp_skew")
    expected = servant_webhook_signature(secret, timestamp=timestamp, nonce=nonce, method=method, path=path, raw_body=raw_body)
    if not hmac.compare_digest(expected, provided_signature):
        raise ForbiddenError("webhook signature is invalid", code="webhook_signature_invalid")
