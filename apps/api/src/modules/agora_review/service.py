from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .domain import (
    ActorContext,
    AuditEvent,
    ConflictError,
    Conversation,
    DomainError,
    ForbiddenError,
    IdempotencyReceipt,
    NotFoundError,
    OutboxEvent,
    PreconditionRequiredError,
    Revision,
    RevisionResult,
    ReviewCase,
    StaleVersionError,
    ValidationError,
    canonical_hash,
    case_db_status_for_state,
    external_ref_matches,
    new_id,
    require_external_ref,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import ArtifactDiffPort, ServantPort, WebhookRequest, WebhookSecretPort, verify_servant_webhook
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_WEBHOOK_TIMESTAMP_RE = re.compile(r"^[0-9]{10}$")
_PURPOSES = {"review", "revision", "counter_analysis", "evidence_request"}
_REQUESTED_OUTPUTS = {"revised_report", "additional_evidence", "counter_analysis", "message_draft"}
_PENDING_REVISION_STATUSES = {"requested", "accepted", "running", "failed"}
_TERMINAL_REVISION_STATUSES = {"completed", "failed", "cancelled", "stale"}
_TERMINAL_CASE_STATUSES = {"rejected", "closed", "ready_to_sign"}
_WEBHOOK_NONCE_TTL_SECONDS = 600


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
    audit_result: str | None = None
    audit_reason_code: str | None = None


class AgoraReviewService:
    """M06 research review workbench and Agora/Servant collaboration service.

    Every state-changing operation runs inside one repository callback so
    domain state, audit, outbox and the idempotency receipt commit atomically.
    Outbound Servant network calls that must not block that transaction are
    limited to the synchronous conversation-open step (a 201 command); the
    revision job submission itself is dispatched by the worker after commit.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        servant: ServantPort,
        diff_port: ArtifactDiffPort,
        webhook_secrets: WebhookSecretPort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False) for adapter in (repository, servant, diff_port, webhook_secrets)
        ):
            raise RuntimeError("fake M06 adapters are forbidden in production")
        self.repository = repository
        self.servant = servant
        self.diff_port = diff_port
        self.webhook_secrets = webhook_secrets
        self._clock = clock

    # ---- commands ---------------------------------------------------------

    def assign_review_case(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"research_supervisor"})
        case_id = require_uuid(case_id, "review_case_id")
        self._body(body, {"assignee_id", "reason_code", "effective_at"}, {"assignee_id", "reason_code"})
        assignee_id = require_uuid(body["assignee_id"], "assignee_id")
        reason = str(body["reason_code"])
        if not 1 <= len(reason) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        if "effective_at" in body and body["effective_at"] is not None:
            self._parse_time(body["effective_at"], "effective_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._cas(case.version, expected_version)
            if case.status in {"rejected", "closed"}:
                raise ConflictError("review case is already resolved", code="case_terminal")
            case.assignee_id = assignee_id
            if case.status == "queued":
                case.status = "assigned"
            case.version += 1
            case.updated_at = now
            return self._case_outcome(state, case, context)

        return self._run_command(context, "assign_review_case", idempotency_key, {"review_case_id": case_id, **body}, correlation_id, mutate)

    def open_servant_conversation(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_analyst_role(context)
        case_id = require_uuid(case_id, "review_case_id")
        self._body(body, {"candidate_id", "artifact_ref", "existing_conversation_ref", "allowed_evidence_refs", "purpose"},
                   {"candidate_id", "artifact_ref", "existing_conversation_ref", "allowed_evidence_refs", "purpose"})
        candidate_id = require_uuid(body["candidate_id"], "candidate_id")
        artifact_ref = require_external_ref(body["artifact_ref"], "artifact_ref")
        existing_ref = body["existing_conversation_ref"]
        if existing_ref is not None:
            existing_ref = require_external_ref(existing_ref, "existing_conversation_ref")
        evidence_refs = body["allowed_evidence_refs"]
        if not isinstance(evidence_refs, list) or not 1 <= len(evidence_refs) <= 100:
            raise ValidationError("allowed_evidence_refs must contain 1 to 100 items", field_name="allowed_evidence_refs")
        evidence_refs = [require_external_ref(value, "allowed_evidence_refs") for value in evidence_refs]
        if body["purpose"] not in _PURPOSES:
            raise ValidationError("purpose is not supported", field_name="purpose")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        request_snapshot = {"review_case_id": case_id, **body}

        def check_preconditions(state: StoreState) -> ReviewCase:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._require_assignee(context, case)
            self._cas(case.version, expected_version)
            self._require_open_case(case)
            if case.stale:
                raise ConflictError("review case source version is stale", code="case_source_stale")
            if candidate_id != case.candidate_id:
                raise ConflictError("candidate_id does not match the review case", code="candidate_mismatch")
            if not case.matches_artifact_ref(artifact_ref):
                raise ConflictError("artifact_ref no longer matches the current candidate version", code="artifact_ref_mismatch")
            return case

        if existing_ref is not None:
            def mutate(state: StoreState) -> _Outcome:
                case = check_preconditions(state)
                conversation = self._find_conversation_by_ref(state, context.tenant_id, case_id, existing_ref)
                if conversation is None:
                    raise NotFoundError("existing conversation reference was not found")
                if conversation.status != "active":
                    raise ConflictError("conversation is not active", code="conversation_not_active")
                conversation.last_synced_at = now
                return self._conversation_outcome(state, conversation, context, 201)

            return self._run_command(context, "open_servant_conversation", idempotency_key, request_snapshot, correlation_id, mutate)

        # A new Servant conversation requires a real network call. That call must not
        # run while holding the repository's transaction lock (SA §5), so precondition
        # validation, the external call and the persist step are three separate steps;
        # the persist step re-validates CAS/terminal/stale state in case it changed
        # while the network call was in flight.
        if not _KEY_RE.fullmatch(idempotency_key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        receipt_key = (context.tenant_id, context.actor_id, "open_servant_conversation", idempotency_key)
        fingerprint = canonical_hash(request_snapshot)

        def load_replay(state: StoreState) -> IdempotencyReceipt | None:
            receipt = state.idempotency.get(receipt_key)
            if receipt is None:
                check_preconditions(state)
                return None
            if receipt.request_sha256 != fingerprint:
                raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
            return receipt

        receipt = self.repository.atomic(load_replay)
        if receipt is not None:
            etag = self._etag(receipt.aggregate_version) if receipt.aggregate_version else None
            return ServiceResponse(receipt.response_value, receipt.response_status, etag, True)

        minimized_payload = {
            "artifact_ref": artifact_ref,
            "purpose": body["purpose"],
            "evidence_refs": evidence_refs,
            "service_tone": "research_professional",
        }
        try:
            ack = self.servant.open_conversation(idempotency_key, minimized_payload)
        except DomainError as exc:
            self._audit_failure(context, "open_servant_conversation", correlation_id, exc.code)
            raise

        def commit(state: StoreState) -> ServiceResponse:
            receipt = state.idempotency.get(receipt_key)
            if receipt is not None:
                etag = self._etag(receipt.aggregate_version) if receipt.aggregate_version else None
                return ServiceResponse(receipt.response_value, receipt.response_status, etag, True)
            case = check_preconditions(state)
            conversation = Conversation(
                id=new_id(),
                tenant_id=context.tenant_id,
                review_case_id=case_id,
                provider="agora",
                external_conversation_id=ack.external_conversation_id,
                kind="servant_conversation",
                scoped_artifact_ref=case.current_artifact_ref(),
                allowed_evidence_refs=evidence_refs,
                purpose=body["purpose"],
                created_by=context.actor_id,
                status="active" if ack.status == "active" else "opening",
                last_synced_at=now,
                created_at=now,
                updated_at=now,
            )
            state.conversations[(context.tenant_id, conversation.id)] = conversation
            if case.status == "assigned":
                case.status = "reviewing"
                case.version += 1
                case.updated_at = now
            outcome = self._conversation_outcome(state, conversation, context, 201)
            self._audit(
                state, context, "open_servant_conversation", outcome.aggregate_type, outcome.aggregate_id,
                outcome.aggregate_version, correlation_id, "succeeded",
            )
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id, actor_id=context.actor_id, operation="open_servant_conversation",
                key=idempotency_key, request_sha256=fingerprint, response_status=outcome.status,
                response_value=outcome.value, error=None, completed_at=self._clock(),
                aggregate_version=outcome.aggregate_version,
            )
            return ServiceResponse(outcome.value, outcome.status, self._etag(outcome.aggregate_version), False)

        try:
            return self.repository.atomic(commit)
        except DomainError as exc:
            self._audit_failure(context, "open_servant_conversation", correlation_id, exc.code)
            raise

    def request_servant_revision(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_analyst_role(context)
        case_id = require_uuid(case_id, "review_case_id")
        self._body(
            body,
            {"conversation_id", "base_artifact_ref", "base_content_sha256", "instructions", "required_evidence_refs", "requested_output"},
            {"conversation_id", "base_artifact_ref", "base_content_sha256", "instructions", "required_evidence_refs", "requested_output"},
        )
        conversation_id = require_uuid(body["conversation_id"], "conversation_id")
        base_artifact_ref = require_external_ref(body["base_artifact_ref"], "base_artifact_ref")
        base_sha = str(body["base_content_sha256"])
        if not _SHA_RE.fullmatch(base_sha):
            raise ValidationError("base_content_sha256 must be a lowercase SHA-256", field_name="base_content_sha256")
        instructions = body["instructions"]
        if not isinstance(instructions, str) or not 1 <= len(instructions) <= 12000:
            raise ValidationError("instructions must contain 1 to 12000 characters", field_name="instructions")
        required_refs = body["required_evidence_refs"]
        if not isinstance(required_refs, list) or len(required_refs) > 100:
            raise ValidationError("required_evidence_refs must be an array up to 100 items", field_name="required_evidence_refs")
        required_refs = [require_external_ref(value, "required_evidence_refs") for value in required_refs]
        if body["requested_output"] not in _REQUESTED_OUTPUTS:
            raise ValidationError("requested_output is not supported", field_name="requested_output")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._require_assignee(context, case)
            self._cas(case.version, expected_version)
            self._require_open_case(case)
            if case.stale:
                raise ConflictError("review case source version is stale", code="case_source_stale")
            conversation = state.conversations.get((context.tenant_id, conversation_id))
            if conversation is None or conversation.review_case_id != case_id:
                raise NotFoundError("conversation was not found on this review case")
            if conversation.status != "active":
                raise ConflictError("conversation is not active", code="conversation_not_active")
            if not case.matches_artifact_ref(base_artifact_ref) or base_sha != case.content_sha256:
                raise ConflictError("base version no longer matches the current candidate version", code="base_version_mismatch")

            already_claimed = {
                revision.retry_of_revision_id for (tenant, _), revision in state.revisions.items()
                if tenant == context.tenant_id and revision.retry_of_revision_id
            }
            unclaimed_failures = sorted(
                (
                    revision for (tenant, _), revision in state.revisions.items()
                    if tenant == context.tenant_id and revision.review_case_id == case_id
                    and revision.conversation_id == conversation_id and revision.status == "failed"
                    and revision.id not in already_claimed
                ),
                key=lambda revision: revision.created_at,
            )
            retry_of = unclaimed_failures[-1].id if unclaimed_failures else None
            revision = Revision(
                id=new_id(),
                tenant_id=context.tenant_id,
                review_case_id=case_id,
                conversation_id=conversation_id,
                base_artifact_ref=base_artifact_ref,
                base_content_sha256=base_sha,
                instructions=instructions,
                required_evidence_refs=required_refs,
                requested_output=body["requested_output"],
                requested_by=context.actor_id,
                external_job_ref=new_id(),
                retry_of_revision_id=retry_of,
                created_at=now,
                updated_at=now,
            )
            revision.external_job_ref = revision.id
            state.revisions[(context.tenant_id, revision.id)] = revision
            case.status = "changes_requested"
            case.version += 1
            case.updated_at = now
            self._emit(
                state,
                event_type="review.revision_requested",
                tenant_id=context.tenant_id,
                aggregate_type="review_case",
                aggregate_id=case.id,
                aggregate_version=case.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:revision-requested",
                payload={
                    "review_case_id": case.id,
                    "revision_id": revision.id,
                    "conversation_id": conversation.id,
                    "base_content_sha256": base_sha,
                },
                now=now,
            )
            return _Outcome(revision.to_api([]), 202, "revision", revision.id, revision.version)

        return self._run_command(
            context, "request_servant_revision", idempotency_key, {"review_case_id": case_id, **body}, correlation_id, mutate
        )

    def receive_servant_revision(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
        webhook: WebhookRequest,
    ) -> ServiceResponse:
        self._authorize_service(context, {"servant_adapter"})
        self._verify_webhook(context, webhook)
        self._body(
            body,
            {"event_id", "revision_id", "conversation_ref", "base_artifact_ref", "result_artifact_ref",
             "result_content_sha256", "status", "completed_at", "transcript_snapshot_asset_id"},
            {"event_id", "revision_id", "conversation_ref", "base_artifact_ref", "status", "completed_at", "transcript_snapshot_asset_id"},
        )
        event_id = require_uuid(body["event_id"], "event_id")
        revision_id = require_uuid(body["revision_id"], "revision_id")
        conversation_ref = require_external_ref(body["conversation_ref"], "conversation_ref")
        base_artifact_ref = require_external_ref(body["base_artifact_ref"], "base_artifact_ref")
        if body["status"] not in {"completed", "failed"}:
            raise ValidationError("status is not supported", field_name="status")
        completed_at = self._parse_time(body["completed_at"], "completed_at")
        transcript_asset_id = require_uuid(body["transcript_snapshot_asset_id"], "transcript_snapshot_asset_id")
        result_ref = body.get("result_artifact_ref")
        if result_ref is not None:
            result_ref = require_external_ref(result_ref, "result_artifact_ref")
        result_sha = body.get("result_content_sha256")
        if result_sha is not None and not _SHA_RE.fullmatch(str(result_sha)):
            raise ValidationError("result_content_sha256 must be a lowercase SHA-256", field_name="result_content_sha256")
        if body["status"] == "completed" and (result_ref is None or result_sha is None):
            raise ValidationError("completed callbacks require result_artifact_ref and result_content_sha256", field_name="result_artifact_ref")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            revision = state.revisions.get((context.tenant_id, revision_id))
            if revision is None:
                return self._receipt_outcome(
                    event_id, "quarantined", now, correlation_id, aggregate_id=revision_id,
                    audit_result="rejected", audit_reason_code="revision_not_found",
                )
            conversation = state.conversations.get((context.tenant_id, revision.conversation_id))
            scope_ok = (
                conversation is not None
                and external_ref_matches(conversation.external_conversation_ref(), conversation_ref)
                and external_ref_matches(revision.base_artifact_ref, base_artifact_ref)
            )
            if not scope_ok:
                return self._receipt_outcome(
                    event_id, "quarantined", now, correlation_id, aggregate_id=revision.id, aggregate_version=revision.version,
                    audit_result="rejected", audit_reason_code="revision_scope_violation",
                )
            if revision.status in _TERMINAL_REVISION_STATUSES:
                return self._receipt_outcome(
                    event_id, "duplicate", now, correlation_id, aggregate_id=revision.id, aggregate_version=revision.version,
                    audit_result="duplicate",
                )

            case = self._stored_case(state, context.tenant_id, revision.review_case_id)
            revision.attempt_count += 1
            revision.completed_at = completed_at
            revision.updated_at = now
            revision.version += 1
            if body["status"] == "failed":
                revision.status = "failed"
                revision.last_error_code = "servant_revision_failed"
            else:
                diffs = self.diff_port.diff(case.content_sha256, str(result_sha))
                result = RevisionResult(
                    id=new_id(),
                    tenant_id=context.tenant_id,
                    request_id=revision.id,
                    revision_no=revision.revision_no + 1,
                    artifact_ref=result_ref,
                    content_sha256=str(result_sha),
                    asset_id=transcript_asset_id,
                    evidence_refs=revision.required_evidence_refs,
                    significant_diff=bool(diffs),
                    diff_summary=list(diffs),
                    produced_at=now,
                )
                state.revision_results[(context.tenant_id, result.id)] = result
                revision.revision_no = result.revision_no
                revision.result_artifact_ref = result_ref
                revision.result_content_sha256 = str(result_sha)
                revision.status = "stale" if case.stale else "completed"
                if not case.stale:
                    case.status = "reviewing"
                    case.version += 1
                    case.updated_at = now
                self._emit(
                    state,
                    event_type="review.revision_received",
                    tenant_id=context.tenant_id,
                    aggregate_type="review_case",
                    aggregate_id=case.id,
                    aggregate_version=case.version,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:revision-received",
                    payload={
                        "review_case_id": case.id,
                        "revision_id": revision.id,
                        "candidate_id": case.candidate_id,
                        "result_content_sha256": str(result_sha),
                        "stale": case.stale,
                    },
                    now=now,
                )
            return self._receipt_outcome(
                event_id, "accepted", now, correlation_id, aggregate_id=revision.id, aggregate_version=revision.version,
            )

        return self._run_command(context, "receive_servant_revision", idempotency_key, body, correlation_id, mutate)

    # ---- queries -----------------------------------------------------------

    def list_review_cases(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"analyst", "analyst_reviewer"})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        want_stale = state_filter == "stale"
        wanted_status = None
        if state_filter and not want_stale:
            try:
                wanted_status = case_db_status_for_state(state_filter)
            except ValueError as exc:
                raise ValidationError("state filter is not supported", field_name="state") from exc
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in state.cases.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not cursor or item.id > cursor)
                    and (not state_filter or (item.stale if want_stale else (not item.stale and item.status == wanted_status)))
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_review_cases", "review_case", None, None, correlation_id, "succeeded")
            return ServiceResponse({
                "items": [self._case_to_api(state, item, context) for item in page],
                "next_cursor": page[-1].id if len(rows) > limit else None,
                "as_of": wire_time(snapshot_at),
                "total_estimate": len(rows),
            })

        return self.repository.atomic(query)

    def get_review_case(self, context: ActorContext, case_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize_staff(context, {"analyst", "analyst_reviewer"})
        case_id = require_uuid(case_id, "review_case_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._audit(state, context, "get_review_case", "review_case", case.id, case.version, correlation_id, "succeeded")
            return ServiceResponse(self._case_to_api(state, case, context), etag=self._etag(case.version))

        return self.repository.atomic(query)

    def get_revision_request(self, context: ActorContext, revision_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize_staff(context, {"analyst", "analyst_reviewer"})
        revision_id = require_uuid(revision_id, "revision_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            revision = self._stored_revision(state, context.tenant_id, revision_id)
            self._audit(state, context, "get_revision_request", "revision", revision.id, revision.version, correlation_id, "succeeded")
            return ServiceResponse(revision.to_api([]), etag=self._etag(revision.version))

        return self.repository.atomic(query)

    def get_review_case_workspace(self, context: ActorContext, case_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Internal UI projection combining case, conversations, revisions and Servant health.

        A degraded Servant dependency never empties or fabricates the last-known
        case/revision data; it is surfaced as a separate ``servant_health`` slice.
        Each revision is enriched here with ``significant_diff``/``diff_summary``
        for the version-compare screen; those two fields are a UI-only superset
        and are not part of the published ``Revision`` OpenAPI schema.
        """

        case_response = self.get_review_case(context, case_id, correlation_id=correlation_id)
        case_id = case_response.data["id"]
        probe = self.servant.health_check()
        health = {"status": probe.status, "last_success_at": wire_time(probe.last_success_at) if probe.last_success_at else None}

        def query(state: StoreState) -> dict[str, Any]:
            conversations = [
                item.to_api(self._conversation_actions(state, item, context))
                for (tenant, _), item in state.conversations.items()
                if tenant == context.tenant_id and item.review_case_id == case_id
            ]
            revisions = []
            for (tenant, _), item in state.revisions.items():
                if tenant != context.tenant_id or item.review_case_id != case_id:
                    continue
                row = item.to_api([])
                latest_result = max(
                    (
                        result for (result_tenant, _), result in state.revision_results.items()
                        if result_tenant == context.tenant_id and result.request_id == item.id
                    ),
                    key=lambda result: result.revision_no,
                    default=None,
                )
                row["significant_diff"] = latest_result.significant_diff if latest_result else False
                row["diff_summary"] = list(latest_result.diff_summary) if latest_result else []
                revisions.append(row)
            return {"case": case_response.data, "servant_health": health, "conversations": conversations, "revisions": revisions}

        return self.repository.atomic(query)

    # Candidate lifecycle events (``research.candidate_validated`` /
    # ``research.candidate_superseded``) are applied by
    # ``workers.tasks.agora_review.consumer.M06EventConsumer`` directly against
    # the shared repository state, so inbox dedup/sequence checks and this
    # service's own command transactions never nest.

    # ---- command transaction helpers ---------------------------------------

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
                return _Outcome(receipt.response_value, receipt.response_status, "replay", None, receipt.aggregate_version, receipt.error), True
            outcome = mutate(state)
            result = outcome.error.code if outcome.error else (outcome.audit_result or "succeeded")
            reason = outcome.error.code if outcome.error else outcome.audit_reason_code
            self._audit(
                state, context, operation, outcome.aggregate_type, outcome.aggregate_id, outcome.aggregate_version,
                correlation_id, result, reason,
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
            classification="research_confidential", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    # ---- authorization, mapping and validation -----------------------------

    @staticmethod
    def _authorize_staff(context: ActorContext, roles: set[str]) -> None:
        if context.principal_type != "staff" or not (context.roles & roles):
            raise ForbiddenError("actor cannot perform this operation")

    @staticmethod
    def _authorize_service(context: ActorContext, roles: set[str]) -> None:
        if context.principal_type != "service" or not (context.roles & roles):
            raise ForbiddenError("actor cannot perform this operation")

    def _authorize_analyst_role(self, context: ActorContext) -> None:
        self._authorize_staff(context, {"analyst"})

    @staticmethod
    def _require_assignee(context: ActorContext, case: ReviewCase) -> None:
        if case.assignee_id != context.actor_id:
            raise ForbiddenError("actor is not the assigned analyst for this review case", code="not_assigned_reviewer")

    @staticmethod
    def _require_open_case(case: ReviewCase) -> None:
        if case.status in _TERMINAL_CASE_STATUSES:
            raise ConflictError("review case is not open for Servant collaboration", code="case_terminal")

    def _verify_webhook(self, context: ActorContext, webhook: WebhookRequest) -> None:
        if not _WEBHOOK_TIMESTAMP_RE.fullmatch(webhook.timestamp):
            raise ValidationError("X-Webhook-Timestamp must be a 10-digit Unix timestamp", field_name="X-Webhook-Timestamp")
        if not 16 <= len(webhook.nonce) <= 128:
            raise ValidationError("X-Webhook-Nonce must contain 16 to 128 characters", field_name="X-Webhook-Nonce")
        if not 8 <= len(webhook.integration_key_id) <= 128:
            raise ValidationError("X-Integration-Key-Id must contain 8 to 128 characters", field_name="X-Integration-Key-Id")
        secret = self.webhook_secrets.secret_for(webhook.integration_key_id)
        if secret is None:
            raise ForbiddenError("integration key is not recognized", code="webhook_key_unknown")
        verify_servant_webhook(
            secret, timestamp=webhook.timestamp, nonce=webhook.nonce, method=webhook.method, path=webhook.path,
            raw_body=webhook.raw_body, provided_signature=webhook.signature, now=self._clock(),
        )

        def record_nonce(state: StoreState) -> None:
            nonce_key = (context.tenant_id, webhook.integration_key_id, webhook.nonce)
            if nonce_key in state.webhook_nonces:
                raise ForbiddenError("webhook nonce was already used", code="webhook_nonce_replay")
            now = self._clock()
            state.webhook_nonces[nonce_key] = now
            cutoff = now - timedelta(seconds=_WEBHOOK_NONCE_TTL_SECONDS)
            for stale_key, seen_at in list(state.webhook_nonces.items()):
                if seen_at < cutoff:
                    del state.webhook_nonces[stale_key]

        self.repository.atomic(record_nonce)

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
    def _stored_case(state: StoreState, tenant_id: str, case_id: str) -> ReviewCase:
        case = state.cases.get((tenant_id, case_id))
        if not case:
            raise NotFoundError("review case was not found")
        return case

    @staticmethod
    def _stored_revision(state: StoreState, tenant_id: str, revision_id: str) -> Revision:
        revision = state.revisions.get((tenant_id, revision_id))
        if not revision:
            raise NotFoundError("revision request was not found")
        return revision

    @staticmethod
    def _find_conversation_by_ref(state: StoreState, tenant_id: str, case_id: str, ref: dict[str, Any]) -> Conversation | None:
        for (tenant, _), conversation in state.conversations.items():
            if tenant == tenant_id and conversation.review_case_id == case_id and external_ref_matches(conversation.external_conversation_ref(), ref):
                return conversation
        return None

    def _pending_revision_ids(self, state: StoreState, tenant_id: str, case_id: str) -> list[str]:
        case_revisions = [
            revision for (tenant, _), revision in state.revisions.items()
            if tenant == tenant_id and revision.review_case_id == case_id
        ]
        # A failed revision that a later retry already claims via retry_of_revision_id
        # is superseded; it stays as preserved evidence (UAT-M06-009) but no longer
        # shows up as outstanding work, otherwise every historical failure would
        # linger in pending_revision_ids forever even after it was retried.
        claimed = {revision.retry_of_revision_id for revision in case_revisions if revision.retry_of_revision_id}
        rows = sorted(
            (
                revision for revision in case_revisions
                if revision.status in _PENDING_REVISION_STATUSES and revision.id not in claimed
            ),
            key=lambda revision: revision.created_at,
        )
        return [revision.id for revision in rows]

    def _case_actions(self, case: ReviewCase, context: ActorContext) -> list[str]:
        actions: list[str] = []
        if context.principal_type == "staff" and "research_supervisor" in context.roles and case.status not in {"rejected", "closed"}:
            actions.append("assign_review_case")
        is_assignee = context.principal_type == "staff" and "analyst" in context.roles and case.assignee_id == context.actor_id
        if is_assignee and not case.stale and case.status not in {"rejected", "closed", "ready_to_sign"}:
            actions.extend(["open_servant_conversation", "request_servant_revision"])
        return sorted(set(actions))

    def _conversation_actions(self, state: StoreState, conversation: Conversation, context: ActorContext) -> list[str]:
        case = state.cases.get((context.tenant_id, conversation.review_case_id))
        if case is None or case.stale or conversation.status != "active":
            return []
        if context.principal_type == "staff" and "analyst" in context.roles and case.assignee_id == context.actor_id:
            return ["request_servant_revision"]
        return []

    def _case_to_api(self, state: StoreState, case: ReviewCase, context: ActorContext) -> dict[str, Any]:
        pending = self._pending_revision_ids(state, context.tenant_id, case.id)
        return case.to_api(self._case_actions(case, context), pending)

    def _case_outcome(self, state: StoreState, case: ReviewCase, context: ActorContext) -> _Outcome:
        return _Outcome(self._case_to_api(state, case, context), 200, "review_case", case.id, case.version)

    def _conversation_outcome(self, state: StoreState, conversation: Conversation, context: ActorContext, status: int) -> _Outcome:
        actions = self._conversation_actions(state, conversation, context)
        return _Outcome(conversation.to_api(actions), status, "conversation", conversation.id, conversation.version)

    @staticmethod
    def _receipt_outcome(
        event_id: str,
        status: str,
        now: datetime,
        correlation_id: str,
        *,
        aggregate_id: str | None = None,
        aggregate_version: int | None = None,
        audit_result: str | None = None,
        audit_reason_code: str | None = None,
    ) -> _Outcome:
        value = {
            "receipt_id": new_id(),
            "event_id": event_id,
            "status": status,
            "received_at": wire_time(now),
            "correlation_id": correlation_id,
        }
        return _Outcome(
            value, 202, "revision", aggregate_id, aggregate_version,
            audit_result=audit_result, audit_reason_code=audit_reason_code,
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
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=f"m06.{action}", aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security", None, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
