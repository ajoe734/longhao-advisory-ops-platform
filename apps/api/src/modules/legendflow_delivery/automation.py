from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


@dataclass(frozen=True)
class ExceptionSummary:
    delivery_id: str
    headline: str
    suggested_next_step: str


@dataclass(frozen=True)
class AutomationRun:
    id: str
    tenant_id: str
    capability: str
    model_version: str
    prompt_version: str
    ruleset_version: str
    source_refs: tuple[str, ...]
    input_sha256: str
    summaries: tuple[ExceptionSummary, ...]
    created_at: datetime
    status: str = "untrusted_draft"


@dataclass(frozen=True)
class AutomationReview:
    id: str
    tenant_id: str
    run_id: str
    reviewer_id: str
    action: str
    reason_code: str
    reviewed_at: datetime


class ExceptionSummaryAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def summarize(self, records: list[dict[str, Any]]) -> tuple[ExceptionSummary, ...]: ...


class LocalExceptionSummaryAssistant:
    """Deterministic local fallback; consumes minimized error codes only, never content or PII."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m09-exception-summary-v1"
    ruleset_version = "m09-dlq-triage-v1"

    _HEADLINES = {
        "entitlement_revoked": "收件人權限已於排程後失效，已安全抑制未送出",
        "content_expired": "建議內容已過期，不可重送",
        "provider_result_unknown": "供應商結果不明，已安全重試並保留單一邏輯訊息",
        "entitlement_not_active": "收件人目前權限未生效，不建議重送",
    }

    def summarize(self, records: list[dict[str, Any]]) -> tuple[ExceptionSummary, ...]:
        summaries: list[ExceptionSummary] = []
        for record in records:
            code = record["error_code"]
            headline = self._HEADLINES.get(code, f"未分類例外代碼：{code}")
            next_step = "需人工核對權限與有效期後方可核准重驅" if record["attempt_count"] >= 1 else "等待下一次排程重試"
            summaries.append(ExceptionSummary(record["delivery_id"], headline, next_step))
        return tuple(summaries)


class DeliveryExceptionAutomationService:
    """Drafts untrusted DLQ/timeout summaries for human triage; has no send or redrive authority.

    LLM output here may only fill a pre-approved template's non-decision
    fields (a plain-language headline and a suggested next step drawn from a
    fixed vocabulary); it never selects recipients, content, channel, timing
    or redrive approval, and a supervisor must adopt or reject the draft
    before any operational action is taken.
    """

    def __init__(self, repository: InMemoryRepository, assistant: ExceptionSummaryAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M09 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def summarize(self, context: ActorContext, records: list[dict[str, Any]]) -> AutomationRun:
        if context.principal_type != "staff" or not context.roles & {"delivery_operator", "delivery_supervisor"}:
            raise ForbiddenError("actor cannot request exception summaries")
        cleaned: list[dict[str, Any]] = []
        source_refs: set[str] = set()
        for record in records:
            if set(record) != {"delivery_id", "error_code", "attempt_count"}:
                raise ValidationError("automation input only accepts minimized exception records", code="automation_input_not_minimized")
            delivery_id = require_uuid(record["delivery_id"], "delivery_id")
            attempt_count = record["attempt_count"]
            if not isinstance(attempt_count, int) or attempt_count < 0:
                raise ValidationError("attempt_count must be a non-negative integer", field_name="attempt_count")
            if not isinstance(record["error_code"], str) or not record["error_code"]:
                raise ValidationError("error_code must be a non-empty string", field_name="error_code")
            source_refs.add(delivery_id)
            cleaned.append({"delivery_id": delivery_id, "error_code": record["error_code"], "attempt_count": attempt_count})
        summaries = self.assistant.summarize(cleaned)
        run = AutomationRun(
            id=new_id(), tenant_id=context.tenant_id, capability="delivery_exception_summary",
            model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version, source_refs=tuple(sorted(source_refs)),
            input_sha256=canonical_hash(cleaned), summaries=summaries, created_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationRun:
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)

    def record_review(self, context: ActorContext, run_id: str, *, action: str, reason_code: str) -> AutomationReview:
        if context.principal_type != "staff" or not context.roles & {"delivery_operator", "delivery_supervisor"}:
            raise ForbiddenError("actor cannot review exception summaries")
        run_id = require_uuid(run_id, "run_id")
        if action not in {"adopted", "modified", "rejected"}:
            raise ValidationError("automation review action is not supported", field_name="action")
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        review = AutomationReview(id=new_id(), tenant_id=context.tenant_id, run_id=run_id, reviewer_id=context.actor_id, action=action, reason_code=reason_code, reviewed_at=utc_now())

        def save(state: StoreState) -> AutomationReview:
            if (context.tenant_id, run_id) not in state.automation_runs:
                raise ValidationError("automation run was not found", code="automation_run_not_found")
            state.automation_reviews[(context.tenant_id, review.id)] = review
            return review

        return self.repository.atomic(save)
