from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def wire_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def new_id() -> str:
    return str(uuid4())


def require_uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(f"{field_name} must be a UUID", field_name=field_name) from exc


@dataclass(frozen=True)
class ActorContext:
    """Identity and scope the trusted server resolved for this request.

    ``customer_id`` is only ever set from a verified member session; it is
    never trusted when supplied by request body or query parameters.
    """

    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()
    principal_type: str = "staff"
    customer_id: str | None = None
    purpose: str = "operations"

    def __post_init__(self) -> None:
        require_uuid(self.tenant_id, "tenant_id")
        require_uuid(self.actor_id, "actor_id")
        if self.customer_id is not None:
            require_uuid(self.customer_id, "customer_id")
        if self.principal_type not in {"customer", "staff", "service"}:
            raise ValidationError("unsupported principal_type", field_name="principal_type")


class DomainError(Exception):
    status = 422
    code = "domain_error"
    retryable = False

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        current_version: int | None = None,
        field_name: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code or self.code
        self.current_version = current_version
        self.field_name = field_name


class ValidationError(DomainError):
    status = 422
    code = "validation_failed"


class UnauthorizedError(DomainError):
    status = 401
    code = "authentication_required"


class ForbiddenError(DomainError):
    status = 403
    code = "forbidden"


class NotFoundError(DomainError):
    status = 404
    code = "resource_not_found"


class ConflictError(DomainError):
    status = 409
    code = "conflict"


class StaleVersionError(DomainError):
    status = 412
    code = "stale_version"


class PreconditionRequiredError(DomainError):
    status = 428
    code = "precondition_required"


class DependencyUnavailableError(DomainError):
    status = 503
    code = "dependency_unavailable"
    retryable = True


# ---- delivery.message state machine ---------------------------------------

#: forward-only progress ranks. ``failed``/``unknown`` are soft, retry-pending
#: states with no rank of their own (they default to -1 below) so a later
#: legitimate progress receipt can still resurrect the message; ``expired``
#: and ``suppressed`` are affirmative, irreversible business decisions and are
#: never overridden by a provider receipt.
_FORWARD_RANK = {"queued": 0, "accepted": 1, "sent": 2, "delivered": 3}
_HARD_FINAL = {"expired", "suppressed"}
#: a ``read`` receipt is strictly downstream of ``delivered`` but must never
#: fabricate a ``delivered_at`` fact the provider did not separately confirm.
_RECEIPT_RANK = {"accepted": 1, "sent": 2, "delivered": 3, "read": 3}

#: static per-provider read-receipt capability; channels that are known not
#: to report reads must not be displayed as an inferred "unread" fact.
CHANNEL_READ_CAPABILITY = {"legendflow": "supported", "line": "supported", "sms": "unsupported", "email": "unknown"}

DELIVERY_CHANNELS = frozenset({"legendflow", "line", "sms", "email"})
BINDABLE_CHANNELS = frozenset({"line", "sms", "email", "web"})
PREFERENCE_CHANNELS = frozenset({"legendflow", "line", "sms", "email", "web"})
PREFERENCE_PURPOSES = frozenset({"service", "marketing"})
CHANNEL_PROVIDER_NAME = {"legendflow": "legendflow", "line": "line", "sms": "sms_gateway", "email": "email_gateway", "web": "web_push"}


@dataclass
class Delivery:
    """``delivery.message``: one logical message per recipient and channel."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    publication_id: str
    recipient_id: str
    customer_id: str
    entitlement_id: str
    channel: str
    rendered_sha256: str
    rendered_asset_id: str
    provider_idempotency_key: str
    expires_at: datetime
    channel_binding_id: str | None = None
    status: str = "queued"
    purpose: str = "service"
    provider_message_ref: str | None = None
    attempt_count: int = 0
    accepted_at: datetime | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    read_capability: str = "unknown"
    last_authorized_at: datetime | None = None
    checked_entitlement_version: int | None = None
    last_entitlement_decision_id: str | None = None
    suppression_reason: str | None = None
    last_error_code: str | None = None
    replaces_delivery_id: str | None = None

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "publication_id": self.publication_id,
            "customer_id": self.customer_id,
            "channel": self.channel,
            "content_sha256": self.rendered_sha256,
            "state": self.status,
            "provider_message_ref": self.provider_message_ref,
            "attempt_count": self.attempt_count,
            "accepted_at": wire_time(self.accepted_at) if self.accepted_at else None,
            "sent_at": wire_time(self.sent_at) if self.sent_at else None,
            "delivered_at": wire_time(self.delivered_at) if self.delivered_at else None,
            "read_at": wire_time(self.read_at) if self.read_at else None,
            "read_capability": self.read_capability,
            "last_entitlement_decision_id": self.last_entitlement_decision_id,
            "last_error_code": self.last_error_code,
        }

    def apply_receipt(self, receipt_type: str, occurred_at: datetime) -> bool:
        """Merge one ordering-unsafe provider receipt. Returns True if state changed.

        Forward rank only ever advances; a stale or conflicting receipt is
        recorded by the caller as an immutable ``delivery.receipt`` row but is
        quarantined here rather than regressing an already-advanced status or
        reviving a hard-final (``expired``/``suppressed``) message.
        """
        changed = False
        if receipt_type == "read":
            if self.status not in _HARD_FINAL and (self.read_at is None or occurred_at < self.read_at):
                self.read_at = occurred_at
                changed = True
            if self.status not in _HARD_FINAL and self.read_capability != "supported":
                self.read_capability = "supported"
                changed = True
        if receipt_type in _RECEIPT_RANK:
            if self.status in _HARD_FINAL:
                return changed
            new_rank = _RECEIPT_RANK[receipt_type]
            current_rank = _FORWARD_RANK.get(self.status, -1)
            if new_rank > current_rank:
                self.status = {1: "accepted", 2: "sent", 3: "delivered"}[new_rank]
                changed = True
            if receipt_type == "accepted" and self.accepted_at is None:
                self.accepted_at, changed = occurred_at, True
            elif receipt_type == "sent" and self.sent_at is None:
                self.sent_at, changed = occurred_at, True
            elif receipt_type == "delivered" and self.delivered_at is None:
                self.delivered_at, changed = occurred_at, True
            return changed
        if receipt_type == "failed":
            if self.status not in _HARD_FINAL and _FORWARD_RANK.get(self.status, -1) < _FORWARD_RANK["sent"] and self.status != "failed":
                self.status = "failed"
                changed = True
            return changed
        return changed


@dataclass
class DeliveryAttempt:
    """``delivery.attempt``: append-only evidence of one provider call."""

    id: str
    tenant_id: str
    message_id: str
    attempt_no: int
    provider: str
    outcome: str
    started_at: datetime
    created_at: datetime
    updated_at: datetime
    provider_message_id: str | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    version: int = 1


@dataclass
class DeliveryReceiptRecord:
    """``delivery.receipt``: append-only, immutable provider callback evidence."""

    id: str
    tenant_id: str
    message_id: str
    provider: str
    provider_event_id: str
    receipt_type: str
    provider_occurred_at: datetime
    received_at: datetime
    payload_sha256: str
    created_at: datetime
    updated_at: datetime
    applied: bool
    version: int = 1


@dataclass
class ChannelBinding:
    """``delivery.channel_binding``: verified member/LINE/phone/email destination."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    channel: str
    provider: str
    destination_hmac: str
    masked_destination: str
    proof_asset_id: str
    verified_at: datetime
    external_identity_id: str | None = None
    status: str = "verified"
    service_delivery_enabled: bool = True

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "channel": self.channel,
            "masked_destination": self.masked_destination,
            "state": self.status,
            "service_delivery_enabled": self.service_delivery_enabled,
        }


@dataclass
class CommunicationPreference:
    """``delivery.communication_preference``: service vs marketing opt-in, per channel."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    customer_id: str
    channel: str
    purpose: str
    enabled: bool
    consent_id: str

    def to_api(self, allowed_actions: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "created_at": wire_time(self.created_at),
            "updated_at": wire_time(self.updated_at),
            "allowed_actions": allowed_actions,
            "customer_id": self.customer_id,
            "channel": self.channel,
            "purpose": self.purpose,
            "enabled": self.enabled,
            "consent_id": self.consent_id,
        }


def resolve_delivery_permission(
    *,
    purpose: str,
    binding: ChannelBinding | None,
    preference: CommunicationPreference | None,
) -> tuple[bool, str]:
    """Pure decision: may a message of ``purpose`` be sent over this destination?

    Marketing and required-service classifications are evaluated independently
    so a withdrawn marketing opt-out can never suppress a contractual service
    notice, and a service preference can never be reclassified as marketing.
    """
    if purpose not in PREFERENCE_PURPOSES:
        raise ValidationError("purpose is not supported", field_name="purpose")
    if binding is None:
        return False, "channel_not_bound"
    if binding.status != "verified":
        return False, f"channel_binding_{binding.status}"
    if not binding.service_delivery_enabled:
        return False, "channel_binding_service_delivery_disabled"
    if purpose == "marketing":
        if preference is None or not preference.enabled:
            return False, "marketing_preference_not_granted"
        return True, "marketing_preference_granted"
    # purpose == "service": a missing preference row defaults to allowed; an
    # explicit opt-out only ever applies to the matching purpose/channel pair.
    if preference is not None and preference.purpose == "service" and not preference.enabled:
        return False, "service_preference_disabled"
    return True, "service_delivery_allowed"


@dataclass
class DispatchJob:
    """Worker lease/retry/dead-letter queue for one delivery's outbound attempts."""

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    message_id: str
    status: str = "pending"
    attempt_count: int = 0
    max_attempts: int = 5
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None
    redrive_reason: str | None = None
    redriven_by: str | None = None
    dead_letter_event_id: str | None = None


@dataclass
class PublicationProjection:
    """Local read model of M08 publication content, built from consumed events.

    M09 does not own publication content; this projection exists only so the
    member feed and dispatch worker can read already-validated facts without
    a synchronous cross-module call on every page view.
    """

    tenant_id: str
    publication_id: str
    report_version_id: str
    signature_id: str
    recommendation_series_id: str
    recommendation_version_id: str
    action: str
    approved_text: str
    body_sha256: str
    report_version_ids: list[str]
    published_at: datetime
    valid_until: datetime
    updated_at: datetime
    state: str = "published"
    correction_publication_id: str | None = None
    withdrawal_record_id: str | None = None


@dataclass
class EntitlementProjection:
    """Local mirror of ``entitlement.changed``; display/audit only.

    Dispatch-time and feed-time authorization always re-check the live
    :class:`~modules.legendflow_delivery.ports.EntitlementPort`; this cache is
    never the sole gate for sending or disclosing content.
    """

    tenant_id: str
    entitlement_id: str
    customer_id: str
    contract_id: str
    state: str
    decision_id: str
    valid_until: datetime
    updated_at: datetime


@dataclass
class AuditEvent:
    id: str
    tenant_id: str
    actor_id: str
    purpose: str
    action: str
    aggregate_type: str
    aggregate_id: str | None
    aggregate_version: int | None
    correlation_id: str
    result: str
    reason_code: str | None
    recorded_at: datetime = field(default_factory=utc_now)


@dataclass
class OutboxEvent:
    event_id: str
    event_type: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    sequence_no: int
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    classification: str
    payload: dict[str, Any]
    occurred_at: datetime
    emitted_at: datetime
    status: str = "pending"
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None

    @property
    def payload_sha256(self) -> str:
        return canonical_hash(self.payload)

    def envelope(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": "1.0.0",
            "tenant_id": self.tenant_id,
            "producer": "M09",
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "aggregate_version": self.aggregate_version,
            "sequence_no": self.sequence_no,
            "occurred_at": wire_time(self.occurred_at),
            "emitted_at": wire_time(self.emitted_at),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "idempotency_key": self.idempotency_key,
            "classification": self.classification,
            "payload_sha256": self.payload_sha256,
            "payload": self.payload,
        }


@dataclass
class InboxReceipt:
    tenant_id: str
    consumer: str
    producer: str
    event_id: str
    event_type: str
    payload_sha256: str
    aggregate_id: str
    sequence_no: int
    status: str
    result_code: str
    applied_at: datetime | None


@dataclass
class IdempotencyReceipt:
    tenant_id: str
    actor_id: str
    operation: str
    key: str
    request_sha256: str
    response_status: int
    response_value: Any
    error: DomainError | None
    completed_at: datetime


@dataclass
class WebhookNonceReceipt:
    key_id: str
    nonce: str
    received_at: datetime
