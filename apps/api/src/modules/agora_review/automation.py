from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


_PRIVILEGED_CAPABILITIES = {"sign", "publish", "payment", "entitlement", "aaml_clear", "aml_clear", "disbursement"}


def assert_no_privileged_capability(capability: str) -> None:
    """LLM/automation has none of these capabilities; see SA section 11."""

    if capability in _PRIVILEGED_CAPABILITIES:
        raise ForbiddenError(
            "automation and LLM drafting cannot sign, publish or move money", code="automation_privileged_capability_denied"
        )


@dataclass(frozen=True)
class DraftSuggestion:
    id: str
    tenant_id: str
    case_id: str
    capability: str
    model_version: str
    prompt_version: str
    ruleset_version: str
    source_refs: tuple[str, ...]
    input_sha256: str
    draft_text: str
    created_at: datetime
    status: str = "untrusted_draft"


@dataclass(frozen=True)
class DraftReview:
    id: str
    tenant_id: str
    draft_id: str
    reviewer_id: str
    action: str
    reason_code: str
    adopted_text: str | None
    reviewed_at: datetime


class DraftAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def draft_revision_instructions(self, diff_points: tuple[str, ...]) -> str: ...


class LocalDraftAssistant:
    """Deterministic local fallback; never calls a real LLM or touches raw PII/KYC content."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m06-revision-draft-v1"
    ruleset_version = "m06-diff-summary-v1"

    def draft_revision_instructions(self, diff_points: tuple[str, ...]) -> str:
        if not diff_points:
            return "請重新核對候選內容與既有結論是否一致，並補充對應證據。"
        bullets = "\n".join(f"- {point}" for point in sorted(set(diff_points)))
        return f"請針對以下差異點補充或修正，並附上可驗證來源：\n{bullets}"


class AgoraDraftAutomationService:
    """Records untrusted draft suggestions; it has no review/sign/publish authority."""

    def __init__(self, repository: InMemoryRepository, assistant: DraftAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M06 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def draft_revision(self, context, case_id: str, diff_points: list[str], source_refs: list[str]):
        if context.principal_type != "staff" or not context.roles & {"analyst", "analyst_reviewer"}:
            raise ForbiddenError("actor cannot request a revision draft")
        case_id = require_uuid(case_id, "case_id")
        if not isinstance(diff_points, list) or any(not isinstance(point, str) or len(point) > 400 for point in diff_points):
            raise ValidationError("diff_points must be an array of short strings", field_name="diff_points")
        source_refs = [require_uuid(value, "source_refs") for value in source_refs]
        draft_text = self.assistant.draft_revision_instructions(tuple(diff_points))
        suggestion = DraftSuggestion(
            id=new_id(),
            tenant_id=context.tenant_id,
            case_id=case_id,
            capability="revision_instruction_draft",
            model_version=self.assistant.model_version,
            prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version,
            source_refs=tuple(sorted(set(source_refs))),
            input_sha256=canonical_hash({"case_id": case_id, "diff_points": sorted(set(diff_points))}),
            draft_text=draft_text,
            created_at=utc_now(),
        )

        def save(state: StoreState):
            state.automation_runs[(context.tenant_id, suggestion.id)] = suggestion
            return suggestion

        return self.repository.atomic(save)

    def record_review(self, context, draft_id: str, *, action: str, reason_code: str, adopted_text: str | None = None):
        if context.principal_type != "staff" or not context.roles & {"analyst", "analyst_reviewer"}:
            raise ForbiddenError("actor cannot review a revision draft")
        draft_id = require_uuid(draft_id, "draft_id")
        if action not in {"adopted", "modified", "rejected"}:
            raise ValidationError("draft review action is not supported", field_name="action")
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        if action in {"adopted", "modified"} and not adopted_text:
            raise ValidationError("adopted or modified review requires adopted_text", field_name="adopted_text")
        review = DraftReview(
            id=new_id(), tenant_id=context.tenant_id, draft_id=draft_id, reviewer_id=context.actor_id,
            action=action, reason_code=reason_code, adopted_text=adopted_text, reviewed_at=utc_now(),
        )

        def save(state: StoreState):
            if (context.tenant_id, draft_id) not in state.automation_runs:
                raise ValidationError("draft run was not found", code="automation_run_not_found")
            state.automation_reviews[(context.tenant_id, review.id)] = review
            return review

        return self.repository.atomic(save)
