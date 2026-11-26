from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


@dataclass(frozen=True)
class ContactDraft:
    work_item_id: str
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
    drafts: tuple[ContactDraft, ...]
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


class ContactDraftAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def draft(self, records: list[dict[str, Any]]) -> tuple[ContactDraft, ...]: ...


class LocalContactDraftAssistant:
    """Deterministic local fallback; consumes minimized worklist facts only, never raw PII or free text."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m10-contact-draft-v1"
    ruleset_version = "m10-worklist-triage-v1"

    _HEADLINES = {
        "renewal_due": "服務即將到期，建議提前聯絡確認續約意願",
        "kyc_refresh_required": "KYC資料需更新，建議協助客戶補件",
        "service_restricted": "服務目前受限，建議先處理服務問題再談續約/升級",
        "awaiting_company_decision": "申請尚待公司審核，建議先確認補件是否齊全",
    }

    def draft(self, records: list[dict[str, Any]]) -> tuple[ContactDraft, ...]:
        drafts: list[ContactDraft] = []
        for record in records:
            reason = record["reason_code"]
            headline = self._HEADLINES.get(reason, f"未分類待辦原因：{reason}")
            next_step = "需業務人工確認聯絡範圍後再撥打" if record["suppression_applied"] else "可依核准聯絡範圍主動聯繫"
            drafts.append(ContactDraft(record["work_item_id"], headline, next_step))
        return tuple(drafts)


class CrmAutomationService:
    """Drafts untrusted daily-worklist contact summaries for human triage.

    This capability has no authority to assign owners, change worklist
    eligibility, suppress/unsuppress a customer, or approve any conversion or
    attribution; it only ever fills a pre-approved template's headline and
    suggested-step fields for a human to adopt, modify or reject.
    """

    def __init__(self, repository: InMemoryRepository, assistant: ContactDraftAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and any(getattr(adapter, "is_fake", False) for adapter in (repository, assistant)):
            raise RuntimeError("fake M10 adapters are forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def draft_daily_contacts(self, context: ActorContext, records: list[dict[str, Any]]) -> AutomationRun:
        if context.principal_type != "staff" or not context.roles & {"sales", "sales_supervisor"}:
            raise ForbiddenError("actor cannot request contact drafts")
        cleaned: list[dict[str, Any]] = []
        source_refs: set[str] = set()
        for record in records:
            if set(record) != {"work_item_id", "reason_code", "suppression_applied"}:
                raise ValidationError("automation input only accepts minimized worklist records", code="automation_input_not_minimized")
            work_item_id = require_uuid(record["work_item_id"], "work_item_id")
            if not isinstance(record["suppression_applied"], bool):
                raise ValidationError("suppression_applied must be boolean", field_name="suppression_applied")
            if not isinstance(record["reason_code"], str) or not record["reason_code"]:
                raise ValidationError("reason_code must be a non-empty string", field_name="reason_code")
            source_refs.add(work_item_id)
            cleaned.append({"work_item_id": work_item_id, "reason_code": record["reason_code"], "suppression_applied": record["suppression_applied"]})
        drafts = self.assistant.draft(cleaned)
        run = AutomationRun(
            id=new_id(), tenant_id=context.tenant_id, capability="daily_contact_draft",
            model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version, source_refs=tuple(sorted(source_refs)),
            input_sha256=canonical_hash(cleaned), drafts=drafts, created_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationRun:
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)

    def record_review(self, context: ActorContext, run_id: str, *, action: str, reason_code: str) -> AutomationReview:
        if context.principal_type != "staff" or not context.roles & {"sales", "sales_supervisor"}:
            raise ForbiddenError("actor cannot review contact drafts")
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
