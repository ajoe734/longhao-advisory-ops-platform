from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from .domain import ActorContext, FINANCE_CHECKER, FINANCE_OPERATOR, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


_DIFFERENCE_TYPES = {"unmatched_payment", "unmatched_settlement", "amount_mismatch", "duplicate", "fee_mismatch", "timing"}
_DECISION_HINTS = {
    "unmatched_payment": "locate the originating settlement or escalate to the provider",
    "unmatched_settlement": "locate the originating payment or escalate",
    "amount_mismatch": "confirm the correct provider amount before matching",
    "duplicate": "reject the duplicate line or re-match it to the correct payment",
    "fee_mismatch": "correct the fee mapping before accepting the difference",
    "timing": "accept the timing difference if the settlement date explains the gap",
}


@dataclass(frozen=True)
class NarrationSuggestion:
    item_id: str
    narrative: str
    evidence_refs: tuple[str, ...]


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
    suggestions: tuple[NarrationSuggestion, ...]
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


class NarrationAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def suggest(self, records: list[dict[str, Any]]) -> tuple[NarrationSuggestion, ...]: ...


class LocalReconciliationAssistant:
    """Deterministic local fallback; drafts explanations strictly from given
    authoritative fields. It never invents amounts, provider names or
    customer data, and the draft is never trusted as an approved resolution.
    """

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m14-reconciliation-narration-v1"
    ruleset_version = "m14-difference-explanation-v1"

    def suggest(self, records: list[dict[str, Any]]) -> tuple[NarrationSuggestion, ...]:
        suggestions: list[NarrationSuggestion] = []
        for record in records:
            difference_type = record["difference_type"]
            expected = Decimal(record["expected_amount"])
            actual = Decimal(record["actual_amount"])
            gap = actual - expected
            hint = _DECISION_HINTS[difference_type]
            narrative = (
                f"{difference_type} of {gap} {record['currency']} "
                f"(expected {expected}, actual {actual}); {hint}."
            )
            suggestions.append(NarrationSuggestion(record["item_id"], narrative, tuple(record["evidence_refs"])))
        return tuple(suggestions)


class ReconciliationNarrationService:
    """Produces untrusted draft explanations for finance staff; it has no
    authority to resolve, match or write off a reconciliation item.
    """

    def __init__(self, repository: InMemoryRepository, assistant: NarrationAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M14 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def suggest(self, context: ActorContext, records: list[dict[str, Any]]) -> AutomationRun:
        if context.principal_type != "staff" or context.roles.isdisjoint({FINANCE_OPERATOR, FINANCE_CHECKER}):
            raise ForbiddenError("actor cannot request reconciliation narration suggestions")
        cleaned: list[dict[str, Any]] = []
        source_refs: set[str] = set()
        for record in records:
            if set(record) != {"item_id", "difference_type", "expected_amount", "actual_amount", "currency", "evidence_refs"}:
                raise ValidationError("automation input only accepts minimized reconciliation fields", code="automation_input_not_minimized")
            item_id = require_uuid(record["item_id"], "item_id")
            if record["difference_type"] not in _DIFFERENCE_TYPES:
                raise ValidationError("difference_type is not supported", field_name="difference_type")
            evidence_refs = record["evidence_refs"]
            if not isinstance(evidence_refs, list):
                raise ValidationError("evidence_refs must be an array", field_name="evidence_refs")
            evidence_refs = [require_uuid(value, "evidence_refs") for value in evidence_refs]
            source_refs.update(evidence_refs)
            cleaned.append(
                {
                    "item_id": item_id,
                    "difference_type": record["difference_type"],
                    "expected_amount": str(record["expected_amount"]),
                    "actual_amount": str(record["actual_amount"]),
                    "currency": record["currency"],
                    "evidence_refs": evidence_refs,
                }
            )
        suggestions = self.assistant.suggest(cleaned)
        run = AutomationRun(
            id=new_id(),
            tenant_id=context.tenant_id,
            capability="reconciliation_difference_narration",
            model_version=self.assistant.model_version,
            prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version,
            source_refs=tuple(sorted(source_refs)),
            input_sha256=canonical_hash(cleaned),
            suggestions=suggestions,
            created_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationRun:
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)

    def record_review(self, context: ActorContext, run_id: str, *, action: str, reason_code: str) -> AutomationReview:
        if context.principal_type != "staff" or context.roles.isdisjoint({FINANCE_OPERATOR, FINANCE_CHECKER}):
            raise ForbiddenError("actor cannot review reconciliation narration suggestions")
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
