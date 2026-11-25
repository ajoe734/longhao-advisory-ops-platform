from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any, Callable

from .domain import (
    ActorContext,
    AudienceSnapshot,
    AuditEvent,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    EntitlementRecord,
    ForbiddenError,
    IdempotencyReceipt,
    Job,
    NotFoundError,
    OutboxEvent,
    PreconditionRequiredError,
    Publication,
    RecommendationSeries,
    RecommendationVersion,
    Recipient,
    StaleVersionError,
    ValidationError,
    VerifiedReport,
    canonical_hash,
    new_id,
    parse_time,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import ContentAssetPort, EntitlementAudiencePort, EvidencePort, ReportBundlePort
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_ACTIONS = {"entry", "update", "exit", "hold", "correction"}
_INSTRUMENT_TYPES = {"equity", "etf", "fund", "bond", "other_approved"}
_EXTERNAL_SYSTEMS = {
    "pantheon", "agora", "legendflow", "signature_provider",
    "screening_provider", "payment_provider", "accounting_provider", "messaging_provider",
}
_PUBLICATION_ACTIVE = {"queued", "publishing", "published", "partially_failed"}
_MAX_PUBLISH_ATTEMPTS = 5


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
class PublishOutcome:
    status: str
    result_code: str
    publication_id: str | None = None


class PublicationService:
    """M08 command/query application service.

    Signature verification drives the only publish path: there is no second
    manual "publish" step. Content is written once at
    ``create_recommendation_version`` and never mutated; a post-signature
    swap is caught by the bundle cross-check before any publication exists.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        content_assets: ContentAssetPort,
        evidence: EvidencePort,
        report_bundles: ReportBundlePort,
        entitlements: EntitlementAudiencePort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False) for adapter in (repository, content_assets, evidence, report_bundles, entitlements)
        ):
            raise RuntimeError("fake M08 adapters are forbidden in production")
        self.repository = repository
        self.content_assets = content_assets
        self.evidence = evidence
        self.report_bundles = report_bundles
        self.entitlements = entitlements
        self._clock = clock

    # ---- commands ----------------------------------------------------

    def create_recommendation_series(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"analyst"})
        self._body(body, {"instrument_ref", "strategy_scope_ref", "research_origin_ref"}, {"instrument_ref", "strategy_scope_ref", "research_origin_ref"})
        instrument_ref = self._validate_instrument_ref(body["instrument_ref"])
        strategy_scope_ref = str(body["strategy_scope_ref"])
        if not strategy_scope_ref or len(strategy_scope_ref) > 160:
            raise ValidationError("strategy_scope_ref must contain 1 to 160 characters", field_name="strategy_scope_ref")
        research_origin_ref = self._validate_external_ref(body["research_origin_ref"])
        series_code = self._series_code(instrument_ref, strategy_scope_ref)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            existing_id = state.series_by_code.get((context.tenant_id, series_code))
            if existing_id:
                series = state.series[(context.tenant_id, existing_id)]
                return self._series_outcome(series, context, state, 201)
            series = RecommendationSeries(
                id=new_id(),
                tenant_id=context.tenant_id,
                version=1,
                instrument_ref=instrument_ref,
                strategy_scope_ref=strategy_scope_ref,
                research_origin_ref=research_origin_ref,
                series_code=series_code,
                created_at=now,
                updated_at=now,
            )
            state.series[(context.tenant_id, series.id)] = series
            state.series_by_code[(context.tenant_id, series_code)] = series.id
            return self._series_outcome(series, context, state, 201)

        return self._run_command(context, "create_recommendation_series", idempotency_key, body, correlation_id, mutate)

    def create_recommendation_version(
        self,
        context: ActorContext,
        series_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"analyst"})
        series_id = require_uuid(series_id, "series_id")
        self._body(
            body,
            {"action", "rationale", "supporting_report_version_ids", "valid_from", "valid_until", "supersedes_version_id", "source_candidate_id"},
            {"action", "rationale", "supporting_report_version_ids", "valid_from", "valid_until", "source_candidate_id"},
        )
        if body["action"] not in _ACTIONS:
            raise ValidationError("action is not supported", field_name="action")
        rationale = body["rationale"]
        if not isinstance(rationale, str) or not 1 <= len(rationale) <= 12000:
            raise ValidationError("rationale must contain 1 to 12000 characters", field_name="rationale")
        supporting_ids = body["supporting_report_version_ids"]
        if not isinstance(supporting_ids, list) or not 1 <= len(supporting_ids) <= 20:
            # OpenAPI's request schema allows an empty array for a future draft-only
            # flow; no such flow is published for this operationId yet (no GET exists
            # to return a version without a supporting report), so this module
            # requires at least one supporting report version. See
            # database/proposals/M08/contract-changes.md for the tracked gap.
            raise ValidationError(
                "supporting_report_version_ids must contain 1 to 20 items",
                code="supporting_report_version_required",
                field_name="supporting_report_version_ids",
            )
        supporting_ids = [require_uuid(value, "supporting_report_version_ids") for value in supporting_ids]
        if len(set(supporting_ids)) != len(supporting_ids):
            raise ValidationError("supporting_report_version_ids must not repeat", field_name="supporting_report_version_ids")
        valid_from = parse_time(body["valid_from"], "valid_from")
        valid_until = parse_time(body["valid_until"], "valid_until")
        if valid_until <= valid_from:
            raise ValidationError("valid_until must be after valid_from", field_name="valid_until")
        source_candidate_id = require_uuid(body["source_candidate_id"], "source_candidate_id")
        supersedes_version_id = body.get("supersedes_version_id")
        if supersedes_version_id is not None:
            supersedes_version_id = require_uuid(supersedes_version_id, "supersedes_version_id")
        if body["action"] == "entry" and supersedes_version_id is not None:
            raise ValidationError("an entry version cannot supersede a prior version", field_name="supersedes_version_id")
        if body["action"] != "entry" and supersedes_version_id is None:
            raise ValidationError("update, exit, hold and correction versions must reference the version they continue", field_name="supersedes_version_id")
        stored_asset = self.content_assets.store(context.tenant_id, rationale)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            series = self._stored_series(state, context.tenant_id, series_id)
            self._cas(series.version, expected_version)
            if supersedes_version_id is not None:
                prior = state.versions.get((context.tenant_id, supersedes_version_id))
                if not prior or prior.series_id != series_id:
                    raise ValidationError("supersedes_version_id must reference a version in the same series", field_name="supersedes_version_id")
            version_number = 1 + sum(
                1 for (tenant, _), item in state.versions.items() if tenant == context.tenant_id and item.series_id == series_id
            )
            version = RecommendationVersion(
                id=new_id(),
                tenant_id=context.tenant_id,
                series_id=series_id,
                version_number=version_number,
                action=body["action"],
                rationale_asset_id=stored_asset.asset_id,
                content_sha256=stored_asset.sha256,
                supporting_report_version_ids=supporting_ids,
                valid_from=valid_from,
                valid_until=valid_until,
                source_candidate_id=source_candidate_id,
                supersedes_version_id=supersedes_version_id,
                created_at=now,
                updated_at=now,
            )
            state.versions[(context.tenant_id, version.id)] = version
            series.latest_version_id = version.id
            series.version += 1
            series.updated_at = now
            return self._version_outcome(version, context, state, 201)

        request = {"series_id": series_id, **body}
        return self._run_command(context, "create_recommendation_version", idempotency_key, request, correlation_id, mutate)

    def withdraw_publication(
        self,
        context: ActorContext,
        publication_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"analyst_reviewer", "compliance_supervisor"})
        publication_id = require_uuid(publication_id, "publication_id")
        self._body(body, {"reason_code", "evidence_asset_id", "notify_recipients", "notice_template_version"}, {"reason_code", "evidence_asset_id", "notify_recipients", "notice_template_version"})
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        evidence_asset_id = require_uuid(body["evidence_asset_id"], "evidence_asset_id")
        if not self.evidence.exists(context.tenant_id, evidence_asset_id):
            raise ValidationError("withdrawal evidence does not exist", code="evidence_not_found", field_name="evidence_asset_id")
        if not isinstance(body["notify_recipients"], bool):
            raise ValidationError("notify_recipients must be boolean", field_name="notify_recipients")
        notice_template_version = str(body["notice_template_version"])
        if not 1 <= len(notice_template_version) <= 80:
            raise ValidationError("notice_template_version must contain 1 to 80 characters", field_name="notice_template_version")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            publication = self._stored_publication(state, context.tenant_id, publication_id)
            self._cas(publication.version, expected_version)
            if publication.state not in _PUBLICATION_ACTIVE:
                raise ConflictError("publication can no longer be withdrawn", code="publication_terminal")
            publication.state = "withdrawn"
            publication.withdrawal_reason_code = reason_code
            publication.version += 1
            publication.updated_at = now
            withdrawal_record_id = new_id()
            self._emit(
                state,
                event_type="publication.withdrawn",
                tenant_id=context.tenant_id,
                aggregate_id=publication.id,
                aggregate_version=publication.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:withdrawn",
                payload={
                    "publication_id": publication.id,
                    "withdrawal_record_id": withdrawal_record_id,
                    "effective_at": wire_time(now),
                    "notify_recipients": body["notify_recipients"],
                },
                now=now,
            )
            return self._publication_outcome(publication, context, 200)

        request = {"publication_id": publication_id, **body}
        return self._run_command(context, "withdraw_publication", idempotency_key, request, correlation_id, mutate)

    def link_publication_correction(
        self,
        context: ActorContext,
        publication_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"analyst_reviewer"})
        publication_id = require_uuid(publication_id, "publication_id")
        self._body(body, {"replacement_publication_id", "replacement_report_version_id", "reason_code"}, {"replacement_publication_id", "replacement_report_version_id", "reason_code"})
        replacement_publication_id = require_uuid(body["replacement_publication_id"], "replacement_publication_id")
        replacement_report_version_id = require_uuid(body["replacement_report_version_id"], "replacement_report_version_id")
        if replacement_publication_id == publication_id:
            raise ValidationError("a publication cannot correct itself", field_name="replacement_publication_id")
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            original = self._stored_publication(state, context.tenant_id, publication_id)
            self._cas(original.version, expected_version)
            if original.state not in {"published", "partially_failed"}:
                raise ConflictError("only a published message can be corrected", code="publication_not_correctable")
            replacement = state.publications.get((context.tenant_id, replacement_publication_id))
            if not replacement:
                raise ValidationError("replacement_publication_id does not reference an existing publication", code="replacement_not_found", field_name="replacement_publication_id")
            if replacement.report_version_id != replacement_report_version_id:
                raise ValidationError("replacement_report_version_id does not match the replacement publication", field_name="replacement_report_version_id")
            if replacement.state != "published":
                raise ConflictError("replacement publication must itself be published", code="replacement_not_published")
            if any(
                item.correction_publication_id == replacement_publication_id
                for (tenant, _), item in state.publications.items()
                if tenant == context.tenant_id
            ):
                raise ConflictError("replacement publication already corrects another message", code="replacement_already_linked")
            original.state = "corrected"
            original.correction_publication_id = replacement_publication_id
            original.version += 1
            original.updated_at = now
            self._emit(
                state,
                event_type="publication.corrected",
                tenant_id=context.tenant_id,
                aggregate_id=original.id,
                aggregate_version=original.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:corrected",
                payload={
                    "publication_id": original.id,
                    "replacement_publication_id": replacement_publication_id,
                    "replacement_report_version_id": replacement_report_version_id,
                },
                now=now,
            )
            return self._publication_outcome(original, context, 200)

        request = {"publication_id": publication_id, **body}
        return self._run_command(context, "link_publication_correction", idempotency_key, request, correlation_id, mutate)

    # ---- queries -------------------------------------------------------

    def get_publication(self, context: ActorContext, publication_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize(context, {"analyst", "service_agent", "compliance_reviewer"})
        publication_id = require_uuid(publication_id, "publication_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            publication = self._stored_publication(state, context.tenant_id, publication_id)
            self._audit(state, context, "get_publication", "publication", publication.id, publication.version, correlation_id, "succeeded")
            return ServiceResponse(publication.to_api(self._publication_actions(publication, context)), etag=self._etag(publication.version))

        return self.repository.atomic(query)

    def list_publications(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize(context, {"analyst", "service_agent", "compliance_reviewer"})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in {"queued", "publishing", "published", "partially_failed", "expired", "corrected", "withdrawn"}:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in state.publications.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not state_filter or item.state == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_publications", "publication", None, None, correlation_id, "succeeded")
            return ServiceResponse(
                {
                    "items": [item.to_api(self._publication_actions(item, context)) for item in page],
                    "next_cursor": page[-1].id if len(rows) > limit else None,
                    "as_of": wire_time(snapshot_at),
                    "total_estimate": len(rows),
                }
            )

        return self.repository.atomic(query)

    # ---- event projections ---------------------------------------------

    def apply_report_signature_verified(self, state: StoreState, tenant_id: str, payload: dict[str, Any]) -> str:
        """Records the verified signature and enqueues a publish job per match.

        Returns a human-readable result code; never raises for a legitimate
        hash mismatch or expiry, because those are per-version business
        rejections, not consumer-level failures (other matches in the same
        event must still be processed).
        """
        report_version_id = payload["report_version_id"]
        existing = state.verified_reports.get((tenant_id, report_version_id))
        verified = VerifiedReport(
            tenant_id=tenant_id,
            report_version_id=report_version_id,
            signature_id=payload["signature_id"],
            report_content_sha256=payload["report_content_sha256"],
            bundle_manifest_sha256=payload["bundle_manifest_sha256"],
            verified_at=parse_time(payload["verified_at"], "verified_at"),
            valid_until=parse_time(payload["valid_until"], "valid_until"),
            revoked=existing.revoked if existing else False,
        )
        state.verified_reports[(tenant_id, report_version_id)] = verified
        matched = 0
        for (tenant, _), version in state.versions.items():
            if tenant != tenant_id or report_version_id not in version.supporting_report_version_ids:
                continue
            if (tenant_id, version.id) in state.publications_by_version:
                continue
            matched += 1
            already_queued = any(
                job.kind == "publish_recommendation_version"
                and job.payload.get("recommendation_version_id") == version.id
                and job.status in {"pending", "retry", "leased"}
                for (tenant, _), job in state.jobs.items()
                if tenant == tenant_id
            )
            if already_queued:
                continue
            job = Job(
                id=new_id(),
                tenant_id=tenant_id,
                kind="publish_recommendation_version",
                payload={"recommendation_version_id": version.id, "triggering_report_version_id": report_version_id},
            )
            state.jobs[(tenant_id, job.id)] = job
        return f"signature_verified_recorded:{matched}"

    def apply_report_signature_revoked(self, state: StoreState, tenant_id: str, payload: dict[str, Any]) -> str:
        report_version_id = payload["report_version_id"]
        existing = state.verified_reports.get((tenant_id, report_version_id))
        if existing:
            existing.revoked = True
            existing.revocation_id = payload["revocation_id"]
        cancelled = 0
        for (tenant, _), job in state.jobs.items():
            if tenant != tenant_id or job.kind != "publish_recommendation_version" or job.status not in {"pending", "retry"}:
                continue
            version = state.versions.get((tenant_id, job.payload.get("recommendation_version_id", "")))
            if version and report_version_id in version.supporting_report_version_ids:
                job.status = "dead_letter"
                job.last_error_code = "supporting_report_signature_revoked"
                cancelled += 1
        return f"signature_revoked_recorded:{cancelled}"

    def apply_entitlement_changed(self, state: StoreState, tenant_id: str, payload: dict[str, Any]) -> str:
        record = EntitlementRecord(
            tenant_id=tenant_id,
            customer_id=payload["customer_id"],
            entitlement_id=payload["entitlement_id"],
            contract_id=payload["contract_id"],
            state=payload["state"],
            version=payload.get("aggregate_version", 1),
            valid_until=parse_time(payload["valid_until"], "valid_until"),
            updated_at=self._clock(),
        )
        state.entitlements[(tenant_id, record.entitlement_id)] = record
        return "entitlement_indexed"

    def apply_delivery_status_changed(self, state: StoreState, tenant_id: str, payload: dict[str, Any]) -> str:
        # M08 is a listed consumer but owns no mutable projection for this
        # event in this phase; the inbox receipt alone proves contract
        # compliance without inventing undocumented M08 side effects.
        return "delivery_status_noted"

    # ---- publish worker entry point -------------------------------------

    def process_publish_job(self, job_id: str, tenant_id: str, worker_id: str) -> PublishOutcome:
        def transaction(state: StoreState) -> PublishOutcome:
            job = state.jobs.get((tenant_id, job_id))
            if job is None:
                raise NotFoundError("publish job was not found")
            version = state.versions.get((tenant_id, job.payload["recommendation_version_id"]))
            if version is None:
                job.status = "dead_letter"
                job.result_code = "recommendation_version_missing"
                return PublishOutcome("dead_letter", "recommendation_version_missing")
            if (tenant_id, version.id) in state.publications_by_version:
                job.status = "complete"
                job.result_code = "already_published"
                return PublishOutcome("complete", "already_published")

            now = self._clock()
            if version.valid_until <= now:
                job.status = "complete"
                job.result_code = "expired_blocked"
                return PublishOutcome("blocked", "expired_blocked")

            verified_rows: list[VerifiedReport] = []
            for report_version_id in version.supporting_report_version_ids:
                row = state.verified_reports.get((tenant_id, report_version_id))
                if row is None or row.revoked or row.valid_until <= now:
                    job.status = "complete"
                    job.result_code = "signature_not_ready"
                    return PublishOutcome("pending", "signature_not_ready")
                verified_rows.append(row)

            bundle_manifest_sha256 = None
            report_content_sha256 = None
            for row in verified_rows:
                bundle = self.report_bundles.verify(tenant_id, row.report_version_id, version.id, version.content_sha256)
                if not bundle.consistent:
                    job.status = "dead_letter"
                    job.result_code = "bundle_hash_mismatch"
                    self._audit_as(
                        state, tenant_id, self._WORKER_ACTOR_ID, "publish_worker", "publish_recommendation_version",
                        "recommendation_version", version.id, version.version,
                        job.payload.get("triggering_report_version_id", ""), "rejected", "bundle_hash_mismatch",
                    )
                    return PublishOutcome("dead_letter", "bundle_hash_mismatch")
                bundle_manifest_sha256 = bundle.bundle_manifest_sha256
                report_content_sha256 = bundle.report_content_sha256

            try:
                candidates = self.entitlements.resolve(tenant_id, self._instrument_ref_for(state, tenant_id, version), now)
            except DependencyUnavailableError as exc:
                job.attempt_count += 1
                job.last_error_code = exc.code
                if job.attempt_count >= _MAX_PUBLISH_ATTEMPTS:
                    job.status = "dead_letter"
                    return PublishOutcome("dead_letter", exc.code)
                job.status = "retry"
                job.next_attempt_at = now + timedelta(seconds=min(300, 2 ** job.attempt_count))
                return PublishOutcome("retry", exc.code)

            publication = Publication(
                id=new_id(),
                tenant_id=tenant_id,
                recommendation_version_id=version.id,
                report_version_id=verified_rows[0].report_version_id,
                signature_id=verified_rows[0].signature_id,
                recommendation_version_ids=[version.id],
                bundle_manifest_sha256=bundle_manifest_sha256 or "",
                created_from_signature_event_id=job.payload.get("triggering_report_version_id", ""),
                audience_snapshot_id="",
                valid_until=version.valid_until,
                created_at=now,
                updated_at=now,
                state="publishing",
            )
            eligible = [candidate for candidate in candidates if candidate.eligible]
            snapshot = AudienceSnapshot(
                id=new_id(),
                tenant_id=tenant_id,
                publication_id=publication.id,
                rule_version=version.supporting_report_version_ids[0],
                evaluated_at=now,
                snapshot_sha256=canonical_hash([candidate.customer_id for candidate in eligible]),
                recipient_count=len(eligible),
                created_at=now,
                updated_at=now,
            )
            publication.audience_snapshot_id = snapshot.id
            publication.recipient_count = len(eligible)
            publication.state = "published"
            publication.published_at = now
            state.snapshots[(tenant_id, snapshot.id)] = snapshot
            for candidate in eligible:
                recipient = Recipient(
                    id=new_id(),
                    tenant_id=tenant_id,
                    snapshot_id=snapshot.id,
                    publication_id=publication.id,
                    customer_id=candidate.customer_id,
                    entitlement_id=candidate.entitlement_id,
                    entitlement_version=candidate.entitlement_version,
                    eligibility_reason=candidate.reason_code,
                    created_at=now,
                    updated_at=now,
                )
                state.recipients[(tenant_id, recipient.id)] = recipient
            state.publications[(tenant_id, publication.id)] = publication
            state.publications_by_version[(tenant_id, version.id)] = publication.id
            self._emit(
                state,
                event_type="publication.created",
                tenant_id=tenant_id,
                aggregate_id=publication.id,
                aggregate_version=1,
                correlation_id=new_id(),
                idempotency_key=f"publish:{job.id}",
                payload={
                    "publication_id": publication.id,
                    "report_version_id": publication.report_version_id,
                    "signature_id": publication.signature_id,
                    "audience_snapshot_id": snapshot.id,
                    "bundle_manifest_sha256": publication.bundle_manifest_sha256,
                    "valid_until": wire_time(publication.valid_until),
                },
                now=now,
            )
            self._audit_as(
                state, tenant_id, self._WORKER_ACTOR_ID, "publish_worker", "publish_recommendation_version",
                "publication", publication.id, 1, job.payload.get("triggering_report_version_id", ""), "succeeded", None,
            )
            job.status = "complete"
            job.result_code = "published"
            return PublishOutcome("published", "published", publication.id)

        return self.repository.atomic(transaction)

    # ---- shared helpers --------------------------------------------------

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
        self,
        state: StoreState,
        *,
        event_type: str,
        tenant_id: str,
        aggregate_id: str,
        aggregate_version: int,
        correlation_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        now: datetime,
        causation_id: str | None = None,
    ) -> None:
        sequence = 1 + max(
            (event.sequence_no for (tenant, _), event in state.outbox.items() if tenant == tenant_id and event.aggregate_id == aggregate_id),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(), event_type=event_type, tenant_id=tenant_id, aggregate_type="publication",
            aggregate_id=aggregate_id, aggregate_version=aggregate_version, sequence_no=sequence,
            correlation_id=correlation_id, causation_id=causation_id, idempotency_key=idempotency_key,
            classification="research_confidential", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    def _authorize(self, context: ActorContext, roles: set[str]) -> None:
        if context.has_any(roles):
            return
        raise ForbiddenError("actor cannot perform this M08 operation")

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

    @staticmethod
    def _validate_instrument_ref(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) - {"market", "instrument_code", "instrument_type"}:
            raise ValidationError("instrument_ref has unsupported fields", field_name="instrument_ref")
        if {"market", "instrument_code", "instrument_type"} - set(value):
            raise ValidationError("instrument_ref is missing required fields", field_name="instrument_ref")
        if not isinstance(value["market"], str) or not 1 <= len(value["market"]) <= 40:
            raise ValidationError("instrument_ref.market must contain 1 to 40 characters", field_name="instrument_ref.market")
        if not isinstance(value["instrument_code"], str) or not 1 <= len(value["instrument_code"]) <= 40:
            raise ValidationError("instrument_ref.instrument_code must contain 1 to 40 characters", field_name="instrument_ref.instrument_code")
        if value["instrument_type"] not in _INSTRUMENT_TYPES:
            raise ValidationError("instrument_ref.instrument_type is not supported", field_name="instrument_ref.instrument_type")
        return {"market": value["market"], "instrument_code": value["instrument_code"], "instrument_type": value["instrument_type"]}

    @staticmethod
    def _validate_external_ref(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) - {"system", "external_id", "kind", "version", "checksum_sha256"}:
            raise ValidationError("research_origin_ref has unsupported fields", field_name="research_origin_ref")
        if {"system", "external_id"} - set(value):
            raise ValidationError("research_origin_ref is missing required fields", field_name="research_origin_ref")
        if value["system"] not in _EXTERNAL_SYSTEMS:
            raise ValidationError("research_origin_ref.system is not supported", field_name="research_origin_ref.system")
        if not isinstance(value["external_id"], str) or not 1 <= len(value["external_id"]) <= 256:
            raise ValidationError("research_origin_ref.external_id must contain 1 to 256 characters", field_name="research_origin_ref.external_id")
        if "kind" in value and (not isinstance(value["kind"], str) or len(value["kind"]) > 80):
            raise ValidationError("research_origin_ref.kind exceeds 80 characters", field_name="research_origin_ref.kind")
        if "version" in value and (not isinstance(value["version"], str) or len(value["version"]) > 128):
            raise ValidationError("research_origin_ref.version exceeds 128 characters", field_name="research_origin_ref.version")
        if "checksum_sha256" in value and not re.fullmatch(r"[a-f0-9]{64}", str(value["checksum_sha256"])):
            raise ValidationError("research_origin_ref.checksum_sha256 must be lowercase SHA-256", field_name="research_origin_ref.checksum_sha256")
        return dict(value)

    @staticmethod
    def _series_code(instrument_ref: dict[str, Any], strategy_scope_ref: str) -> str:
        return f"{instrument_ref['market']}:{instrument_ref['instrument_code']}:{strategy_scope_ref}"

    @staticmethod
    def _instrument_ref_for(state: StoreState, tenant_id: str, version: RecommendationVersion) -> dict[str, Any]:
        series = state.series[(tenant_id, version.series_id)]
        return series.instrument_ref

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _etag(version: int) -> str:
        return f'"{version}"'

    def _series_derived_state(self, state: StoreState, tenant_id: str, series: RecommendationSeries) -> str:
        if series.latest_version_id is None:
            return "open"
        latest = state.versions.get((tenant_id, series.latest_version_id))
        if latest and latest.action == "exit" and (tenant_id, latest.id) in state.publications_by_version:
            return "closed"
        return "open"

    def _version_derived_state(self, state: StoreState, tenant_id: str, version: RecommendationVersion) -> str:
        publication_id = state.publications_by_version.get((tenant_id, version.id))
        if publication_id:
            publication = state.publications[(tenant_id, publication_id)]
            return "withdrawn" if publication.state == "withdrawn" else "signed"
        if version.valid_until <= self._clock():
            return "expired"
        return "under_review"

    def _series_outcome(self, series: RecommendationSeries, context: ActorContext, state: StoreState, status: int) -> _Outcome:
        derived = self._series_derived_state(state, context.tenant_id, series)
        actions = ["create_recommendation_version"] if context.has_any({"analyst"}) and derived == "open" else []
        return _Outcome(series.to_api(actions, derived), status, "recommendation_series", series.id, series.version)

    def _version_outcome(self, version: RecommendationVersion, context: ActorContext, state: StoreState, status: int) -> _Outcome:
        derived = self._version_derived_state(state, context.tenant_id, version)
        return _Outcome(version.to_api([], derived), status, "recommendation_version", version.id, version.version)

    def _publication_actions(self, publication: Publication, context: ActorContext) -> list[str]:
        actions: list[str] = []
        if publication.state in _PUBLICATION_ACTIVE and context.has_any({"analyst_reviewer", "compliance_supervisor"}):
            actions.append("withdraw_publication")
        if publication.state in {"published", "partially_failed"} and context.has_any({"analyst_reviewer"}):
            actions.append("link_publication_correction")
        return sorted(actions)

    def _publication_outcome(self, publication: Publication, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(publication.to_api(self._publication_actions(publication, context)), status, "publication", publication.id, publication.version)

    @staticmethod
    def _stored_series(state: StoreState, tenant_id: str, series_id: str) -> RecommendationSeries:
        series = state.series.get((tenant_id, series_id))
        if not series:
            raise NotFoundError("recommendation series was not found")
        return series

    @staticmethod
    def _stored_publication(state: StoreState, tenant_id: str, publication_id: str) -> Publication:
        publication = state.publications.get((tenant_id, publication_id))
        if not publication:
            raise NotFoundError("publication was not found")
        return publication

    _WORKER_ACTOR_ID = "00000000-0000-0000-0000-000000000001"

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
        self._audit_as(
            state, context.tenant_id, context.actor_id, context.purpose, action,
            aggregate_type, aggregate_id, aggregate_version, correlation_id, result, reason_code,
        )

    def _audit_as(
        self,
        state: StoreState,
        tenant_id: str,
        actor_id: str,
        purpose: str,
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
                tenant_id=tenant_id,
                actor_id=actor_id,
                purpose=purpose,
                action=f"m08.{action}",
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                correlation_id=correlation_id,
                result=result,
                reason_code=reason_code,
                recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security", None, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
