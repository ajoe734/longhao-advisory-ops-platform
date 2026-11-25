from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


_REASON_TEMPLATES = {
    "gate_not_satisfied": "審查尚未全部通過，待補齊後才能重新送出公司接受。",
    "stale_assessment": "部分審查已有新版本，請以最新結果重新送出。",
    "segregation_conflict": "本決策需由未參與條款製作的授權人處理。",
    "package_hash_mismatch": "文件版本已變更，請重新完成最新版本的簽署。",
}


@dataclass(frozen=True)
class NoticeDraft:
    locale: str
    subject: str
    body: str


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
    draft: NoticeDraft
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


class NoticeDraftAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def draft(self, reason_code: str, locale: str, package_code: str) -> NoticeDraft: ...


class LocalNoticeDraftAssistant:
    """Deterministic local fallback; only reads approved reason codes, never raw PII."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m02-notice-draft-v1"
    ruleset_version = "m02-approved-reason-templates-v1"

    def draft(self, reason_code: str, locale: str, package_code: str) -> NoticeDraft:
        body = _REASON_TEMPLATES.get(reason_code, "請洽業務單位了解目前申辦狀態。")
        return NoticeDraft(locale=locale, subject=f"關於您的申辦（方案 {package_code}）", body=body)


class NoticeAutomationService:
    """Drafts customer-facing notice copy; it has no command authority.

    Output is stored as ``untrusted_draft`` and never sent on its own. A human
    reviewer must adopt or reject it before any channel dispatch, and only
    approved reason codes / template text may be used — never raw AML or KYC
    case content, and never new contract terms the customer has not agreed to.
    """

    def __init__(self, repository: InMemoryRepository, assistant: NoticeDraftAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M02 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def draft_notice(self, context: ActorContext, *, reason_code: str, package_code: str, locale: str = "zh-TW") -> AutomationRun:
        if context.principal_type != "staff" or not context.roles & {"contract_operator", "company_acceptor"}:
            raise ForbiddenError("actor cannot request a notice draft")
        if reason_code not in _REASON_TEMPLATES and reason_code != "manual":
            raise ValidationError("reason_code is not an approved template key", field_name="reason_code")
        if locale not in {"zh-TW", "en"}:
            raise ValidationError("locale is not supported", field_name="locale")
        draft = self.assistant.draft(reason_code, locale, package_code)
        payload = {"reason_code": reason_code, "package_code": package_code, "locale": locale}
        run = AutomationRun(
            id=new_id(), tenant_id=context.tenant_id, capability="acceptance_notice_draft",
            model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version, source_refs=(reason_code,),
            input_sha256=canonical_hash(payload), draft=draft, created_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationRun:
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)

    def record_review(self, context: ActorContext, run_id: str, *, action: str, reason_code: str) -> AutomationReview:
        if context.principal_type != "staff" or not context.roles & {"contract_operator", "company_acceptor"}:
            raise ForbiddenError("actor cannot review a notice draft")
        run_id = require_uuid(run_id, "run_id")
        if action not in {"adopted", "modified", "rejected"}:
            raise ValidationError("automation review action is not supported", field_name="action")
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        review = AutomationReview(
            id=new_id(), tenant_id=context.tenant_id, run_id=run_id, reviewer_id=context.actor_id,
            action=action, reason_code=reason_code, reviewed_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationReview:
            if (context.tenant_id, run_id) not in state.automation_runs:
                raise ValidationError("automation run was not found", code="automation_run_not_found")
            state.automation_reviews[(context.tenant_id, review.id)] = review
            return review

        return self.repository.atomic(save)
