from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable

from .domain import (
    ActorContext,
    AuditEvent,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    ForbiddenError,
    IdempotencyReceipt,
    KycSubmission,
    NotFoundError,
    OutboxEvent,
    PreconditionRequiredError,
    RefreshCase,
    StaleVersionError,
    SubmissionDocument,
    SuitabilityAssessment,
    ValidationError,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import ApplicationGatewayPort, CustomerGatewayPort
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9]\d{0,13})(?:\.\d{1,6})?$")
_ANSWER_TYPES = {"text", "choice", "multi_choice", "number", "boolean", "date"}
_DOCUMENT_TYPES = {
    "identity_front", "identity_back", "passport", "address_evidence",
    "corporate_registry", "beneficial_owner_evidence", "other_approved",
}
_EXTENSION_MEDIA_TYPE = {
    "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
}
_VERIFICATION_METHODS = {"approved_remote_identity", "verified_e_signature", "manual_document_review", "approved_equivalent"}
_DECISIONS = {"approve", "reject", "request_information"}
_SUITABILITY_DECISIONS = {"suitable", "conditional", "not_suitable"}
_REFRESH_TRIGGERS = {"scheduled", "identity_change", "risk_change", "service_change", "regulatory_request"}

SUPPORTED_FORM_VERSIONS = {"m03-kyc-form-v1"}
REQUIRED_QUESTIONS_BY_FORM: dict[str, frozenset[str]] = {
    "m03-kyc-form-v1": frozenset({"occupation", "identity_basis"}),
}
SUPPORTED_QUESTIONNAIRE_VERSIONS = {"m03-suitability-questionnaire-v1"}
SUPPORTED_RULE_VERSIONS = {"m03-suitability-rule-v1"}
_HIGH_RISK_SERVICE_CODES = frozenset({"leveraged_fx", "derivatives_advisory"})

MODULE_ID = "M03"


def compute_suitability(answers: dict[str, dict[str, Any]], service_codes: list[str]) -> tuple[str, list[str], str]:
    """Pure, deterministic rule evaluation. Never an LLM output; same inputs -> same result."""
    risk_tolerance = "low"
    choice = answers.get("risk_tolerance")
    if choice and choice["answer_type"] == "choice" and choice["value"]:
        risk_tolerance = choice["value"][0]
    experience_years = Decimal("0")
    number = answers.get("investment_experience_years")
    if number and number["answer_type"] == "number":
        try:
            experience_years = Decimal(number["value"])
        except InvalidOperation:
            experience_years = Decimal("0")
    requests_high_risk = bool(_HIGH_RISK_SERVICE_CODES & set(service_codes))
    if requests_high_risk and risk_tolerance == "low":
        return "not_suitable", [], "risk_tolerance_mismatch"
    if requests_high_risk and risk_tolerance == "medium" and experience_years < Decimal("3"):
        return "conditional", ["enhanced_disclosure_required"], "limited_experience_for_high_risk_service"
    return "suitable", [], "rule_pass"


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


class KycSuitabilityService:
    """M03 command/query application service.

    Every state-changing operation runs through one repository callback so
    domain state, audit, outbox and the idempotency receipt commit atomically.
    Original customer answers and reviewer evidence are never overwritten in
    place; a resubmission while under review opens a new submission_version.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        application_gateway: ApplicationGatewayPort,
        customer_gateway: CustomerGatewayPort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False) for adapter in (repository, application_gateway, customer_gateway)
        ):
            raise RuntimeError("fake M03 adapters are forbidden in production")
        self.repository = repository
        self.application_gateway = application_gateway
        self.customer_gateway = customer_gateway
        self._clock = clock

    # ---- commands ---------------------------------------------------------

    def create_kyc_submission(
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
        self._body(body, {"form_version", "customer_signature_id"}, {"form_version", "customer_signature_id"})
        form_version = self._form_version(body["form_version"])
        customer_signature_id = require_uuid(body["customer_signature_id"], "customer_signature_id")
        correlation_id = self._correlation(correlation_id)
        signed = self._get_signed_application(context, application_id, correlation_id)
        self._authorize_submission_subject(context, signed.customer_id, {"kyc_reviewer"})
        if signed.customer_signature_id != customer_signature_id:
            raise ConflictError("customer_signature_id does not match the signed application", code="signature_reference_mismatch")
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            self._cas(signed.version, expected_version)
            existing = self._latest_submission(state, context.tenant_id, application_id)
            if existing and existing.status not in {"approved", "rejected"}:
                return self._submission_outcome(existing, context, 201)
            if existing and existing.status == "approved":
                raise ConflictError(
                    "submission already approved; request a due-diligence refresh for a new cycle",
                    code="submission_already_approved",
                )
            next_version = (existing.submission_version + 1) if existing else 1
            submission = KycSubmission(
                id=new_id(),
                tenant_id=context.tenant_id,
                application_id=application_id,
                customer_id=signed.customer_id,
                submission_version=next_version,
                form_version=form_version,
                status="not_started",
                created_at=now,
                updated_at=now,
            )
            state.submissions[(context.tenant_id, submission.id)] = submission
            return self._submission_outcome(submission, context, 201)

        return self._run_command(context, "create_kyc_submission", idempotency_key, {"application_id": application_id, **body}, correlation_id, mutate)

    def request_kyc_upload(
        self,
        context: ActorContext,
        submission_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        submission_id = require_uuid(submission_id, "submission_id")
        self._body(
            body,
            {"document_type", "filename_extension", "media_type", "size_bytes", "sha256"},
            {"document_type", "filename_extension", "media_type", "size_bytes", "sha256"},
        )
        if body["document_type"] not in _DOCUMENT_TYPES:
            raise ValidationError("document_type is not supported", field_name="document_type")
        extension = body["filename_extension"]
        if extension not in _EXTENSION_MEDIA_TYPE:
            raise ValidationError("filename_extension is not supported", field_name="filename_extension")
        if body["media_type"] != _EXTENSION_MEDIA_TYPE[extension]:
            raise ValidationError("media_type does not match filename_extension", code="mime_mismatch", field_name="media_type")
        size_bytes = body["size_bytes"]
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or not 1 <= size_bytes <= 20_971_520:
            raise ValidationError("size_bytes must be between 1 and 20971520", field_name="size_bytes")
        sha_value = str(body["sha256"])
        if not _SHA_RE.fullmatch(sha_value):
            raise ValidationError("sha256 must be lowercase SHA-256", field_name="sha256")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            submission = self._stored_submission(state, context.tenant_id, submission_id)
            self._authorize_submission_subject(context, submission.customer_id, {"kyc_reviewer"})
            self._cas(submission.version, expected_version)
            if submission.status in {"approved", "rejected"}:
                raise ConflictError("submission is closed for new uploads", code="submission_closed")
            document = SubmissionDocument(
                id=new_id(),
                tenant_id=context.tenant_id,
                submission_id=submission_id,
                customer_id=submission.customer_id,
                document_type=body["document_type"],
                filename_extension=extension,
                media_type=body["media_type"],
                size_bytes=size_bytes,
                sha256=sha_value,
                created_at=now,
                updated_at=now,
            )
            state.documents[(context.tenant_id, document.id)] = document
            submission.document_ids.append(document.id)
            submission.version += 1
            submission.updated_at = now
            ticket = {
                "asset_id": document.id,
                "upload_url": f"https://uploads.longhao.internal/quarantine/{document.id}",
                "expires_at": wire_time(now + timedelta(minutes=15)),
                "max_size_bytes": 20_971_520,
                "scan_required": True,
                "classification": document.classification,
            }
            return _Outcome(ticket, 201, "kyc_submission", submission_id, submission.version)

        return self._run_command(context, "request_kyc_upload", idempotency_key, {"submission_id": submission_id, **body}, correlation_id, mutate)

    def submit_kyc(
        self,
        context: ActorContext,
        submission_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        submission_id = require_uuid(submission_id, "submission_id")
        self._body(
            body,
            {"form_version", "answers", "document_ids", "declaration_consent_id"},
            {"form_version", "answers", "document_ids", "declaration_consent_id"},
        )
        form_version = self._form_version(body["form_version"])
        answers = self._validate_answers(body["answers"])
        document_ids_raw = body["document_ids"]
        if not isinstance(document_ids_raw, list) or not 1 <= len(document_ids_raw) <= 30:
            raise ValidationError("document_ids must contain 1 to 30 items", field_name="document_ids")
        document_ids = [require_uuid(value, "document_ids") for value in document_ids_raw]
        declaration_consent_id = require_uuid(body["declaration_consent_id"], "declaration_consent_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            submission = self._stored_submission(state, context.tenant_id, submission_id)
            self._authorize_submission_subject(context, submission.customer_id, {"kyc_reviewer"})
            self._cas(submission.version, expected_version)
            if submission.status in {"approved", "rejected"}:
                raise ConflictError("submission is closed", code="submission_closed")
            if submission.form_version != form_version:
                raise ValidationError("form_version does not match the open submission", field_name="form_version")
            for document_id in document_ids:
                document = state.documents.get((context.tenant_id, document_id))
                if not document or document.submission_id != submission.id:
                    raise ValidationError("document_ids must reference uploads for this submission", field_name="document_ids")
                if document.scan_status == "rejected":
                    raise ValidationError(
                        "a quarantined document cannot be submitted for review", code="document_quarantined", field_name="document_ids"
                    )

            if submission.status in {"not_started", "in_progress"}:
                target = submission
            else:
                if not self._is_latest_submission(state, context.tenant_id, submission):
                    raise ConflictError("a newer submission version already exists", code="stale_submission")
                target = KycSubmission(
                    id=new_id(),
                    tenant_id=context.tenant_id,
                    application_id=submission.application_id,
                    customer_id=submission.customer_id,
                    submission_version=submission.submission_version + 1,
                    form_version=form_version,
                    status="in_progress",
                    created_at=now,
                    updated_at=now,
                )
                state.submissions[(context.tenant_id, target.id)] = target

            missing = self._missing_fields(form_version, answers, document_ids)
            target.answers = answers
            target.answer_snapshot_asset_id = new_id()
            target.answer_sha256 = canonical_hash(answers)
            target.document_ids = document_ids
            target.declaration_consent_id = declaration_consent_id
            target.missing_field_codes = missing
            target.status = "incomplete" if missing else "submitted"
            target.submitted_at = now
            target.version += 1 if target is submission else 0
            target.updated_at = now
            return _Outcome(target.to_api(self._submission_actions(target, context)), 202, "kyc_submission", target.id, target.version)

        return self._run_command(context, "submit_kyc", idempotency_key, {"submission_id": submission_id, **body}, correlation_id, mutate)

    def decide_kyc(
        self,
        context: ActorContext,
        submission_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        submission_id = require_uuid(submission_id, "submission_id")
        self._body(
            body,
            {"decision", "identity_verification_method", "verification_evidence_ids", "missing_field_codes", "reason_code", "next_review_due_at"},
            {"decision", "identity_verification_method", "verification_evidence_ids", "reason_code"},
        )
        if body["decision"] not in _DECISIONS:
            raise ValidationError("decision is not supported", field_name="decision")
        if body["identity_verification_method"] not in _VERIFICATION_METHODS:
            raise ValidationError("identity_verification_method is not supported", field_name="identity_verification_method")
        evidence_raw = body["verification_evidence_ids"]
        if not isinstance(evidence_raw, list) or not 1 <= len(evidence_raw) <= 30:
            raise ValidationError("verification_evidence_ids must contain 1 to 30 items", field_name="verification_evidence_ids")
        verification_evidence_ids = [require_uuid(value, "verification_evidence_ids") for value in evidence_raw]
        reason_code = self._reason_code(body["reason_code"])
        missing_field_codes = body.get("missing_field_codes", [])
        if not isinstance(missing_field_codes, list) or any(not isinstance(value, str) for value in missing_field_codes):
            raise ValidationError("missing_field_codes must be an array of strings", field_name="missing_field_codes")
        next_review_due_at = self._parse_time(body["next_review_due_at"], "next_review_due_at") if body.get("next_review_due_at") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            submission = self._stored_submission(state, context.tenant_id, submission_id)
            self._authorize_staff(context, {"kyc_reviewer"})
            self._cas(submission.version, expected_version)
            if not self._is_latest_submission(state, context.tenant_id, submission):
                raise ConflictError("a newer submission version supersedes this one; evaluate it instead", code="stale_submission")
            if submission.status not in {"submitted", "under_review", "incomplete"}:
                raise ConflictError("submission is not awaiting a decision", code="submission_not_awaiting_decision")

            wire_decision = {"approve": "approved", "reject": "rejected", "request_information": "incomplete"}[body["decision"]]
            if body["decision"] == "request_information":
                submission.status = "incomplete"
                submission.missing_field_codes = sorted(set(submission.missing_field_codes) | set(missing_field_codes))
                submission.next_review_due_at = None
            elif body["decision"] == "reject":
                submission.status = "rejected"
                submission.next_review_due_at = None
            else:
                if submission.missing_field_codes:
                    raise ConflictError("submission still has missing fields", code="submission_incomplete")
                submission.status = "approved"
                submission.next_review_due_at = next_review_due_at
            submission.decision_reason_code = reason_code
            submission.identity_verification_method = body["identity_verification_method"]
            submission.verification_evidence_ids = verification_evidence_ids
            submission.reviewer_id = context.actor_id
            submission.reviewed_at = now
            submission.version += 1
            submission.updated_at = now
            self._emit(
                state,
                event_type="kyc.decision_recorded",
                tenant_id=context.tenant_id,
                aggregate_type="kyc_submission",
                aggregate_id=submission.id,
                aggregate_version=submission.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:decision-recorded",
                payload={
                    "submission_id": submission.id,
                    "application_id": submission.application_id,
                    "decision": wire_decision,
                    "next_review_due_at": wire_time(submission.next_review_due_at) if submission.next_review_due_at else None,
                },
                now=now,
            )
            return self._submission_outcome(submission, context, 200)

        return self._run_command(context, "decide_kyc", idempotency_key, {"submission_id": submission_id, **body}, correlation_id, mutate)

    def assess_suitability(
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
            {"kyc_submission_id", "questionnaire_version", "rule_version", "answers", "service_codes", "decision", "conditions", "reason_code", "next_review_due_at"},
            {"kyc_submission_id", "questionnaire_version", "rule_version", "answers", "service_codes", "decision", "conditions", "reason_code", "next_review_due_at"},
        )
        self._authorize_staff(context, {"suitability_reviewer"})
        kyc_submission_id = require_uuid(body["kyc_submission_id"], "kyc_submission_id")
        if body["questionnaire_version"] not in SUPPORTED_QUESTIONNAIRE_VERSIONS:
            raise ValidationError("questionnaire_version is not supported", field_name="questionnaire_version")
        if body["rule_version"] not in SUPPORTED_RULE_VERSIONS:
            raise ValidationError("rule_version is not supported", field_name="rule_version")
        answers = self._validate_answers(body["answers"])
        service_codes = body["service_codes"]
        if not isinstance(service_codes, list) or not 1 <= len(service_codes) <= 20 or any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in service_codes):
            raise ValidationError("service_codes must contain 1 to 20 short codes", field_name="service_codes")
        requested_decision = body["decision"]
        if requested_decision not in _SUITABILITY_DECISIONS:
            raise ValidationError("decision is not supported", field_name="decision")
        conditions = body["conditions"]
        if not isinstance(conditions, list) or len(conditions) > 20 or any(not isinstance(v, str) or len(v) > 300 for v in conditions):
            raise ValidationError("conditions must be an array of short strings", field_name="conditions")
        if requested_decision == "conditional" and not conditions:
            raise ValidationError("conditional suitability requires at least one condition", field_name="conditions")
        reason_code = self._reason_code(body["reason_code"])
        next_review_due_at = self._parse_time(body["next_review_due_at"], "next_review_due_at")
        correlation_id = self._correlation(correlation_id)
        signed = self._get_signed_application(context, application_id, correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            self._cas(signed.version, expected_version)
            submission = self._stored_submission(state, context.tenant_id, kyc_submission_id)
            if submission.application_id != application_id:
                raise ValidationError("kyc_submission_id does not belong to this application", field_name="kyc_submission_id")
            if submission.status != "approved":
                raise ConflictError("suitability requires an approved kyc submission", code="suitability_requires_approved_kyc")
            existing = self._latest_assessment_for_application(state, context.tenant_id, application_id)

            computed_decision, computed_conditions, computed_reason = compute_suitability(answers, service_codes)
            is_override = requested_decision != computed_decision
            if is_override:
                self._authorize_staff(context, {"suitability_authorizer"})

            assessment_id = existing.id if existing else new_id()
            next_version = (existing.version + 1) if existing else 1
            assessment = SuitabilityAssessment(
                id=assessment_id,
                tenant_id=context.tenant_id,
                application_id=application_id,
                customer_id=submission.customer_id,
                kyc_submission_id=kyc_submission_id,
                questionnaire_version=body["questionnaire_version"],
                rule_version=body["rule_version"],
                version=next_version,
                status=requested_decision,
                answers_sha256=canonical_hash(answers),
                service_codes=sorted(service_codes),
                computed_decision=computed_decision,
                conditions=list(conditions),
                reason_code=reason_code,
                overridden=is_override,
                assessed_by=context.actor_id,
                assessed_at=now,
                next_review_due_at=next_review_due_at,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            state.assessments[(context.tenant_id, assessment.id)] = assessment
            if is_override:
                state.suitability_overrides[(context.tenant_id, new_id())] = {
                    "assessment_id": assessment.id,
                    "authorizer_id": context.actor_id,
                    "computed_decision": computed_decision,
                    "resulting_decision": requested_decision,
                    "reason_code": reason_code,
                    "decided_at": now,
                }
            self._emit(
                state,
                event_type="suitability.assessed",
                tenant_id=context.tenant_id,
                aggregate_type="suitability_assessment",
                aggregate_id=assessment.id,
                aggregate_version=assessment.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:assessed",
                payload={
                    "assessment_id": assessment.id,
                    "application_id": application_id,
                    "state": assessment.status,
                    "rule_version": assessment.rule_version,
                },
                now=now,
            )
            return self._assessment_outcome(assessment, context, 201 if existing is None else 200)

        return self._run_command(context, "assess_suitability", idempotency_key, {"application_id": application_id, **body}, correlation_id, mutate)

    def request_due_diligence_refresh(
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
        self._authorize_submission_subject(context, customer_id, {"kyc_reviewer"})
        self._body(body, {"trigger", "reason_code", "due_at", "form_version"}, {"trigger", "reason_code", "due_at", "form_version"})
        if body["trigger"] not in _REFRESH_TRIGGERS:
            raise ValidationError("trigger is not supported", field_name="trigger")
        reason_code = self._reason_code(body["reason_code"])
        due_at = self._parse_time(body["due_at"], "due_at")
        form_version = self._form_version(body["form_version"])
        correlation_id = self._correlation(correlation_id)
        customer_version = self._get_customer_version(context, customer_id, correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            self._cas(customer_version, expected_version)
            submission = self._latest_submission_for_customer(state, context.tenant_id, customer_id)
            if submission is None:
                raise NotFoundError("customer has no kyc submission to refresh")
            if submission.status != "approved":
                raise ConflictError("only an approved kyc submission can be scheduled for refresh", code="refresh_requires_approved_kyc")
            submission.status = "refresh_required"
            submission.version += 1
            submission.updated_at = now
            case = RefreshCase(
                id=new_id(),
                tenant_id=context.tenant_id,
                customer_id=customer_id,
                submission_id=submission.id,
                trigger=body["trigger"],
                reason_code=reason_code,
                due_at=due_at,
                next_form_version=form_version,
                requested_by=context.actor_id,
                created_at=now,
                updated_at=now,
            )
            state.refresh_cases[(context.tenant_id, case.id)] = case
            assessment = self._latest_assessment_for_application(state, context.tenant_id, submission.application_id)
            if assessment and assessment.status in {"suitable", "conditional"}:
                assessment.status = "reassessment_due"
                assessment.version += 1
                assessment.updated_at = now
            self._emit(
                state,
                event_type="kyc.refresh_required",
                tenant_id=context.tenant_id,
                aggregate_type="kyc_submission",
                aggregate_id=submission.id,
                aggregate_version=submission.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:refresh-required",
                payload={
                    "customer_id": customer_id,
                    "submission_id": submission.id,
                    "refresh_case_id": case.id,
                    "due_at": wire_time(due_at),
                },
                now=now,
            )
            return self._submission_outcome(submission, context, 201)

        return self._run_command(context, "request_due_diligence_refresh", idempotency_key, {"customer_id": customer_id, **body}, correlation_id, mutate)

    # ---- queries ------------------------------------------------------------

    def get_kyc_submission(self, context: ActorContext, submission_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        submission_id = require_uuid(submission_id, "submission_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            submission = self._stored_submission(state, context.tenant_id, submission_id)
            if self._staff(context, {"kyc_reviewer"}):
                view = submission.to_api(self._submission_actions(submission, context))
            elif self._staff(context, {"sales", "service_agent"}):
                view = submission.masked_business_view(self._submission_actions(submission, context))
            else:
                raise ForbiddenError("actor cannot view this kyc submission")
            self._audit(state, context, "get_kyc_submission", "kyc_submission", submission.id, submission.version, correlation_id, "succeeded")
            return ServiceResponse(view, etag=self._etag(submission.version))

        try:
            return self.repository.atomic(query)
        except DomainError as exc:
            self._audit_failure(context, "get_kyc_submission", correlation_id, exc.code, submission_id)
            raise

    def get_document_status(self, context: ActorContext, asset_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        asset_id = require_uuid(asset_id, "asset_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            document = self._stored_document_by_asset(state, context.tenant_id, asset_id)
            if context.principal_type == "customer":
                if context.customer_id != document.customer_id:
                    raise NotFoundError("document was not found")
            elif not self._staff(context, {"kyc_reviewer"}):
                raise ForbiddenError("actor cannot view this document")
            self._audit(state, context, "get_document_status", "submission_document", document.id, document.version, correlation_id, "succeeded")
            return ServiceResponse(document.to_status_api())

        try:
            return self.repository.atomic(query)
        except DomainError as exc:
            self._audit_failure(context, "get_document_status", correlation_id, exc.code, asset_id)
            raise

    # ---- command transaction helpers ----------------------------------------

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

    # ---- authorization, mapping and validation -------------------------------

    def _authorize_staff(self, context: ActorContext, roles: set[str]) -> None:
        if self._staff(context, roles):
            return
        raise ForbiddenError("staff role is not allowed for this operation")

    def _authorize_submission_subject(self, context: ActorContext, customer_id: str, staff_roles: set[str]) -> None:
        if context.principal_type == "customer":
            if context.customer_id == customer_id:
                return
            raise NotFoundError("resource was not found")
        if self._staff(context, staff_roles):
            return
        if context.principal_type == "service" and "m03:*" in context.scopes:
            return
        raise ForbiddenError("actor cannot access this customer's kyc records")

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
    def _form_version(value: Any) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 80:
            raise ValidationError("form_version is invalid", field_name="form_version")
        if value not in SUPPORTED_FORM_VERSIONS:
            raise ValidationError("form_version is not supported", field_name="form_version")
        return value

    @staticmethod
    def _reason_code(value: Any) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        return value

    def _validate_answers(self, raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, list) or not 1 <= len(raw) <= 150:
            raise ValidationError("answers must contain 1 to 150 items", field_name="answers")
        normalized: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValidationError("each answer must be an object", field_name=f"answers.{index}")
            question_id = item.get("question_id")
            answer_type = item.get("answer_type")
            if not isinstance(question_id, str) or not 1 <= len(question_id) <= 80:
                raise ValidationError("question_id is invalid", field_name=f"answers.{index}.question_id")
            if answer_type not in _ANSWER_TYPES:
                raise ValidationError("answer_type is not supported", field_name=f"answers.{index}.answer_type")
            if answer_type == "text":
                self._body(item, {"question_id", "answer_type", "text_value"}, {"question_id", "answer_type", "text_value"})
                value: Any = item["text_value"]
                if not isinstance(value, str) or not 1 <= len(value) <= 2000:
                    raise ValidationError("text_value is invalid", field_name=f"answers.{index}.text_value")
            elif answer_type == "choice":
                self._body(item, {"question_id", "answer_type", "choice_codes"}, {"question_id", "answer_type", "choice_codes"})
                value = item["choice_codes"]
                if not isinstance(value, list) or len(value) != 1 or not all(isinstance(v, str) and 1 <= len(v) <= 80 for v in value):
                    raise ValidationError("choice_codes must contain exactly one code", field_name=f"answers.{index}.choice_codes")
            elif answer_type == "multi_choice":
                self._body(item, {"question_id", "answer_type", "choice_codes"}, {"question_id", "answer_type", "choice_codes"})
                value = item["choice_codes"]
                if not isinstance(value, list) or not 1 <= len(value) <= 30 or not all(isinstance(v, str) and 1 <= len(v) <= 80 for v in value):
                    raise ValidationError("choice_codes is invalid", field_name=f"answers.{index}.choice_codes")
            elif answer_type == "number":
                self._body(item, {"question_id", "answer_type", "number_value"}, {"question_id", "answer_type", "number_value"})
                value = item["number_value"]
                if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value):
                    raise ValidationError("number_value must be a decimal string", field_name=f"answers.{index}.number_value")
            elif answer_type == "boolean":
                self._body(item, {"question_id", "answer_type", "boolean_value"}, {"question_id", "answer_type", "boolean_value"})
                value = item["boolean_value"]
                if not isinstance(value, bool):
                    raise ValidationError("boolean_value must be boolean", field_name=f"answers.{index}.boolean_value")
            else:
                self._body(item, {"question_id", "answer_type", "date_value"}, {"question_id", "answer_type", "date_value"})
                value = item["date_value"]
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValidationError("date_value must be an ISO date", field_name=f"answers.{index}.date_value")
            if question_id in normalized:
                raise ValidationError("question_id must be unique", field_name=f"answers.{index}.question_id")
            normalized[question_id] = {"answer_type": answer_type, "value": value}
        return normalized

    @staticmethod
    def _missing_fields(form_version: str, answers: dict[str, dict[str, Any]], document_ids: list[str]) -> list[str]:
        required = REQUIRED_QUESTIONS_BY_FORM.get(form_version, frozenset())
        missing = sorted(question_id for question_id in required if question_id not in answers)
        if not document_ids:
            missing.append("identity_document")
        return missing

    def _get_signed_application(self, context: ActorContext, application_id: str, correlation_id: str | None):
        try:
            return self.application_gateway.get_signed_application(context.tenant_id, application_id)
        except (NotFoundError, DependencyUnavailableError) as exc:
            self._audit_failure(context, "verify_signed_application", correlation_id, exc.code)
            raise

    def _get_customer_version(self, context: ActorContext, customer_id: str, correlation_id: str | None) -> int:
        try:
            return self.customer_gateway.get_customer_version(context.tenant_id, customer_id)
        except (NotFoundError, DependencyUnavailableError) as exc:
            self._audit_failure(context, "verify_customer_version", correlation_id, exc.code)
            raise

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
    def _stored_submission(state: StoreState, tenant_id: str, submission_id: str) -> KycSubmission:
        submission = state.submissions.get((tenant_id, submission_id))
        if not submission:
            raise NotFoundError("kyc submission was not found")
        return submission

    @staticmethod
    def _stored_document_by_asset(state: StoreState, tenant_id: str, asset_id: str) -> SubmissionDocument:
        document = state.documents.get((tenant_id, asset_id))
        if not document:
            raise NotFoundError("document was not found")
        return document

    @staticmethod
    def _latest_submission(state: StoreState, tenant_id: str, application_id: str) -> KycSubmission | None:
        rows = [row for (tenant, _), row in state.submissions.items() if tenant == tenant_id and row.application_id == application_id]
        return max(rows, key=lambda row: row.submission_version) if rows else None

    @staticmethod
    def _latest_submission_for_customer(state: StoreState, tenant_id: str, customer_id: str) -> KycSubmission | None:
        rows = [row for (tenant, _), row in state.submissions.items() if tenant == tenant_id and row.customer_id == customer_id]
        return max(rows, key=lambda row: row.created_at) if rows else None

    def _is_latest_submission(self, state: StoreState, tenant_id: str, submission: KycSubmission) -> bool:
        latest = self._latest_submission(state, tenant_id, submission.application_id)
        return latest is not None and latest.id == submission.id

    @staticmethod
    def _latest_assessment_for_application(state: StoreState, tenant_id: str, application_id: str) -> SuitabilityAssessment | None:
        rows = [row for (tenant, _), row in state.assessments.items() if tenant == tenant_id and row.application_id == application_id]
        return max(rows, key=lambda row: row.version) if rows else None

    def _submission_actions(self, submission: KycSubmission, context: ActorContext) -> list[str]:
        actions: list[str] = []
        is_subject = (context.principal_type == "customer" and context.customer_id == submission.customer_id) or self._staff(context, {"kyc_reviewer"})
        if not is_subject:
            return actions
        if submission.status in {"not_started", "in_progress", "incomplete"}:
            actions.append("request_kyc_upload")
            actions.append("submit_kyc")
        if self._staff(context, {"kyc_reviewer"}) and submission.status in {"submitted", "under_review", "incomplete"}:
            actions.append("decide_kyc")
        if self._staff(context, {"kyc_reviewer"}) and submission.status == "approved":
            actions.append("request_due_diligence_refresh")
        return sorted(set(actions))

    def _assessment_actions(self, assessment: SuitabilityAssessment, context: ActorContext) -> list[str]:
        if self._staff(context, {"suitability_reviewer"}) and assessment.status == "reassessment_due":
            return ["assess_suitability"]
        return []

    def _submission_outcome(self, submission: KycSubmission, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(submission.to_api(self._submission_actions(submission, context)), status, "kyc_submission", submission.id, submission.version)

    def _assessment_outcome(self, assessment: SuitabilityAssessment, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(
            assessment.to_api(self._assessment_actions(assessment, context)), status, "suitability_assessment", assessment.id, assessment.version
        )

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
                action=f"m03.{action}",
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
            self._audit(state, context, action, "security" if aggregate_id is None else "kyc_submission", aggregate_id, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
