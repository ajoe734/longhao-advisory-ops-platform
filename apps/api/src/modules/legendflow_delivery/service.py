from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
from hashlib import sha256
import re
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from .domain import (
    ActorContext,
    AuditEvent,
    BINDABLE_CHANNELS,
    CHANNEL_PROVIDER_NAME,
    CHANNEL_READ_CAPABILITY,
    ChannelBinding,
    CommunicationPreference,
    ConflictError,
    Delivery,
    DeliveryAttempt,
    DeliveryReceiptRecord,
    DependencyUnavailableError,
    DispatchJob,
    DomainError,
    ForbiddenError,
    IdempotencyReceipt,
    NotFoundError,
    OutboxEvent,
    PREFERENCE_CHANNELS,
    PREFERENCE_PURPOSES,
    PreconditionRequiredError,
    PublicationProjection,
    StaleVersionError,
    UnauthorizedError,
    ValidationError,
    WebhookNonceReceipt,
    canonical_hash,
    new_id,
    require_uuid,
    resolve_delivery_permission,
    utc_now,
    wire_time,
)
from .ports import (
    ChannelProofPort,
    ChannelSendPort,
    EntitlementPort,
    IntegrationKeyPort,
    PublicationPort,
    PublicationRecipientPort,
    ReportAccessPort,
)
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_TIMESTAMP_RE = re.compile(r"^[0-9]{10}$")
_REASON_RE_MAX = 80
_WEBHOOK_WINDOW_SECONDS = 300
_REPORT_ACCESS_TTL_SECONDS = 120
_ACTIVE_ENTITLEMENT_STATES = {"active", "eligible"}
_DELIVERY_STATES = {"queued", "accepted", "sent", "delivered", "failed", "expired", "suppressed", "unknown"}
_RECIPIENT_NAMESPACE_ACTOR = uuid5(NAMESPACE_URL, "longhao:m09:messaging-adapter-actor")


@dataclass(frozen=True)
class ServiceResponse:
    data: dict[str, Any]
    status: int = 200
    etag: str | None = None
    idempotency_replayed: bool = False


@dataclass
class _Outcome:
    value: dict[str, Any]
    status: int
    aggregate_type: str
    aggregate_id: str | None
    aggregate_version: int | None
    error: DomainError | None = None


@dataclass(frozen=True)
class DispatchSummary:
    publication_id: str
    created: int
    suppressed: int
    skipped_existing: int


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    message_id: str
    result: str  # accepted / failed / unknown / suppressed / expired / dead_letter


@dataclass(frozen=True)
class _JobPrep:
    """Result of ``process_job``'s local-only revalidation phase.

    ``done`` is set when the job was fully resolved without needing a
    provider call (expired/withdrawn/revoked/suppressed/already-advanced);
    otherwise the remaining fields describe exactly what the caller needs to
    run the provider call outside of any repository transaction.
    """

    done: JobOutcome | None
    message_id: str | None = None
    entitlement_id: str | None = None
    channel: str | None = None
    fingerprint: str | None = None
    idempotency_key: str | None = None
    content_sha256: str | None = None
    message_status: str | None = None


class LegendflowDeliveryService:
    """M09 LegendFlow member feed, channel binding and delivery dispatch.

    Every state-changing operation runs inside one repository callback so
    domain state, the append-only evidence rows, the audit trail and the
    outbox entry commit atomically. Cross-module authority (entitlement,
    publication content, provider sends) is always re-checked live through
    the injected ports; local projections fed by consumed events are display
    and audit aids only, never the sole gate for sending or disclosure.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        *,
        publications: PublicationPort,
        recipients: PublicationRecipientPort,
        entitlements: EntitlementPort,
        report_access: ReportAccessPort,
        channel_proof: ChannelProofPort,
        channel_send: ChannelSendPort,
        integration_keys: IntegrationKeyPort,
        hmac_key: bytes,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if len(hmac_key) < 32:
            raise ValueError("M09 HMAC key must contain at least 32 bytes")
        adapters = (repository, publications, recipients, entitlements, report_access, channel_proof, channel_send, integration_keys)
        if environment == "production" and any(getattr(adapter, "is_fake", False) for adapter in adapters):
            raise RuntimeError("fake M09 adapters are forbidden in production")
        self.repository = repository
        self.publications = publications
        self.recipients_port = recipients
        self.entitlements = entitlements
        self.report_access = report_access
        self.channel_proof = channel_proof
        self.channel_send = channel_send
        self.integration_keys = integration_keys
        self._hmac_key = hmac_key
        self._clock = clock

    # ---- member-facing queries and commands ------------------------------

    def get_member_feed(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._require_member(context)
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in {"published", "corrected", "withdrawn"}:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()
        now = self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    (message_id, state.publications[(tenant, message.publication_id)])
                    for (tenant, message_id), message in state.deliveries.items()
                    if tenant == context.tenant_id
                    and message.customer_id == context.customer_id
                    and message.channel == "legendflow"
                    and message.created_at <= snapshot_at
                    and (tenant, message.publication_id) in state.publications
                    and (not state_filter or state.publications[(tenant, message.publication_id)].state == state_filter)
                    and self._currently_entitled(message.entitlement_id, context.tenant_id, now)
                    and (not cursor or message_id > cursor)
                ),
                key=lambda row: row[0],
            )
            page = rows[:limit]
            self._audit(state, context, "get_member_feed", "delivery", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [self._feed_item(projection) for _, projection in page],
                    "next_cursor": page[-1][0] if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def request_member_report_access(
        self, context: ActorContext, report_version_id: str, *, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._require_member(context)
        report_version_id = require_uuid(report_version_id, "report_version_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def query(state: StoreState) -> ServiceResponse:
            # ``atomic()`` discards the whole working copy when the callback
            # raises, so a denial/failure audit row must never be appended on
            # the same path that then raises; it is written by a dedicated
            # follow-up transaction in the except block below instead.
            if (context.tenant_id, report_version_id) in state.revoked_signatures:
                raise ForbiddenError("report access policy denied the request", code="report_access_signature_revoked")
            eligible = any(
                report_version_id in state.publications[(tenant, message.publication_id)].report_version_ids
                and self._currently_entitled(message.entitlement_id, context.tenant_id, now)
                for (tenant, _), message in state.deliveries.items()
                if tenant == context.tenant_id
                and message.customer_id == context.customer_id
                and (tenant, message.publication_id) in state.publications
            )
            if not eligible:
                raise ForbiddenError("report access policy denied the request", code="report_access_not_entitled")
            grant = self.report_access.sign(context.tenant_id, report_version_id, context.customer_id)
            audit_access_id = new_id()
            self._audit(state, context, "request_member_report_access", "delivery", None, None, correlation_id, "succeeded", None, audit_access_id)
            return ServiceResponse(
                {
                    "report_version_id": report_version_id,
                    "download_url": grant.download_url,
                    "expires_at": wire_time(now + timedelta(seconds=_REPORT_ACCESS_TTL_SECONDS)),
                    "content_sha256": grant.content_sha256,
                    "audit_access_id": audit_access_id,
                }
            )

        try:
            return self.repository.atomic(query)
        except ForbiddenError as exc:
            self._audit_failure(context, "request_member_report_access", correlation_id, exc.code, result="denied")
            raise
        except DependencyUnavailableError as exc:
            self._audit_failure(context, "request_member_report_access", correlation_id, exc.code)
            raise

    def bind_member_channel(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._require_member(context)
        self._body(body, {"channel", "verified_channel_proof_ref", "service_delivery_enabled"}, {"channel", "verified_channel_proof_ref", "service_delivery_enabled"})
        channel = body["channel"]
        if channel not in BINDABLE_CHANNELS:
            raise ValidationError("channel is not bindable", field_name="channel")
        if not isinstance(body["service_delivery_enabled"], bool):
            raise ValidationError("service_delivery_enabled must be a boolean", field_name="service_delivery_enabled")
        proof_ref = require_uuid(body["verified_channel_proof_ref"], "verified_channel_proof_ref")
        request = {"customer_id": context.customer_id, **body}
        # A verified channel proof is typically single-use at the real provider:
        # calling verify() again on a replay (or on a conflicting-body retry
        # that _run_command will reject as 409) would fail even though the
        # proof was already consumed. Only re-verify when no receipt exists
        # yet for this key, i.e. this is genuinely a brand-new request.
        if self._has_idempotency_receipt(context, "bind_member_channel", idempotency_key):
            proof = None
        else:
            try:
                proof = self.channel_proof.verify(channel, proof_ref)
            except (UnauthorizedError, DependencyUnavailableError) as exc:
                self._audit_failure(context, "bind_member_channel", correlation_id, exc.code)
                raise
        provider = CHANNEL_PROVIDER_NAME[channel]
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        service_delivery_enabled = body["service_delivery_enabled"]

        def mutate(state: StoreState) -> _Outcome:
            assert proof is not None  # only reached when verification above ran
            index_key = (context.tenant_id, provider, channel, proof.destination_fingerprint)
            existing_id = state.binding_index.get(index_key)
            if existing_id:
                binding = state.channel_bindings[(context.tenant_id, existing_id)]
                if binding.customer_id != context.customer_id:
                    error = ConflictError("destination is already verified for a different member", code="destination_already_bound")
                    return _Outcome({}, error.status, "channel_binding", binding.id, binding.version, error)
                changed = binding.status != "verified" or binding.service_delivery_enabled != service_delivery_enabled
                binding.status = "verified"
                binding.service_delivery_enabled = service_delivery_enabled
                binding.verified_at = proof.verified_at
                binding.proof_asset_id = proof.proof_asset_id
                binding.external_identity_id = proof.external_identity_id
                if changed:
                    binding.version += 1
                    binding.updated_at = now
                return _Outcome(binding.to_api([]), 201, "channel_binding", binding.id, binding.version)

            binding = ChannelBinding(
                id=new_id(),
                tenant_id=context.tenant_id,
                version=1,
                created_at=now,
                updated_at=now,
                customer_id=context.customer_id,
                channel=channel,
                provider=provider,
                destination_hmac=proof.destination_fingerprint,
                masked_destination=proof.masked_destination,
                proof_asset_id=proof.proof_asset_id,
                verified_at=proof.verified_at,
                external_identity_id=proof.external_identity_id,
                service_delivery_enabled=service_delivery_enabled,
            )
            state.channel_bindings[(context.tenant_id, binding.id)] = binding
            state.binding_index[index_key] = binding.id
            return _Outcome(binding.to_api([]), 201, "channel_binding", binding.id, binding.version)

        return self._run_command(context, "bind_member_channel", idempotency_key, request, correlation_id, mutate)

    def set_communication_preference(
        self, context: ActorContext, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None
    ) -> ServiceResponse:
        self._require_member(context)
        self._body(body, {"channel", "purpose", "enabled", "consent_id"}, {"channel", "purpose", "enabled", "consent_id"})
        channel = body["channel"]
        purpose = body["purpose"]
        if channel not in PREFERENCE_CHANNELS:
            raise ValidationError("channel is not supported", field_name="channel")
        if purpose not in PREFERENCE_PURPOSES:
            raise ValidationError("purpose is not supported", field_name="purpose")
        if not isinstance(body["enabled"], bool):
            raise ValidationError("enabled must be a boolean", field_name="enabled")
        consent_id = require_uuid(body["consent_id"], "consent_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        enabled = body["enabled"]

        def mutate(state: StoreState) -> _Outcome:
            consent = state.consents.get((context.tenant_id, consent_id))
            if not consent or consent["customer_id"] != context.customer_id:
                raise ValidationError("consent_id does not exist for this member", code="consent_not_found", field_name="consent_id")
            if purpose == "marketing" and enabled:
                if consent.get("purpose") != "marketing" or not consent.get("granted"):
                    raise ValidationError(
                        "marketing opt-in requires a granted marketing consent", code="consent_required_for_marketing_optin", field_name="consent_id",
                    )
                if state.suppressions.get((context.tenant_id, context.customer_id, "marketing_contact")) is True or state.suppressions.get((context.tenant_id, context.customer_id, "all_nonessential_contact")) is True:
                    raise ValidationError(
                        "marketing opt-in is blocked by an active CRM suppression", code="marketing_opt_in_blocked_by_suppression", field_name="enabled",
                    )

            index_key = (context.tenant_id, context.customer_id, channel, purpose)
            existing_id = state.preference_index.get(index_key)
            if existing_id:
                preference = state.preferences[(context.tenant_id, existing_id)]
                changed = preference.enabled != enabled or preference.consent_id != consent_id
                preference.enabled = enabled
                preference.consent_id = consent_id
                if changed:
                    preference.version += 1
                    preference.updated_at = now
                return _Outcome(preference.to_api([]), 200, "communication_preference", preference.id, preference.version)

            preference = CommunicationPreference(
                id=new_id(), tenant_id=context.tenant_id, version=1, created_at=now, updated_at=now,
                customer_id=context.customer_id, channel=channel, purpose=purpose, enabled=enabled, consent_id=consent_id,
            )
            state.preferences[(context.tenant_id, preference.id)] = preference
            state.preference_index[index_key] = preference.id
            return _Outcome(preference.to_api([]), 200, "communication_preference", preference.id, preference.version)

        return self._run_command(context, "set_communication_preference", idempotency_key, {"customer_id": context.customer_id, **body}, correlation_id, mutate)

    # ---- staff-facing operations ----------------------------------------

    def list_deliveries(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        # Only the two roles published in api/openapi.yaml's x-allowed-roles for
        # list_deliveries may call it; granting delivery_supervisor here without
        # a merged contract change would let code and contract diverge silently.
        self._authorize_staff(context, {"service_agent", "delivery_operator"})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in _DELIVERY_STATES:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in state.deliveries.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not state_filter or item.status == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_deliveries", "delivery", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._delivery_actions(state, item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def receive_message_receipt(
        self,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        key_id: str,
        timestamp: str,
        nonce: str,
        signature: str,
        method: str,
        path: str,
        raw_body: bytes,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        if not 8 <= len(key_id) <= 128:
            raise UnauthorizedError("integration key id is malformed", code="integration_key_id_invalid")
        if not 16 <= len(nonce) <= 128:
            raise UnauthorizedError("webhook nonce is malformed", code="webhook_nonce_invalid")
        key = self.integration_keys.resolve(key_id)
        self._verify_webhook_signature(timestamp, nonce, method, path, raw_body, signature, key.secret)
        actor_id = str(uuid5(_RECIPIENT_NAMESPACE_ACTOR, key_id))
        context = ActorContext(key.tenant_id, actor_id, frozenset({"messaging_adapter"}), principal_type="service")
        self._body(body, {"event_id", "provider_message_ref", "delivery_id", "provider_sequence", "provider_status", "provider_occurred_at", "provider_receipt_asset_id"}, {"event_id", "provider_message_ref", "delivery_id", "provider_status", "provider_occurred_at", "provider_receipt_asset_id"})
        event_id = require_uuid(body["event_id"], "event_id")
        delivery_id = require_uuid(body["delivery_id"], "delivery_id")
        receipt_asset_id = require_uuid(body["provider_receipt_asset_id"], "provider_receipt_asset_id")
        provider_message_ref = body["provider_message_ref"]
        if not isinstance(provider_message_ref, str) or not 1 <= len(provider_message_ref) <= 200:
            raise ValidationError("provider_message_ref must contain 1 to 200 characters", field_name="provider_message_ref")
        provider_sequence = body.get("provider_sequence")
        if provider_sequence is not None and (isinstance(provider_sequence, bool) or not isinstance(provider_sequence, int) or provider_sequence < 0):
            raise ValidationError("provider_sequence must be a non-negative integer or null", field_name="provider_sequence")
        provider_status = body["provider_status"]
        if provider_status not in {"accepted", "sent", "delivered", "failed", "read"}:
            raise ValidationError("provider_status is not supported", field_name="provider_status")
        occurred_at = self._parse_time(body["provider_occurred_at"], "provider_occurred_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def check_and_burn_nonce(state: StoreState) -> None:
            # Runs before _run_command's idempotency-replay short-circuit (see
            # ``before_idempotency_check``), so a reused nonce is always
            # rejected -- even when it rides along with an otherwise-valid
            # replay of the same Idempotency-Key -- and a legitimate retry
            # with a fresh nonce still burns that nonce even though the
            # operation itself just replays the cached response. The nonce is
            # only durably recorded if this whole transaction commits; a
            # request that fails validation above never wastes a single-use
            # nonce (see ``_verify_webhook_signature``).
            nonce_key = (key_id, nonce)
            if nonce_key in state.webhook_nonces:
                raise UnauthorizedError("webhook nonce was already used", code="webhook_nonce_replayed")
            state.webhook_nonces[nonce_key] = WebhookNonceReceipt(key_id=key_id, nonce=nonce, received_at=now)

        def mutate(state: StoreState) -> _Outcome:
            duplicate_id = state.receipt_index.get((context.tenant_id, key.provider, event_id))
            if duplicate_id:
                existing = state.receipts[(context.tenant_id, duplicate_id)]
                return _Outcome(
                    {"receipt_id": existing.id, "event_id": event_id, "status": "duplicate", "received_at": wire_time(existing.received_at), "correlation_id": correlation_id},
                    202, "delivery", delivery_id, None,
                )
            message = state.deliveries.get((context.tenant_id, delivery_id))
            if message is None:
                raise NotFoundError("delivery was not found")
            if CHANNEL_PROVIDER_NAME.get(message.channel) != key.provider:
                raise ValidationError("integration key provider does not match the delivery channel", code="receipt_provider_mismatch", field_name="key_id")
            payload_sha256 = canonical_hash(body)
            changed = message.apply_receipt(provider_status, occurred_at)
            receipt = DeliveryReceiptRecord(
                id=new_id(), tenant_id=context.tenant_id, message_id=delivery_id, provider=key.provider,
                provider_event_id=event_id, receipt_type=provider_status, provider_occurred_at=occurred_at,
                received_at=now, payload_sha256=payload_sha256, created_at=now, updated_at=now, applied=changed,
            )
            state.receipts[(context.tenant_id, receipt.id)] = receipt
            state.receipt_index[(context.tenant_id, key.provider, event_id)] = receipt.id
            if changed:
                message.version += 1
                message.updated_at = now
                self._emit(
                    state, event_type="delivery.status_changed", tenant_id=context.tenant_id, aggregate_type="delivery",
                    aggregate_id=message.id, aggregate_version=message.version, correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:status-changed", now=now,
                    payload={
                        "delivery_id": message.id, "publication_id": message.publication_id, "customer_id": message.customer_id,
                        "channel": message.channel, "state": message.status, "receipt_id": receipt.id,
                        "read_at": wire_time(message.read_at) if message.read_at else None,
                    },
                )
            status = "accepted" if changed else "quarantined"
            return _Outcome(
                {"receipt_id": receipt.id, "event_id": event_id, "status": status, "received_at": wire_time(receipt.received_at), "correlation_id": correlation_id},
                202, "delivery", delivery_id, message.version,
            )

        return self._run_command(
            context, "receive_message_receipt", idempotency_key, {"raw": body, "key_id": key_id}, correlation_id, mutate,
            default_status=202, before_idempotency_check=check_and_burn_nonce,
        )

    def reconcile_delivery(
        self,
        context: ActorContext,
        delivery_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"delivery_operator"})
        delivery_id = require_uuid(delivery_id, "delivery_id")
        self._body(body, {"reason_code", "last_known_provider_message_ref"}, {"reason_code"})
        reason_code = body["reason_code"]
        if not 1 <= len(reason_code) <= _REASON_RE_MAX:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        request = {"delivery_id": delivery_id, **body}
        # Skip the pre-CAS read and the provider network lookup whenever a
        # receipt already exists for this key: an exact-body replay just
        # needs _run_command to return the cached 202, and a conflicting
        # body needs _run_command to raise 409 -- neither path should run
        # (or re-run) the external side effect or a stale-version check.
        if self._has_idempotency_receipt(context, "reconcile_delivery", idempotency_key):
            outcome = None
        else:
            def fetch(state: StoreState) -> Delivery:
                message = state.deliveries.get((context.tenant_id, delivery_id))
                if message is None:
                    raise NotFoundError("delivery was not found")
                return message

            snapshot = self.repository.read(fetch)
            self._cas(snapshot.version, expected_version)
            # The provider lookup is a network call; it must never run while holding
            # the in-process transaction lock (or, against a real repository, an
            # open DB transaction) the way the commit callback below does.
            outcome = self.channel_send.lookup(snapshot.channel, snapshot.provider_idempotency_key)

        def mutate(state: StoreState) -> _Outcome:
            assert outcome is not None  # only reached when no idempotency receipt existed above
            message = state.deliveries.get((context.tenant_id, delivery_id))
            if message is None:
                raise NotFoundError("delivery was not found")
            self._cas(message.version, expected_version)
            attempt_outcome = {"accepted": "accepted", "rejected": "rejected", "timeout": "unknown", "unknown": "unknown"}[outcome.outcome]
            attempt = DeliveryAttempt(
                id=new_id(), tenant_id=context.tenant_id, message_id=message.id, attempt_no=message.attempt_count + 1,
                provider=CHANNEL_PROVIDER_NAME.get(message.channel, message.channel), outcome=attempt_outcome,
                started_at=now, created_at=now, updated_at=now, finished_at=now,
                provider_message_id=outcome.provider_message_id, error_code=outcome.error_code,
            )
            state.attempts[(context.tenant_id, attempt.id)] = attempt
            message.attempt_count += 1
            changed = False
            if outcome.outcome == "accepted":
                changed = message.apply_receipt("accepted", now)
                if outcome.provider_message_id:
                    message.provider_message_ref = outcome.provider_message_id
            elif outcome.outcome == "rejected":
                if message.status in {"queued", "unknown"}:
                    message.status, message.last_error_code, changed = "failed", outcome.error_code, True
            message.version += 1
            message.updated_at = now
            self._audit(state, context, "reconcile_delivery", "delivery", message.id, message.version, correlation_id, "succeeded", None, reason_code)
            if changed:
                self._emit(
                    state, event_type="delivery.status_changed", tenant_id=context.tenant_id, aggregate_type="delivery",
                    aggregate_id=message.id, aggregate_version=message.version, correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:reconciled", now=now,
                    payload={
                        "delivery_id": message.id, "publication_id": message.publication_id, "customer_id": message.customer_id,
                        "channel": message.channel, "state": message.status, "receipt_id": None,
                        "read_at": wire_time(message.read_at) if message.read_at else None,
                    },
                )
            return _Outcome(message.to_api(self._delivery_actions(state, message, context)), 202, "delivery", message.id, message.version)

        return self._run_command(context, "reconcile_delivery", idempotency_key, request, correlation_id, mutate, default_status=202)

    def redrive_delivery_event(
        self,
        context: ActorContext,
        dead_letter_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"delivery_supervisor"})
        dead_letter_id = require_uuid(dead_letter_id, "dead_letter_id")
        self._body(
            body,
            {"reason_code", "expected_event_id", "approved_by", "revalidate_entitlement", "revalidate_content_expiry"},
            {"reason_code", "expected_event_id", "approved_by", "revalidate_entitlement", "revalidate_content_expiry"},
        )
        reason_code = body["reason_code"]
        if not 1 <= len(reason_code) <= _REASON_RE_MAX:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        expected_event_id = require_uuid(body["expected_event_id"], "expected_event_id")
        approved_by = require_uuid(body["approved_by"], "approved_by")
        if body["revalidate_entitlement"] is not True or body["revalidate_content_expiry"] is not True:
            raise ValidationError("redrive must request both revalidations", field_name="revalidate_entitlement")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            job = state.jobs.get((context.tenant_id, dead_letter_id))
            if job is None:
                raise NotFoundError("dead letter job was not found")
            self._cas(job.version, expected_version)
            if job.status != "dead_letter":
                error = ConflictError("only dead-letter jobs can be redriven", code="job_not_dead_letter")
                return _Outcome({}, error.status, "dispatch_job", job.id, job.version, error)
            if job.dead_letter_event_id and job.dead_letter_event_id != expected_event_id:
                error = ConflictError("expected_event_id does not match the dead-lettered event", code="expected_event_id_mismatch")
                return _Outcome({}, error.status, "dispatch_job", job.id, job.version, error)
            message = state.deliveries[(context.tenant_id, job.message_id)]
            if message.expires_at <= now:
                self._audit(state, context, "redrive_delivery_event", "dispatch_job", job.id, job.version, correlation_id, "denied", "content_expired", reason_code)
                error = ConflictError("message content has expired and cannot be redriven", code="content_expired_cannot_redrive")
                return _Outcome({}, error.status, "dispatch_job", job.id, job.version, error)
            entitlement = self.entitlements.current(context.tenant_id, message.entitlement_id)
            if entitlement.state not in _ACTIVE_ENTITLEMENT_STATES or entitlement.valid_until <= now:
                self._audit(state, context, "redrive_delivery_event", "dispatch_job", job.id, job.version, correlation_id, "denied", "entitlement_not_active", reason_code)
                error = ConflictError("entitlement is not currently active and cannot be redriven", code="entitlement_not_active_cannot_redrive")
                return _Outcome({}, error.status, "dispatch_job", job.id, job.version, error)
            job.status = "retry"
            job.next_attempt_at = now
            job.lease_owner = None
            job.lease_until = None
            job.redrive_reason = reason_code
            job.redriven_by = approved_by
            job.version += 1
            job.updated_at = now
            self._audit(state, context, "redrive_delivery_event", "dispatch_job", job.id, job.version, correlation_id, "succeeded", None, reason_code)
            return _Outcome(
                {"receipt_id": new_id(), "event_id": expected_event_id, "status": "accepted", "received_at": wire_time(now), "correlation_id": correlation_id},
                202, "dispatch_job", job.id, job.version,
            )

        return self._run_command(context, "redrive_delivery_event", idempotency_key, {"dead_letter_id": dead_letter_id, **body}, correlation_id, mutate, default_status=202)

    # ---- worker-facing dispatch -------------------------------------------

    def dispatch_publication(
        self, tenant_id: str, publication_id: str, *, correlation_id: str, causation_id: str | None = None
    ) -> DispatchSummary:
        """Create (idempotently) one delivery per entitled recipient/channel.

        Always creates the ``legendflow`` feed message for every recipient on
        the audience snapshot; additionally fans out to any other verified,
        service-enabled channel binding the recipient already holds.
        """
        snapshot = self.publications.get(tenant_id, publication_id)
        recipient_rows = self.recipients_port.recipients(tenant_id, publication_id)
        now = self._clock()

        def mutate(state: StoreState) -> DispatchSummary:
            existing = state.publications.get((tenant_id, publication_id))
            if existing is None or existing.state == "published":
                state.publications[(tenant_id, publication_id)] = PublicationProjection(
                    tenant_id=tenant_id, publication_id=snapshot.publication_id, report_version_id=snapshot.report_version_id,
                    signature_id=snapshot.signature_id, recommendation_series_id=snapshot.recommendation_series_id,
                    recommendation_version_id=snapshot.recommendation_version_id, action=snapshot.action,
                    approved_text=snapshot.approved_text, body_sha256=snapshot.body_sha256,
                    report_version_ids=list(snapshot.report_version_ids), published_at=snapshot.published_at,
                    valid_until=snapshot.valid_until, updated_at=now,
                    state=existing.state if existing else "published",
                    correction_publication_id=existing.correction_publication_id if existing else None,
                    withdrawal_record_id=existing.withdrawal_record_id if existing else None,
                )
            created = suppressed = skipped = 0
            original_publication_id = state.correction_links.get((tenant_id, publication_id))
            for recipient in recipient_rows:
                for channel in self._dispatch_channels(state, tenant_id, recipient.customer_id):
                    binding_id = self._binding_id_for(state, tenant_id, recipient.customer_id, channel)
                    key = f"{publication_id}:{recipient.recipient_id}:{channel}"
                    if (tenant_id, key) in state.delivery_by_idempotency_key:
                        skipped += 1
                        continue
                    try:
                        entitlement = self.entitlements.current(tenant_id, recipient.entitlement_id)
                        entitled = entitlement.state in _ACTIVE_ENTITLEMENT_STATES and entitlement.valid_until > now
                        decision_id = entitlement.decision_id
                    except DependencyUnavailableError:
                        entitled, decision_id = False, None
                    # keyed per contract: terminating one of a customer's contracts
                    # must never block dispatch tied to a different, still-active one.
                    projection = state.entitlements.get((tenant_id, recipient.entitlement_id))
                    blocked = projection is not None and (tenant_id, recipient.customer_id, projection.contract_id) in state.terminated_customers
                    replaces_delivery_id = (
                        self._find_replaced_delivery(state, tenant_id, original_publication_id, recipient.recipient_id, channel)
                        if original_publication_id
                        else None
                    )
                    message = Delivery(
                        id=new_id(), tenant_id=tenant_id, version=1, created_at=now, updated_at=now,
                        publication_id=publication_id, recipient_id=recipient.recipient_id, customer_id=recipient.customer_id,
                        entitlement_id=recipient.entitlement_id, channel=channel, channel_binding_id=binding_id,
                        rendered_sha256=snapshot.body_sha256, rendered_asset_id=snapshot.report_version_id,
                        provider_idempotency_key=key, expires_at=snapshot.valid_until,
                        read_capability=CHANNEL_READ_CAPABILITY.get(channel, "unknown"),
                        last_authorized_at=now, checked_entitlement_version=None, last_entitlement_decision_id=decision_id,
                        replaces_delivery_id=replaces_delivery_id,
                    )
                    state.deliveries[(tenant_id, message.id)] = message
                    state.delivery_by_idempotency_key[(tenant_id, key)] = message.id
                    if entitled and not blocked:
                        message.status = "queued"
                        job = DispatchJob(id=new_id(), tenant_id=tenant_id, version=1, created_at=now, updated_at=now, message_id=message.id, status="pending")
                        state.jobs[(tenant_id, job.id)] = job
                        state.job_by_message[(tenant_id, message.id)] = job.id
                        created += 1
                    else:
                        message.status = "suppressed"
                        message.suppression_reason = "contract_terminated" if blocked else "entitlement_not_active"
                        attempt = DeliveryAttempt(
                            id=new_id(), tenant_id=tenant_id, message_id=message.id, attempt_no=1,
                            provider=CHANNEL_PROVIDER_NAME.get(channel, channel), outcome="rejected",
                            started_at=now, finished_at=now, created_at=now, updated_at=now,
                            error_code=message.suppression_reason,
                        )
                        state.attempts[(tenant_id, attempt.id)] = attempt
                        suppressed += 1
                    self._emit(
                        state, event_type="delivery.status_changed", tenant_id=tenant_id, aggregate_type="delivery",
                        aggregate_id=message.id, aggregate_version=1, correlation_id=correlation_id, causation_id=causation_id,
                        idempotency_key=f"dispatch:{key}", now=now,
                        payload={
                            "delivery_id": message.id, "publication_id": publication_id, "customer_id": recipient.customer_id,
                            "channel": channel, "state": message.status, "receipt_id": None, "read_at": None,
                        },
                    )
            return DispatchSummary(publication_id, created, suppressed, skipped)

        return self.repository.atomic(mutate)

    def lease_jobs(self, worker_id: str, tenant_id: str, limit: int = 20) -> list[DispatchJob]:
        now = self._clock()

        def lease(state: StoreState) -> list[DispatchJob]:
            selected = [
                job for (tenant, _), job in state.jobs.items()
                if tenant == tenant_id
                and job.status in {"pending", "retry"}
                and (job.next_attempt_at is None or job.next_attempt_at <= now)
                and (job.lease_until is None or job.lease_until <= now)
            ][:limit]
            for job in selected:
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_until = now + timedelta(seconds=30)
                job.updated_at = now
            return selected

        return self.repository.atomic(lease)

    def process_job(self, worker_id: str, tenant_id: str, job_id: str) -> JobOutcome:
        """Revalidate, call the provider adapter and record one attempt.

        A ``timeout``/``unknown`` outcome always queries the provider by the
        message's stable idempotency key before deciding retry/dead-letter, so
        a worker restart never produces a second logical message; a job that
        is *retrying* an already-``unknown`` message looks the result up
        first and only sends if the provider genuinely never saw it, so a
        bounded retry can never blind-send a second real request. Revalidation
        (expiry, withdrawal, revoked signature, binding/preference, live
        entitlement) and the provider call itself run outside the repository
        transaction; only the final commit re-checks the lease.
        """
        now = self._clock()

        def prepare(state: StoreState) -> _JobPrep:
            job = state.jobs[(tenant_id, job_id)]
            if job.lease_owner != worker_id or job.status != "leased":
                raise ConflictError("dispatch job lease was lost", code="worker_lease_lost")
            message = state.deliveries[(tenant_id, job.message_id)]

            if message.status not in {"queued", "unknown", "failed"}:
                # already advanced (e.g. a concurrent receipt or a duplicate
                # lease after this job's lease expired mid-flight); nothing
                # left for this worker to do.
                job.status, job.lease_owner, job.lease_until = "sent", None, None
                job.updated_at = now
                return _JobPrep(JobOutcome(job.id, message.id, message.status))

            if message.expires_at <= now:
                self._suppress(state, message, job, "content_expired", now, final_job_status="cancelled")
                return _JobPrep(JobOutcome(job.id, message.id, "expired"))

            publication = state.publications.get((tenant_id, message.publication_id))
            if publication is not None and publication.state == "withdrawn":
                self._suppress(state, message, job, "publication_withdrawn", now, final_job_status="cancelled")
                return _JobPrep(JobOutcome(job.id, message.id, "suppressed"))
            if publication is not None and (tenant_id, publication.report_version_id) in state.revoked_signatures:
                self._suppress(state, message, job, "signature_revoked", now, final_job_status="cancelled")
                return _JobPrep(JobOutcome(job.id, message.id, "suppressed"))

            fingerprint = message.channel
            if message.channel != "legendflow":
                binding = message.channel_binding_id and state.channel_bindings.get((tenant_id, message.channel_binding_id))
                preference = next(
                    (
                        p for (t, _), p in state.preferences.items()
                        if t == tenant_id and p.customer_id == message.customer_id and p.channel == message.channel and p.purpose == "service"
                    ),
                    None,
                )
                allowed, reason = resolve_delivery_permission(purpose="service", binding=binding, preference=preference)
                if not allowed:
                    self._suppress(state, message, job, reason, now, final_job_status="cancelled")
                    return _JobPrep(JobOutcome(job.id, message.id, "suppressed"))
                fingerprint = binding.destination_hmac if binding else message.channel

            return _JobPrep(
                None, message_id=message.id, entitlement_id=message.entitlement_id, channel=message.channel,
                fingerprint=fingerprint, idempotency_key=message.provider_idempotency_key,
                content_sha256=message.rendered_sha256, message_status=message.status,
            )

        prep = self.repository.atomic(prepare)
        if prep.done is not None:
            return prep.done

        try:
            entitlement = self.entitlements.current(tenant_id, prep.entitlement_id)
        except DependencyUnavailableError:

            def unavailable(state: StoreState) -> JobOutcome:
                job = state.jobs.get((tenant_id, job_id))
                message = state.deliveries.get((tenant_id, prep.message_id))
                if job is None or message is None or job.lease_owner != worker_id or job.status != "leased":
                    raise ConflictError("dispatch job lease was lost", code="worker_lease_lost")
                dead = self._fail_job(job, "entitlement_lookup_unavailable", now)
                return JobOutcome(job.id, message.id, "dead_letter" if dead else "unknown")

            return self.repository.atomic(unavailable)

        if entitlement.state not in _ACTIVE_ENTITLEMENT_STATES or entitlement.valid_until <= now:

            def revoked(state: StoreState) -> JobOutcome:
                job = state.jobs.get((tenant_id, job_id))
                message = state.deliveries.get((tenant_id, prep.message_id))
                if job is None or message is None or job.lease_owner != worker_id or job.status != "leased":
                    raise ConflictError("dispatch job lease was lost", code="worker_lease_lost")
                message.last_entitlement_decision_id = entitlement.decision_id
                self._suppress(state, message, job, "entitlement_revoked", now, final_job_status="cancelled")
                return JobOutcome(job.id, message.id, "suppressed")

            return self.repository.atomic(revoked)

        # The provider call is a network round-trip; it must never run while
        # holding the in-process transaction lock (or, against a real
        # repository, an open DB transaction).
        if prep.message_status == "unknown":
            result = self.channel_send.lookup(prep.channel, prep.idempotency_key)
            if result.outcome == "unknown":
                result = self.channel_send.send(prep.channel, prep.fingerprint, prep.idempotency_key, prep.content_sha256)
        else:
            result = self.channel_send.send(prep.channel, prep.fingerprint, prep.idempotency_key, prep.content_sha256)
        if result.outcome in {"timeout", "unknown"}:
            result = self.channel_send.lookup(prep.channel, prep.idempotency_key)

        def finalize(state: StoreState) -> JobOutcome:
            job = state.jobs.get((tenant_id, job_id))
            message = state.deliveries.get((tenant_id, prep.message_id))
            if job is None or message is None or job.lease_owner != worker_id or job.status != "leased":
                raise ConflictError("dispatch job lease was lost", code="worker_lease_lost")

            message.attempt_count += 1
            attempt = DeliveryAttempt(
                id=new_id(), tenant_id=tenant_id, message_id=message.id, attempt_no=message.attempt_count,
                provider=CHANNEL_PROVIDER_NAME.get(message.channel, message.channel),
                outcome={"accepted": "accepted", "rejected": "rejected", "timeout": "unknown", "unknown": "unknown"}[result.outcome],
                started_at=now, finished_at=now, created_at=now, updated_at=now,
                provider_message_id=result.provider_message_id, error_code=result.error_code,
            )
            state.attempts[(tenant_id, attempt.id)] = attempt

            if result.outcome == "accepted":
                message.apply_receipt("accepted", now)
                message.provider_message_ref = result.provider_message_id
                message.version += 1
                message.updated_at = now
                job.status, job.lease_owner, job.lease_until = "sent", None, None
                job.updated_at = now
                self._emit_status_changed(state, message, correlation_id=new_id(), causation_id=None, now=now)
                return JobOutcome(job.id, message.id, "accepted")

            if result.outcome == "rejected":
                if message.status in {"queued", "unknown", "failed"}:
                    message.status = "failed"
                message.last_error_code = result.error_code
                message.version += 1
                message.updated_at = now
                self._fail_job(job, result.error_code, now)
                self._emit_status_changed(state, message, correlation_id=new_id(), causation_id=None, now=now)
                return JobOutcome(job.id, message.id, "failed")

            # still unknown after a provider lookup: safe bounded retry on the same key.
            # Never regress a status that already advanced past "accepted".
            if message.status in {"queued", "unknown", "failed"}:
                message.status = "unknown"
            message.version += 1
            message.updated_at = now
            dead = self._fail_job(job, "provider_result_unknown", now)
            if dead:
                self._emit_status_changed(state, message, correlation_id=new_id(), causation_id=None, now=now)
            return JobOutcome(job.id, message.id, "dead_letter" if dead else "unknown")

        return self.repository.atomic(finalize)

    # ---- command transaction helpers -------------------------------------

    def _has_idempotency_receipt(self, context: ActorContext, operation: str, key: str) -> bool:
        """True when ``key`` already has a committed receipt for ``operation``, regardless of body.

        Lets a caller skip re-running a single-use or network external side
        effect (e.g. verifying a one-time channel proof, looking up provider
        status) before handing off to :meth:`_run_command`. ``_run_command``
        alone decides what happens next -- an exact-body replay returns the
        cached response, a conflicting body raises ``409 idempotency_conflict``
        -- and neither path needs, or should re-trigger, the external call.
        """
        if not _KEY_RE.fullmatch(key):
            return False
        receipt_key = (context.tenant_id, context.actor_id, operation, key)
        return self.repository.read(lambda state: receipt_key in state.idempotency)

    def _run_command(
        self,
        context: ActorContext,
        operation: str,
        key: str,
        request: dict[str, Any],
        correlation_id: str,
        mutate: Callable[[StoreState], _Outcome],
        *,
        default_status: int = 200,
        before_idempotency_check: Callable[[StoreState], None] | None = None,
    ) -> ServiceResponse:
        if not _KEY_RE.fullmatch(key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, operation, key)

        def transaction(state: StoreState) -> tuple[_Outcome, bool]:
            if before_idempotency_check is not None:
                # Runs unconditionally, inside the same atomic transaction,
                # before the idempotency replay short-circuit below -- e.g.
                # webhook nonce replay detection must reject a reused nonce
                # even when the rest of the request is an idempotent replay.
                before_idempotency_check(state)
            receipt = state.idempotency.get(receipt_key)
            if receipt:
                if receipt.request_sha256 != fingerprint:
                    raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
                return _Outcome(receipt.response_value, receipt.response_status, "replay", None, None, receipt.error), True
            outcome = mutate(state)
            self._audit(
                state, context, operation, outcome.aggregate_type, outcome.aggregate_id, outcome.aggregate_version,
                correlation_id, outcome.error.code if outcome.error else "succeeded", outcome.error.code if outcome.error else None,
            )
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id, actor_id=context.actor_id, operation=operation, key=key,
                request_sha256=fingerprint, response_status=outcome.status, response_value=outcome.value,
                error=outcome.error, completed_at=self._clock(),
            )
            return outcome, False

        try:
            outcome, replayed = self.repository.atomic(transaction)
        except DomainError as exc:
            self._audit_failure(context, operation, correlation_id, exc.code)
            raise
        if outcome.error:
            raise outcome.error
        etag = self._etag(outcome.aggregate_version) if outcome.aggregate_version else None
        status = outcome.status or default_status
        return ServiceResponse(outcome.value, status, etag, replayed)

    def _emit(
        self,
        state: StoreState,
        *,
        event_type: str,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        correlation_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        now: datetime,
        causation_id: str | None = None,
    ) -> None:
        sequence = 1 + max(
            (
                event.sequence_no for (tenant, _), event in state.outbox.items()
                if tenant == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id
            ),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(), event_type=event_type, tenant_id=tenant_id, aggregate_type=aggregate_type,
            aggregate_id=aggregate_id, aggregate_version=aggregate_version, sequence_no=sequence,
            correlation_id=correlation_id, causation_id=causation_id, idempotency_key=idempotency_key,
            classification="customer_confidential", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    def _emit_status_changed(self, state: StoreState, message: Delivery, *, correlation_id: str, causation_id: str | None, now: datetime) -> None:
        self._emit(
            state, event_type="delivery.status_changed", tenant_id=message.tenant_id, aggregate_type="delivery",
            aggregate_id=message.id, aggregate_version=message.version, correlation_id=correlation_id, causation_id=causation_id,
            idempotency_key=f"status-changed:{message.id}:{message.version}", now=now,
            payload={
                "delivery_id": message.id, "publication_id": message.publication_id, "customer_id": message.customer_id,
                "channel": message.channel, "state": message.status, "receipt_id": None,
                "read_at": wire_time(message.read_at) if message.read_at else None,
            },
        )

    def _suppress(self, state: StoreState, message: Delivery, job: DispatchJob, reason: str, now: datetime, *, final_job_status: str) -> None:
        if message.status not in {"delivered", "expired", "suppressed"}:
            message.status = "expired" if reason == "content_expired" else "suppressed"
            message.suppression_reason = reason
        message.version += 1
        message.updated_at = now
        attempt = DeliveryAttempt(
            id=new_id(), tenant_id=message.tenant_id, message_id=message.id, attempt_no=message.attempt_count + 1,
            provider=CHANNEL_PROVIDER_NAME.get(message.channel, message.channel), outcome="rejected",
            started_at=now, finished_at=now, created_at=now, updated_at=now, error_code=reason,
        )
        state.attempts[(message.tenant_id, attempt.id)] = attempt
        message.attempt_count += 1
        job.status, job.lease_owner, job.lease_until = final_job_status, None, None
        job.updated_at = now
        self._emit_status_changed(state, message, correlation_id=new_id(), causation_id=None, now=now)

    def _fail_job(self, job: DispatchJob, error_code: str, now: datetime, *, max_attempts: int = 5) -> bool:
        job.attempt_count += 1
        job.last_error_code = error_code
        job.lease_owner = None
        job.lease_until = None
        if job.attempt_count >= max_attempts:
            job.status = "dead_letter"
            job.dead_letter_event_id = new_id()
            job.updated_at = now
            return True
        job.status = "retry"
        job.next_attempt_at = now + timedelta(seconds=min(300, 2 ** job.attempt_count))
        job.updated_at = now
        return False

    # ---- authorization, validation and mapping ---------------------------

    def _require_member(self, context: ActorContext) -> None:
        if context.principal_type != "customer" or not context.customer_id or "member" not in context.roles:
            raise ForbiddenError("operation requires a verified member session", code="member_session_required")

    def _authorize_staff(self, context: ActorContext, roles: set[str]) -> None:
        if context.principal_type != "staff" or context.roles.isdisjoint(roles):
            raise ForbiddenError("actor is not authorized for this operation")

    @staticmethod
    def _body(body: dict[str, Any], allowed: set[str], required: set[str], min_properties: int = 0) -> None:
        if not isinstance(body, dict):
            raise ValidationError("request body must be an object")
        extra = set(body) - allowed
        missing = required - set(body)
        if extra:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(extra))}", field_name=sorted(extra)[0])
        if missing:
            raise ValidationError(f"missing fields: {', '.join(sorted(missing))}", field_name=sorted(missing)[0])
        if len(body) < min_properties:
            raise ValidationError("request body has too few properties")

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _parse_time(value: str, field_name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        except (ValueError, AttributeError) as exc:
            raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _etag(version: int) -> str:
        return f'"{version}"'

    def _currently_entitled(self, entitlement_id: str, tenant_id: str, now: datetime) -> bool:
        try:
            status = self.entitlements.current(tenant_id, entitlement_id)
        except DependencyUnavailableError:
            return False
        return status.state in _ACTIVE_ENTITLEMENT_STATES and status.valid_until > now

    @staticmethod
    def _feed_item(projection: PublicationProjection) -> dict[str, Any]:
        return {
            "publication_id": projection.publication_id,
            "recommendation_series_id": projection.recommendation_series_id,
            "recommendation_version_id": projection.recommendation_version_id,
            "action": projection.action,
            "approved_text": projection.approved_text,
            "body_sha256": projection.body_sha256,
            "report_version_ids": list(projection.report_version_ids),
            "published_at": wire_time(projection.published_at),
            "valid_until": wire_time(projection.valid_until),
            "state": projection.state,
            "correction_publication_id": projection.correction_publication_id,
        }

    def _delivery_actions(self, state: StoreState, message: Delivery, context: ActorContext) -> list[str]:
        actions: list[str] = []
        if self._has_role(context, {"delivery_operator"}) and message.status in {"unknown", "queued", "accepted", "failed"}:
            actions.append("reconcile_delivery")
        # delivery_supervisor cannot call list_deliveries today (see
        # database/proposals/M09/contract-changes.md); this stays forward
        # compatible so the action appears correctly once a contract change
        # grants that role read access, instead of defaulting to a silent [].
        if self._has_role(context, {"delivery_supervisor"}):
            job_id = state.job_by_message.get((message.tenant_id, message.id))
            job = state.jobs.get((message.tenant_id, job_id)) if job_id else None
            if job is not None and job.status == "dead_letter":
                actions.append("redrive_delivery_event")
        return sorted(set(actions))

    @staticmethod
    def _has_role(context: ActorContext, roles: set[str]) -> bool:
        return context.principal_type == "staff" and bool(context.roles & roles)

    @staticmethod
    def _dispatch_channels(state: StoreState, tenant_id: str, customer_id: str) -> list[str]:
        """``legendflow`` is always a destination; other channels require an
        active, service-enabled binding with the matching preference."""
        channels = ["legendflow"]
        for (tenant, _), binding in state.channel_bindings.items():
            if tenant != tenant_id or binding.customer_id != customer_id or binding.channel not in {"line", "sms", "email"}:
                continue
            preference = next(
                (p for (t, _), p in state.preferences.items() if t == tenant_id and p.customer_id == customer_id and p.channel == binding.channel and p.purpose == "service"),
                None,
            )
            allowed, _reason = resolve_delivery_permission(purpose="service", binding=binding, preference=preference)
            if allowed:
                channels.append(binding.channel)
        return channels

    @staticmethod
    def _binding_id_for(state: StoreState, tenant_id: str, customer_id: str, channel: str) -> str | None:
        if channel == "legendflow":
            return None
        for (tenant, _), binding in state.channel_bindings.items():
            if tenant == tenant_id and binding.customer_id == customer_id and binding.channel == channel:
                return binding.id
        return None

    @staticmethod
    def _find_replaced_delivery(state: StoreState, tenant_id: str, original_publication_id: str | None, recipient_id: str, channel: str) -> str | None:
        if not original_publication_id:
            return None
        for (tenant, _), message in state.deliveries.items():
            if tenant == tenant_id and message.publication_id == original_publication_id and message.recipient_id == recipient_id and message.channel == channel:
                return message.id
        return None

    def link_correction_deliveries(self, state: StoreState, tenant_id: str, original_publication_id: str, replacement_publication_id: str) -> int:
        """Backfill ``replaces_delivery_id`` when the correction event arrives
        after the replacement publication has already been dispatched."""
        originals = {
            (message.recipient_id, message.channel): message.id
            for (tenant, _), message in state.deliveries.items()
            if tenant == tenant_id and message.publication_id == original_publication_id
        }
        now = self._clock()
        linked = 0
        for (tenant, _), message in state.deliveries.items():
            if tenant != tenant_id or message.publication_id != replacement_publication_id:
                continue
            original_id = originals.get((message.recipient_id, message.channel))
            if original_id is not None and message.replaces_delivery_id != original_id:
                message.replaces_delivery_id = original_id
                message.version += 1
                message.updated_at = now
                linked += 1
        return linked

    def cancel_queued_deliveries(self, state: StoreState, tenant_id: str, publication_id: str, reason: str) -> int:
        """Cancel any delivery that has not yet reached the provider for ``publication_id``.

        Used when a publication is withdrawn so a still-queued or
        unresolved-retry job is never dispatched with retracted content; a
        message that already advanced to accepted/sent/delivered is untouched.
        """
        now = self._clock()
        cancelled = 0
        for (tenant, _), message in state.deliveries.items():
            if tenant != tenant_id or message.publication_id != publication_id or message.status not in {"queued", "unknown", "failed"}:
                continue
            job_id = state.job_by_message.get((tenant_id, message.id))
            job = state.jobs.get((tenant_id, job_id)) if job_id else None
            if job is None or job.status not in {"pending", "retry", "leased"}:
                continue
            self._suppress(state, message, job, reason, now, final_job_status="cancelled")
            cancelled += 1
        return cancelled

    def _verify_webhook_signature(self, timestamp: str, nonce: str, method: str, path: str, raw_body: bytes, signature: str, secret: bytes) -> None:
        """Pure HMAC/timestamp check; never burns the nonce.

        The nonce is only recorded once inside the same transaction that
        commits the business write (see ``receive_message_receipt``), so a
        request that fails body/idempotency validation never permanently
        wastes a single-use nonce.
        """
        if not _TIMESTAMP_RE.fullmatch(timestamp):
            raise UnauthorizedError("webhook timestamp is malformed", code="webhook_timestamp_invalid")
        skew = abs(int(self._clock().timestamp()) - int(timestamp))
        if skew > _WEBHOOK_WINDOW_SECONDS:
            raise UnauthorizedError("webhook timestamp is outside the allowed window", code="webhook_timestamp_expired")
        material = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{sha256(raw_body).hexdigest()}".encode("utf-8")
        expected = hmac.new(secret, material, sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.strip().lower()):
            raise UnauthorizedError("webhook signature is invalid", code="webhook_signature_invalid")

    def _audit(
        self,
        state: StoreState,
        context: ActorContext,
        action: str,
        aggregate_type: str,
        aggregate_id: str | None,
        aggregate_version: int | None,
        correlation_id: str,
        result: str,
        reason_code: str | None = None,
        extra: str | None = None,
    ) -> None:
        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=action, aggregate_type=aggregate_type, aggregate_id=aggregate_id, aggregate_version=aggregate_version,
                correlation_id=correlation_id, result=result, reason_code=reason_code or extra, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, code: str, *, result: str = "failed") -> None:
        def record(state: StoreState) -> None:
            self._audit(state, context, action, "delivery", None, None, correlation_id or new_id(), result, code)

        self.repository.atomic(record)
