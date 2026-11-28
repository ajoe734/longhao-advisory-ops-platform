from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import re
from typing import Any, Callable

from .domain import (
    APPROVAL_MODES,
    ActorContext,
    AuditEvent,
    CASE_CATEGORIES,
    CASE_PRIORITIES,
    CASE_STATES,
    CaseInteraction,
    CaseResolution,
    ConflictError,
    DISPATCH_CHANNELS,
    DomainError,
    ESCALATION_QUEUES,
    ForbiddenError,
    IdempotencyReceipt,
    INTAKE_CHANNELS,
    INTERACTION_DIRECTIONS,
    Job,
    NotFoundError,
    OutboxEvent,
    PreconditionRequiredError,
    RECEIPT_PROVIDER_STATUSES,
    ResponseApproval,
    ResponseDispatch,
    ResponseDraft,
    ResponseReceipt,
    ServiceCase,
    SlaEvent,
    StaleVersionError,
    ValidationError,
    _DISPATCH_RANK,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
    wire_time,
)
from .automation import DraftingAssistantPort
from .ports import ChannelBindingPort, IntegrationKeyPort, KnowledgeBasePort, ObjectAssetPort, SuppressionRequestPort
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_WEBHOOK_TS_RE = re.compile(r"^[0-9]{10}$")
_WEBHOOK_NONCE_RE = re.compile(r"^.{16,128}$")
_WEBHOOK_TIMESTAMP_SKEW_SECONDS = 300
_WEBHOOK_RECEIPT_PATH = "/webhooks/service-response-receipts"

SLA_POLICY_VERSION = "m13-sla-v1"
_FIRST_RESPONSE_HOURS = {"urgent": 1, "high": 4, "normal": 24}
_RESOLUTION_HOURS = {"urgent": 24, "high": 72, "normal": 168}

_SERVICE_AGENT = {"service_agent"}
_SERVICE_SUPERVISOR = {"service_supervisor"}
_TIMELINE_READERS = _SERVICE_AGENT | _SERVICE_SUPERVISOR
_RESPONSE_SENDERS = {"service_agent", "service_supervisor", "service_response_service"}
_RESPONSE_READERS = {"service_agent", "service_supervisor"}
_APPROVERS = {"service_agent", "service_supervisor", "service_response_service"}
_MESSAGING_ADAPTER = {"service_messaging_adapter"}
# low-risk template approval bypasses human maker-checker review, so it is
# restricted to the controlled policy service, never a human agent/supervisor
# (docs/modules/M13-service_cases.md §4: "low-risk template由受控policy service記錄").
_LOW_RISK_TEMPLATE_APPROVERS = {"service_response_service"}
# investment judgment is already hard-blocked above; complaints, refunds and
# research questions must still go through an authorized human/system workflow
# per docs/modules/M13-service_cases.md §4, as must any case already routed to
# the compliance (AML) queue.
_LOW_RISK_TEMPLATE_BLOCKED_CATEGORIES = {"complaint", "refund", "research_question"}
_CUSTOMER_VISIBLE_DISPATCH_STATES = {"sent", "delivered"}


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


def _text_sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class ServiceCaseService:
    """M13 command/query application service for cross-channel service cases.

    Every state-changing operation runs inside one repository callback so
    domain state, audit, outbox and the idempotency receipt commit atomically.
    External sends (messaging provider, M10 suppression) are only enqueued
    here; a worker performs the network call after the transaction commits.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        object_assets: ObjectAssetPort,
        knowledge_base: KnowledgeBasePort,
        channel_bindings: ChannelBindingPort,
        suppression: SuppressionRequestPort,
        assistant: DraftingAssistantPort,
        integration_keys: IntegrationKeyPort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False)
            for adapter in (repository, object_assets, knowledge_base, channel_bindings, suppression, assistant, integration_keys)
        ):
            raise RuntimeError("fake M13 adapters are forbidden in production")
        self.repository = repository
        self.object_assets = object_assets
        self.knowledge_base = knowledge_base
        self.channel_bindings = channel_bindings
        self.suppression = suppression
        self.assistant = assistant
        self.integration_keys = integration_keys
        self._clock = clock

    # ---- case commands ---------------------------------------------------

    def create_service_case(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_case_create(context)
        self._body(
            body,
            {"customer_id", "intake_channel", "category", "subject", "description", "original_content_asset_id",
             "contract_id", "publication_id", "payment_id", "priority"},
            {"intake_channel", "category", "subject", "description", "priority"},
        )
        intake_channel = body["intake_channel"]
        if intake_channel not in INTAKE_CHANNELS:
            raise ValidationError("intake_channel is not supported", field_name="intake_channel")
        category = body["category"]
        if category not in CASE_CATEGORIES:
            raise ValidationError("category is not supported", field_name="category")
        priority = body["priority"]
        if priority not in CASE_PRIORITIES:
            raise ValidationError("priority is not supported", field_name="priority")
        subject = body["subject"]
        if not isinstance(subject, str) or not 1 <= len(subject) <= 200:
            raise ValidationError("subject must be 1 to 200 characters", field_name="subject")
        description = body["description"]
        if not isinstance(description, str) or not 1 <= len(description) <= 12000:
            raise ValidationError("description must be 1 to 12000 characters", field_name="description")

        customer_id = body.get("customer_id")
        if context.principal_type == "customer":
            if customer_id is not None and require_uuid(customer_id, "customer_id") != context.customer_id:
                raise ForbiddenError("customer cannot open a case for another customer", code="customer_mismatch")
            customer_id = context.customer_id
        elif customer_id is not None:
            customer_id = require_uuid(customer_id, "customer_id")

        original_content_asset_id = body.get("original_content_asset_id")
        if original_content_asset_id is not None:
            original_content_asset_id = require_uuid(original_content_asset_id, "original_content_asset_id")
            if not self.object_assets.exists(context.tenant_id, original_content_asset_id):
                raise ValidationError("original_content_asset_id does not exist", code="evidence_not_found", field_name="original_content_asset_id")
        contract_id = require_uuid(body["contract_id"], "contract_id") if body.get("contract_id") else None
        publication_id = require_uuid(body["publication_id"], "publication_id") if body.get("publication_id") else None
        payment_id = require_uuid(body["payment_id"], "payment_id") if body.get("payment_id") else None

        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case_id = new_id()
            case_number = self._unique_case_number(state, context.tenant_id)
            first_due = now + timedelta(hours=_FIRST_RESPONSE_HOURS[priority])
            resolution_due = now + timedelta(hours=_RESOLUTION_HOURS[priority])
            case = ServiceCase(
                id=case_id,
                tenant_id=context.tenant_id,
                case_number=case_number,
                version=1,
                created_at=now,
                updated_at=now,
                status="new",
                customer_id=customer_id,
                intake_channel=intake_channel,
                category=category,
                subject=subject,
                description=description,
                priority=priority,
                sla_policy_version=SLA_POLICY_VERSION,
                first_response_due_at=first_due,
                resolution_due_at=resolution_due,
                contract_id=contract_id,
                publication_id=publication_id,
                payment_id=payment_id,
                original_content_asset_id=original_content_asset_id,
            )
            state.cases[(context.tenant_id, case_id)] = case
            state.case_number_index[(context.tenant_id, case_number)] = case_id
            sla_event_id = new_id()
            state.sla_events[(context.tenant_id, sla_event_id)] = SlaEvent(
                id=sla_event_id, tenant_id=context.tenant_id, case_id=case_id, action="started",
                occurred_at=now, actor_id=context.actor_id, reason_code="case_opened",
            )
            self._emit(
                state, event_type="support.case_opened", tenant_id=context.tenant_id,
                aggregate_type="service_case", aggregate_id=case_id, aggregate_version=1,
                correlation_id=correlation_id, idempotency_key=f"{idempotency_key}:case-opened",
                payload={"case_id": case_id, "customer_id": customer_id, "category": category, "priority": priority},
                now=now,
            )
            return self._case_outcome(case, context, 201)

        return self._run_command(context, "create_service_case", idempotency_key, body, correlation_id, mutate)

    def list_service_cases(
        self,
        context: ActorContext,
        *,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, _SERVICE_SUPERVISOR | _SERVICE_AGENT)
        if state_filter is not None and state_filter not in CASE_STATES:
            raise ValidationError("state is not a recognised case state", field_name="state")
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        offset = 0
        if cursor is not None:
            try:
                offset = int(cursor)
                assert offset >= 0
            except (ValueError, AssertionError) as exc:
                raise ValidationError("cursor is invalid", field_name="cursor") from exc
        as_of = as_of or self._clock()
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> _Outcome:
            matches = [
                case for (tenant, _), case in state.cases.items()
                if tenant == context.tenant_id and case.created_at <= as_of and (state_filter is None or case.status == state_filter)
            ]
            matches.sort(key=lambda case: (case.first_response_due_at, case.created_at))
            page = matches[offset:offset + limit]
            next_cursor = str(offset + limit) if offset + limit < len(matches) else None
            value = {
                "items": [case.to_api(self._case_actions(case, context)) for case in page],
                "next_cursor": next_cursor,
                "as_of": wire_time(as_of),
                "total_estimate": len(matches),
            }
            self._audit(state, context, "list_service_cases", "service_case", None, None, correlation_id, "succeeded")
            return _Outcome(value, 200, "service_case", None, None)

        outcome = self.repository.atomic(query)
        return ServiceResponse(outcome.value, outcome.status)

    def get_service_case(self, context: ActorContext, case_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._authorize_staff(context, _SERVICE_AGENT)
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._audit(state, context, "get_service_case", "service_case", case.id, case.version, correlation_id, "succeeded")
            return self._case_outcome(case, context, 200)

        outcome = self.repository.atomic(query)
        return ServiceResponse(outcome.value, outcome.status, self._etag(outcome.aggregate_version))

    def list_case_interactions(
        self,
        context: ActorContext,
        case_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        """List a case's cross-channel interactions in deterministic time order.

        Staff receive the evidence-bearing record.  Customers receive only their
        own case's non-internal interaction projection, never asset references,
        staff identifiers or internal publication links.
        """
        case_id = require_uuid(case_id, "case_id")
        offset = self._page_offset(cursor, limit)
        as_of = as_of or self._clock()
        correlation_id = self._correlation(correlation_id)
        # Authorize non-customer principals before the case lookup.  Otherwise
        # an unauthorized caller can distinguish an existing case (403 after
        # authorization) from an absent case (404 from _stored_case).
        self._authorize_case_timeline_principal(context)

        def query(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._authorize_case_timeline_reader(context, case)
            interactions = [
                interaction for (tenant, _), interaction in state.interactions.items()
                if tenant == context.tenant_id
                and interaction.case_id == case.id
                and interaction.created_at <= as_of
                and (
                    context.principal_type != "customer"
                    or interaction.direction != "internal"
                )
            ]
            interactions.sort(key=lambda interaction: (interaction.occurred_at, interaction.id))
            page = interactions[offset:offset + limit]
            if context.principal_type == "customer":
                items = [interaction.to_customer_timeline_api() for interaction in page]
            else:
                items = [interaction.to_api(self._interaction_actions(context)) for interaction in page]
            value = self._page_value(items, offset, limit, len(interactions), as_of)
            self._audit(state, context, "list_case_interactions", "service_case", case.id, case.version, correlation_id, "succeeded")
            return _Outcome(value, 200, "service_case", case.id, case.version)

        outcome = self.repository.atomic(query)
        return ServiceResponse(outcome.value, outcome.status)

    def list_case_responses(
        self,
        context: ActorContext,
        case_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        """List drafts, approvals and dispatches as one case response timeline.

        A draft or approval is not customer-facing content.  The customer view is
        therefore restricted to the customer's own dispatches after a provider
        reports sent/delivered, and intentionally excludes all workflow evidence.
        """
        case_id = require_uuid(case_id, "case_id")
        offset = self._page_offset(cursor, limit)
        as_of = as_of or self._clock()
        correlation_id = self._correlation(correlation_id)
        # Keep the same pre-lookup gate as the interaction timeline so either
        # endpoint has an identical non-customer existence-oracle boundary.
        self._authorize_case_timeline_principal(context)

        def query(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._authorize_case_timeline_reader(context, case)
            if context.principal_type == "customer":
                drafts_by_id = {
                    draft.id: draft
                    for (tenant, _), draft in state.response_drafts.items()
                    if tenant == context.tenant_id and draft.case_id == case.id
                }
                dispatches = [
                    dispatch for (tenant, _), dispatch in state.response_dispatches.items()
                    if tenant == context.tenant_id
                    and dispatch.case_id == case.id
                    and dispatch.customer_id == context.customer_id
                    and dispatch.customer_visible_at() <= as_of
                    and dispatch.status in _CUSTOMER_VISIBLE_DISPATCH_STATES
                ]
                dispatches.sort(key=lambda dispatch: (dispatch.customer_visible_at(), dispatch.id))
                page = dispatches[offset:offset + limit]
                value = self._page_value(
                    [
                        dispatch.to_customer_timeline_api(
                            drafts_by_id[dispatch.draft_id].text or "",
                        )
                        for dispatch in page
                    ],
                    offset,
                    limit,
                    len(dispatches),
                    as_of,
                )
            else:
                timeline: list[tuple[datetime, str, dict[str, Any]]] = []
                drafts_by_id = {
                    draft.id: draft
                    for (tenant, _), draft in state.response_drafts.items()
                    if tenant == context.tenant_id and draft.case_id == case.id and draft.created_at <= as_of
                }
                for draft in drafts_by_id.values():
                    timeline.append((
                        draft.created_at,
                        draft.id,
                        {
                            "id": draft.id,
                            "record_type": "draft",
                            "occurred_at": wire_time(draft.created_at),
                            "channel": None,
                            "draft": draft.to_api(self._draft_actions(draft, context)),
                            "approval": None,
                            "response": None,
                        },
                    ))
                for (tenant, _), approval in state.response_approvals.items():
                    draft = drafts_by_id.get(approval.draft_id)
                    if tenant != context.tenant_id or draft is None or approval.approved_at > as_of:
                        continue
                    timeline.append((
                        approval.approved_at,
                        approval.id,
                        {
                            "id": approval.id,
                            "record_type": "approval",
                            "occurred_at": wire_time(approval.approved_at),
                            "channel": None,
                            "draft": None,
                            "approval": approval.to_api([]),
                            "response": None,
                        },
                    ))
                for (tenant, _), dispatch in state.response_dispatches.items():
                    if tenant != context.tenant_id or dispatch.case_id != case.id or dispatch.created_at > as_of:
                        continue
                    timeline.append((
                        dispatch.created_at,
                        dispatch.id,
                        {
                            "id": dispatch.id,
                            "record_type": "response",
                            "occurred_at": wire_time(dispatch.created_at),
                            "channel": dispatch.channel,
                            "draft": None,
                            "approval": None,
                            "response": dispatch.to_api(self._dispatch_actions(dispatch, context)),
                        },
                    ))
                timeline.sort(key=lambda entry: (entry[0], entry[1]))
                page = timeline[offset:offset + limit]
                value = self._page_value([entry[2] for entry in page], offset, limit, len(timeline), as_of)
            self._audit(state, context, "list_case_responses", "service_case", case.id, case.version, correlation_id, "succeeded")
            return _Outcome(value, 200, "service_case", case.id, case.version)

        outcome = self.repository.atomic(query)
        return ServiceResponse(outcome.value, outcome.status)

    def add_case_interaction(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._body(
            body,
            {"channel", "direction", "occurred_at", "content_asset_id", "summary", "publication_reference_ids", "recording_consent_evidence_id"},
            {"channel", "direction", "occurred_at", "content_asset_id", "summary"},
        )
        channel = body["channel"]
        if channel not in INTAKE_CHANNELS:
            raise ValidationError("channel is not supported", field_name="channel")
        direction = body["direction"]
        if direction not in INTERACTION_DIRECTIONS:
            raise ValidationError("direction is not supported", field_name="direction")
        if context.principal_type == "customer" and direction != "inbound":
            raise ForbiddenError("a customer may only record inbound interactions", code="customer_direction_forbidden")
        occurred_at = self._parse_time(body["occurred_at"], "occurred_at")
        content_asset_id = require_uuid(body["content_asset_id"], "content_asset_id")
        if not self.object_assets.exists(context.tenant_id, content_asset_id):
            raise ValidationError("content_asset_id does not exist", code="evidence_not_found", field_name="content_asset_id")
        summary = body["summary"]
        if not isinstance(summary, str) or not 1 <= len(summary) <= 4000:
            raise ValidationError("summary must be 1 to 4000 characters", field_name="summary")
        references = body.get("publication_reference_ids", [])
        if not isinstance(references, list) or len(references) > 20:
            raise ValidationError("publication_reference_ids must contain at most 20 items", field_name="publication_reference_ids")
        references = [require_uuid(value, "publication_reference_ids") for value in references]
        consent_ref = body.get("recording_consent_evidence_id")
        if channel in {"phone", "onsite"} and not consent_ref:
            raise ValidationError("recording_consent_evidence_id is required for phone or onsite interactions", field_name="recording_consent_evidence_id")
        if consent_ref is not None:
            consent_ref = require_uuid(consent_ref, "recording_consent_evidence_id")
            if not self.object_assets.exists(context.tenant_id, consent_ref):
                raise ValidationError("recording_consent_evidence_id does not exist", code="evidence_not_found", field_name="recording_consent_evidence_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._authorize_case_actor(context, case, _SERVICE_AGENT)
            self._cas(case.version, expected_version)
            interaction = CaseInteraction(
                id=new_id(), tenant_id=context.tenant_id, case_id=case_id, version=1,
                created_at=now, updated_at=now, channel=channel, direction=direction,
                occurred_at=occurred_at, recorded_by=context.actor_id, content_asset_id=content_asset_id,
                summary=summary, publication_reference_ids=references, recording_consent_ref=consent_ref,
            )
            state.interactions[(context.tenant_id, interaction.id)] = interaction
            case.version += 1
            case.updated_at = now
            return _Outcome(
                interaction.to_api(self._interaction_actions(context)), 201, "case_interaction", interaction.id, interaction.version,
            )

        return self._run_command(context, "add_case_interaction", idempotency_key, {"case_id": case_id, **body}, correlation_id, mutate)

    def assign_service_case(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._authorize_staff(context, _SERVICE_SUPERVISOR)
        self._body(body, {"assignee_id", "reason_code", "effective_at"}, {"assignee_id", "reason_code"})
        assignee_id = require_uuid(body["assignee_id"], "assignee_id")
        reason_code = body["reason_code"]
        if not isinstance(reason_code, str) or not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must be 1 to 80 characters", field_name="reason_code")
        if body.get("effective_at") is not None:
            self._parse_time(body["effective_at"], "effective_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._cas(case.version, expected_version)
            if case.status == "closed":
                raise ConflictError("closed cases cannot be reassigned", code="case_closed")
            case.assignee_id = assignee_id
            if case.status in {"new", "triaged"}:
                case.status = "assigned"
            case.version += 1
            case.updated_at = now
            return self._case_outcome(case, context, 200)

        return self._run_command(context, "assign_service_case", idempotency_key, {"case_id": case_id, **body}, correlation_id, mutate)

    def escalate_service_case(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._authorize_staff(context, _SERVICE_AGENT)
        self._body(body, {"target_queue", "reason_code", "due_at", "minimum_necessary_context_asset_id"},
                   {"target_queue", "reason_code", "due_at", "minimum_necessary_context_asset_id"})
        target_queue = body["target_queue"]
        if target_queue not in ESCALATION_QUEUES:
            raise ValidationError("target_queue is not supported", field_name="target_queue")
        reason_code = body["reason_code"]
        if not isinstance(reason_code, str) or not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must be 1 to 80 characters", field_name="reason_code")
        due_at = self._parse_time(body["due_at"], "due_at")
        context_asset_id = require_uuid(body["minimum_necessary_context_asset_id"], "minimum_necessary_context_asset_id")
        if not self.object_assets.exists(context.tenant_id, context_asset_id):
            raise ValidationError("minimum_necessary_context_asset_id does not exist", code="evidence_not_found", field_name="minimum_necessary_context_asset_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._cas(case.version, expected_version)
            if case.status in {"resolved", "closed"}:
                raise ConflictError("resolved or closed cases cannot be escalated", code="case_already_closed")
            case.escalated_queue = target_queue
            case.escalated_due_at = due_at
            case.escalation_evidence_asset_id = context_asset_id
            case.status = "waiting_internal"
            case.version += 1
            case.updated_at = now
            sla_event_id = new_id()
            state.sla_events[(context.tenant_id, sla_event_id)] = SlaEvent(
                id=sla_event_id, tenant_id=context.tenant_id, case_id=case_id, action="escalated",
                occurred_at=now, actor_id=context.actor_id, reason_code=target_queue,
            )
            if case.category == "complaint" and target_queue in {"service_supervisor", "compliance"} and case.customer_id:
                job_id = new_id()
                state.jobs[(context.tenant_id, job_id)] = Job(
                    id=job_id, tenant_id=context.tenant_id, kind="request_suppression",
                    payload={"customer_id": case.customer_id, "scope": "marketing", "reason_code": "major_complaint_escalation"},
                )
            self._emit(
                state, event_type="support.case_escalated", tenant_id=context.tenant_id,
                aggregate_type="service_case", aggregate_id=case_id, aggregate_version=case.version,
                correlation_id=correlation_id, idempotency_key=f"{idempotency_key}:case-escalated",
                payload={"case_id": case_id, "target_queue": target_queue, "due_at": wire_time(due_at)},
                now=now,
            )
            return self._case_outcome(case, context, 200)

        return self._run_command(context, "escalate_service_case", idempotency_key, {"case_id": case_id, **body}, correlation_id, mutate)

    def draft_service_response(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._authorize_staff(context, _SERVICE_AGENT)
        self._body(body, {"knowledge_version", "approved_source_refs", "intent", "requested_tone", "model_policy_version"},
                   {"knowledge_version", "approved_source_refs", "intent", "requested_tone", "model_policy_version"})
        knowledge_version = body["knowledge_version"]
        if not isinstance(knowledge_version, str) or not 1 <= len(knowledge_version) <= 80:
            raise ValidationError("knowledge_version must be 1 to 80 characters", field_name="knowledge_version")
        refs = body["approved_source_refs"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 30:
            raise ValidationError("approved_source_refs must contain 1 to 30 items", field_name="approved_source_refs")
        refs = [require_uuid(value, "approved_source_refs") for value in refs]
        for ref in refs:
            if not self.knowledge_base.approved(context.tenant_id, knowledge_version, ref):
                raise ValidationError("approved_source_refs must reference approved knowledge", code="source_ref_not_approved", field_name="approved_source_refs")
        intent = body["intent"]
        if intent not in {"status", "procedure", "approved_report_explanation", "billing_explanation"}:
            raise ValidationError("intent is not supported", field_name="intent")
        if body["requested_tone"] not in {"professional", "concise"}:
            raise ValidationError("requested_tone is not supported", field_name="requested_tone")
        model_policy_version = body["model_policy_version"]
        if not isinstance(model_policy_version, str) or not 1 <= len(model_policy_version) <= 80:
            raise ValidationError("model_policy_version must be 1 to 80 characters", field_name="model_policy_version")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._cas(case.version, expected_version)
            draft_id = new_id()
            if case.escalated_queue == "compliance":
                draft = ResponseDraft(
                    id=draft_id, tenant_id=context.tenant_id, case_id=case_id, version=1,
                    created_at=now, updated_at=now, status="blocked", created_by=context.actor_id,
                    knowledge_version=knowledge_version, model_version="manual", prompt_version="manual",
                    source_refs=refs,
                )
            else:
                result = self.assistant.draft({
                    "category": case.category, "intent": intent, "requested_tone": body["requested_tone"],
                    "knowledge_version": knowledge_version, "approved_source_refs": refs,
                })
                if result.requires_human or not result.text:
                    # baseline 0001_domains.sql CHECK requires content_asset_id
                    # whenever status is 'requires_human'; store an internal
                    # evidence marker (never shown as draft text) so the row
                    # stays valid without implying a usable draft exists.
                    placeholder = "requires_human: automation declined to draft a response for this case"
                    placeholder_asset_id = self.object_assets.store(context.tenant_id, placeholder)
                    draft = ResponseDraft(
                        id=draft_id, tenant_id=context.tenant_id, case_id=case_id, version=1,
                        created_at=now, updated_at=now, status="requires_human", created_by=context.actor_id,
                        knowledge_version=knowledge_version, model_version=self.assistant.model_version,
                        prompt_version=self.assistant.prompt_version, source_refs=refs,
                        content_asset_id=placeholder_asset_id, content_sha256=_text_sha256(placeholder),
                        contains_new_investment_judgment=result.contains_new_investment_judgment,
                    )
                else:
                    content_asset_id = self.object_assets.store(context.tenant_id, result.text)
                    draft = ResponseDraft(
                        id=draft_id, tenant_id=context.tenant_id, case_id=case_id, version=1,
                        created_at=now, updated_at=now, status="drafted", created_by=context.actor_id,
                        knowledge_version=knowledge_version, model_version=self.assistant.model_version,
                        prompt_version=self.assistant.prompt_version, source_refs=refs,
                        text=result.text, content_asset_id=content_asset_id, content_sha256=_text_sha256(result.text),
                        contains_new_investment_judgment=result.contains_new_investment_judgment,
                    )
            state.response_drafts[(context.tenant_id, draft_id)] = draft
            return _Outcome(draft.to_api(self._draft_actions(draft, context)), 202, "response_draft", draft.id, draft.version)

        return self._run_command(context, "draft_service_response", idempotency_key, {"case_id": case_id, **body}, correlation_id, mutate)

    def resolve_service_case(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._authorize_staff(context, _SERVICE_AGENT)
        self._body(body, {"resolution_code", "resolution_summary", "customer_notice_evidence_id", "linked_refund_id", "follow_up_due_at"},
                   {"resolution_code", "resolution_summary", "customer_notice_evidence_id"})
        resolution_code = body["resolution_code"]
        if not isinstance(resolution_code, str) or not 1 <= len(resolution_code) <= 80:
            raise ValidationError("resolution_code must be 1 to 80 characters", field_name="resolution_code")
        resolution_summary = body["resolution_summary"]
        if not isinstance(resolution_summary, str) or not 1 <= len(resolution_summary) <= 4000:
            raise ValidationError("resolution_summary must be 1 to 4000 characters", field_name="resolution_summary")
        notice_evidence_id = require_uuid(body["customer_notice_evidence_id"], "customer_notice_evidence_id")
        if not self.object_assets.exists(context.tenant_id, notice_evidence_id):
            raise ValidationError("customer_notice_evidence_id does not exist", code="evidence_not_found", field_name="customer_notice_evidence_id")
        linked_refund_id = require_uuid(body["linked_refund_id"], "linked_refund_id") if body.get("linked_refund_id") else None
        follow_up_due_at = self._parse_time(body["follow_up_due_at"], "follow_up_due_at") if body.get("follow_up_due_at") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._cas(case.version, expected_version)
            if case.status == "closed":
                raise ConflictError("closed cases must be reopened before resolving again", code="case_closed")
            if case.status == "resolved":
                raise ConflictError("case is already resolved", code="case_already_resolved")
            resolution = CaseResolution(
                id=new_id(), tenant_id=context.tenant_id, case_id=case_id, resolution_code=resolution_code,
                resolution_summary=resolution_summary, customer_notice_evidence_id=notice_evidence_id,
                resolved_by=context.actor_id, resolved_at=now, linked_refund_id=linked_refund_id,
                follow_up_due_at=follow_up_due_at,
            )
            state.case_resolutions[(context.tenant_id, resolution.id)] = resolution
            case.status = "resolved"
            case.resolved_at = now
            case.resolution_code = resolution_code
            if linked_refund_id:
                case.linked_refund_id = linked_refund_id
            case.version += 1
            case.updated_at = now
            breached = bool(case.resolution_due_at and now > case.resolution_due_at)
            sla_event_id = new_id()
            state.sla_events[(context.tenant_id, sla_event_id)] = SlaEvent(
                id=sla_event_id, tenant_id=context.tenant_id, case_id=case_id,
                action="breached" if breached else "met", occurred_at=now,
                actor_id=context.actor_id, reason_code=resolution_code,
            )
            self._emit(
                state, event_type="support.case_resolved", tenant_id=context.tenant_id,
                aggregate_type="service_case", aggregate_id=case_id, aggregate_version=case.version,
                correlation_id=correlation_id, idempotency_key=f"{idempotency_key}:case-resolved",
                payload={"case_id": case_id, "customer_id": case.customer_id, "resolution_record_id": resolution.id},
                now=now,
            )
            return self._case_outcome(case, context, 200)

        return self._run_command(context, "resolve_service_case", idempotency_key, {"case_id": case_id, **body}, correlation_id, mutate)

    def reopen_service_case(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._body(body, {"reason_code", "note", "evidence_asset_ids"}, {"reason_code"})
        reason_code = body["reason_code"]
        if not isinstance(reason_code, str) or not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must be 1 to 80 characters", field_name="reason_code")
        note = body.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 4000):
            raise ValidationError("note must be at most 4000 characters", field_name="note")
        evidence_ids = body.get("evidence_asset_ids", [])
        if not isinstance(evidence_ids, list) or len(evidence_ids) > 30:
            raise ValidationError("evidence_asset_ids must contain at most 30 items", field_name="evidence_asset_ids")
        evidence_ids = [require_uuid(value, "evidence_asset_ids") for value in evidence_ids]
        for asset_id in evidence_ids:
            if not self.object_assets.exists(context.tenant_id, asset_id):
                raise ValidationError("evidence_asset_ids references an asset that does not exist", code="evidence_not_found", field_name="evidence_asset_ids")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._authorize_case_actor(context, case, _SERVICE_AGENT)
            self._cas(case.version, expected_version)
            if case.status not in {"resolved", "closed"}:
                raise ConflictError("only resolved or closed cases can be reopened", code="case_not_resolved_or_closed")
            case.status = "reopened"
            case.version += 1
            case.updated_at = now
            sla_event_id = new_id()
            state.sla_events[(context.tenant_id, sla_event_id)] = SlaEvent(
                id=sla_event_id, tenant_id=context.tenant_id, case_id=case_id, action="reopened",
                occurred_at=now, actor_id=context.actor_id, reason_code=reason_code,
            )
            return self._case_outcome(case, context, 200)

        return self._run_command(context, "reopen_service_case", idempotency_key, {"case_id": case_id, **body}, correlation_id, mutate)

    # ---- response draft / dispatch commands ------------------------------

    def get_service_response_draft(self, context: ActorContext, draft_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        draft_id = require_uuid(draft_id, "draft_id")
        self._authorize_staff(context, _SERVICE_AGENT)
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> _Outcome:
            draft = self._stored_draft(state, context.tenant_id, draft_id)
            self._audit(state, context, "get_service_response_draft", "response_draft", draft.id, draft.version, correlation_id, "succeeded")
            return _Outcome(draft.to_api(self._draft_actions(draft, context)), 200, "response_draft", draft.id, draft.version)

        outcome = self.repository.atomic(query)
        return ServiceResponse(outcome.value, outcome.status, self._etag(outcome.aggregate_version))

    def approve_service_response_draft(
        self,
        context: ActorContext,
        draft_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        draft_id = require_uuid(draft_id, "draft_id")
        self._authorize_staff(context, _APPROVERS)
        self._body(
            body,
            {"draft_content_sha256", "decision", "approval_mode", "template_version", "policy_version", "reason_code", "publication_reference_ids", "valid_until"},
            {"draft_content_sha256", "decision", "approval_mode", "template_version", "policy_version", "reason_code", "publication_reference_ids", "valid_until"},
        )
        sha = body["draft_content_sha256"]
        if not _SHA_RE.fullmatch(sha):
            raise ValidationError("draft_content_sha256 must be a lowercase sha-256 hex digest", field_name="draft_content_sha256")
        decision = body["decision"]
        if decision not in {"approve", "reject"}:
            raise ValidationError("decision is not supported", field_name="decision")
        approval_mode = body["approval_mode"]
        if approval_mode not in APPROVAL_MODES:
            raise ValidationError("approval_mode is not supported", field_name="approval_mode")
        template_version = body["template_version"]
        if not isinstance(template_version, str) or not 1 <= len(template_version) <= 80:
            raise ValidationError("template_version must be 1 to 80 characters", field_name="template_version")
        policy_version = body["policy_version"]
        if not isinstance(policy_version, str) or not 1 <= len(policy_version) <= 80:
            raise ValidationError("policy_version must be 1 to 80 characters", field_name="policy_version")
        reason_code = body["reason_code"]
        if not isinstance(reason_code, str) or not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must be 1 to 80 characters", field_name="reason_code")
        references = body["publication_reference_ids"]
        if not isinstance(references, list) or len(references) > 20:
            raise ValidationError("publication_reference_ids must contain at most 20 items", field_name="publication_reference_ids")
        references = [require_uuid(value, "publication_reference_ids") for value in references]
        valid_until = self._parse_time(body["valid_until"], "valid_until")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        if valid_until <= now:
            raise ValidationError("valid_until must be in the future", field_name="valid_until")

        def mutate(state: StoreState) -> _Outcome:
            draft = self._stored_draft(state, context.tenant_id, draft_id)
            self._cas(draft.version, expected_version)
            if decision == "approve" and draft.status != "drafted":
                raise ConflictError("only a drafted response can be approved for sending", code="draft_not_ready")
            if draft.content_sha256 != sha:
                raise ConflictError("draft_content_sha256 does not match the current draft", code="draft_content_mismatch")
            if decision == "approve" and draft.contains_new_investment_judgment:
                raise ForbiddenError("a draft containing new investment judgment cannot be approved", code="new_investment_judgment_blocked")
            if approval_mode == "human_reviewed":
                if decision == "approve" and context.actor_id == draft.created_by:
                    raise ForbiddenError("the approver must differ from the drafter", code="maker_checker_violation")
            else:
                if decision == "approve" and not self._authorized(context, _LOW_RISK_TEMPLATE_APPROVERS):
                    raise ForbiddenError(
                        "approved_low_risk_template may only be recorded by the controlled policy service",
                        code="low_risk_template_requires_policy_service",
                    )
                case = self._stored_case(state, context.tenant_id, draft.case_id)
                if decision == "approve" and (
                    case.category in _LOW_RISK_TEMPLATE_BLOCKED_CATEGORIES or case.escalated_queue == "compliance"
                ):
                    raise ForbiddenError(
                        "approved_low_risk_template cannot authorize this case category",
                        code="low_risk_template_category_blocked",
                    )
            evidence_record = canonical_hash({
                "draft_id": draft_id, "decision": decision, "reviewer_id": context.actor_id,
                "approved_at": wire_time(now), "draft_content_sha256": sha,
            })
            evidence_asset_id = self.object_assets.store(context.tenant_id, evidence_record)
            approval = ResponseApproval(
                id=new_id(), tenant_id=context.tenant_id, draft_id=draft_id, version=1,
                created_at=now, updated_at=now, draft_content_sha256=sha, decision=decision,
                approval_mode=approval_mode, reviewer_id=context.actor_id, template_version=template_version,
                policy_version=policy_version, evidence_asset_id=evidence_asset_id, approved_at=now, valid_until=valid_until,
            )
            state.response_approvals[(context.tenant_id, approval.id)] = approval
            draft.version += 1
            draft.updated_at = now
            return _Outcome(approval.to_api([]), 201, "response_approval", approval.id, approval.version)

        return self._run_command(context, "approve_service_response_draft", idempotency_key, {"draft_id": draft_id, **body}, correlation_id, mutate)

    def send_service_response(
        self,
        context: ActorContext,
        case_id: str,
        body: dict[str, Any],
        *,
        expected_version: int | None,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        case_id = require_uuid(case_id, "case_id")
        self._authorize_staff(context, _RESPONSE_SENDERS)
        self._body(
            body,
            {"draft_id", "draft_content_sha256", "channel_binding_id", "channel", "approval_mode", "approval_id",
             "template_version", "publication_reference_ids", "valid_until"},
            {"draft_id", "draft_content_sha256", "channel_binding_id", "channel", "approval_mode", "approval_id",
             "template_version", "publication_reference_ids", "valid_until"},
        )
        draft_id = require_uuid(body["draft_id"], "draft_id")
        sha = body["draft_content_sha256"]
        if not _SHA_RE.fullmatch(sha):
            raise ValidationError("draft_content_sha256 must be a lowercase sha-256 hex digest", field_name="draft_content_sha256")
        channel_binding_id = require_uuid(body["channel_binding_id"], "channel_binding_id")
        channel = body["channel"]
        if channel not in DISPATCH_CHANNELS:
            raise ValidationError("channel is not supported", field_name="channel")
        approval_mode = body["approval_mode"]
        if approval_mode not in APPROVAL_MODES:
            raise ValidationError("approval_mode is not supported", field_name="approval_mode")
        approval_id = require_uuid(body["approval_id"], "approval_id")
        template_version = body["template_version"]
        if not isinstance(template_version, str) or not 1 <= len(template_version) <= 80:
            raise ValidationError("template_version must be 1 to 80 characters", field_name="template_version")
        references = body["publication_reference_ids"]
        if not isinstance(references, list) or len(references) > 20:
            raise ValidationError("publication_reference_ids must contain at most 20 items", field_name="publication_reference_ids")
        references = [require_uuid(value, "publication_reference_ids") for value in references]
        valid_until = self._parse_time(body["valid_until"], "valid_until")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        if valid_until <= now:
            raise ValidationError("valid_until must be in the future", field_name="valid_until")

        def mutate(state: StoreState) -> _Outcome:
            case = self._stored_case(state, context.tenant_id, case_id)
            self._cas(case.version, expected_version)
            if not case.customer_id:
                raise ConflictError("an unlinked case cannot send a verified channel response", code="case_not_linked_to_customer")
            draft = self._stored_draft(state, context.tenant_id, draft_id)
            if draft.case_id != case_id:
                raise ValidationError("draft_id does not belong to this case", field_name="draft_id")
            if draft.contains_new_investment_judgment:
                raise ForbiddenError("a draft containing new investment judgment cannot be sent", code="new_investment_judgment_blocked")
            if draft.status != "drafted":
                raise ConflictError("draft is not ready to send", code="draft_not_ready")
            if draft.content_sha256 != sha:
                raise ConflictError("draft_content_sha256 does not match the current draft", code="draft_content_mismatch")
            approval = state.response_approvals.get((context.tenant_id, approval_id))
            if not approval or approval.draft_id != draft_id:
                raise NotFoundError("approval was not found for this draft")
            if approval.decision != "approve" or approval.draft_content_sha256 != sha or approval.approval_mode != approval_mode:
                raise ConflictError("approval does not authorize this exact draft content", code="approval_mismatch")
            if now > approval.valid_until:
                raise ConflictError("approval has expired", code="approval_expired")
            if not self.channel_bindings.verified(context.tenant_id, case.customer_id, channel_binding_id, channel):
                raise ValidationError("channel_binding_id is not a verified binding for this channel", code="channel_binding_not_verified", field_name="channel_binding_id")
            dispatch = ResponseDispatch(
                id=new_id(), tenant_id=context.tenant_id, case_id=case_id, customer_id=case.customer_id,
                draft_id=draft_id, version=1, created_at=now, updated_at=now, status="queued",
                content_sha256=sha, channel_binding_id=channel_binding_id, channel=channel,
                approval_id=approval_id, approval_mode=approval_mode, idempotency_key=idempotency_key,
                requested_by=context.actor_id, valid_until=valid_until, publication_reference_ids=references,
            )
            state.response_dispatches[(context.tenant_id, dispatch.id)] = dispatch
            case.version += 1
            case.updated_at = now
            self._emit(
                state, event_type="support.response_status_changed", tenant_id=context.tenant_id,
                aggregate_type="service_response", aggregate_id=dispatch.id, aggregate_version=1,
                correlation_id=correlation_id, idempotency_key=f"{idempotency_key}:response-queued",
                payload={"response_id": dispatch.id, "case_id": case_id, "state": "queued", "receipt_id": None},
                now=now,
            )
            return _Outcome(dispatch.to_api(self._dispatch_actions(dispatch, context)), 202, "service_response", dispatch.id, dispatch.version)

        return self._run_command(context, "send_service_response", idempotency_key, {"case_id": case_id, **body}, correlation_id, mutate)

    def get_service_response(self, context: ActorContext, response_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        response_id = require_uuid(response_id, "response_id")
        self._authorize_staff(context, _RESPONSE_READERS)
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> _Outcome:
            dispatch = self._stored_dispatch(state, context.tenant_id, response_id)
            self._audit(state, context, "get_service_response", "service_response", dispatch.id, dispatch.version, correlation_id, "succeeded")
            return _Outcome(dispatch.to_api(self._dispatch_actions(dispatch, context)), 200, "service_response", dispatch.id, dispatch.version)

        outcome = self.repository.atomic(query)
        return ServiceResponse(outcome.value, outcome.status, self._etag(outcome.aggregate_version))

    def receive_service_response_receipt(
        self,
        context: ActorContext,
        body: dict[str, Any],
        *,
        provider: str,
        idempotency_key: str,
        webhook_timestamp: str = "",
        webhook_nonce: str = "",
        signature: str = "",
        raw_body: bytes = b"",
        correlation_id: str | None = None,
    ) -> ServiceResponse:
        self._authorize_staff(context, _MESSAGING_ADAPTER)
        self._body(
            body,
            {"event_id", "response_id", "provider_message_ref", "provider_status", "provider_occurred_at", "provider_receipt_asset_id"},
            {"event_id", "response_id", "provider_message_ref", "provider_status", "provider_occurred_at", "provider_receipt_asset_id"},
        )
        event_id = require_uuid(body["event_id"], "event_id")
        response_id = require_uuid(body["response_id"], "response_id")
        provider_message_ref = body["provider_message_ref"]
        if not isinstance(provider_message_ref, str) or not 1 <= len(provider_message_ref) <= 200:
            raise ValidationError("provider_message_ref must be 1 to 200 characters", field_name="provider_message_ref")
        provider_status = body["provider_status"]
        if provider_status not in RECEIPT_PROVIDER_STATUSES:
            raise ValidationError("provider_status is not supported", field_name="provider_status")
        provider_occurred_at = self._parse_time(body["provider_occurred_at"], "provider_occurred_at")
        receipt_asset_id = require_uuid(body["provider_receipt_asset_id"], "provider_receipt_asset_id")
        if not self.object_assets.exists(context.tenant_id, receipt_asset_id):
            raise ValidationError("provider_receipt_asset_id does not exist", code="evidence_not_found", field_name="provider_receipt_asset_id")
        if not provider:
            raise ValidationError("Integration-Key-Id header is required", field_name="Integration-Key-Id")
        if not _KEY_RE.fullmatch(idempotency_key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        if not _WEBHOOK_TS_RE.fullmatch(webhook_timestamp):
            raise ValidationError("X-Webhook-Timestamp must be unix seconds", field_name="X-Webhook-Timestamp")
        if not _WEBHOOK_NONCE_RE.fullmatch(webhook_nonce):
            raise ValidationError("X-Webhook-Nonce must be 16 to 128 characters", field_name="X-Webhook-Nonce")
        now = self._clock()
        if abs(int(now.timestamp()) - int(webhook_timestamp)) > _WEBHOOK_TIMESTAMP_SKEW_SECONDS:
            raise ValidationError("X-Webhook-Timestamp is outside the allowed skew", field_name="X-Webhook-Timestamp")
        secret = self.integration_keys.secret(context.tenant_id, provider)
        if not secret:
            raise ForbiddenError("Integration-Key-Id is not registered", code="integration_key_unknown")
        expected_signature = self._webhook_signature(secret, webhook_timestamp, webhook_nonce, raw_body)
        if not hmac.compare_digest(expected_signature, signature or ""):
            raise ForbiddenError("X-Webhook-Signature is invalid", code="webhook_signature_invalid")
        correlation_id = self._correlation(correlation_id)
        payload_sha256 = canonical_hash({
            "event_id": event_id, "response_id": response_id, "provider_message_ref": provider_message_ref,
            "provider_status": provider_status, "provider_occurred_at": wire_time(provider_occurred_at),
        })

        def mutate(state: StoreState) -> tuple[dict[str, Any], int]:
            nonce_key = (context.tenant_id, provider, webhook_nonce)
            if nonce_key in state.webhook_nonces:
                raise ConflictError("X-Webhook-Nonce was already used", code="webhook_nonce_replayed")
            state.webhook_nonces[nonce_key] = now
            receipt_id = new_id()
            existing_id = next(
                (rid for (tenant, pv, eid), rid in self._receipt_index(state).items() if tenant == context.tenant_id and pv == provider and eid == event_id),
                None,
            )
            if existing_id:
                existing = state.response_receipts[(context.tenant_id, existing_id)]
                status = "duplicate" if existing.payload_sha256 == payload_sha256 else "quarantined"
                self._audit(state, context, "receive_service_response_receipt", "service_response_receipt", existing_id, None, correlation_id, status)
                return self._callback_receipt(existing_id, event_id, status, now, correlation_id), 202
            dispatch = state.response_dispatches.get((context.tenant_id, response_id))
            if not dispatch:
                self._audit(state, context, "receive_service_response_receipt", "service_response_receipt", receipt_id, None, correlation_id, "quarantined")
                return self._callback_receipt(receipt_id, event_id, "quarantined", now, correlation_id), 202
            receipt = ResponseReceipt(
                id=receipt_id, tenant_id=context.tenant_id, response_id=response_id, provider=provider,
                event_id=event_id, provider_message_ref=provider_message_ref, provider_status=provider_status,
                provider_occurred_at=provider_occurred_at, received_at=now, provider_receipt_asset_id=receipt_asset_id,
                payload_sha256=payload_sha256,
            )
            state.response_receipts[(context.tenant_id, receipt.id)] = receipt
            self._apply_receipt(dispatch, provider_status, provider_occurred_at)
            dispatch.version += 1
            dispatch.updated_at = now
            self._emit(
                state, event_type="support.response_status_changed", tenant_id=context.tenant_id,
                aggregate_type="service_response", aggregate_id=dispatch.id, aggregate_version=dispatch.version,
                correlation_id=correlation_id, idempotency_key=f"{idempotency_key}:receipt-{event_id}",
                payload={"response_id": dispatch.id, "case_id": dispatch.case_id, "state": dispatch.status, "receipt_id": receipt.id},
                now=now,
            )
            self._audit(state, context, "receive_service_response_receipt", "service_response_receipt", receipt.id, None, correlation_id, "accepted")
            return self._callback_receipt(receipt.id, event_id, "accepted", now, correlation_id), 202

        value, status = self.repository.atomic(mutate)
        return ServiceResponse(value, status)

    # ---- run-command / outcome helpers ------------------------------------

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

    # ---- authorization, mapping and validation ----------------------------

    @staticmethod
    def _authorized(context: ActorContext, roles: set[str]) -> bool:
        return bool(context.roles & roles)

    def _authorize_staff(self, context: ActorContext, roles: set[str]) -> None:
        if self._authorized(context, roles):
            return
        raise ForbiddenError("actor is not authorized for this operation")

    def _authorize_case_create(self, context: ActorContext) -> None:
        if context.principal_type == "customer":
            return
        if self._authorized(context, _SERVICE_AGENT):
            return
        raise ForbiddenError("actor cannot create service cases")

    def _authorize_case_actor(self, context: ActorContext, case: ServiceCase, staff_roles: set[str]) -> None:
        if context.principal_type == "customer":
            if context.customer_id == case.customer_id and case.customer_id is not None:
                return
            # A not-found shaped response prevents service case enumeration.
            raise NotFoundError("resource was not found")
        if self._authorized(context, staff_roles):
            return
        raise ForbiddenError("actor cannot access this case")

    def _authorize_case_timeline_reader(self, context: ActorContext, case: ServiceCase) -> None:
        self._authorize_case_timeline_principal(context)
        if context.principal_type == "customer":
            self._authorize_case_actor(context, case, set())

    def _authorize_case_timeline_principal(self, context: ActorContext) -> None:
        """Reject non-readers before a case lookup can reveal its existence.

        Customer authorization remains resource-bound: after a lookup it uses a
        NotFound-shaped result for another, unlinked, cross-tenant, or missing
        case.  Staff timeline readers are allowed to continue to that lookup.
        """
        if context.principal_type == "customer":
            return
        if context.principal_type == "staff" and self._authorized(context, _TIMELINE_READERS):
            return
        raise ForbiddenError("actor cannot read this case timeline")

    @staticmethod
    def _page_offset(cursor: str | None, limit: int) -> int:
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if cursor is None:
            return 0
        try:
            offset = int(cursor)
            assert offset >= 0
            return offset
        except (TypeError, ValueError, AssertionError) as exc:
            raise ValidationError("cursor is invalid", field_name="cursor") from exc

    @staticmethod
    def _page_value(items: list[dict[str, Any]], offset: int, limit: int, total: int, as_of: datetime) -> dict[str, Any]:
        return {
            "items": items,
            "next_cursor": str(offset + limit) if offset + limit < total else None,
            "as_of": wire_time(as_of),
            "total_estimate": total,
        }

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
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        except (ValueError, AttributeError) as exc:
            raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _webhook_signature(secret: bytes, timestamp: str, nonce: str, raw_body: bytes) -> str:
        body_hash = sha256(raw_body).hexdigest()
        message = f"{timestamp}\n{nonce}\nPOST\n{_WEBHOOK_RECEIPT_PATH}\n{body_hash}".encode("utf-8")
        return hmac.new(secret, message, sha256).hexdigest()

    @staticmethod
    def _etag(version: int | None) -> str | None:
        return f'"{version}"' if version else None

    @staticmethod
    def _unique_case_number(state: StoreState, tenant_id: str) -> str:
        while True:
            candidate = f"SC-{new_id()[:8].upper()}"
            if (tenant_id, candidate) not in state.case_number_index:
                return candidate

    @staticmethod
    def _stored_case(state: StoreState, tenant_id: str, case_id: str) -> ServiceCase:
        case = state.cases.get((tenant_id, case_id))
        if not case:
            raise NotFoundError("service case was not found")
        return case

    @staticmethod
    def _stored_draft(state: StoreState, tenant_id: str, draft_id: str) -> ResponseDraft:
        draft = state.response_drafts.get((tenant_id, draft_id))
        if not draft:
            raise NotFoundError("response draft was not found")
        return draft

    @staticmethod
    def _stored_dispatch(state: StoreState, tenant_id: str, response_id: str) -> ResponseDispatch:
        dispatch = state.response_dispatches.get((tenant_id, response_id))
        if not dispatch:
            raise NotFoundError("service response was not found")
        return dispatch

    @staticmethod
    def _receipt_index(state: StoreState) -> dict[tuple[str, str, str], str]:
        return {
            (receipt.tenant_id, receipt.provider, receipt.event_id): rid
            for (_, rid), receipt in state.response_receipts.items()
        }

    @staticmethod
    def _apply_receipt(dispatch: ResponseDispatch, provider_status: str, occurred_at: datetime) -> None:
        if provider_status == "read":
            dispatch.read_at = dispatch.read_at or occurred_at
            if _DISPATCH_RANK.get(dispatch.status, -1) < _DISPATCH_RANK["delivered"]:
                dispatch.status = "delivered"
                dispatch.delivered_at = dispatch.delivered_at or occurred_at
            return
        if provider_status == "failed":
            if dispatch.status not in {"delivered"}:
                dispatch.status = "failed"
            return
        new_rank = _DISPATCH_RANK.get(provider_status)
        if new_rank is None:
            return
        if new_rank > _DISPATCH_RANK.get(dispatch.status, -1):
            dispatch.status = provider_status
            if provider_status == "accepted":
                dispatch.accepted_at = dispatch.accepted_at or occurred_at
            elif provider_status == "sent":
                dispatch.sent_at = dispatch.sent_at or occurred_at
            elif provider_status == "delivered":
                dispatch.delivered_at = dispatch.delivered_at or occurred_at

    def _callback_receipt(self, receipt_id: str, event_id: str, status: str, now: datetime, correlation_id: str) -> dict[str, Any]:
        return {
            "receipt_id": receipt_id,
            "event_id": event_id,
            "status": status,
            "received_at": wire_time(now),
            "correlation_id": correlation_id,
        }

    def _case_actions(self, case: ServiceCase, context: ActorContext) -> list[str]:
        actions: list[str] = []
        is_owner_customer = context.principal_type == "customer" and context.customer_id == case.customer_id and case.customer_id is not None
        if is_owner_customer:
            actions.append("add_case_interaction")
        if self._authorized(context, _SERVICE_AGENT):
            if case.status not in {"resolved", "closed"}:
                actions.extend(["add_case_interaction", "escalate_service_case", "draft_service_response"])
            if case.status not in {"resolved", "closed"}:
                actions.append("resolve_service_case")
        if self._authorized(context, _RESPONSE_SENDERS) and case.customer_id and case.status not in {"resolved", "closed"}:
            actions.append("send_service_response")
        if self._authorized(context, _SERVICE_SUPERVISOR) and case.status != "closed":
            actions.append("assign_service_case")
        if case.status in {"resolved", "closed"} and (is_owner_customer or self._authorized(context, _SERVICE_AGENT)):
            actions.append("reopen_service_case")
        return sorted(set(actions))

    def _interaction_actions(self, context: ActorContext) -> list[str]:
        return []

    def _draft_actions(self, draft: ResponseDraft, context: ActorContext) -> list[str]:
        if draft.status != "drafted" or draft.contains_new_investment_judgment:
            return []
        if not self._authorized(context, _APPROVERS):
            return []
        if context.actor_id == draft.created_by and not self._authorized(context, _LOW_RISK_TEMPLATE_APPROVERS):
            return []
        return ["approve_service_response_draft"]

    def _dispatch_actions(self, dispatch: ResponseDispatch, context: ActorContext) -> list[str]:
        return []

    def _case_outcome(self, case: ServiceCase, context: ActorContext, status: int) -> _Outcome:
        return _Outcome(case.to_api(self._case_actions(case, context)), status, "service_case", case.id, case.version)

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
                action=f"m13.{action}", aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context: ActorContext, action: str, correlation_id: str | None, reason_code: str, aggregate_id: str | None = None) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security" if aggregate_id is None else "service_case", aggregate_id, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)
