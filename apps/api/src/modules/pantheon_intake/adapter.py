from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from .domain import ActorContext, ValidationError, new_id, require_uuid
from .ports import WebhookSignaturePort
from .repository import InMemoryRepository, StoreState


SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})


@dataclass(frozen=True)
class SafetyReceipt:
    """Kept even when a raw envelope is rejected outright, so research ops can trace it."""

    id: str
    tenant_id: str
    reason_code: str
    received_schema_version: str | None


class UnsupportedSchemaVersion(ValidationError):
    status = 422
    code = "unsupported_schema"


class PantheonAdapter:
    """Translates the raw Pantheon bridge envelope into the canonical CandidateCreate body.

    This is the "本平台adapter" the task brief asks for: a version gate plus a thin
    reshape step. It never assumes the real Pantheon wire format -- only this
    platform's own bridge contract, exercised here through fixtures/mocks.
    """

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def translate(self, tenant_id: str, raw_envelope: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_envelope, dict) or "schema_version" not in raw_envelope or "candidate" not in raw_envelope:
            raise ValidationError("pantheon envelope must contain schema_version and candidate", code="envelope_malformed")
        schema_version = raw_envelope["schema_version"]
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            self._record_safety_receipt(tenant_id, "unsupported_schema", str(schema_version))
            raise UnsupportedSchemaVersion(
                f"pantheon envelope schema_version {schema_version!r} is not supported",
                field_name="schema_version",
            )
        candidate = raw_envelope["candidate"]
        if not isinstance(candidate, dict):
            raise ValidationError("pantheon envelope candidate must be an object", code="envelope_malformed")
        return candidate

    def _record_safety_receipt(self, tenant_id: str, reason_code: str, received_schema_version: str | None) -> None:
        receipt = SafetyReceipt(id=new_id(), tenant_id=tenant_id, reason_code=reason_code, received_schema_version=received_schema_version)

        def save(state: StoreState) -> None:
            state.safety_receipts[(tenant_id, receipt.id)] = receipt

        self.repository.atomic(save)


def resolve_pantheon_adapter_context(
    signature_port: WebhookSignaturePort,
    *,
    tenant_id: str,
    method: str,
    path: str,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> ActorContext:
    """Composition-root helper: verify the ``integrationSignature`` scheme and only
    then mint the ``pantheon_adapter`` integration principal.

    Mirrors how ``longhao_platform``'s authenticator resolves a Principal from
    trusted headers before any module service runs -- the HMAC check belongs at
    this boundary, not inside ``PantheonIntakeService``.
    """

    lowered = {key.lower(): value for key, value in headers.items()}
    key_id = lowered.get("x-integration-key-id")
    timestamp = lowered.get("x-webhook-timestamp")
    nonce = lowered.get("x-webhook-nonce")
    signature = lowered.get("x-webhook-signature")
    if not key_id or not timestamp or not nonce or not signature:
        raise ValidationError(
            "X-Integration-Key-Id, X-Webhook-Timestamp, X-Webhook-Nonce and X-Webhook-Signature are required",
            code="webhook_headers_required",
        )
    signature_port.verify(
        key_id=key_id,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        method=method,
        path=path,
        raw_body_sha256=sha256(raw_body).hexdigest(),
    )
    actor_id = str(uuid5(NAMESPACE_URL, f"longhao:pantheon-adapter:{key_id}"))
    return ActorContext(require_uuid(tenant_id, "tenant_id"), actor_id, frozenset({"pantheon_adapter"}), principal_type="integration")
