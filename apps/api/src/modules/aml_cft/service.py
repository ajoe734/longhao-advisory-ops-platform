from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

from .domain import (
    ActorContext,
    AmlCaseAction,
    AmlCaseFile,
    AmlCaseReadGrant,
    AmlDeclaration,
    AmlScreening,
    AuditEvent,
    BusinessClearance,
    ConflictError,
    DomainError,
    ForbiddenError,
    IdempotencyReceipt,
    Job,
    NotFoundError,
    OutboxEvent,
    PreconditionRequiredError,
    StaleVersionError,
    SYSTEM_ACTOR_ID,
    UnauthorizedError,
    ValidationError,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import ApplicationAccessPort, EvidencePort, ScreeningProviderPort
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_WEBHOOK_TIMESTAMP_RE = re.compile(r"^[0-9]{10}$")
_WEBHOOK_SKEW_SECONDS = 300
_SCOPE_VALUES = {"sanctions", "terrorist_financing", "pep", "adverse_media"}
_ACTION_VALUES = {"request_edd", "request_information", "escalate", "record_investigation", "record_filing_reference"}
_DECISION_VALUES = {"approve", "deny", "request_information"}
_RISK_VALUES = {"low", "standard", "high"}
_CASE_RESOLVED = {"cleared", "rejected", "closed"}
# A "clear" screening result must never auto-approve while any case for the
# same application/customer is still unresolved or was previously rejected;
# only a case that was itself cleared may be superseded by a fresh clear.
_CASE_BLOCKS_AUTO_CLEAR = {"open", "under_review", "escalated", "rejected", "closed"}

# Deterministic synthetic system actor used for automated screening/outcome
# projections (clear or error results) that are not an individual officer's
# decision. No real human identity is ever used here.
_SYSTEM_ACTOR_ID = SYSTEM_ACTOR_ID

_CLEAR_VALIDITY = timedelta(days=180)
_PENDING_VALIDITY = timedelta(days=1)
_INFORMATION_VALIDITY = timedelta(days=7)
_DENIED_VALIDITY = timedelta(days=365)
_APPROVE_VALIDITY_DEFAULT = timedelta(days=180)


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
    # (event_type, aggregate_id, aggregate_version, payload) - the version is the
    # *event's own target aggregate* version, which is not always the command's
    # primary aggregate (e.g. a business_clearance.changed event emitted from a
    # screening or case command carries the clearance's own version).
    events: tuple[tuple[str, str, int, dict[str, Any]], ...] = ()


class AmlCftService:
    """M04 AML/CFT command/query application service.

    Restricted case content (list hits, filing rationale, investigation notes)
    never crosses into the general-purpose outbox or response payloads; only
    the minimal ``business_clearance.changed`` safe summary and the AML-only
    ``aml.case_changed`` projection leave this module's restricted boundary.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        screening_provider: ScreeningProviderPort,
        evidence: EvidencePort,
        application_access: ApplicationAccessPort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False) for adapter in (repository, screening_provider, evidence, application_access)
        ):
            raise RuntimeError("fake M04 adapters are forbidden in production")
        self.repository = repository
        self.screening_provider = screening_provider
        self.evidence = evidence
        self.application_access = application_access
        self._clock = clock

    # ---- commands ---------------------------------------------------------

    def submit_aml_declaration(
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
        self._body(
            body,
            {"form_version", "declaration_consent_id", "restricted_answer_asset_id", "restricted_document_ids", "customer_signature_id"},
            {"form_version", "declaration_consent_id", "restricted_answer_asset_id", "restricted_document_ids", "customer_signature_id"},
        )
        if not isinstance(body["form_version"], str) or not 1 <= len(body["form_version"]) <= 80:
            raise ValidationError("form_version must contain 1 to 80 characters", field_name="form_version")
        declaration_consent_id = require_uuid(body["declaration_consent_id"], "declaration_consent_id")
        restricted_answer_asset_id = require_uuid(body["restricted_answer_asset_id"], "restricted_answer_asset_id")
        customer_signature_id = require_uuid(body["customer_signature_id"], "customer_signature_id")
        document_ids = body["restricted_document_ids"]
        if not isinstance(document_ids, list) or len(document_ids) > 30:
            raise ValidationError("restricted_document_ids must contain 0 to 30 items", field_name="restricted_document_ids")
        document_ids = [require_uuid(value, "restricted_document_ids") for value in document_ids]
        # Ownership/authorization must be resolved before any evidence-existence
        # lookup: otherwise an actor who does not own (or whose application does
        # not exist) could still learn whether an arbitrary evidence UUID exists
        # in the tenant from the error returned, independent of their access.
        customer_id = self.application_access.customer_for(context.tenant_id, application_id)
        if customer_id is None:
            raise NotFoundError("application was not found")
        self._authorize_declaration(context, customer_id, action="submit_aml_declaration", correlation_id=correlation_id)
        if not self.evidence.exists(context.tenant_id, restricted_answer_asset_id):
            raise ValidationError("restricted_answer_asset_id does not exist", code="evidence_not_found", field_name="restricted_answer_asset_id")
        if any(not self.evidence.exists(context.tenant_id, value) for value in document_ids):
            raise ValidationError("restricted document evidence does not exist", code="evidence_not_found", field_name="restricted_document_ids")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            existing_id = state.declaration_by_application.get((context.tenant_id, application_id))
            existing = state.declarations.get((context.tenant_id, existing_id)) if existing_id else None
            if existing is None:
                declaration_id = new_id()
                state_label = "received" if document_ids else "requires_information"
                declaration = AmlDeclaration(
                    id=declaration_id,
                    tenant_id=context.tenant_id,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    application_id=application_id,
                    customer_id=customer_id,
                    form_version=body["form_version"],
                    declaration_consent_id=declaration_consent_id,
                    restricted_answer_asset_id=restricted_answer_asset_id,
                    restricted_document_ids=document_ids,
                    customer_signature_id=customer_signature_id,
                    received_at=now,
                    state=state_label,
                )
                state.declarations[(context.tenant_id, declaration_id)] = declaration
                state.declaration_by_application[(context.tenant_id, application_id)] = declaration_id
            else:
                self._cas(existing.version, expected_version)
                existing.form_version = body["form_version"]
                existing.declaration_consent_id = declaration_consent_id
                existing.restricted_answer_asset_id = restricted_answer_asset_id
                existing.restricted_document_ids = document_ids
                existing.customer_signature_id = customer_signature_id
                existing.received_at = now
                existing.state = "received" if document_ids else "requires_information"
                existing.version += 1
                existing.updated_at = now
                declaration = existing
            return _Outcome(declaration.to_api([]), 201, "aml_declaration", declaration.id, declaration.version)

        return self._run_command(context, "submit_aml_declaration", idempotency_key, {"application_id": application_id, **body}, correlation_id, mutate)

    def start_aml_screening(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"aml_officer"}, action="start_aml_screening", correlation_id=correlation_id)
        self._body(
            body,
            {"application_id", "customer_id", "kyc_submission_id", "screening_policy_version", "scope", "restricted_identity_asset_id", "reason_code"},
            {"application_id", "customer_id", "kyc_submission_id", "screening_policy_version", "scope", "restricted_identity_asset_id", "reason_code"},
        )
        application_id = require_uuid(body["application_id"], "application_id")
        customer_id = require_uuid(body["customer_id"], "customer_id")
        kyc_submission_id = require_uuid(body["kyc_submission_id"], "kyc_submission_id")
        restricted_identity_asset_id = require_uuid(body["restricted_identity_asset_id"], "restricted_identity_asset_id")
        if not isinstance(body["screening_policy_version"], str) or not 1 <= len(body["screening_policy_version"]) <= 80:
            raise ValidationError("screening_policy_version must contain 1 to 80 characters", field_name="screening_policy_version")
        scope = body["scope"]
        if not isinstance(scope, list) or not 1 <= len(scope) <= 4 or any(value not in _SCOPE_VALUES for value in scope):
            raise ValidationError("scope must contain 1 to 4 supported screening scopes", field_name="scope")
        if not isinstance(body["reason_code"], str) or not 1 <= len(body["reason_code"]) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        if not self.evidence.exists(context.tenant_id, restricted_identity_asset_id):
            raise ValidationError("restricted_identity_asset_id does not exist", code="evidence_not_found", field_name="restricted_identity_asset_id")
        known_customer_id = self.application_access.customer_for(context.tenant_id, application_id)
        if known_customer_id is not None and known_customer_id != customer_id:
            raise ValidationError("customer_id does not match application", field_name="customer_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        request_sha256 = canonical_hash(
            {"application_id": application_id, "customer_id": customer_id, "kyc_submission_id": kyc_submission_id, "scope": sorted(scope)}
        )

        def mutate(state: StoreState) -> _Outcome:
            screening_id = new_id()
            screening = AmlScreening(
                id=screening_id,
                tenant_id=context.tenant_id,
                version=1,
                created_at=now,
                updated_at=now,
                application_id=application_id,
                customer_id=customer_id,
                submission_id=kyc_submission_id,
                provider="",
                provider_request_id="",
                provider_ref="",
                policy_version=body["screening_policy_version"],
                scope=list(scope),
                request_sha256=request_sha256,
                reason_code=body["reason_code"],
                restricted_identity_asset_id=restricted_identity_asset_id,
                state="pending",
            )
            state.screenings[(context.tenant_id, screening_id)] = screening
            job = Job(
                id=new_id(),
                tenant_id=context.tenant_id,
                kind="dispatch_aml_screening",
                payload={"screening_id": screening_id, "scope": list(scope), "request_sha256": request_sha256},
                created_at=now,
                updated_at=now,
            )
            state.jobs[(context.tenant_id, job.id)] = job
            return _Outcome(screening.to_api([]), 202, "aml_screening", screening_id, 1)

        return self._run_command(context, "start_aml_screening", idempotency_key, body, correlation_id, mutate)

    def receive_aml_screening(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        webhook_timestamp: str | None = None,
        webhook_nonce: str | None = None,
        integration_key_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_service(context, {"aml_adapter"}, action="receive_aml_screening", correlation_id=correlation_id)
        self._authorize_webhook(webhook_timestamp, webhook_nonce, integration_key_id)
        self._body(
            body,
            {"event_id", "screening_id", "provider_ref", "list_version", "screened_at", "result", "restricted_result_asset_id", "result_sha256"},
            {"event_id", "screening_id", "provider_ref", "list_version", "screened_at", "result", "restricted_result_asset_id", "result_sha256"},
        )
        event_id = require_uuid(body["event_id"], "event_id")
        screening_id = require_uuid(body["screening_id"], "screening_id")
        restricted_result_asset_id = require_uuid(body["restricted_result_asset_id"], "restricted_result_asset_id")
        if body["result"] not in {"clear", "possible_match", "confirmed_match", "error"}:
            raise ValidationError("result is not supported", field_name="result")
        if not _SHA_RE.fullmatch(str(body["result_sha256"])):
            raise ValidationError("result_sha256 must be lowercase SHA-256", field_name="result_sha256")
        if not isinstance(body["provider_ref"], str) or not 1 <= len(body["provider_ref"]) <= 160:
            raise ValidationError("provider_ref must contain 1 to 160 characters", field_name="provider_ref")
        if not isinstance(body["list_version"], str) or not 1 <= len(body["list_version"]) <= 160:
            raise ValidationError("list_version must contain 1 to 160 characters", field_name="list_version")
        screened_at = self._parse_time(body["screened_at"], "screened_at")
        if not self.evidence.exists(context.tenant_id, restricted_result_asset_id):
            raise ValidationError("restricted_result_asset_id does not exist", code="evidence_not_found", field_name="restricted_result_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            screening = state.screenings.get((context.tenant_id, screening_id))
            if screening is None:
                raise NotFoundError("screening was not found")
            if screening.state != "pending":
                return _Outcome(
                    {"receipt_id": new_id(), "event_id": event_id, "status": "duplicate", "received_at": wire_time(now), "correlation_id": correlation_id},
                    202, "aml_screening", screening.id, screening.version,
                )
            screening.list_version = body["list_version"]
            screening.provider_ref = body["provider_ref"]
            screening.screened_at = screened_at
            screening.result_asset_id = restricted_result_asset_id
            screening.state = body["result"]
            screening.version += 1
            screening.updated_at = now

            events: list[tuple[str, str, int, dict[str, Any]]] = []
            if screening.state == "clear":
                blocking_case = self._blocking_case_for(state, context.tenant_id, screening.application_id, screening.customer_id)
                if blocking_case is not None:
                    # A prior possible/confirmed match (or rejection) for this
                    # application/customer is still unresolved: a later clean
                    # screening must never silently re-approve over it.
                    screening.expires_at = None
                    clearance = self._append_clearance(
                        state, context.tenant_id, screening.application_id, screening.customer_id,
                        decision="pending", policy_version=screening.policy_version, decided_by=_SYSTEM_ACTOR_ID,
                        decided_at=now, valid_until=now + _PENDING_VALIDITY, safe_reason_code="case_pending_review", case_id=blocking_case.id,
                    )
                else:
                    screening.expires_at = screened_at + _CLEAR_VALIDITY
                    clearance = self._append_clearance(
                        state, context.tenant_id, screening.application_id, screening.customer_id,
                        decision="approved", policy_version=screening.policy_version, decided_by=_SYSTEM_ACTOR_ID,
                        decided_at=now, valid_until=screening.expires_at, safe_reason_code="screening_clear", case_id=None,
                    )
                events.append(("business_clearance.changed", clearance.id, clearance.version, self._clearance_event_payload(clearance)))
            elif screening.state in {"possible_match", "confirmed_match"}:
                case = self._open_or_attach_case(state, context, screening, restricted_result_asset_id, now)
                clearance = self._append_clearance(
                    state, context.tenant_id, screening.application_id, screening.customer_id,
                    decision="pending", policy_version=screening.policy_version, decided_by=_SYSTEM_ACTOR_ID,
                    decided_at=now, valid_until=now + _PENDING_VALIDITY, safe_reason_code="review_required", case_id=case.id,
                )
                events.append(("aml.case_changed", case.id, case.version, {"case_id": case.id, "screening_id": screening.id, "restricted_record_version": case.version}))
                events.append(("business_clearance.changed", clearance.id, clearance.version, self._clearance_event_payload(clearance)))
            else:  # error
                screening.manual_required = True
                clearance = self._append_clearance(
                    state, context.tenant_id, screening.application_id, screening.customer_id,
                    decision="pending", policy_version=screening.policy_version, decided_by=_SYSTEM_ACTOR_ID,
                    decided_at=now, valid_until=now + _PENDING_VALIDITY, safe_reason_code="manual_required", case_id=None,
                )
                events.append(("business_clearance.changed", clearance.id, clearance.version, self._clearance_event_payload(clearance)))

            receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "accepted", "received_at": wire_time(now), "correlation_id": correlation_id}
            return _Outcome(receipt, 202, "aml_screening", screening.id, screening.version, events=tuple(events))

        return self._run_command(context, "receive_aml_screening", idempotency_key, body, correlation_id, mutate)

    def add_aml_case_action(
        self,
        context: ActorContext,
        aml_case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"aml_officer"}, action="add_aml_case_action", correlation_id=correlation_id)
        aml_case_id = require_uuid(aml_case_id, "aml_case_id")
        self._body(
            body,
            {"action", "reason_code", "restricted_note_asset_id", "restricted_evidence_ids", "due_at"},
            {"action", "reason_code", "restricted_note_asset_id"},
        )
        if body["action"] not in _ACTION_VALUES:
            raise ValidationError("action is not supported", field_name="action")
        if not isinstance(body["reason_code"], str) or not 1 <= len(body["reason_code"]) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        restricted_note_asset_id = require_uuid(body["restricted_note_asset_id"], "restricted_note_asset_id")
        if not self.evidence.exists(context.tenant_id, restricted_note_asset_id):
            raise ValidationError("restricted_note_asset_id does not exist", code="evidence_not_found", field_name="restricted_note_asset_id")
        evidence_ids = body.get("restricted_evidence_ids", [])
        if not isinstance(evidence_ids, list) or len(evidence_ids) > 30:
            raise ValidationError("restricted_evidence_ids must contain 0 to 30 items", field_name="restricted_evidence_ids")
        evidence_ids = [require_uuid(value, "restricted_evidence_ids") for value in evidence_ids]
        if any(not self.evidence.exists(context.tenant_id, value) for value in evidence_ids):
            raise ValidationError("restricted evidence does not exist", code="evidence_not_found", field_name="restricted_evidence_ids")
        due_at = self._parse_time(body["due_at"], "due_at") if body.get("due_at") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, aml_case_id)
            self._cas(case.version, expected_version)
            if case.state in _CASE_RESOLVED:
                raise ConflictError("case is already resolved", code="case_already_resolved")
            action = AmlCaseAction(
                id=new_id(), tenant_id=context.tenant_id, version=1, created_at=now, updated_at=now,
                case_id=case.id, actor_id=context.actor_id, action_code=body["action"], reason_code=body["reason_code"],
                restricted_note_asset_id=restricted_note_asset_id, occurred_at=now, restricted_evidence_ids=evidence_ids, due_at=due_at,
            )
            state.case_actions[(context.tenant_id, action.id)] = action
            case.maker_actor_ids.add(context.actor_id)
            case.restricted_evidence_ids = sorted(set(case.restricted_evidence_ids) | set(evidence_ids) | {restricted_note_asset_id})
            events: list[tuple[str, str, int, dict[str, Any]]] = []
            if body["action"] == "escalate":
                case.state = "escalated"
            elif body["action"] in {"request_edd", "request_information"}:
                if case.state == "open":
                    case.state = "under_review"
                case.next_review_due_at = due_at or (now + _INFORMATION_VALIDITY)
                clearance = self._append_clearance(
                    state, context.tenant_id, case.application_id, case.customer_id,
                    decision="pending", policy_version=self._case_policy_version(state, case), decided_by=context.actor_id,
                    decided_at=now, valid_until=case.next_review_due_at, safe_reason_code="information_requested", case_id=case.id,
                )
                events.append(("business_clearance.changed", clearance.id, clearance.version, self._clearance_event_payload(clearance)))
            case.version += 1
            case.updated_at = now
            events.append(("aml.case_changed", case.id, case.version, {"case_id": case.id, "screening_id": case.screening_ids[-1], "restricted_record_version": case.version}))
            return _Outcome(case.to_api(self._case_actions(case, context)), 200, "aml_case", case.id, case.version, events=tuple(events))

        return self._run_command(context, "add_aml_case_action", idempotency_key, {"aml_case_id": aml_case_id, **body}, correlation_id, mutate)

    def decide_aml_case(
        self,
        context: ActorContext,
        aml_case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"aml_supervisor"}, action="decide_aml_case", correlation_id=correlation_id)
        aml_case_id = require_uuid(aml_case_id, "aml_case_id")
        self._body(
            body,
            {"decision", "risk_level", "reason_code", "restricted_evidence_ids", "independent_reviewer_id", "expires_at"},
            {"decision", "risk_level", "reason_code", "restricted_evidence_ids"},
        )
        if body["decision"] not in _DECISION_VALUES:
            raise ValidationError("decision is not supported", field_name="decision")
        if body["risk_level"] not in _RISK_VALUES:
            raise ValidationError("risk_level is not supported", field_name="risk_level")
        if not isinstance(body["reason_code"], str) or not 1 <= len(body["reason_code"]) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        evidence_ids = body["restricted_evidence_ids"]
        if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 50:
            raise ValidationError("restricted_evidence_ids must contain 1 to 50 items", field_name="restricted_evidence_ids")
        evidence_ids = [require_uuid(value, "restricted_evidence_ids") for value in evidence_ids]
        if any(not self.evidence.exists(context.tenant_id, value) for value in evidence_ids):
            raise ValidationError("restricted evidence does not exist", code="evidence_not_found", field_name="restricted_evidence_ids")
        independent_reviewer_id = body.get("independent_reviewer_id")
        if independent_reviewer_id is not None:
            independent_reviewer_id = require_uuid(independent_reviewer_id, "independent_reviewer_id")
            if independent_reviewer_id == context.actor_id:
                raise ValidationError("independent_reviewer_id must differ from the deciding actor", field_name="independent_reviewer_id")
        expires_at = self._parse_time(body["expires_at"], "expires_at") if body.get("expires_at") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, aml_case_id)
            self._cas(case.version, expected_version)
            if case.state in _CASE_RESOLVED:
                raise ConflictError("case is already decided", code="case_already_decided")
            if context.actor_id in case.maker_actor_ids:
                raise ForbiddenError("the case maker cannot also decide this case", code="maker_checker_violation")
            case.risk_level = body["risk_level"]
            case.restricted_evidence_ids = sorted(set(case.restricted_evidence_ids) | set(evidence_ids))
            decision = body["decision"]
            if decision == "approve":
                case.state = "cleared"
                clearance_decision, safe_reason, default_validity, action_code = "approved", "case_cleared", _APPROVE_VALIDITY_DEFAULT, "decision_approve"
            elif decision == "deny":
                case.state = "rejected"
                clearance_decision, safe_reason, default_validity, action_code = "denied", "case_rejected", _DENIED_VALIDITY, "decision_deny"
            else:
                case.state = "under_review"
                clearance_decision, safe_reason, default_validity, action_code = "pending", "information_requested", _INFORMATION_VALIDITY, "decision_request_information"
            valid_until = expires_at or (now + default_validity)
            if valid_until <= now:
                raise ValidationError("expires_at must be in the future", field_name="expires_at")
            clearance = self._append_clearance(
                state, context.tenant_id, case.application_id, case.customer_id,
                decision=clearance_decision, policy_version=self._case_policy_version(state, case), decided_by=context.actor_id,
                decided_at=now, valid_until=valid_until, safe_reason_code=safe_reason, case_id=case.id,
            )
            # The checker's decision is itself a case action (maker/checker
            # trail), distinct from the maker's add_aml_case_action entries.
            action = AmlCaseAction(
                id=new_id(), tenant_id=context.tenant_id, version=1, created_at=now, updated_at=now,
                case_id=case.id, actor_id=context.actor_id, action_code=action_code, reason_code=body["reason_code"],
                restricted_note_asset_id=evidence_ids[0], occurred_at=now, restricted_evidence_ids=evidence_ids,
                independent_reviewer_id=independent_reviewer_id,
            )
            state.case_actions[(context.tenant_id, action.id)] = action
            case.version += 1
            case.updated_at = now
            events = (
                ("aml.case_changed", case.id, case.version, {"case_id": case.id, "screening_id": case.screening_ids[-1], "restricted_record_version": case.version}),
                ("business_clearance.changed", clearance.id, clearance.version, self._clearance_event_payload(clearance)),
            )
            return _Outcome(clearance.to_api([], as_of=now), 200, "aml_case", case.id, case.version, events=events)

        return self._run_command(context, "decide_aml_case", idempotency_key, {"aml_case_id": aml_case_id, **body}, correlation_id, mutate)

    # ---- queries ------------------------------------------------------------

    def list_aml_cases(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        is_full_access = self._staff(context, {"aml_officer", "aml_supervisor"})
        if not is_full_access and not self._staff(context, {"internal_auditor"}):
            self._audit_failure(context, "list_aml_cases", correlation_id, "forbidden")
            raise ForbiddenError("staff role is not allowed for this operation")
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in {"open", "under_review", "escalated", "cleared", "rejected", "closed"}:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            # internal_auditor never gets blanket case visibility: the
            # listing is scoped to only the case_ids they hold an active,
            # time-boxed grant for (REQ-M04-010), not the full queue.
            granted_case_ids: set[str] | None = None
            if not is_full_access:
                now = self._clock()
                granted_case_ids = {
                    row.case_id for (tenant, _), row in state.read_grants.items()
                    if tenant == context.tenant_id and row.auditor_actor_id == context.actor_id and row.expires_at > now
                }
            rows = sorted(
                (
                    item for (tenant, _), item in state.cases.items()
                    if tenant == context.tenant_id
                    and item.opened_at <= snapshot_at
                    and (not state_filter or item.state == state_filter)
                    and (not cursor or item.id > cursor)
                    and (granted_case_ids is None or item.id in granted_case_ids)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_aml_cases", "aml_case", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._case_actions(item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    def get_aml_case(self, context: ActorContext, aml_case_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        aml_case_id = require_uuid(aml_case_id, "aml_case_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            # Authorize using only the case_id, before the case lookup: an
            # unauthorized actor must get the same Forbidden either way,
            # never a different error depending on whether the case
            # actually exists (UAT-M04-003 / SA-SD §12 existence oracle).
            self._authorize_case_read(state, context, aml_case_id)
            case = self._stored_case(state, context.tenant_id, aml_case_id)
            self._audit(state, context, "get_aml_case", "aml_case", case.id, case.version, correlation_id, "succeeded")
            return ServiceResponse(case.to_api(self._case_actions(case, context)), etag=self._etag(case.version))

        try:
            return self.repository.atomic(query)
        except DomainError as exc:
            self._audit_failure(context, "get_aml_case", correlation_id, exc.code, aml_case_id)
            raise

    def get_business_clearance(self, context: ActorContext, application_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize_staff(context, {"company_acceptor"}, action="get_business_clearance", correlation_id=correlation_id)
        application_id = require_uuid(application_id, "application_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def query(state: StoreState) -> ServiceResponse:
            clearance = self._latest_clearance(state, context.tenant_id, application_id)
            self._audit(state, context, "get_business_clearance", "business_clearance", clearance.id if clearance else None, clearance.version if clearance else None, correlation_id, "succeeded")
            if clearance is None:
                synthetic = BusinessClearance(
                    id=new_id(), tenant_id=context.tenant_id, version=1, created_at=now, updated_at=now,
                    application_id=application_id, customer_id=self.application_access.customer_for(context.tenant_id, application_id) or application_id,
                    decision="pending", policy_version="awaiting_screening-v1", decided_by=_SYSTEM_ACTOR_ID,
                    decided_at=now, valid_until=now + _PENDING_VALIDITY, safe_reason_code="awaiting_screening",
                )
                synthetic.decided_at = None  # type: ignore[assignment]
                return ServiceResponse(self._nullable_clearance_payload(synthetic, now))
            return ServiceResponse(clearance.to_api([], as_of=now), etag=self._etag(clearance.version))

        return self.repository.atomic(query)

    # ---- internal administrative capability (not yet a public route) -----

    def grant_case_read(
        self,
        context: ActorContext,
        case_id: str,
        auditor_actor_id: str,
        *,
        reason_code: str,
        expires_at: datetime,
    ) -> AmlCaseReadGrant:
        """Fail-closed internal grant pending shared API/event registration.

        Internal audit access to a specific restricted case must be explicitly
        time-boxed; it is never derived from the aml_officer/aml_supervisor role.
        """
        self._authorize_staff(context, {"aml_supervisor"})
        case_id = require_uuid(case_id, "case_id")
        auditor_actor_id = require_uuid(auditor_actor_id, "auditor_actor_id")
        if not reason_code.strip():
            raise ValidationError("reason_code is required", field_name="reason_code")
        now = self._clock()
        if expires_at <= now:
            raise ValidationError("expires_at must be in the future", field_name="expires_at")
        grant = AmlCaseReadGrant(
            id=new_id(), tenant_id=context.tenant_id, case_id=case_id, auditor_actor_id=auditor_actor_id,
            granted_by=context.actor_id, reason_code=reason_code[:80], created_at=now, expires_at=expires_at,
        )

        def save(state: StoreState) -> AmlCaseReadGrant:
            self._stored_case(state, context.tenant_id, case_id)
            state.read_grants[(context.tenant_id, grant.id)] = grant
            return grant

        return self.repository.atomic(save)

    def apply_legal_hold(self, context: ActorContext, case_id: str, *, reason_code: str) -> AmlCaseFile:
        """Fail-closed internal retention capability pending shared core.legal_hold API."""
        self._authorize_staff(context, {"aml_supervisor"})
        case_id = require_uuid(case_id, "case_id")
        if not reason_code.strip():
            raise ValidationError("reason_code is required", field_name="reason_code")

        def apply(state: StoreState) -> AmlCaseFile:
            case = self._stored_case(state, context.tenant_id, case_id)
            case.legal_hold = True
            case.legal_hold_reason_code = reason_code[:80]
            case.updated_at = self._clock()
            return case

        return self.repository.atomic(apply)

    # ---- command transaction helpers --------------------------------------

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
                return _Outcome(receipt.response_value, receipt.response_status, "replay", None, None, receipt.error), True
            outcome = mutate(state)
            self._audit(
                state, context, operation, outcome.aggregate_type, outcome.aggregate_id, outcome.aggregate_version,
                correlation_id, outcome.error.code if outcome.error else "succeeded", outcome.error.code if outcome.error else None,
            )
            if not outcome.error:
                for event_type, aggregate_id, aggregate_version, payload in outcome.events:
                    self._emit(
                        state, event_type=event_type, tenant_id=context.tenant_id, aggregate_id=aggregate_id,
                        aggregate_version=aggregate_version, correlation_id=correlation_id,
                        idempotency_key=f"{key}:{event_type}:{aggregate_id}", payload=payload, now=self._clock(),
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
        return ServiceResponse(outcome.value, outcome.status, etag, replayed)

    def _emit(
        self, state: StoreState, *, event_type: str, tenant_id: str, aggregate_id: str, aggregate_version: int,
        correlation_id: str, idempotency_key: str, payload: dict[str, Any], now: datetime,
    ) -> None:
        aggregate_type = "aml_case" if event_type == "aml.case_changed" else "business_clearance"
        classification = "aml_restricted" if event_type == "aml.case_changed" else "customer_confidential"
        sequence = 1 + max(
            (event.sequence_no for (tenant, _), event in state.outbox.items() if tenant == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(), event_type=event_type, tenant_id=tenant_id, aggregate_type=aggregate_type,
            aggregate_id=aggregate_id, aggregate_version=aggregate_version, sequence_no=sequence,
            correlation_id=correlation_id, causation_id=None, idempotency_key=idempotency_key,
            classification=classification, payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    # ---- aggregate helpers --------------------------------------------------

    def _append_clearance(
        self, state: StoreState, tenant_id: str, application_id: str, customer_id: str, *,
        decision: str, policy_version: str, decided_by: str, decided_at: datetime, valid_until: datetime,
        safe_reason_code: str, case_id: str | None,
    ) -> BusinessClearance:
        clearance = BusinessClearance(
            id=new_id(), tenant_id=tenant_id, version=1 + len(state.clearance_by_application.get((tenant_id, application_id), [])),
            created_at=decided_at, updated_at=decided_at, application_id=application_id, customer_id=customer_id,
            decision=decision, policy_version=policy_version, decided_by=decided_by, decided_at=decided_at,
            valid_until=valid_until, safe_reason_code=safe_reason_code, case_id=case_id,
        )
        state.clearances[(tenant_id, clearance.id)] = clearance
        state.clearance_by_application.setdefault((tenant_id, application_id), []).append(clearance.id)
        return clearance

    @staticmethod
    def _latest_clearance(state: StoreState, tenant_id: str, application_id: str) -> BusinessClearance | None:
        ids = state.clearance_by_application.get((tenant_id, application_id))
        if not ids:
            return None
        return state.clearances[(tenant_id, ids[-1])]

    @staticmethod
    def _clearance_event_payload(clearance: BusinessClearance) -> dict[str, Any]:
        return {
            "clearance_id": clearance.id,
            "application_id": clearance.application_id,
            "decision": clearance.decision,
            "expires_at": wire_time(clearance.valid_until) if clearance.valid_until else None,
        }

    @staticmethod
    def _nullable_clearance_payload(clearance: BusinessClearance, now: datetime) -> dict[str, Any]:
        return {
            "id": clearance.id,
            "tenant_id": clearance.tenant_id,
            "version": clearance.version,
            "created_at": wire_time(clearance.created_at),
            "updated_at": wire_time(clearance.updated_at),
            "allowed_actions": [],
            "application_id": clearance.application_id,
            "customer_id": clearance.customer_id,
            "decision": "pending",
            "decided_at": None,
            "expires_at": None,
            "policy_version": clearance.policy_version,
        }

    @staticmethod
    def _blocking_case_for(state: StoreState, tenant_id: str, application_id: str, customer_id: str) -> AmlCaseFile | None:
        return next(
            (
                case for (tenant, _), case in state.cases.items()
                if tenant == tenant_id
                and case.application_id == application_id
                and case.customer_id == customer_id
                and case.state in _CASE_BLOCKS_AUTO_CLEAR
            ),
            None,
        )

    def _open_or_attach_case(
        self, state: StoreState, context: ActorContext, screening: AmlScreening, restricted_evidence_id: str, now: datetime,
    ) -> AmlCaseFile:
        existing = next(
            (
                case for (tenant, _), case in state.cases.items()
                if tenant == context.tenant_id
                and case.application_id == screening.application_id
                and case.customer_id == screening.customer_id
                and case.state not in _CASE_RESOLVED
            ),
            None,
        )
        if existing:
            if screening.id not in existing.screening_ids:
                existing.screening_ids.append(screening.id)
            if restricted_evidence_id not in existing.restricted_evidence_ids:
                existing.restricted_evidence_ids.append(restricted_evidence_id)
            existing.version += 1
            existing.updated_at = now
            screening.case_id = existing.id
            return existing
        case = AmlCaseFile(
            id=new_id(), tenant_id=context.tenant_id, version=1, created_at=now, updated_at=now,
            customer_id=screening.customer_id, application_id=screening.application_id,
            screening_ids=[screening.id], opened_at=now, state="open", risk_level="standard",
            restricted_evidence_ids=[restricted_evidence_id],
        )
        state.cases[(context.tenant_id, case.id)] = case
        screening.case_id = case.id
        return case

    @staticmethod
    def _case_policy_version(state: StoreState, case: AmlCaseFile) -> str:
        for screening_id in reversed(case.screening_ids):
            screening = state.screenings.get((case.tenant_id, screening_id))
            if screening:
                return screening.policy_version
        return "case-decision-v1"

    # ---- authorization and validation --------------------------------------

    def _authorize_declaration(self, context: ActorContext, customer_id: str, *, action: str, correlation_id: str | None) -> None:
        if context.principal_type == "customer":
            if context.customer_id == customer_id:
                return
            self._audit_failure(context, action, correlation_id, "resource_not_found")
            raise NotFoundError("application was not found")
        if self._staff(context, {"aml_officer"}):
            return
        self._audit_failure(context, action, correlation_id, "forbidden")
        raise ForbiddenError("actor cannot submit this AML declaration")

    def _authorize_staff(self, context: ActorContext, roles: set[str], *, action: str | None = None, correlation_id: str | None = None) -> None:
        if self._staff(context, roles):
            return
        if action:
            self._audit_failure(context, action, correlation_id, "forbidden")
        raise ForbiddenError("staff role is not allowed for this operation")

    def _authorize_service(self, context: ActorContext, roles: set[str], *, action: str | None = None, correlation_id: str | None = None) -> None:
        if context.principal_type == "service" and bool(context.roles & roles):
            return
        if action:
            self._audit_failure(context, action, correlation_id, "forbidden")
        raise ForbiddenError("integration adapter role is not allowed for this operation")

    def _authorize_case_read(self, state: StoreState, context: ActorContext, case_id: str) -> None:
        if self._staff(context, {"aml_officer", "aml_supervisor"}):
            return
        if self._staff(context, {"internal_auditor"}):
            grant = next(
                (
                    row for (tenant, _), row in state.read_grants.items()
                    if tenant == context.tenant_id and row.case_id == case_id and row.auditor_actor_id == context.actor_id
                ),
                None,
            )
            if grant and grant.expires_at > self._clock():
                return
        raise ForbiddenError("actor cannot read this AML case")

    def _authorize_webhook(self, webhook_timestamp: str | None, webhook_nonce: str | None, integration_key_id: str | None) -> None:
        if not isinstance(webhook_timestamp, str) or not _WEBHOOK_TIMESTAMP_RE.fullmatch(webhook_timestamp):
            raise UnauthorizedError("X-Webhook-Timestamp must be a signed 10-digit Unix timestamp", code="webhook_signature_invalid")
        signed_at = datetime.fromtimestamp(int(webhook_timestamp), tz=timezone.utc)
        if abs((self._clock() - signed_at).total_seconds()) > _WEBHOOK_SKEW_SECONDS:
            raise UnauthorizedError("X-Webhook-Timestamp is outside the allowed clock skew", code="webhook_signature_expired")
        if not isinstance(webhook_nonce, str) or not 16 <= len(webhook_nonce) <= 128:
            raise UnauthorizedError("X-Webhook-Nonce must contain 16 to 128 characters", code="webhook_signature_invalid")
        if not isinstance(integration_key_id, str) or not 8 <= len(integration_key_id) <= 128:
            raise UnauthorizedError("X-Integration-Key-Id must contain 8 to 128 characters", code="webhook_signature_invalid")

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

    @staticmethod
    def _stored_case(state: StoreState, tenant_id: str, case_id: str) -> AmlCaseFile:
        case = state.cases.get((tenant_id, case_id))
        if not case:
            raise NotFoundError("AML case was not found")
        return case

    def _case_actions(self, case: AmlCaseFile, context: ActorContext) -> list[str]:
        if case.state in _CASE_RESOLVED:
            return []
        actions: list[str] = []
        if self._staff(context, {"aml_officer"}):
            actions.append("add_aml_case_action")
        if self._staff(context, {"aml_supervisor"}) and context.actor_id not in case.maker_actor_ids:
            actions.append("decide_aml_case")
        return sorted(set(actions))

    def _audit(
        self, state: StoreState, context: ActorContext, action: str, aggregate_type: str, aggregate_id: str | None,
        aggregate_version: int | None, correlation_id: str, result: str, reason_code: str | None = None,
    ) -> None:
        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=f"m04.{action}", aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str, aggregate_id: str | None = None) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security" if aggregate_id is None else "aml_case", aggregate_id, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
