from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import re
from hashlib import sha256
from typing import Any, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from .domain import (
    ActorContext,
    Application,
    AuditEvent,
    ConflictError,
    Consent,
    Customer,
    DependencyUnavailableError,
    DomainError,
    ExternalIdentity,
    ForbiddenError,
    IdempotencyReceipt,
    Job,
    MergeCase,
    NotFoundError,
    OutboxEvent,
    PreconditionRequiredError,
    StaleVersionError,
    UnauthorizedError,
    ValidationError,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import CustomerSourcePort, EvidencePort, IdentityProviderPort, VerifiedIdentity
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_PROVIDERS = {"legendflow", "line", "email", "phone"}
_INTENTS = {"new", "renewal", "upgrade", "reactivation"}
_CONSENT_PURPOSES = {
    "privacy",
    "electronic_signing",
    "marketing",
    "service_notification",
    "aml_declaration",
    "suitability_declaration",
}


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


class CustomerIdentityApplicationService:
    """M01 command/query application service.

    Every state-changing operation is executed through one repository callback,
    making domain state, audit, outbox and the idempotency receipt atomic. The
    external identity proof is verified before that transaction and is never
    persisted as a raw token.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        identity_provider: IdentityProviderPort,
        evidence: EvidencePort,
        customer_sources: CustomerSourcePort,
        *,
        hmac_key: bytes,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if len(hmac_key) < 32:
            raise ValueError("M01 HMAC key must contain at least 32 bytes")
        if environment == "production" and any(
            getattr(adapter, "is_fake", False) for adapter in (repository, identity_provider, evidence, customer_sources)
        ):
            raise RuntimeError("fake M01 adapters are forbidden in production")
        self.repository = repository
        self.identity_provider = identity_provider
        self.evidence = evidence
        self.customer_sources = customer_sources
        self._hmac_key = hmac_key
        self._clock = clock

    # ---- public commands -------------------------------------------------

    def create_customer(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_create_customer(context)
        self._body(body, {"external_system", "verified_identity_proof_ref", "display_name", "marketing_source_ref"}, {"external_system", "verified_identity_proof_ref"})
        if body["external_system"] not in {"legendflow", "manual"}:
            raise ValidationError("external_system is not supported", field_name="external_system")
        if body["external_system"] == "manual" and not self._staff(context, {"cms_operator"}):
            raise ForbiddenError("manual customer creation requires cms_operator", code="manual_creation_forbidden")
        marketing_source_ref = body.get("marketing_source_ref")
        if marketing_source_ref is not None and (
            not isinstance(marketing_source_ref, str) or len(marketing_source_ref) > 160
        ):
            raise ValidationError(
                "marketing_source_ref must be a string of at most 160 characters", field_name="marketing_source_ref"
            )
        proof_ref = require_uuid(body["verified_identity_proof_ref"], "verified_identity_proof_ref")
        identity = self._verify_identity(context, proof_ref, correlation_id)
        if identity.provider not in _PROVIDERS:
            raise ValidationError("verified identity provider is not supported", field_name="verified_identity_proof_ref")
        now = self._clock()
        if identity.expires_at <= now:
            self._audit_failure(context, "create_customer", correlation_id, "identity_proof_expired")
            raise UnauthorizedError("identity proof has expired", code="identity_proof_expired")
        subject_hmac = self._hmac(f"{identity.provider}:{identity.subject}")
        contact_hmacs = self._contact_hmacs(identity)
        display_name = str(body.get("display_name") or identity.display_name or "未命名客戶")
        if len(display_name) > 100:
            raise ValidationError("display_name exceeds 100 characters", field_name="display_name")
        correlation_id = self._correlation(correlation_id)

        def mutate(state: StoreState) -> _Outcome:
            existing_id = state.identity_index.get((context.tenant_id, identity.provider, subject_hmac))
            if existing_id:
                linked = state.identities[(context.tenant_id, existing_id)]
                customer = self._stored_customer(state, context.tenant_id, linked.customer_id)
                if linked.status != "verified":
                    error = ConflictError("revoked identity requires an authorized correction review", code="identity_revoked")
                    return _Outcome({}, error.status, "customer", customer.id, customer.version, error)
                return self._customer_outcome(customer, context, 201)

            customer_id = new_id()
            attribution_ref = body.get("marketing_source_ref")
            attribution_id = self._attribution_id(context.tenant_id, attribution_ref) if attribution_ref else None
            customer = Customer(
                id=customer_id,
                tenant_id=context.tenant_id,
                version=1,
                created_at=now,
                updated_at=now,
                status="prospect",
                display_name=display_name,
                masked_email=self._mask_email(identity.email),
                masked_phone=self._mask_phone(identity.phone),
                original_marketing_attribution_id=attribution_id,
                original_marketing_source_ref=attribution_ref,
                contact_lookup_hmacs=contact_hmacs,
                acquisition_touches=[attribution_ref] if attribution_ref else [],
            )
            state.customers[(context.tenant_id, customer_id)] = customer

            external_identity = ExternalIdentity(
                id=new_id(),
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                provider=identity.provider,
                subject_hash=subject_hmac,
                verified_at=identity.verified_at,
                proof_ref=proof_ref,
                created_at=now,
                updated_at=now,
            )
            state.identities[(context.tenant_id, external_identity.id)] = external_identity
            state.identity_index[(context.tenant_id, identity.provider, subject_hmac)] = external_identity.id

            possible_matches: set[str] = set()
            for contact_hash in contact_hmacs:
                possible_matches.update(state.contact_index.get((context.tenant_id, contact_hash), set()))
                state.contact_index.setdefault((context.tenant_id, contact_hash), set()).add(customer_id)
            possible_matches.discard(customer_id)
            for possible_customer_id in sorted(possible_matches):
                if not self._open_merge_exists(state, context.tenant_id, customer_id, possible_customer_id):
                    merge_case = MergeCase(
                        id=new_id(),
                        tenant_id=context.tenant_id,
                        source_customer_id=customer_id,
                        target_customer_id=possible_customer_id,
                        proposer_id=context.actor_id,
                        evidence_asset_ids=[proof_ref],
                        reason_code="verified_contact_match_requires_review",
                        created_at=now,
                        updated_at=now,
                    )
                    state.merge_cases[(context.tenant_id, merge_case.id)] = merge_case

            self._emit(
                state,
                event_type="customer.created",
                tenant_id=context.tenant_id,
                aggregate_type="customer",
                aggregate_id=customer_id,
                aggregate_version=1,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:customer-created",
                payload={"customer_id": customer_id, "marketing_attribution_id": attribution_id},
                now=now,
            )
            self._emit(
                state,
                event_type="customer.identity_linked",
                tenant_id=context.tenant_id,
                aggregate_type="customer",
                aggregate_id=customer_id,
                aggregate_version=1,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:identity-linked",
                payload={"customer_id": customer_id, "identity_id": external_identity.id, "provider": identity.provider},
                now=now,
            )
            return self._customer_outcome(customer, context, 201)

        return self._run_command(context, "create_customer", idempotency_key, body, correlation_id, mutate)

    def update_customer(
        self,
        context: ActorContext,
        customer_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        customer_id = require_uuid(customer_id, "customer_id")
        self._authorize_customer_resource(context, customer_id, {"cms_operator", "service_agent"})
        self._body(body, {"display_name", "preferred_locale", "contact_update_proof_ref"}, set(), min_properties=1)
        if "display_name" in body and (not isinstance(body["display_name"], str) or len(body["display_name"]) > 100):
            raise ValidationError("display_name must be a string up to 100 characters", field_name="display_name")
        if "preferred_locale" in body and body["preferred_locale"] not in {"zh-TW", "en"}:
            raise ValidationError("preferred_locale is not supported", field_name="preferred_locale")
        proof_ref = body.get("contact_update_proof_ref")
        if proof_ref is not None:
            proof_ref = require_uuid(proof_ref, "contact_update_proof_ref")
            if not self.evidence.exists(context.tenant_id, proof_ref):
                raise ValidationError("contact update proof does not exist", code="evidence_not_found", field_name="contact_update_proof_ref")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            customer = self._stored_customer(state, context.tenant_id, customer_id)
            self._cas(customer.version, expected_version)
            if customer.status == "merged":
                raise ConflictError("merged customer must be updated through the surviving customer", code="customer_merged")
            changed: list[str] = []
            if "display_name" in body and body["display_name"] != customer.display_name:
                customer.display_name = body["display_name"]
                changed.append("display_name")
            if "preferred_locale" in body and body["preferred_locale"] != customer.preferred_locale:
                customer.preferred_locale = body["preferred_locale"]
                changed.append("contact_channel")
            if proof_ref is not None:
                changed.append("identity_evidence")
            if changed:
                customer.version += 1
                customer.updated_at = now
                self._emit(
                    state,
                    event_type="customer.profile_updated",
                    tenant_id=context.tenant_id,
                    aggregate_type="customer",
                    aggregate_id=customer.id,
                    aggregate_version=customer.version,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:profile-updated",
                    payload={
                        "customer_id": customer.id,
                        "changed_field_codes": sorted(set(changed)),
                        "profile_evidence_ref": proof_ref or correlation_id,
                    },
                    now=now,
                )
            return self._customer_outcome(customer, context, 200)

        return self._run_command(context, "update_customer", idempotency_key, {"customer_id": customer_id, **body}, correlation_id, mutate)

    def link_external_identity(
        self,
        context: ActorContext,
        customer_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        customer_id = require_uuid(customer_id, "customer_id")
        self._authorize_customer_resource(context, customer_id, {"cms_operator"})
        self._body(body, {"provider", "verified_proof_ref", "proof_expires_at"}, {"provider", "verified_proof_ref"})
        if body["provider"] not in _PROVIDERS:
            raise ValidationError("provider is not supported", field_name="provider")
        proof_ref = require_uuid(body["verified_proof_ref"], "verified_proof_ref")
        identity = self._verify_identity(context, proof_ref, correlation_id)
        if identity.provider != body["provider"]:
            self._audit_failure(context, "link_external_identity", correlation_id, "identity_provider_mismatch")
            raise UnauthorizedError("proof provider does not match the request", code="identity_provider_mismatch")
        now = self._clock()
        requested_expiry = self._parse_time(body.get("proof_expires_at"), "proof_expires_at") if body.get("proof_expires_at") else identity.expires_at
        if min(identity.expires_at, requested_expiry) <= now:
            raise UnauthorizedError("identity proof has expired", code="identity_proof_expired")
        subject_hmac = self._hmac(f"{identity.provider}:{identity.subject}")
        correlation_id = self._correlation(correlation_id)

        def mutate(state: StoreState) -> _Outcome:
            customer = self._stored_customer(state, context.tenant_id, customer_id)
            self._cas(customer.version, expected_version)
            existing_id = state.identity_index.get((context.tenant_id, identity.provider, subject_hmac))
            if existing_id:
                existing = state.identities[(context.tenant_id, existing_id)]
                if existing.customer_id == customer_id:
                    if existing.status != "verified":
                        error = ConflictError("revoked identity cannot be silently relinked", code="identity_revoked")
                        return _Outcome({}, error.status, "customer", customer_id, customer.version, error)
                    return _Outcome(existing.to_api([]), 201, "customer", customer_id, customer.version)
                if not self._open_merge_exists(state, context.tenant_id, customer_id, existing.customer_id):
                    merge_case = MergeCase(
                        id=new_id(),
                        tenant_id=context.tenant_id,
                        source_customer_id=customer_id,
                        target_customer_id=existing.customer_id,
                        proposer_id=context.actor_id,
                        evidence_asset_ids=[proof_ref],
                        reason_code="external_subject_conflict",
                        created_at=now,
                        updated_at=now,
                    )
                    state.merge_cases[(context.tenant_id, merge_case.id)] = merge_case
                error = ConflictError("external subject is already linked; a restricted merge review was created", code="identity_link_conflict")
                return _Outcome({}, error.status, "customer", customer_id, customer.version, error)
            linked = ExternalIdentity(
                id=new_id(),
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                provider=identity.provider,
                subject_hash=subject_hmac,
                verified_at=identity.verified_at,
                proof_ref=proof_ref,
                created_at=now,
                updated_at=now,
            )
            state.identities[(context.tenant_id, linked.id)] = linked
            state.identity_index[(context.tenant_id, identity.provider, subject_hmac)] = linked.id
            customer.version += 1
            customer.updated_at = now
            self._emit(
                state,
                event_type="customer.identity_linked",
                tenant_id=context.tenant_id,
                aggregate_type="customer",
                aggregate_id=customer_id,
                aggregate_version=customer.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:identity-linked",
                payload={"customer_id": customer_id, "identity_id": linked.id, "provider": linked.provider},
                now=now,
            )
            return _Outcome(linked.to_api([]), 201, "customer", customer_id, customer.version)

        return self._run_command(context, "link_external_identity", idempotency_key, {"customer_id": customer_id, **body}, correlation_id, mutate)

    def revoke_external_identity(
        self,
        context: ActorContext,
        customer_id: str,
        identity_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        """Fail-closed internal correction command pending shared API/event registration.

        The verified link is retained as revoked evidence; subject indexes and
        historical transactions are never deleted or reassigned.
        """
        self._authorize_staff(context, {"cms_supervisor"})
        customer_id = require_uuid(customer_id, "customer_id")
        identity_id = require_uuid(identity_id, "identity_id")
        self._body(body, {"reason_code", "evidence_asset_ids"}, {"reason_code", "evidence_asset_ids"})
        evidence_ids = body["evidence_asset_ids"]
        if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 20:
            raise ValidationError("evidence_asset_ids must contain 1 to 20 items", field_name="evidence_asset_ids")
        evidence_ids = [require_uuid(value, "evidence_asset_ids") for value in evidence_ids]
        if any(not self.evidence.exists(context.tenant_id, value) for value in evidence_ids):
            raise ValidationError("identity correction evidence does not exist", code="evidence_not_found", field_name="evidence_asset_ids")
        if not isinstance(body["reason_code"], str) or not 1 <= len(body["reason_code"]) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            customer = self._stored_customer(state, context.tenant_id, customer_id)
            self._cas(customer.version, expected_version)
            identity = state.identities.get((context.tenant_id, identity_id))
            if not identity or identity.customer_id != customer_id:
                raise NotFoundError("identity link was not found")
            if identity.status == "revoked":
                return _Outcome(identity.to_api([]), 200, "customer", customer_id, customer.version)
            identity.status = "revoked"
            identity.version += 1
            identity.updated_at = now
            customer.version += 1
            customer.updated_at = now
            # No event is invented here: API/event stewards must register the
            # proposed customer.identity_revoked contract before publication.
            return _Outcome(identity.to_api([]), 200, "customer", customer_id, customer.version)

        request = {"customer_id": customer_id, "identity_id": identity_id, **body}
        return self._run_command(context, "revoke_external_identity", idempotency_key, request, correlation_id, mutate)

    def propose_customer_merge(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"cms_operator"})
        self._body(body, {"source_customer_id", "target_customer_id", "evidence_asset_ids", "reason_code"}, {"source_customer_id", "target_customer_id", "evidence_asset_ids", "reason_code"})
        source_id = require_uuid(body["source_customer_id"], "source_customer_id")
        target_id = require_uuid(body["target_customer_id"], "target_customer_id")
        if source_id == target_id:
            raise ValidationError("source and target customer must differ", field_name="target_customer_id")
        evidence_ids = body["evidence_asset_ids"]
        if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 20:
            raise ValidationError("evidence_asset_ids must contain 1 to 20 items", field_name="evidence_asset_ids")
        evidence_ids = [require_uuid(value, "evidence_asset_ids") for value in evidence_ids]
        if any(not self.evidence.exists(context.tenant_id, value) for value in evidence_ids):
            raise ValidationError("merge evidence does not exist", code="evidence_not_found", field_name="evidence_asset_ids")
        reason = str(body["reason_code"])
        if not reason or len(reason) > 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            self._stored_customer(state, context.tenant_id, source_id)
            self._stored_customer(state, context.tenant_id, target_id)
            if self._open_merge_exists(state, context.tenant_id, source_id, target_id):
                existing = next(
                    case for (tenant, _), case in state.merge_cases.items()
                    if tenant == context.tenant_id
                    and case.status == "proposed"
                    and {case.source_customer_id, case.target_customer_id} == {source_id, target_id}
                )
                return self._merge_outcome(existing, context, 201)
            case = MergeCase(
                id=new_id(),
                tenant_id=context.tenant_id,
                source_customer_id=source_id,
                target_customer_id=target_id,
                proposer_id=context.actor_id,
                evidence_asset_ids=evidence_ids,
                reason_code=reason,
                created_at=now,
                updated_at=now,
            )
            state.merge_cases[(context.tenant_id, case.id)] = case
            return self._merge_outcome(case, context, 201)

        return self._run_command(context, "propose_customer_merge", idempotency_key, body, correlation_id, mutate)

    def decide_customer_merge(
        self,
        context: ActorContext,
        merge_case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"cms_supervisor"})
        merge_case_id = require_uuid(merge_case_id, "merge_case_id")
        self._body(body, {"decision", "reason_code", "note", "evidence_asset_ids"}, {"decision", "reason_code"})
        if body["decision"] not in {"approve", "reject", "request_changes"}:
            raise ValidationError("decision is not supported", field_name="decision")
        reviewer_evidence = body.get("evidence_asset_ids", [])
        if not isinstance(reviewer_evidence, list) or len(reviewer_evidence) > 30:
            raise ValidationError("evidence_asset_ids must be an array up to 30 items", field_name="evidence_asset_ids")
        reviewer_evidence = [require_uuid(value, "evidence_asset_ids") for value in reviewer_evidence]
        if any(not self.evidence.exists(context.tenant_id, value) for value in reviewer_evidence):
            raise ValidationError("review evidence does not exist", code="evidence_not_found", field_name="evidence_asset_ids")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_merge(state, context.tenant_id, merge_case_id)
            self._cas(case.version, expected_version)
            if case.status != "proposed":
                raise ConflictError("merge case is already decided", code="merge_case_already_decided")
            if case.proposer_id == context.actor_id:
                raise ForbiddenError("maker cannot review the same merge case", code="maker_checker_violation")
            if not self.evidence.merge_allowed(context.tenant_id, (case.source_customer_id, case.target_customer_id)):
                raise ConflictError("retention or legal hold policy blocks this merge", code="merge_retention_blocked")
            case.reviewer_id = context.actor_id
            case.reviewer_evidence_asset_ids = reviewer_evidence
            case.decided_at = now
            case.updated_at = now
            case.version += 1
            if body["decision"] == "approve":
                source = self._stored_customer(state, context.tenant_id, case.source_customer_id)
                target = self._stored_customer(state, context.tenant_id, case.target_customer_id)
                if source.status == "merged":
                    raise ConflictError("source customer has already been merged", code="customer_already_merged")
                source.status = "merged"
                source.merged_into_customer_id = target.id
                source.version += 1
                source.updated_at = now
                state.aliases[(context.tenant_id, source.id)] = target.id
                case.status = "executed"
                case.executed_at = now
            else:
                case.status = "rejected"
                if body["decision"] == "request_changes":
                    case.reason_code = f"request_changes:{body['reason_code']}"[:80]
            return self._merge_outcome(case, context, 200)

        return self._run_command(context, "decide_customer_merge", idempotency_key, {"merge_case_id": merge_case_id, **body}, correlation_id, mutate)

    def create_application(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._body(body, {"customer_id", "package_version_id", "service_selections", "acquisition_touch_ref", "intent", "preceding_contract_id"}, {"customer_id", "package_version_id", "service_selections", "intent"})
        customer_id = require_uuid(body["customer_id"], "customer_id")
        self._authorize_customer_resource(context, customer_id, {"cms_operator"})
        package_id = require_uuid(body["package_version_id"], "package_version_id")
        if body["intent"] not in _INTENTS:
            raise ValidationError("intent is not supported", field_name="intent")
        preceding_contract_id = body.get("preceding_contract_id")
        if preceding_contract_id is not None:
            preceding_contract_id = require_uuid(preceding_contract_id, "preceding_contract_id")
        self._validate_service_selections(body["service_selections"])
        touch = body.get("acquisition_touch_ref")
        if touch is not None and (not isinstance(touch, str) or len(touch) > 160):
            raise ValidationError("acquisition_touch_ref exceeds 160 characters", field_name="acquisition_touch_ref")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            customer = self._stored_customer(state, context.tenant_id, customer_id)
            if customer.status == "merged":
                raise ConflictError("applications must use the surviving customer", code="customer_merged")
            existing = next(
                (
                    app for (tenant, _), app in state.applications.items()
                    if tenant == context.tenant_id
                    and app.customer_id == customer_id
                    and app.package_version_id == package_id
                    and app.intent == body["intent"]
                    and app.state == "draft"
                ),
                None,
            )
            if existing:
                if touch and touch not in existing.acquisition_touches:
                    existing.acquisition_touches.append(touch)
                if touch and touch not in customer.acquisition_touches:
                    customer.acquisition_touches.append(touch)
                return self._application_outcome(existing, context, 201)
            app = Application(
                id=new_id(),
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                package_version_id=package_id,
                service_selections=body["service_selections"],
                intent=body["intent"],
                preceding_contract_id=preceding_contract_id,
                original_acquisition_touch_ref=touch,
                acquisition_touches=[touch] if touch else [],
                created_at=now,
                updated_at=now,
            )
            state.applications[(context.tenant_id, app.id)] = app
            if customer.status == "prospect":
                customer.status = "applicant"
                customer.version += 1
                customer.updated_at = now
            if touch:
                customer.acquisition_touches.append(touch)
            self._emit(
                state,
                event_type="application.created",
                tenant_id=context.tenant_id,
                aggregate_type="application",
                aggregate_id=app.id,
                aggregate_version=app.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:application-created",
                payload={
                    "application_id": app.id,
                    "customer_id": app.customer_id,
                    "package_version_id": app.package_version_id,
                    "intent": app.intent,
                },
                now=now,
            )
            return self._application_outcome(app, context, 201)

        return self._run_command(context, "create_application", idempotency_key, body, correlation_id, mutate)

    def withdraw_application(
        self,
        context: ActorContext,
        application_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        application_id = require_uuid(application_id, "application_id")
        self._body(body, {"reason_code", "note", "evidence_asset_ids"}, {"reason_code"})
        reason = str(body["reason_code"])
        if not reason or len(reason) > 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            app = self._stored_application(state, context.tenant_id, application_id)
            self._authorize_customer_resource(context, app.customer_id, {"cms_operator"})
            self._cas(app.version, expected_version)
            if app.state in {"accepted", "rejected", "withdrawn"}:
                raise ConflictError("application can no longer be withdrawn", code="application_terminal")
            app.state = "withdrawn"
            app.withdrawal_reason_code = reason
            app.withdrawn_at = now
            app.version += 1
            app.updated_at = now
            if app.payment_status in {"pending", "authorized", "paid"}:
                job = Job(
                    id=new_id(),
                    tenant_id=context.tenant_id,
                    kind="withdrawal_finance_review",
                    payload={"application_id": app.id, "payment_status": app.payment_status, "reason_code": reason},
                    created_at=now,
                    updated_at=now,
                )
                state.jobs[(context.tenant_id, job.id)] = job
            return self._application_outcome(app, context, 200)

        return self._run_command(context, "withdraw_application", idempotency_key, {"application_id": application_id, **body}, correlation_id, mutate)

    def record_consent(
        self,
        context: ActorContext,
        customer_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        customer_id = require_uuid(customer_id, "customer_id")
        self._authorize_customer_resource(context, customer_id, {"cms_operator"})
        self._body(body, {"purpose", "document_version", "document_sha256", "granted", "evidence_asset_id"}, {"purpose", "document_version", "document_sha256", "granted", "evidence_asset_id"})
        if body["purpose"] not in _CONSENT_PURPOSES:
            raise ValidationError("consent purpose is not supported", field_name="purpose")
        if not isinstance(body["granted"], bool):
            raise ValidationError("granted must be boolean", field_name="granted")
        if not _SHA_RE.fullmatch(str(body["document_sha256"])):
            raise ValidationError("document_sha256 must be lowercase SHA-256", field_name="document_sha256")
        if not isinstance(body["document_version"], str) or len(body["document_version"]) > 80:
            raise ValidationError("document_version exceeds 80 characters", field_name="document_version")
        evidence_asset_id = require_uuid(body["evidence_asset_id"], "evidence_asset_id")
        if not self.evidence.exists(context.tenant_id, evidence_asset_id):
            raise ValidationError("consent evidence does not exist", code="evidence_not_found", field_name="evidence_asset_id")
        if not self.evidence.customer_authored(context.tenant_id, evidence_asset_id, customer_id):
            self._audit_failure(context, "record_consent", correlation_id, "customer_consent_evidence_required", customer_id)
            raise ForbiddenError("staff-assisted entry cannot impersonate customer consent", code="customer_consent_evidence_required")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            customer = self._stored_customer(state, context.tenant_id, customer_id)
            self._cas(customer.version, expected_version)
            consent = Consent(
                id=new_id(),
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                purpose=body["purpose"],
                document_version=body["document_version"],
                document_sha256=body["document_sha256"],
                granted=body["granted"],
                evidence_asset_id=evidence_asset_id,
                recorded_at=now,
                channel="customer_authenticated" if context.principal_type == "customer" else "verified_assisted",
                created_at=now,
                updated_at=now,
            )
            state.consents[(context.tenant_id, consent.id)] = consent
            customer.version += 1
            customer.updated_at = now
            self._emit(
                state,
                event_type="customer.consent_changed",
                tenant_id=context.tenant_id,
                aggregate_type="customer",
                aggregate_id=customer_id,
                aggregate_version=customer.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:consent-changed",
                payload={
                    "customer_id": customer_id,
                    "consent_id": consent.id,
                    "purpose": consent.purpose,
                    "granted": consent.granted,
                },
                now=now,
            )
            return _Outcome(consent.to_api(), 201, "customer", customer_id, customer.version)

        return self._run_command(context, "record_consent", idempotency_key, {"customer_id": customer_id, **body}, correlation_id, mutate)

    # ---- public queries --------------------------------------------------

    def get_customer(self, context: ActorContext, customer_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        customer_id = require_uuid(customer_id, "customer_id")
        correlation_id = self._correlation(correlation_id)
        try:
            self._authorize_customer_resource(context, customer_id, {"cms_operator", "sales", "service_agent"})
        except DomainError as exc:
            self._audit_failure(context, "get_customer", correlation_id, exc.code, customer_id)
            raise

        def query(state: StoreState) -> ServiceResponse:
            customer = self._stored_customer(state, context.tenant_id, customer_id, resolve_alias=True)
            self._audit(state, context, "get_customer", "customer", customer.id, customer.version, correlation_id, "succeeded")
            return ServiceResponse(customer.to_api(self._customer_actions(customer, context)), etag=self._etag(customer.version))

        return self.repository.atomic(query)

    def list_customers(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"cms_operator", "sales", "service_agent"})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter == "blocked":
            state_filter = "restricted"
        if state_filter and state_filter not in {"prospect", "applicant", "active", "inactive", "restricted", "merged"}:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(store: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in store.customers.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not state_filter or item.status == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            next_cursor = page[-1].id if len(rows) > limit else None
            self._audit(store, context, "list_customers", "customer", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._customer_actions(item, context)) for item in page],
                    "next_cursor": next_cursor,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_application(self, context: ActorContext, application_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        application_id = require_uuid(application_id, "application_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            app = self._stored_application(state, context.tenant_id, application_id)
            self._authorize_customer_resource(context, app.customer_id, {"cms_operator"})
            self._audit(state, context, "get_application", "application", app.id, app.version, correlation_id, "succeeded")
            return ServiceResponse(app.to_api(self._application_actions(app, context)), etag=self._etag(app.version))

        try:
            return self.repository.atomic(query)
        except DomainError as exc:
            self._audit_failure(context, "get_application", correlation_id, exc.code, application_id)
            if isinstance(exc, ForbiddenError):
                raise NotFoundError("application was not found") from None
            raise

    def list_customer_merges(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"cms_supervisor"})
        if state_filter == "applied":
            state_filter = "executed"
        if state_filter and state_filter not in {"proposed", "approved", "rejected", "executed", "cancelled"}:
            raise ValidationError("state filter is not supported", field_name="state")
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(store: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in store.merge_cases.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not state_filter or item.status == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(store, context, "list_customer_merges", "merge_case", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._merge_actions(item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_customer_merge(self, context: ActorContext, merge_case_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize_staff(context, {"cms_operator", "cms_supervisor"})
        merge_case_id = require_uuid(merge_case_id, "merge_case_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            case = self._stored_merge(state, context.tenant_id, merge_case_id)
            self._audit(state, context, "get_customer_merge", "merge_case", case.id, case.version, correlation_id, "succeeded")
            return ServiceResponse(case.to_api(self._merge_actions(case, context)), etag=self._etag(case.version))

        return self.repository.atomic(query)

    def get_customer_360(self, context: ActorContext, customer_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Internal UI projection preserving degraded-vs-empty source semantics."""
        customer_response = self.get_customer(context, customer_id, correlation_id=correlation_id)
        customer_id = customer_response.data["id"]
        snapshots = self.customer_sources.snapshots(context.tenant_id, customer_id)
        return {
            "customer": customer_response.data,
            "sources": [
                {
                    "source": item.source,
                    "source_status": item.status,
                    "last_success_at": wire_time(item.last_success_at) if item.last_success_at else None,
                    "items": item.items if item.status == "available" else None,
                    "error_code": item.error_code,
                }
                for item in snapshots
            ],
        }

    # ---- command transaction helpers ------------------------------------

    def _run_command(
        self,
        context: ActorContext,
        operation: str,
        key: str,
        request: dict[str, Any],
        correlation_id: str,
        mutate: Callable[[StoreState], _Outcome],
    ) -> ServiceResponse:
        if not _KEY_RE.fullmatch(key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, operation, key)

        def transaction(state: StoreState) -> tuple[_Outcome, bool]:
            receipt = state.idempotency.get(receipt_key)
            if receipt:
                if receipt.request_sha256 != fingerprint:
                    raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
                return _Outcome(
                    receipt.response_value,
                    receipt.response_status,
                    "replay",
                    None,
                    None,
                    receipt.error,
                ), True
            outcome = mutate(state)
            self._audit(
                state,
                context,
                operation,
                outcome.aggregate_type,
                outcome.aggregate_id,
                outcome.aggregate_version,
                correlation_id,
                outcome.error.code if outcome.error else "succeeded",
                outcome.error.code if outcome.error else None,
            )
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                operation=operation,
                key=key,
                request_sha256=fingerprint,
                response_status=outcome.status,
                response_value=outcome.value,
                error=outcome.error,
                completed_at=self._clock(),
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
        return ServiceResponse(outcome.value, outcome.status, etag, replayed)

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
            event_id=new_id(),
            event_type=event_type,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            sequence_no=sequence,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            classification="customer_confidential",
            payload=payload,
            occurred_at=now,
            emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    # ---- authorization, mapping and validation --------------------------

    def _authorize_create_customer(self, context: ActorContext) -> None:
        if context.principal_type == "customer":
            return
        if self._staff(context, {"cms_operator"}):
            return
        if context.principal_type == "service" and "m01:create_customer" in context.scopes:
            return
        raise ForbiddenError("actor cannot create customers")

    def _authorize_staff(self, context: ActorContext, roles: set[str]) -> None:
        if self._staff(context, roles):
            return
        raise ForbiddenError("staff role is not allowed for this operation")

    def _authorize_customer_resource(self, context: ActorContext, customer_id: str, staff_roles: set[str]) -> None:
        if context.principal_type == "customer":
            if context.customer_id == customer_id:
                return
            # A not-found shaped response prevents customer record enumeration.
            raise NotFoundError("resource was not found")
        if self._staff(context, staff_roles):
            return
        if context.principal_type == "service" and "m01:*" in context.scopes:
            return
        raise ForbiddenError("actor cannot access this customer")

    @staticmethod
    def _staff(context: ActorContext, roles: set[str]) -> bool:
        return context.principal_type == "staff" and bool(context.roles & roles)

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

    def _verify_identity(self, context: ActorContext, proof_ref: str, correlation_id: str | None) -> VerifiedIdentity:
        try:
            return self.identity_provider.verify(proof_ref)
        except (UnauthorizedError, DependencyUnavailableError) as exc:
            self._audit_failure(context, "verify_external_identity", correlation_id, exc.code)
            raise

    def _validate_service_selections(self, selections: Any) -> None:
        if not isinstance(selections, list) or not 1 <= len(selections) <= 10:
            raise ValidationError("service_selections must contain 1 to 10 items", field_name="service_selections")
        required = {"service_code", "offer_version", "quantity", "billing_interval", "quoted_price", "tax_mapping_version"}
        for index, item in enumerate(selections):
            self._body(item, required, required)
            if item["billing_interval"] not in {"month", "year", "one_time"}:
                raise ValidationError("billing_interval is not supported", field_name=f"service_selections.{index}.billing_interval")
            if not isinstance(item["quantity"], int) or not 1 <= item["quantity"] <= 100:
                raise ValidationError("quantity must be between 1 and 100", field_name=f"service_selections.{index}.quantity")
            price = item["quoted_price"]
            if not isinstance(price, dict) or set(price) != {"currency", "amount"}:
                raise ValidationError("quoted_price must contain currency and amount", field_name=f"service_selections.{index}.quoted_price")
            if not re.fullmatch(r"[A-Z]{3}", str(price["currency"])) or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", str(price["amount"])):
                raise ValidationError("quoted_price has an invalid currency or decimal amount", field_name=f"service_selections.{index}.quoted_price")

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _parse_time(value: str, field_name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        except (ValueError, AttributeError) as exc:
            raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc

    def _hmac(self, value: str) -> str:
        return hmac.new(self._hmac_key, value.strip().casefold().encode("utf-8"), sha256).hexdigest()

    def _contact_hmacs(self, identity: VerifiedIdentity) -> set[str]:
        values: set[str] = set()
        if identity.email:
            values.add(self._hmac(f"email:{identity.email.strip().casefold()}"))
        if identity.phone:
            normalized = re.sub(r"[^0-9+]", "", identity.phone)
            values.add(self._hmac(f"phone:{normalized}"))
        return values

    @staticmethod
    def _mask_email(value: str | None) -> str | None:
        if not value or "@" not in value:
            return None
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"

    @staticmethod
    def _mask_phone(value: str | None) -> str | None:
        if not value:
            return None
        digits = re.sub(r"\D", "", value)
        return f"***{digits[-4:]}" if digits else None

    @staticmethod
    def _attribution_id(tenant_id: str, reference: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"longhao:{tenant_id}:attribution:{reference}"))

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _etag(version: int) -> str:
        return f'"{version}"'

    @staticmethod
    def _stored_customer(state: StoreState, tenant_id: str, customer_id: str, resolve_alias: bool = False) -> Customer:
        lookup_id = state.aliases.get((tenant_id, customer_id), customer_id) if resolve_alias else customer_id
        customer = state.customers.get((tenant_id, lookup_id))
        if not customer:
            raise NotFoundError("customer was not found")
        return customer

    @staticmethod
    def _stored_application(state: StoreState, tenant_id: str, application_id: str) -> Application:
        app = state.applications.get((tenant_id, application_id))
        if not app:
            raise NotFoundError("application was not found")
        return app

    @staticmethod
    def _stored_merge(state: StoreState, tenant_id: str, case_id: str) -> MergeCase:
        case = state.merge_cases.get((tenant_id, case_id))
        if not case:
            raise NotFoundError("merge case was not found")
        return case

    @staticmethod
    def _open_merge_exists(state: StoreState, tenant_id: str, first: str, second: str) -> bool:
        return any(
            tenant == tenant_id and case.status == "proposed" and {case.source_customer_id, case.target_customer_id} == {first, second}
            for (tenant, _), case in state.merge_cases.items()
        )

    def _customer_actions(self, customer: Customer, context: ActorContext) -> list[str]:
        if customer.status == "merged":
            return []
        actions: list[str] = []
        if context.principal_type == "customer" and context.customer_id == customer.id:
            actions.extend(["update_customer", "link_external_identity", "create_application", "record_consent"])
        if self._staff(context, {"cms_operator"}):
            actions.extend(["update_customer", "link_external_identity", "create_application", "propose_customer_merge"])
        if self._staff(context, {"service_agent"}):
            actions.append("update_customer")
        return sorted(set(actions))

    def _application_actions(self, app: Application, context: ActorContext) -> list[str]:
        return ["withdraw_application"] if app.state not in {"accepted", "rejected", "withdrawn"} else []

    def _merge_actions(self, case: MergeCase, context: ActorContext) -> list[str]:
        if case.status == "proposed" and self._staff(context, {"cms_supervisor"}) and case.proposer_id != context.actor_id:
            return ["decide_customer_merge"]
        return []

    def _customer_outcome(self, customer: Customer, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(customer.to_api(self._customer_actions(customer, context)), status, "customer", customer.id, customer.version)

    def _application_outcome(self, app: Application, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(app.to_api(self._application_actions(app, context)), status, "application", app.id, app.version)

    def _merge_outcome(self, case: MergeCase, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(case.to_api(self._merge_actions(case, context)), status, "merge_case", case.id, case.version)

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
    ) -> None:
        state.audit_events.append(
            AuditEvent(
                id=new_id(),
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                purpose=context.purpose,
                action=f"m01.{action}",
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                correlation_id=correlation_id,
                result=result,
                reason_code=reason_code,
                recorded_at=self._clock(),
            )
        )

    def _audit_failure(
        self,
        context: ActorContext,
        action: str,
        correlation_id: str | None,
        reason_code: str,
        aggregate_id: str | None = None,
    ) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security" if aggregate_id is None else "customer", aggregate_id, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
