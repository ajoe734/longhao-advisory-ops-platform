from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable

from .domain import (
    ActorContext,
    AuditEvent,
    CandidateEvidence,
    ConflictError,
    DomainError,
    ForbiddenError,
    IdempotencyReceipt,
    NotFoundError,
    OutboxEvent,
    PreconditionRequiredError,
    ResearchCandidate,
    StaleVersionError,
    ValidationError,
    canonical_hash,
    deterministic_review_case_id,
    external_ref_from_wire,
    external_ref_key,
    new_id,
    origin_from_wire,
    origin_to_wire,
    require_uuid,
    status_to_wire,
    utc_now,
    wire_time,
)
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_PRODUCER = "pantheon"
_CUSTOMER_DISTRIBUTION_LICENSE = "customer_distribution_licensed"
_VALIDATE_TRANSIENT_CODES = frozenset(
    {"content_hash_mismatch_on_fetch", "evidence_access_unconfirmed", "license_scope_unconfirmed", "customer_distribution_denied"}
)


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


class PantheonIntakeService:
    """M05 ingest/validate/reject command and read surface.

    Every state-changing operation runs inside one repository callback so the
    candidate row, audit entry, outbox event and idempotency receipt commit
    atomically. Evidence fetch/hash verification happens out of process (the
    worker); this service only records the outcome the worker reports.
    """

    def __init__(self, repository: InMemoryRepository, *, clock: Callable[[], datetime] = utc_now, environment: str = "test") -> None:
        if environment == "production" and getattr(repository, "is_fake", False):
            raise RuntimeError("fake M05 repository is forbidden in production")
        self.repository = repository
        self._clock = clock

    # ---- commands ---------------------------------------------------------

    def ingest_research_candidate(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_integration(context, "pantheon_adapter")
        allowed = {
            "external_event_id", "origin", "persona_ref", "pool_ref", "ticket_ref", "plan_ref",
            "artifact_ref", "artifact_content_sha256", "evidence_bundle_refs", "source_created_at",
            "source_available_at", "source_committed_at", "data_scope", "provenance_snapshot_asset_id",
            "license_scope_ref",
        }
        required = {
            "external_event_id", "origin", "ticket_ref", "artifact_ref", "artifact_content_sha256",
            "evidence_bundle_refs", "source_created_at", "source_available_at", "data_scope",
            "provenance_snapshot_asset_id", "license_scope_ref",
        }
        self._body(body, allowed, required)
        external_event_id = require_uuid(body["external_event_id"], "external_event_id")
        origin = origin_from_wire(body["origin"])
        ticket_ref = external_ref_from_wire(body["ticket_ref"], "ticket_ref")
        plan_ref = external_ref_from_wire(body["plan_ref"], "plan_ref") if body.get("plan_ref") is not None else None
        artifact_ref = external_ref_from_wire(body["artifact_ref"], "artifact_ref")
        content_sha256 = str(body["artifact_content_sha256"])
        if not _SHA_RE.fullmatch(content_sha256):
            raise ValidationError("artifact_content_sha256 must be lowercase SHA-256", field_name="artifact_content_sha256")
        evidence_refs = body["evidence_bundle_refs"]
        if not isinstance(evidence_refs, list) or not 1 <= len(evidence_refs) <= 100:
            raise ValidationError("evidence_bundle_refs must contain 1 to 100 items", field_name="evidence_bundle_refs")
        evidence_refs = [external_ref_from_wire(item, f"evidence_bundle_refs.{index}") for index, item in enumerate(evidence_refs)]
        if body["data_scope"] != "research_only":
            raise ValidationError("data_scope must be research_only", field_name="data_scope")
        provenance_snapshot_asset_id = require_uuid(body["provenance_snapshot_asset_id"], "provenance_snapshot_asset_id")
        license_scope_ref = str(body["license_scope_ref"])
        if not 1 <= len(license_scope_ref) <= 160:
            raise ValidationError("license_scope_ref must be 1..160 characters", field_name="license_scope_ref")
        persona_ref = self._optional_ref_string(body.get("persona_ref"), "persona_ref")
        pool_ref = self._optional_ref_string(body.get("pool_ref"), "pool_ref")
        source_occurred_at = self._parse_time(body["source_created_at"], "source_created_at")
        source_available_at = self._parse_time(body["source_available_at"], "source_available_at")
        source_committed_at = self._parse_time(body["source_committed_at"], "source_committed_at") if body.get("source_committed_at") else None

        external_candidate_id = artifact_ref["external_id"]
        external_version = self._artifact_version(artifact_ref)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        seeded_codes: set[str] = set()
        if any("checksum_sha256" not in ref for ref in evidence_refs):
            seeded_codes.add("evidence_incomplete")
        if source_committed_at is not None and source_committed_at < source_occurred_at:
            seeded_codes.add("source_time_inconsistent")
        if source_occurred_at > now:
            seeded_codes.add("source_occurred_at_in_future")
        if source_available_at > now:
            seeded_codes.add("source_available_at_in_future")

        def mutate(state: StoreState) -> _Outcome:
            event_key = (context.tenant_id, _PRODUCER, external_event_id)
            existing_event_candidate_id = state.candidates_by_event.get(event_key)
            if existing_event_candidate_id:
                existing = state.candidates[(context.tenant_id, existing_event_candidate_id)]
                return self._candidate_outcome(existing, context, 202)

            lineage_key = (context.tenant_id, _PRODUCER, external_candidate_id)
            head_id = state.candidate_lineage_head.get(lineage_key)
            if head_id:
                head = state.candidates[(context.tenant_id, head_id)]
                if head.external_version == external_version:
                    if head.content_sha256 == content_sha256:
                        state.candidates_by_event[event_key] = head.id
                        return self._candidate_outcome(head, context, 202)
                    head.status = "quarantined"
                    head.validation_failure_codes = sorted(set(head.validation_failure_codes) | {"artifact_hash_conflict"})
                    head.conflicting_receipts.append({
                        "external_event_id": external_event_id,
                        "artifact_content_sha256": content_sha256,
                        "received_at": wire_time(now),
                    })
                    head.version += 1
                    head.updated_at = now
                    error = ConflictError(
                        "this artifact version was already received with a different content hash",
                        code="artifact_hash_conflict",
                        current_version=head.version,
                    )
                    return _Outcome({}, error.status, "research_candidate", head.id, head.version, error)
                if external_version < head.external_version:
                    error = ConflictError("external_version is older than the current lineage head", code="stale_external_version")
                    return _Outcome({}, error.status, "research_candidate", head.id, head.version, error)

            candidate = ResearchCandidate(
                id=new_id(),
                tenant_id=context.tenant_id,
                producer=_PRODUCER,
                external_candidate_id=external_candidate_id,
                external_version=external_version,
                external_event_id=external_event_id,
                origin=origin,
                ticket_ref=ticket_ref,
                artifact_ref=artifact_ref,
                content_sha256=content_sha256,
                snapshot_asset_id=provenance_snapshot_asset_id,
                source_occurred_at=source_occurred_at,
                source_available_at=source_available_at,
                received_at=now,
                license_scope_ref=license_scope_ref,
                data_scope=body["data_scope"],
                raw_evidence_refs=tuple(evidence_refs),
                persona_ref=persona_ref,
                pool_ref=pool_ref,
                plan_ref=plan_ref,
                source_committed_at=source_committed_at,
                validation_failure_codes=sorted(seeded_codes),
                created_at=now,
                updated_at=now,
            )
            if head_id:
                head = state.candidates[(context.tenant_id, head_id)]
                candidate.supersedes_candidate_id = head.id
            state.candidates[(context.tenant_id, candidate.id)] = candidate
            state.candidates_by_event[event_key] = candidate.id
            state.candidate_lineage_head[lineage_key] = candidate.id

            seen_keys: set[str] = set()
            for ref in evidence_refs:
                checksum = ref.get("checksum_sha256")
                if checksum is None:
                    continue
                key = external_ref_key(ref)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                row = CandidateEvidence(
                    id=new_id(),
                    tenant_id=context.tenant_id,
                    candidate_id=candidate.id,
                    evidence_ref=key,
                    evidence_sha256=checksum,
                    event_time=source_occurred_at,
                    available_time=source_available_at,
                    license_scope=license_scope_ref,
                    created_at=now,
                    updated_at=now,
                )
                state.evidence[(context.tenant_id, row.id)] = row

            self._emit(
                state,
                event_type="research.candidate_received",
                tenant_id=context.tenant_id,
                aggregate_id=candidate.id,
                aggregate_version=1,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:received",
                payload={
                    "candidate_id": candidate.id,
                    "external_event_id": external_event_id,
                    "artifact_content_sha256": content_sha256,
                    "origin": origin_to_wire(origin),
                },
                now=now,
            )
            if head_id:
                head = state.candidates[(context.tenant_id, head_id)]
                head.last_active_state = head.last_active_state or head.status
                head.status = "superseded"
                head.superseded_by_candidate_id = candidate.id
                head.version += 1
                head.updated_at = now
                self._emit(
                    state,
                    event_type="research.candidate_superseded",
                    tenant_id=context.tenant_id,
                    aggregate_id=head.id,
                    aggregate_version=head.version,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:superseded",
                    payload={
                        "candidate_id": head.id,
                        "replacement_candidate_id": candidate.id,
                        "previous_artifact_sha256": head.content_sha256,
                        "replacement_artifact_sha256": candidate.content_sha256,
                    },
                    now=now,
                )
            return self._candidate_outcome(candidate, context, 202)

        return self._run_command(context, "ingest_research_candidate", idempotency_key, body, correlation_id, mutate)

    def validate_research_candidate(
        self,
        context: ActorContext,
        candidate_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_service(context, "research_intake_service")
        candidate_id = require_uuid(candidate_id, "candidate_id")
        allowed = {"observed_content_sha256", "evidence_access_confirmed", "license_scope_confirmed", "source_snapshot_asset_id"}
        self._body(body, allowed, allowed)
        observed_sha256 = str(body["observed_content_sha256"])
        if not _SHA_RE.fullmatch(observed_sha256):
            raise ValidationError("observed_content_sha256 must be lowercase SHA-256", field_name="observed_content_sha256")
        if not isinstance(body["evidence_access_confirmed"], bool) or not isinstance(body["license_scope_confirmed"], bool):
            raise ValidationError("evidence_access_confirmed and license_scope_confirmed must be boolean")
        source_snapshot_asset_id = require_uuid(body["source_snapshot_asset_id"], "source_snapshot_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            candidate = self._stored_candidate(state, context.tenant_id, candidate_id)
            self._cas(candidate.version, expected_version)
            if candidate.status in {"rejected", "superseded"}:
                raise ConflictError("candidate is no longer open for validation", code="candidate_terminal")
            codes = set(candidate.validation_failure_codes) - _VALIDATE_TRANSIENT_CODES
            if observed_sha256 != candidate.content_sha256:
                codes.add("content_hash_mismatch_on_fetch")
            if not body["evidence_access_confirmed"]:
                codes.add("evidence_access_unconfirmed")
            if not body["license_scope_confirmed"]:
                codes.add("license_scope_unconfirmed")
            distribution_allowed = candidate.license_scope_ref == _CUSTOMER_DISTRIBUTION_LICENSE and body["license_scope_confirmed"]
            if not distribution_allowed:
                codes.add("customer_distribution_denied")
            candidate.customer_distribution_allowed = distribution_allowed
            candidate.validated_snapshot_asset_id = source_snapshot_asset_id
            blocking = codes - {"customer_distribution_denied"}
            result = "quarantined" if blocking else "ready"
            candidate.status = result
            candidate.validation_failure_codes = sorted(codes)
            if candidate.review_case_id is None:
                candidate.review_case_id = deterministic_review_case_id(context.tenant_id, candidate.id)
            candidate.version += 1
            candidate.updated_at = now
            self._emit(
                state,
                event_type="research.candidate_validated",
                tenant_id=context.tenant_id,
                aggregate_id=candidate.id,
                aggregate_version=candidate.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:validated",
                payload={
                    "candidate_id": candidate.id,
                    "review_case_id": candidate.review_case_id,
                    "artifact_content_sha256": candidate.content_sha256,
                    "validation_result": "accepted" if result == "ready" else "quarantined",
                },
                now=now,
            )
            return self._candidate_outcome(candidate, context, 202)

        return self._run_command(context, "validate_research_candidate", idempotency_key, {"candidate_id": candidate_id, **body}, correlation_id, mutate)

    def reject_research_candidate(
        self,
        context: ActorContext,
        candidate_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, {"analyst_reviewer"})
        candidate_id = require_uuid(candidate_id, "candidate_id")
        self._body(body, {"reason_code", "note"}, {"reason_code"})
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        note = body.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ValidationError("note must be a string up to 2000 characters", field_name="note")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            candidate = self._stored_candidate(state, context.tenant_id, candidate_id)
            self._cas(candidate.version, expected_version)
            if candidate.status in {"rejected", "superseded"}:
                raise ConflictError("candidate is already in a terminal state", code="candidate_terminal")
            candidate.status = "rejected"
            candidate.validation_failure_codes = sorted(set(candidate.validation_failure_codes) | {"rejected"})
            if candidate.review_case_id is None:
                candidate.review_case_id = deterministic_review_case_id(context.tenant_id, candidate.id)
            candidate.version += 1
            candidate.updated_at = now
            self._emit(
                state,
                event_type="research.candidate_validated",
                tenant_id=context.tenant_id,
                aggregate_id=candidate.id,
                aggregate_version=candidate.version,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:rejected",
                payload={
                    "candidate_id": candidate.id,
                    "review_case_id": candidate.review_case_id,
                    "artifact_content_sha256": candidate.content_sha256,
                    "validation_result": "rejected",
                },
                now=now,
            )
            return self._candidate_outcome(candidate, context, 200)

        return self._run_command(context, "reject_research_candidate", idempotency_key, {"candidate_id": candidate_id, **body}, correlation_id, mutate)

    # ---- queries ------------------------------------------------------------

    def get_research_candidate(self, context: ActorContext, candidate_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        candidate_id = require_uuid(candidate_id, "candidate_id")
        self._authorize_staff(context, {"analyst", "analyst_reviewer"})
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            candidate = self._stored_candidate(state, context.tenant_id, candidate_id)
            self._audit(state, context, "get_research_candidate", candidate.id, candidate.version, correlation_id, "succeeded")
            return ServiceResponse(candidate.to_api(self._candidate_actions(candidate, context)), etag=self._etag(candidate.version))

        return self.repository.atomic(query)

    def list_research_candidates(
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
        wire_states = {"received", "validating", "accepted", "quarantined", "rejected"}
        if state_filter and state_filter not in wire_states:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    item for (tenant, _), item in state.candidates.items()
                    if tenant == context.tenant_id
                    and item.created_at <= snapshot_at
                    and (not state_filter or status_to_wire(item.status, item.last_active_state) == state_filter)
                    and (not cursor or item.id > cursor)
                ),
                key=lambda item: item.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_research_candidates", None, None, correlation_id, "succeeded")
            return ServiceResponse({
                "items": [item.to_api(self._candidate_actions(item, context)) for item in page],
                "next_cursor": page[-1].id if len(rows) > limit else None,
                "as_of": wire_time(snapshot_at),
                "total_estimate": len(rows),
            })

        return self.repository.atomic(query)

    # ---- transaction helpers -------------------------------------------------

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
                state, context, operation, outcome.aggregate_id, outcome.aggregate_version, correlation_id,
                outcome.error.code if outcome.error else "succeeded", outcome.error.code if outcome.error else None,
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
    ) -> None:
        sequence = 1 + max(
            (
                event.sequence_no for (tenant, _), event in state.outbox.items()
                if tenant == tenant_id and event.aggregate_type == "research_candidate" and event.aggregate_id == aggregate_id
            ),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(), event_type=event_type, tenant_id=tenant_id, aggregate_type="research_candidate",
            aggregate_id=aggregate_id, aggregate_version=aggregate_version, sequence_no=sequence,
            correlation_id=correlation_id, causation_id=None, idempotency_key=idempotency_key,
            classification="research_confidential", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    # ---- authorization and validation ---------------------------------------

    def _authorize_integration(self, context: ActorContext, role: str) -> None:
        if context.principal_type == "integration" and role in context.roles:
            return
        raise ForbiddenError("actor cannot submit integration events for this operation")

    def _authorize_service(self, context: ActorContext, role: str) -> None:
        if context.principal_type == "service" and role in context.roles:
            return
        raise ForbiddenError("actor cannot perform this service operation")

    def _authorize_staff(self, context: ActorContext, roles: set[str]) -> None:
        if context.principal_type == "staff" and context.roles & roles:
            return
        raise ForbiddenError("staff role is not allowed for this operation")

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
    def _optional_ref_string(value: Any, field_name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 160:
            raise ValidationError(f"{field_name} must be a string up to 160 characters", field_name=field_name)
        return value

    @staticmethod
    def _artifact_version(artifact_ref: dict[str, Any]) -> int:
        raw = artifact_ref.get("version")
        if raw is None:
            return 1
        if not re.fullmatch(r"[1-9][0-9]*", str(raw)):
            raise ValidationError("artifact_ref.version must be a positive integer", field_name="artifact_ref.version")
        return int(raw)

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

    @staticmethod
    def _stored_candidate(state: StoreState, tenant_id: str, candidate_id: str) -> ResearchCandidate:
        candidate = state.candidates.get((tenant_id, candidate_id))
        if not candidate:
            raise NotFoundError("research candidate was not found")
        return candidate

    def _candidate_actions(self, candidate: ResearchCandidate, context: ActorContext) -> list[str]:
        actions: list[str] = []
        if candidate.status not in {"rejected", "superseded"} and context.principal_type == "service" and "research_intake_service" in context.roles:
            actions.append("validate_research_candidate")
        if candidate.status not in {"rejected", "superseded"} and context.principal_type == "staff" and "analyst_reviewer" in context.roles:
            actions.append("reject_research_candidate")
        return sorted(set(actions))

    def _candidate_outcome(self, candidate: ResearchCandidate, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(candidate.to_api(self._candidate_actions(candidate, context)), status, "research_candidate", candidate.id, candidate.version)

    def _audit(
        self,
        state: StoreState,
        context: ActorContext,
        action: str,
        aggregate_id: str | None,
        aggregate_version: int | None,
        correlation_id: str,
        result: str,
        reason_code: str | None = None,
    ) -> None:
        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=f"m05.{action}", aggregate_type="research_candidate", aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, None, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
