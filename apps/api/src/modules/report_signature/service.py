from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable

from .domain import (
    REPORT_TYPES,
    REVIEW_DECISIONS,
    REVIEW_TYPES,
    ActorContext,
    AdvisoryReport,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    ForbiddenError,
    IdempotencyReceipt,
    NotFoundError,
    PreconditionRequiredError,
    ReportVersion,
    ReviewDecision,
    Signature,
    SignatureRequest,
    SignatureRevocation,
    StaleVersionError,
    UnauthorizedError,
    ValidationError,
    canonical_hash,
    new_id,
    require_uuid,
    sha256_text,
    utc_now,
    wire_time,
)
from .ports import (
    CandidateLineagePort,
    ObjectAssetPort,
    ReviewCaseLookupPort,
    SignatureProviderPort,
    SignerQualificationPort,
)
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_INSTRUMENT_TYPES = {"equity", "etf", "fund", "bond", "other_approved"}
_CHANNELS = {"legendflow", "line", "sms", "email"}
_LOCALES = {"zh-TW"}
_EXTERNAL_SYSTEMS = {
    "pantheon", "agora", "legendflow", "signature_provider", "screening_provider",
    "payment_provider", "accounting_provider", "messaging_provider",
}
_VERIFIED_CALLBACK_REQUIRED = {"signed_asset_id", "signed_file_sha256", "signed_at", "verification_evidence_asset_id", "signer_subject_ref"}


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


class ReportSignatureService:
    """M07 command/query application service.

    Every state-changing operation runs inside one repository callback so
    domain state, audit, outbox and the idempotency receipt commit atomically.
    Provider/qualification verification happens before that transaction and
    is re-checked once more inside it against the latest aggregate state.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        signature_provider: SignatureProviderPort,
        qualifications: SignerQualificationPort,
        assets: ObjectAssetPort,
        review_cases: ReviewCaseLookupPort,
        candidates: CandidateLineagePort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False)
            for adapter in (repository, signature_provider, qualifications, assets, review_cases, candidates)
        ):
            raise RuntimeError("fake M07 adapters are forbidden in production")
        self.repository = repository
        self.signature_provider = signature_provider
        self.qualifications = qualifications
        self.assets = assets
        self.review_cases = review_cases
        self.candidates = candidates
        self._clock = clock

    # ---- public commands -------------------------------------------------

    def create_report(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"analyst"})
        self._body(body, {"review_case_id", "title", "instrument_refs", "report_type", "preceding_report_id"}, {"review_case_id", "title", "instrument_refs", "report_type"})
        review_case_id = require_uuid(body["review_case_id"], "review_case_id")
        if not self.review_cases.exists(context.tenant_id, review_case_id):
            raise ValidationError("review_case_id does not exist", code="review_case_not_found", field_name="review_case_id")
        title = body["title"]
        if not isinstance(title, str) or not 1 <= len(title) <= 200:
            raise ValidationError("title must contain 1 to 200 characters", field_name="title")
        instrument_refs = body["instrument_refs"]
        if not isinstance(instrument_refs, list) or not 1 <= len(instrument_refs) <= 50:
            raise ValidationError("instrument_refs must contain 1 to 50 items", field_name="instrument_refs")
        instrument_refs = [self._validate_instrument_ref(item, index) for index, item in enumerate(instrument_refs)]
        if body["report_type"] not in REPORT_TYPES:
            raise ValidationError("report_type is not supported", field_name="report_type")
        preceding_report_id = body.get("preceding_report_id")
        if preceding_report_id is not None:
            preceding_report_id = require_uuid(preceding_report_id, "preceding_report_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            if preceding_report_id is not None and (context.tenant_id, preceding_report_id) not in state.reports:
                raise ValidationError("preceding_report_id does not exist", code="resource_not_found", field_name="preceding_report_id")
            report = AdvisoryReport(
                id=new_id(), tenant_id=context.tenant_id, review_case_id=review_case_id, title=title,
                instrument_refs=instrument_refs, report_type=body["report_type"],
                report_number=f"RPT-{new_id()[:8].upper()}", created_by=context.actor_id,
                preceding_report_id=preceding_report_id, created_at=now, updated_at=now,
            )
            state.reports[(context.tenant_id, report.id)] = report
            return self._report_outcome(state, report, context, 201)

        return self._run_command(context, "create_report", idempotency_key, body, correlation_id, mutate)

    def create_report_version(
        self,
        context: ActorContext,
        report_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        report_id = require_uuid(report_id, "report_id")
        self._authorize_staff(context, {"analyst"})
        allowed = {
            "source_candidate_id", "source_artifact_ref", "report_asset_id", "report_content_sha256",
            "evidence_snapshot_asset_ids", "recommendation_version_ids", "channel_texts", "audience_policy",
            "valid_from", "valid_until", "supersedes_version_id",
        }
        required = allowed - {"supersedes_version_id"}
        self._body(body, allowed, required)
        source_candidate_id = require_uuid(body["source_candidate_id"], "source_candidate_id")
        if not self.candidates.exists(context.tenant_id, source_candidate_id):
            raise ValidationError("source_candidate_id does not exist", code="candidate_not_found", field_name="source_candidate_id")
        source_artifact_ref = self._validate_external_ref(body["source_artifact_ref"])
        report_asset_id = require_uuid(body["report_asset_id"], "report_asset_id")
        if not self.assets.exists(context.tenant_id, report_asset_id):
            raise ValidationError("report_asset_id does not exist", code="evidence_not_found", field_name="report_asset_id")
        report_content_sha256 = str(body["report_content_sha256"])
        if not _SHA_RE.fullmatch(report_content_sha256):
            raise ValidationError("report_content_sha256 must be lowercase SHA-256", field_name="report_content_sha256")
        stored_hash = self.assets.sha256_of(context.tenant_id, report_asset_id)
        if stored_hash is not None and stored_hash != report_content_sha256:
            raise ValidationError("report_content_sha256 does not match the stored asset", code="report_content_hash_mismatch", field_name="report_content_sha256")
        evidence_ids = body["evidence_snapshot_asset_ids"]
        if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 100:
            raise ValidationError("evidence_snapshot_asset_ids must contain 1 to 100 items", field_name="evidence_snapshot_asset_ids")
        evidence_ids = [require_uuid(value, "evidence_snapshot_asset_ids") for value in evidence_ids]
        if any(not self.assets.exists(context.tenant_id, value) for value in evidence_ids):
            raise ValidationError("evidence_snapshot_asset_ids references a missing asset", code="evidence_not_found", field_name="evidence_snapshot_asset_ids")
        recommendation_ids = body["recommendation_version_ids"]
        if not isinstance(recommendation_ids, list) or not 1 <= len(recommendation_ids) <= 50:
            raise ValidationError("recommendation_version_ids must contain 1 to 50 items", field_name="recommendation_version_ids")
        recommendation_ids = [require_uuid(value, "recommendation_version_ids") for value in recommendation_ids]
        channel_texts_in = body["channel_texts"]
        if not isinstance(channel_texts_in, list) or not 1 <= len(channel_texts_in) <= 8:
            raise ValidationError("channel_texts must contain 1 to 8 items", field_name="channel_texts")
        channel_texts = [self._validate_channel_text(item, index) for index, item in enumerate(channel_texts_in)]
        audience_policy = self._validate_audience_policy(body["audience_policy"])
        valid_from = self._parse_time(body["valid_from"], "valid_from")
        valid_until = self._parse_time(body["valid_until"], "valid_until")
        if valid_until <= valid_from:
            raise ValidationError("valid_until must be after valid_from", field_name="valid_until")
        supersedes_version_id = body.get("supersedes_version_id")
        if supersedes_version_id is not None:
            supersedes_version_id = require_uuid(supersedes_version_id, "supersedes_version_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        bundle_manifest = {
            # REQ-M07-002/UAT-M07-002: the signable manifest must be sensitive to the report
            # content and its publication window, not only channel text/audience rules, so a
            # content or expiry change always yields a different bundle_manifest_sha256.
            "report_asset_id": report_asset_id,
            "report_content_sha256": report_content_sha256,
            "valid_from": wire_time(valid_from),
            "valid_until": wire_time(valid_until),
            "channel_texts": channel_texts,
            "audience_policy": audience_policy,
            "recommendation_version_ids": sorted(set(recommendation_ids)),
            "evidence_snapshot_asset_ids": sorted(set(evidence_ids)),
        }
        bundle_manifest_asset_id, bundle_manifest_sha256 = self.assets.write(context.tenant_id, bundle_manifest)

        def mutate(state: StoreState) -> _Outcome:
            report = self._stored_report(state, context.tenant_id, report_id)
            self._cas(report.version, expected_version)
            if supersedes_version_id is not None:
                superseded = state.versions.get((context.tenant_id, supersedes_version_id))
                if not superseded or superseded.report_id != report_id:
                    raise ValidationError("supersedes_version_id does not belong to this report", code="resource_not_found", field_name="supersedes_version_id")
                if superseded.status == "signed":
                    superseded.status = "superseded"
                    superseded.version += 1
                    superseded.updated_at = now
            version_number = 1 + sum(1 for (tenant, _), item in state.versions.items() if tenant == context.tenant_id and item.report_id == report_id)
            version = ReportVersion(
                id=new_id(), tenant_id=context.tenant_id, report_id=report_id, version_number=version_number,
                source_candidate_id=source_candidate_id, source_artifact_ref=source_artifact_ref,
                report_asset_id=report_asset_id, report_content_sha256=report_content_sha256,
                bundle_manifest_asset_id=bundle_manifest_asset_id, bundle_manifest=bundle_manifest,
                bundle_manifest_sha256=bundle_manifest_sha256, channel_texts=channel_texts, audience_policy=audience_policy,
                recommendation_version_ids=sorted(set(recommendation_ids)), evidence_snapshot_asset_ids=sorted(set(evidence_ids)),
                valid_from=valid_from, valid_until=valid_until, created_by=context.actor_id,
                supersedes_version_id=supersedes_version_id, created_at=now, updated_at=now,
            )
            state.versions[(context.tenant_id, version.id)] = version
            report.current_version_id = version.id
            report.version += 1
            report.updated_at = now
            return self._version_outcome(version, context, 201)

        return self._run_command(context, "create_report_version", idempotency_key, {"report_id": report_id, **body}, correlation_id, mutate)

    def record_report_review(
        self,
        context: ActorContext,
        report_version_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        report_version_id = require_uuid(report_version_id, "report_version_id")
        self._body(body, {"review_type", "decision", "report_content_sha256", "bundle_manifest_sha256", "review_policy_version", "checklist_result_asset_id", "reason_code"},
                   {"review_type", "decision", "report_content_sha256", "bundle_manifest_sha256", "review_policy_version", "checklist_result_asset_id", "reason_code"})
        if body["review_type"] not in REVIEW_TYPES:
            raise ValidationError("review_type is not supported", field_name="review_type")
        if body["decision"] not in REVIEW_DECISIONS:
            raise ValidationError("decision is not supported", field_name="decision")
        required_role = "analyst_reviewer" if body["review_type"] == "professional" else "compliance_reviewer"
        self._authorize_staff(context, {required_role})
        report_content_sha256 = str(body["report_content_sha256"])
        bundle_manifest_sha256 = str(body["bundle_manifest_sha256"])
        if not _SHA_RE.fullmatch(report_content_sha256) or not _SHA_RE.fullmatch(bundle_manifest_sha256):
            raise ValidationError("content and bundle hashes must be lowercase SHA-256", field_name="report_content_sha256")
        review_policy_version = body["review_policy_version"]
        if not isinstance(review_policy_version, str) or not 1 <= len(review_policy_version) <= 80:
            raise ValidationError("review_policy_version must contain 1 to 80 characters", field_name="review_policy_version")
        checklist_result_asset_id = require_uuid(body["checklist_result_asset_id"], "checklist_result_asset_id")
        if not self.assets.exists(context.tenant_id, checklist_result_asset_id):
            raise ValidationError("checklist_result_asset_id does not exist", code="evidence_not_found", field_name="checklist_result_asset_id")
        reason_code = body["reason_code"]
        if not isinstance(reason_code, str) or not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            version = self._stored_version(state, context.tenant_id, report_version_id)
            self._cas(version.version, expected_version)
            if context.actor_id == version.created_by:
                raise ForbiddenError("reviewer cannot review a version they authored", code="maker_checker_violation")
            if version.status not in {"draft", "in_review", "changes_requested"}:
                raise ConflictError("report version is not open for review", code="report_version_not_reviewable")
            if version.report_content_sha256 != report_content_sha256 or version.bundle_manifest_sha256 != bundle_manifest_sha256:
                raise ConflictError("review target content has changed", code="review_target_mismatch")
            if review_policy_version != version.review_policy_version:
                raise ValidationError("review_policy_version does not match the current policy", code="review_policy_version_mismatch", field_name="review_policy_version")
            review = ReviewDecision(
                id=new_id(), tenant_id=context.tenant_id, report_version_id=report_version_id,
                review_type=body["review_type"], decision=body["decision"], reviewer_id=context.actor_id,
                reviewed_at=now, report_content_sha256=report_content_sha256, bundle_manifest_sha256=bundle_manifest_sha256,
                checklist_result_asset_id=checklist_result_asset_id, reason_code=reason_code, created_at=now, updated_at=now,
            )
            state.reviews[(context.tenant_id, review.id)] = review
            version.status = "in_review" if body["decision"] == "approve" else "changes_requested"
            version.version += 1
            version.updated_at = now
            return _Outcome(review.to_api(), 201, "review_decision", review.id, review.version)

        return self._run_command(context, "record_report_review", idempotency_key, {"report_version_id": report_version_id, **body}, correlation_id, mutate)

    def freeze_report_version(
        self,
        context: ActorContext,
        report_version_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        report_version_id = require_uuid(report_version_id, "report_version_id")
        self._authorize_staff(context, {"analyst"})
        self._body(body, {"report_content_sha256", "bundle_manifest_sha256", "required_review_ids", "canonicalization"},
                   {"report_content_sha256", "bundle_manifest_sha256", "required_review_ids", "canonicalization"})
        if body["canonicalization"] != "RFC8785":
            raise ValidationError("canonicalization must be RFC8785", field_name="canonicalization")
        report_content_sha256 = str(body["report_content_sha256"])
        bundle_manifest_sha256 = str(body["bundle_manifest_sha256"])
        if not _SHA_RE.fullmatch(report_content_sha256) or not _SHA_RE.fullmatch(bundle_manifest_sha256):
            raise ValidationError("content and bundle hashes must be lowercase SHA-256", field_name="report_content_sha256")
        required_review_ids = body["required_review_ids"]
        if not isinstance(required_review_ids, list) or not 1 <= len(required_review_ids) <= 10:
            raise ValidationError("required_review_ids must contain 1 to 10 items", field_name="required_review_ids")
        required_review_ids = [require_uuid(value, "required_review_ids") for value in required_review_ids]
        if len(set(required_review_ids)) != len(required_review_ids):
            raise ValidationError("required_review_ids must not contain duplicates", code="duplicate_review_id", field_name="required_review_ids")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            version = self._stored_version(state, context.tenant_id, report_version_id)
            self._cas(version.version, expected_version)
            if (context.tenant_id, version.source_candidate_id) in state.superseded_candidates:
                raise ConflictError("source candidate has been superseded", code="source_candidate_superseded")
            if version.status != "in_review":
                raise ConflictError("report version is not ready to freeze", code="report_version_not_ready")
            if version.report_content_sha256 != report_content_sha256 or version.bundle_manifest_sha256 != bundle_manifest_sha256:
                raise ConflictError("content has changed since review", code="content_changed_since_review")
            reviews = [state.reviews[(context.tenant_id, review_id)] for review_id in required_review_ids if (context.tenant_id, review_id) in state.reviews]
            if len(reviews) != len(required_review_ids):
                raise ValidationError("required_review_ids references a missing review", code="missing_review", field_name="required_review_ids")
            if any(review.report_version_id != report_version_id for review in reviews):
                raise ValidationError("required_review_ids references a review for a different version", code="missing_review", field_name="required_review_ids")
            valid_reviews = [
                review for review in reviews
                if review.decision == "approve"
                and review.report_content_sha256 == report_content_sha256
                and review.bundle_manifest_sha256 == bundle_manifest_sha256
            ]
            if len(valid_reviews) != len(reviews):
                raise ValidationError("required_review_ids references a review that is not an approval of the current content", code="missing_review", field_name="required_review_ids")
            covered_types = {review.review_type for review in valid_reviews}
            required_types = {"professional"} | ({"compliance"} if version.requires_compliance_review else set())
            if not required_types.issubset(covered_types):
                raise ValidationError("a required review type is missing or not approved", code="missing_review")
            # Mirror report.guard_signature's "unresolved review rejection" gate across the
            # version's full review history (not just the caller-selected required_review_ids):
            # a request_changes/reject on this exact content must have a strictly later approve
            # of the same review_type and the same hashes, or freezing is blocked. Ordering uses
            # record order (state.reviews preserves insertion order), not reviewed_at, because a
            # deterministic test/replay clock can legitimately produce equal timestamps for two
            # calls that are still strictly ordered in real sequence.
            all_reviews = [
                row for (tenant, _), row in state.reviews.items()
                if tenant == context.tenant_id and row.report_version_id == report_version_id
            ]
            for index, review in enumerate(all_reviews):
                if review.decision not in {"request_changes", "reject"}:
                    continue
                resolved = any(
                    later.review_type == review.review_type and later.decision == "approve"
                    and later.report_content_sha256 == report_content_sha256 and later.bundle_manifest_sha256 == bundle_manifest_sha256
                    for later_index, later in enumerate(all_reviews) if later_index > index
                )
                if not resolved:
                    raise ConflictError("an unresolved review rejection exists for this version", code="unresolved_review_rejection")
            version.status = "ready_to_sign"
            version.frozen_at = now
            version.required_review_ids = required_review_ids
            version.version += 1
            version.updated_at = now
            self._emit(
                state, event_type="report.version_frozen", tenant_id=context.tenant_id, aggregate_type="report_version",
                aggregate_id=report_version_id, aggregate_version=version.version, correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:version-frozen",
                payload={
                    "report_version_id": report_version_id, "report_content_sha256": report_content_sha256,
                    "bundle_manifest_sha256": bundle_manifest_sha256, "review_decision_ids": required_review_ids,
                },
                now=now,
            )
            return self._version_outcome(version, context, 200)

        return self._run_command(context, "freeze_report_version", idempotency_key, {"report_version_id": report_version_id, **body}, correlation_id, mutate)

    def request_analyst_signature(
        self,
        context: ActorContext,
        report_version_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        report_version_id = require_uuid(report_version_id, "report_version_id")
        self._authorize_staff(context, {"analyst_signer"})
        self._body(body, {"report_content_sha256", "bundle_manifest_sha256", "credential_id", "required_review_ids", "signing_intent_confirmed"},
                   {"report_content_sha256", "bundle_manifest_sha256", "credential_id", "required_review_ids", "signing_intent_confirmed"})
        if body["signing_intent_confirmed"] is not True:
            raise ValidationError("signing_intent_confirmed must be true", field_name="signing_intent_confirmed")
        report_content_sha256 = str(body["report_content_sha256"])
        bundle_manifest_sha256 = str(body["bundle_manifest_sha256"])
        if not _SHA_RE.fullmatch(report_content_sha256) or not _SHA_RE.fullmatch(bundle_manifest_sha256):
            raise ValidationError("content and bundle hashes must be lowercase SHA-256", field_name="report_content_sha256")
        credential_id = require_uuid(body["credential_id"], "credential_id")
        required_review_ids = body["required_review_ids"]
        if not isinstance(required_review_ids, list) or not 1 <= len(required_review_ids) <= 10:
            raise ValidationError("required_review_ids must contain 1 to 10 items", field_name="required_review_ids")
        required_review_ids = sorted(require_uuid(value, "required_review_ids") for value in required_review_ids)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        request_payload = {"report_version_id": report_version_id, **body}

        # Idempotent replay must win over every later precondition: a retried
        # request_analyst_signature call with the same key+body has to return the
        # original 202 even after the first call already moved the version on to
        # signature_pending (otherwise the replay sees "not ready to sign" instead
        # of the receipt). Checked before any CAS/provider/qualification side effect.
        replay = self._idempotency_lookup(context, "request_analyst_signature", idempotency_key, request_payload)
        if replay is not None:
            return replay

        def check_signable(state: StoreState) -> ReportVersion:
            version = self._stored_version(state, context.tenant_id, report_version_id)
            # CAS is part of the signability check itself: a stale/missing If-Match
            # must fail here, before the qualification lookup or provider session
            # creation below ever run, so a stale request cannot leak a provider session.
            self._cas(version.version, expected_version)
            if (context.tenant_id, version.source_candidate_id) in state.superseded_candidates:
                raise ConflictError("source candidate has been superseded", code="source_candidate_superseded")
            if version.status != "ready_to_sign":
                raise ConflictError("report version is not ready to sign", code="report_version_not_ready_to_sign")
            if version.report_content_sha256 != report_content_sha256 or version.bundle_manifest_sha256 != bundle_manifest_sha256:
                raise ConflictError("content has changed since freeze", code="content_changed_since_freeze")
            if sorted(version.required_review_ids) != required_review_ids:
                raise ValidationError("required_review_ids does not match the frozen review set", code="required_reviews_mismatch")
            return version

        try:
            self.repository.read(check_signable)
        except DomainError as exc:
            self._audit_failure(context, "request_analyst_signature", correlation_id, exc.code, report_version_id)
            raise
        credential = self._verify_qualification(context, credential_id, correlation_id)
        handle = self.signature_provider.create_session(
            report_version_id=report_version_id, bundle_sha256=bundle_manifest_sha256, signer_subject_ref=credential.subject_ref,
        )

        def mutate(state: StoreState) -> _Outcome:
            version = check_signable(state)
            request = SignatureRequest(
                id=new_id(), tenant_id=context.tenant_id, report_version_id=report_version_id, signer_id=context.actor_id,
                credential_id=credential_id, bundle_sha256=bundle_manifest_sha256, provider=handle.provider,
                provider_transaction_id=handle.provider_transaction_id, redirect_url=handle.redirect_url,
                requested_at=now, expires_at=handle.expires_at, created_at=now, updated_at=now,
            )
            state.signature_requests[(context.tenant_id, request.id)] = request
            version.status = "signature_pending"
            version.version += 1
            version.updated_at = now
            return _Outcome(request.to_api([]), 202, "signature_request", request.id, request.version)

        return self._run_command(context, "request_analyst_signature", idempotency_key, request_payload, correlation_id, mutate)

    def receive_analyst_signature(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_service(context, {"signature_adapter"})
        allowed = {
            "event_id", "signature_request_id", "provider_session_ref", "report_version_id", "report_content_sha256",
            "bundle_manifest_sha256", "signed_asset_id", "signed_file_sha256", "signer_subject_ref", "signed_at",
            "verification_evidence_asset_id", "verification_result", "certificate_chain_asset_id", "trusted_timestamp_asset_id",
        }
        required = {"event_id", "signature_request_id", "provider_session_ref", "report_version_id", "report_content_sha256", "bundle_manifest_sha256", "verification_result"}
        self._body(body, allowed, required)
        event_id = require_uuid(body["event_id"], "event_id")
        signature_request_id = require_uuid(body["signature_request_id"], "signature_request_id")
        report_version_id = require_uuid(body["report_version_id"], "report_version_id")
        provider_session_ref = str(body["provider_session_ref"])
        if not 1 <= len(provider_session_ref) <= 160:
            raise ValidationError("provider_session_ref must contain 1 to 160 characters", field_name="provider_session_ref")
        verification_result = body["verification_result"]
        if verification_result not in {"verified", "failed"}:
            raise ValidationError("verification_result is not supported", field_name="verification_result")
        report_content_sha256 = str(body["report_content_sha256"])
        bundle_manifest_sha256 = str(body["bundle_manifest_sha256"])
        if not _SHA_RE.fullmatch(report_content_sha256) or not _SHA_RE.fullmatch(bundle_manifest_sha256):
            raise ValidationError("content and bundle hashes must be lowercase SHA-256", field_name="report_content_sha256")
        signed_asset_id = verification_evidence_asset_id = signed_file_sha256 = signer_subject_ref = None
        certificate_chain_asset_id = trusted_timestamp_asset_id = None
        if verification_result == "verified":
            missing = _VERIFIED_CALLBACK_REQUIRED - set(body)
            if missing:
                raise ValidationError(f"missing fields for a verified callback: {', '.join(sorted(missing))}", field_name=sorted(missing)[0])
            signed_asset_id = require_uuid(body["signed_asset_id"], "signed_asset_id")
            verification_evidence_asset_id = require_uuid(body["verification_evidence_asset_id"], "verification_evidence_asset_id")
            signed_file_sha256 = str(body["signed_file_sha256"])
            if not _SHA_RE.fullmatch(signed_file_sha256):
                raise ValidationError("signed_file_sha256 must be lowercase SHA-256", field_name="signed_file_sha256")
            signer_subject_ref = str(body["signer_subject_ref"])
            if not 1 <= len(signer_subject_ref) <= 256:
                raise ValidationError("signer_subject_ref must contain 1 to 256 characters", field_name="signer_subject_ref")
            if body.get("certificate_chain_asset_id") is not None:
                certificate_chain_asset_id = require_uuid(body["certificate_chain_asset_id"], "certificate_chain_asset_id")
            if body.get("trusted_timestamp_asset_id") is not None:
                trusted_timestamp_asset_id = require_uuid(body["trusted_timestamp_asset_id"], "trusted_timestamp_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            request = state.signature_requests.get((context.tenant_id, signature_request_id))
            if not request or request.report_version_id != report_version_id:
                raise NotFoundError("signature request was not found")
            version = self._stored_version(state, context.tenant_id, report_version_id)

            if request.status == "verified":
                receipt = {"receipt_id": request.last_receipt_id, "event_id": event_id, "status": "duplicate", "received_at": wire_time(now), "correlation_id": correlation_id}
                return _Outcome(receipt, 202, "signature_request", request.id, request.version)
            if request.status in {"failed", "expired"}:
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "quarantined", "received_at": wire_time(now), "correlation_id": correlation_id}
                self._record_audit(state, context, "receive_analyst_signature", "signature_request", request.id, request.version, correlation_id, "rejected", "signature_request_already_closed")
                return _Outcome(receipt, 202, "signature_request", request.id, request.version)
            if request.manual_review_flagged_at is not None:
                # Flagged for manual reconciliation after an unexplained provider state; a
                # callback must not silently resolve it either way until a human clears it.
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "quarantined", "received_at": wire_time(now), "correlation_id": correlation_id}
                self._record_audit(state, context, "receive_analyst_signature", "signature_request", request.id, request.version, correlation_id, "rejected", "signature_request_pending_manual_review")
                return _Outcome(receipt, 202, "signature_request", request.id, request.version)

            def quarantine(reason_code: str) -> _Outcome:
                self._reopen_for_retry(request, version, now)
                self._record_audit(state, context, "receive_analyst_signature", "signature_request", request.id, request.version, correlation_id, "rejected", reason_code)
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "quarantined", "received_at": wire_time(now), "correlation_id": correlation_id}
                return _Outcome(receipt, 202, "signature_request", request.id, request.version)

            # The provider must be challenging back the exact session we opened for this
            # request; otherwise an attacker (or a misrouted callback) could bind an
            # unrelated provider session's result onto this signature_request_id.
            if provider_session_ref != request.provider_transaction_id:
                return quarantine("signature_provider_session_mismatch")
            # A source candidate can be superseded after request_analyst_signature already
            # opened a provider session; the frozen package must not auto-publish on stale
            # source data just because the callback itself race-wins the supersede event.
            if (context.tenant_id, version.source_candidate_id) in state.superseded_candidates:
                return quarantine("source_candidate_superseded")

            hashes_match = (
                version.report_content_sha256 == report_content_sha256
                and version.bundle_manifest_sha256 == bundle_manifest_sha256
                and request.bundle_sha256 == bundle_manifest_sha256
            )
            if not hashes_match:
                return quarantine("signature_payload_mismatch")
            if verification_result == "failed":
                self._reopen_for_retry(request, version, now)
                self._record_audit(state, context, "receive_analyst_signature", "signature_request", request.id, request.version, correlation_id, "rejected", "provider_verification_failed")
                receipt = {"receipt_id": new_id(), "event_id": event_id, "status": "accepted", "received_at": wire_time(now), "correlation_id": correlation_id}
                return _Outcome(receipt, 202, "signature_request", request.id, request.version)
            signed_at = self._parse_time(body["signed_at"], "signed_at")
            if signed_at < version.frozen_at or signed_at > now or signed_at >= request.expires_at or signed_at >= version.valid_until:
                return quarantine("signed_at_out_of_window")
            try:
                credential = self.qualifications.verify(context.tenant_id, request.signer_id, request.credential_id)
            except DomainError:
                return quarantine("qualification_invalid_at_verification")
            # The provider-asserted signer must be the same subject the credential registry
            # knows for this request's signer_id/credential_id, not merely a well-formed ref.
            if credential.subject_ref != signer_subject_ref:
                return quarantine("signer_subject_mismatch")
            for asset_id in filter(None, [signed_asset_id, verification_evidence_asset_id, certificate_chain_asset_id, trusted_timestamp_asset_id]):
                if not self.assets.exists(context.tenant_id, asset_id):
                    raise ValidationError("signature evidence asset does not exist", code="evidence_not_found")
            # content_hash != signed_file_hash by design (packaging adds the certificate), but
            # when the asset store does record a digest for the signed container it must match
            # the asserted signed_file_sha256 — this is not the same comparison as content_hash.
            stored_signed_hash = self.assets.sha256_of(context.tenant_id, signed_asset_id)
            if stored_signed_hash is not None and stored_signed_hash != signed_file_sha256:
                return quarantine("signed_file_hash_mismatch")

            receipt_id = new_id()
            signature = Signature(
                id=new_id(), tenant_id=context.tenant_id, request_id=request.id, report_version_id=report_version_id,
                signer_id=request.signer_id, credential_id=request.credential_id, content_sha256=report_content_sha256,
                bundle_sha256=bundle_manifest_sha256, signed_file_sha256=signed_file_sha256,
                signed_asset_id=signed_asset_id, verification_asset_id=verification_evidence_asset_id,
                signed_at=signed_at, verified_at=now, created_at=now, updated_at=now,
            )
            state.signatures[(context.tenant_id, signature.id)] = signature
            request.status = "verified"
            request.last_receipt_id = receipt_id
            request.version += 1
            request.updated_at = now
            version.status = "signed"
            version.signed_at = signed_at
            version.signature_id = signature.id
            version.signed_file_sha256 = signature.signed_file_sha256
            version.version += 1
            version.updated_at = now
            self._emit(
                state, event_type="report.signature_verified", tenant_id=context.tenant_id, aggregate_type="report_version",
                aggregate_id=report_version_id, aggregate_version=version.version, correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:signature-verified",
                payload={
                    "report_version_id": report_version_id, "signature_id": signature.id,
                    "report_content_sha256": report_content_sha256, "bundle_manifest_sha256": bundle_manifest_sha256,
                    "signed_file_sha256": signature.signed_file_sha256, "verified_at": wire_time(now),
                    "valid_until": wire_time(version.valid_until),
                },
                now=now,
            )
            receipt = {"receipt_id": receipt_id, "event_id": event_id, "status": "accepted", "received_at": wire_time(now), "correlation_id": correlation_id}
            return _Outcome(receipt, 202, "signature_request", request.id, request.version)

        return self._run_command(context, "receive_analyst_signature", idempotency_key, body, correlation_id, mutate)

    def revoke_signature(
        self,
        context: ActorContext,
        signature_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        signature_id = require_uuid(signature_id, "signature_id")
        self._authorize_staff(context, {"compliance_supervisor"})
        self._body(body, {"reason_code", "evidence_asset_id", "effective_at", "notify_recipients"}, {"reason_code", "evidence_asset_id", "effective_at", "notify_recipients"})
        reason_code = body["reason_code"]
        if not isinstance(reason_code, str) or not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        evidence_asset_id = require_uuid(body["evidence_asset_id"], "evidence_asset_id")
        if not self.assets.exists(context.tenant_id, evidence_asset_id):
            raise ValidationError("evidence_asset_id does not exist", code="evidence_not_found", field_name="evidence_asset_id")
        effective_at = self._parse_time(body["effective_at"], "effective_at")
        notify_recipients = body["notify_recipients"]
        if not isinstance(notify_recipients, bool):
            raise ValidationError("notify_recipients must be boolean", field_name="notify_recipients")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            signature = self._stored_signature(state, context.tenant_id, signature_id)
            self._cas(signature.version, expected_version)
            already_revoked = any(row.signature_id == signature_id for (tenant, _), row in state.revocations.items() if tenant == context.tenant_id)
            if already_revoked:
                raise ConflictError("signature has already been revoked", code="signature_already_revoked")
            revocation_id = new_id()
            event_id = self._emit(
                state, event_type="report.signature_revoked", tenant_id=context.tenant_id, aggregate_type="signature",
                aggregate_id=signature_id, aggregate_version=signature.version + 1, correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:signature-revoked",
                payload={
                    "signature_id": signature_id, "report_version_id": signature.report_version_id,
                    "revocation_id": revocation_id, "effective_at": wire_time(effective_at),
                },
                now=now,
            )
            revocation = SignatureRevocation(
                id=revocation_id, tenant_id=context.tenant_id, signature_id=signature_id, reason_code=reason_code,
                evidence_asset_id=evidence_asset_id, effective_at=effective_at, revoked_by=context.actor_id,
                publication_withdrawal_event_id=event_id, created_at=now, updated_at=now,
            )
            state.revocations[(context.tenant_id, revocation.id)] = revocation
            signature.version += 1
            signature.updated_at = now
            return _Outcome(revocation.to_api(), 201, "signature", signature_id, signature.version)

        return self._run_command(context, "revoke_signature", idempotency_key, {"signature_id": signature_id, **body}, correlation_id, mutate)

    # ---- public queries --------------------------------------------------

    def get_report_version(self, context: ActorContext, report_version_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        report_version_id = require_uuid(report_version_id, "report_version_id")
        correlation_id = self._correlation(correlation_id)
        try:
            self._authorize_staff(context, {"analyst", "analyst_reviewer", "compliance_reviewer"})
        except DomainError as exc:
            self._audit_failure(context, "get_report_version", correlation_id, exc.code, report_version_id)
            raise

        def query(state: StoreState) -> ServiceResponse:
            version = self._stored_version(state, context.tenant_id, report_version_id)
            self._audit(state, context, "get_report_version", "report_version", version.id, version.version, correlation_id, "succeeded")
            return ServiceResponse(version.to_api(self._version_actions(context, version)), etag=self._etag(version.version))

        try:
            return self.repository.atomic(query)
        except DomainError as exc:
            self._audit_failure(context, "get_report_version", correlation_id, exc.code, report_version_id)
            raise

    def get_report_workbench(self, context: ActorContext, report_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Internal admin-UI projection; not a published OpenAPI operation."""
        report_id = require_uuid(report_id, "report_id")
        self._authorize_staff(context, {"analyst", "analyst_reviewer", "compliance_reviewer", "analyst_signer", "compliance_supervisor"})
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> dict[str, Any]:
            report = self._stored_report(state, context.tenant_id, report_id)
            versions = sorted(
                (item for (tenant, _), item in state.versions.items() if tenant == context.tenant_id and item.report_id == report_id),
                key=lambda item: item.version_number,
            )
            reviews_by_version = {
                version.id: sorted(
                    (row.to_api() for (tenant, _), row in state.reviews.items() if tenant == context.tenant_id and row.report_version_id == version.id),
                    key=lambda row: row["reviewed_at"],
                )
                for version in versions
            }
            state_label = self._report_state_label(state, report)
            self._audit(state, context, "get_report_workbench", "report", report.id, report.version, correlation_id, "succeeded")
            return {
                "report": report.to_api(self._report_actions(context, state_label), state_label),
                "versions": [
                    {**version.to_api(self._version_actions(context, version)), "reviews": reviews_by_version[version.id]}
                    for version in versions
                ],
            }

        return self.repository.atomic(query)

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
            raise PreconditionRequiredError("Idempotency-Key must match the published contract", code="idempotency_key_required")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, operation, key)

        def transaction(state: StoreState) -> tuple[_Outcome, bool]:
            receipt = state.idempotency.get(receipt_key)
            if receipt:
                if receipt.request_sha256 != fingerprint:
                    raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
                prior = _Outcome(receipt.response_value, receipt.response_status, "replay", None, receipt.aggregate_version, receipt.error)
                return prior, True
            outcome = mutate(state)
            self._record_audit(
                state, context, operation, outcome.aggregate_type, outcome.aggregate_id, outcome.aggregate_version,
                correlation_id, outcome.error.code if outcome.error else "succeeded", outcome.error.code if outcome.error else None,
            )
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id, actor_id=context.actor_id, operation=operation, key=key,
                request_sha256=fingerprint, response_status=outcome.status, response_value=outcome.value,
                error=outcome.error, completed_at=self._clock(), aggregate_version=outcome.aggregate_version,
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

    def _idempotency_lookup(
        self, context: ActorContext, operation: str, key: str, request: dict[str, Any],
    ) -> ServiceResponse | None:
        """Fast-path idempotent replay, checked before any CAS/provider side effect.

        Mirrors the receipt lookup ``_run_command`` performs inside its atomic
        transaction so a replay of an already-completed command still succeeds
        even once the aggregate has since moved on to a state the command's
        own preconditions would otherwise reject (e.g. ``ready_to_sign`` ->
        ``signature_pending``). ``_run_command`` still re-checks the receipt
        itself for race safety; this only short-circuits the external calls.
        """
        if not _KEY_RE.fullmatch(key):
            raise PreconditionRequiredError("Idempotency-Key must match the published contract", code="idempotency_key_required")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, operation, key)
        receipt = self.repository.read(lambda state: state.idempotency.get(receipt_key))
        if not receipt:
            return None
        if receipt.request_sha256 != fingerprint:
            raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
        if receipt.error:
            raise receipt.error
        etag = self._etag(receipt.aggregate_version) if receipt.aggregate_version else None
        return ServiceResponse(receipt.response_value, receipt.response_status, etag, True)

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
    ) -> str:
        from .domain import OutboxEvent

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
            classification="research_confidential", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event
        return event.event_id

    # ---- authorization, mapping and validation --------------------------

    def _authorize_staff(self, context: ActorContext, roles: set[str]) -> None:
        if self._staff(context, roles):
            return
        raise ForbiddenError("staff role is not allowed for this operation")

    def _authorize_service(self, context: ActorContext, roles: set[str]) -> None:
        if context.principal_type == "service" and bool(context.roles & roles):
            return
        raise ForbiddenError("service identity is not allowed for this operation")

    @staticmethod
    def _staff(context: ActorContext, roles: set[str]) -> bool:
        return context.principal_type == "staff" and bool(context.roles & roles)

    @staticmethod
    def _body(body: dict[str, Any], allowed: set[str], required: set[str]) -> None:
        if not isinstance(body, dict):
            raise ValidationError("request body must be an object")
        extra = set(body) - allowed
        missing = required - set(body)
        if extra:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(extra))}", field_name=sorted(extra)[0])
        if missing:
            raise ValidationError(f"missing fields: {', '.join(sorted(missing))}", field_name=sorted(missing)[0])

    def _validate_instrument_ref(self, item: Any, index: int) -> dict[str, Any]:
        self._body(item, {"market", "instrument_code", "instrument_type"}, {"market", "instrument_code", "instrument_type"})
        if not isinstance(item["market"], str) or not 1 <= len(item["market"]) <= 40:
            raise ValidationError("market must contain 1 to 40 characters", field_name=f"instrument_refs.{index}.market")
        if not isinstance(item["instrument_code"], str) or not 1 <= len(item["instrument_code"]) <= 40:
            raise ValidationError("instrument_code must contain 1 to 40 characters", field_name=f"instrument_refs.{index}.instrument_code")
        if item["instrument_type"] not in _INSTRUMENT_TYPES:
            raise ValidationError("instrument_type is not supported", field_name=f"instrument_refs.{index}.instrument_type")
        return {"market": item["market"], "instrument_code": item["instrument_code"], "instrument_type": item["instrument_type"]}

    def _validate_external_ref(self, item: Any) -> dict[str, Any]:
        self._body(item, {"system", "external_id", "kind", "version", "checksum_sha256"}, {"system", "external_id", "kind"})
        if item["system"] not in _EXTERNAL_SYSTEMS:
            raise ValidationError("source_artifact_ref.system is not supported", field_name="source_artifact_ref.system")
        if not isinstance(item["external_id"], str) or not 1 <= len(item["external_id"]) <= 256:
            raise ValidationError("source_artifact_ref.external_id must contain 1 to 256 characters", field_name="source_artifact_ref.external_id")
        if not isinstance(item["kind"], str) or not 1 <= len(item["kind"]) <= 80:
            raise ValidationError("source_artifact_ref.kind must contain 1 to 80 characters", field_name="source_artifact_ref.kind")
        version = item.get("version")
        if version is not None and (not isinstance(version, str) or len(version) > 128):
            raise ValidationError("source_artifact_ref.version exceeds 128 characters", field_name="source_artifact_ref.version")
        checksum = item.get("checksum_sha256")
        if checksum is not None and not _SHA_RE.fullmatch(str(checksum)):
            raise ValidationError("source_artifact_ref.checksum_sha256 must be lowercase SHA-256", field_name="source_artifact_ref.checksum_sha256")
        result = {"system": item["system"], "external_id": item["external_id"], "kind": item["kind"]}
        if version is not None:
            result["version"] = version
        if checksum is not None:
            result["checksum_sha256"] = checksum
        return result

    def _validate_channel_text(self, item: Any, index: int) -> dict[str, Any]:
        self._body(item, {"channel", "locale", "body", "body_sha256", "template_version", "recommendation_version_ids"},
                   {"channel", "locale", "body", "body_sha256", "template_version", "recommendation_version_ids"})
        if item["channel"] not in _CHANNELS:
            raise ValidationError("channel is not supported", field_name=f"channel_texts.{index}.channel")
        if item["locale"] not in _LOCALES:
            raise ValidationError("locale is not supported", field_name=f"channel_texts.{index}.locale")
        body = item["body"]
        if not isinstance(body, str) or not 1 <= len(body) <= 12000:
            raise ValidationError("body must contain 1 to 12000 characters", field_name=f"channel_texts.{index}.body")
        if str(item["body_sha256"]) != sha256_text(body):
            raise ValidationError("body_sha256 does not match the channel text body", code="channel_text_hash_mismatch", field_name=f"channel_texts.{index}.body_sha256")
        template_version = item["template_version"]
        if not isinstance(template_version, str) or not 1 <= len(template_version) <= 80:
            raise ValidationError("template_version must contain 1 to 80 characters", field_name=f"channel_texts.{index}.template_version")
        recommendation_version_ids = item["recommendation_version_ids"]
        if not isinstance(recommendation_version_ids, list) or not 1 <= len(recommendation_version_ids) <= 50:
            raise ValidationError("recommendation_version_ids must contain 1 to 50 items", field_name=f"channel_texts.{index}.recommendation_version_ids")
        recommendation_version_ids = [require_uuid(value, "recommendation_version_ids") for value in recommendation_version_ids]
        return {
            "channel": item["channel"], "locale": item["locale"], "body": body, "body_sha256": item["body_sha256"],
            "template_version": template_version, "recommendation_version_ids": recommendation_version_ids,
        }

    def _validate_audience_policy(self, item: Any) -> dict[str, Any]:
        self._body(item, {"policy_id", "policy_version", "service_codes", "required_entitlement_state", "restriction_policy_version", "audience_policy_sha256"},
                   {"policy_id", "policy_version", "service_codes", "required_entitlement_state", "restriction_policy_version", "audience_policy_sha256"})
        policy_id = require_uuid(item["policy_id"], "audience_policy.policy_id")
        policy_version = item["policy_version"]
        if not isinstance(policy_version, str) or not 1 <= len(policy_version) <= 80:
            raise ValidationError("audience_policy.policy_version must contain 1 to 80 characters", field_name="audience_policy.policy_version")
        service_codes = item["service_codes"]
        if not isinstance(service_codes, list) or not 1 <= len(service_codes) <= 30 or any(not isinstance(code, str) or len(code) > 80 for code in service_codes):
            raise ValidationError("audience_policy.service_codes must contain 1 to 30 strings up to 80 characters", field_name="audience_policy.service_codes")
        if item["required_entitlement_state"] != "active":
            raise ValidationError("audience_policy.required_entitlement_state must be active", field_name="audience_policy.required_entitlement_state")
        restriction_policy_version = item["restriction_policy_version"]
        if not isinstance(restriction_policy_version, str) or not 1 <= len(restriction_policy_version) <= 80:
            raise ValidationError("audience_policy.restriction_policy_version must contain 1 to 80 characters", field_name="audience_policy.restriction_policy_version")
        result = {
            "policy_id": policy_id, "policy_version": policy_version, "service_codes": service_codes,
            "required_entitlement_state": "active", "restriction_policy_version": restriction_policy_version,
        }
        if str(item["audience_policy_sha256"]) != canonical_hash(result):
            raise ValidationError("audience_policy_sha256 does not match the policy fields", code="audience_policy_hash_mismatch", field_name="audience_policy.audience_policy_sha256")
        result["audience_policy_sha256"] = item["audience_policy_sha256"]
        return result

    def _verify_qualification(self, context: ActorContext, credential_id: str, correlation_id: str | None):
        try:
            return self.qualifications.verify(context.tenant_id, context.actor_id, credential_id)
        except (ForbiddenError, UnauthorizedError, DependencyUnavailableError) as exc:
            self._audit_failure(context, "verify_signer_qualification", correlation_id, exc.code)
            raise

    @staticmethod
    def _reopen_for_retry(request: SignatureRequest, version: ReportVersion, now: datetime) -> None:
        request.status = "failed"
        request.version += 1
        request.updated_at = now
        version.status = "ready_to_sign"
        version.version += 1
        version.updated_at = now

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
    def _stored_report(state: StoreState, tenant_id: str, report_id: str) -> AdvisoryReport:
        report = state.reports.get((tenant_id, report_id))
        if not report:
            raise NotFoundError("report was not found")
        return report

    @staticmethod
    def _stored_version(state: StoreState, tenant_id: str, report_version_id: str) -> ReportVersion:
        version = state.versions.get((tenant_id, report_version_id))
        if not version:
            raise NotFoundError("report version was not found")
        return version

    @staticmethod
    def _stored_signature(state: StoreState, tenant_id: str, signature_id: str) -> Signature:
        signature = state.signatures.get((tenant_id, signature_id))
        if not signature:
            raise NotFoundError("signature was not found")
        return signature

    @staticmethod
    def _report_state_label(state: StoreState, report: AdvisoryReport) -> str:
        versions = [item for (tenant, _), item in state.versions.items() if tenant == report.tenant_id and item.report_id == report.id]
        if not versions:
            return "draft"
        if any(item.status == "signed" for item in versions):
            return "active"
        if all(item.status == "withdrawn" for item in versions):
            return "withdrawn"
        return "draft"

    def _report_actions(self, context: ActorContext, state_label: str) -> list[str]:
        if state_label != "withdrawn" and self._staff(context, {"analyst"}):
            return ["create_report_version"]
        return []

    def _version_actions(self, context: ActorContext, version: ReportVersion) -> list[str]:
        actions: list[str] = []
        if version.status in {"draft", "in_review", "changes_requested"} and self._staff(context, {"analyst_reviewer", "compliance_reviewer"}):
            actions.append("record_report_review")
        if version.status == "in_review" and self._staff(context, {"analyst"}):
            actions.append("freeze_report_version")
        if version.status == "ready_to_sign" and self._staff(context, {"analyst_signer"}):
            actions.append("request_analyst_signature")
        return sorted(set(actions))

    def _report_outcome(self, state: StoreState, report: AdvisoryReport, context: ActorContext, status: int) -> _Outcome:
        state_label = self._report_state_label(state, report)
        return _Outcome(report.to_api(self._report_actions(context, state_label), state_label), status, "report", report.id, report.version)

    def _version_outcome(self, version: ReportVersion, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(version.to_api(self._version_actions(context, version)), status, "report_version", version.id, version.version)

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
        self._record_audit(state, context, action, aggregate_type, aggregate_id, aggregate_version, correlation_id, result, reason_code)

    def _record_audit(
        self,
        state: StoreState,
        context: ActorContext,
        action: str,
        aggregate_type: str,
        aggregate_id: str | None,
        aggregate_version: int | None,
        correlation_id: str,
        result: str,
        reason_code: str | None,
    ) -> None:
        from .domain import AuditEvent

        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=f"m07.{action}", aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str, aggregate_id: str | None = None) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._record_audit(state, context, action, "security" if aggregate_id is None else "report", aggregate_id, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
