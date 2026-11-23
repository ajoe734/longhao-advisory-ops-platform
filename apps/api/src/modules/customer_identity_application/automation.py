from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


_HMAC = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class DuplicateSuggestion:
    source_customer_id: str
    target_customer_id: str
    shared_fingerprint_count: int
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
    suggestions: tuple[DuplicateSuggestion, ...]
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
    selected_customer_ids: tuple[str, ...]
    reviewed_at: datetime


class DuplicateAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def suggest(self, records: list[dict[str, Any]]) -> tuple[DuplicateSuggestion, ...]: ...


class LocalDuplicateAssistant:
    """Deterministic local fallback; consumes keyed fingerprints, never PII."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m01-duplicate-candidate-v1"
    ruleset_version = "m01-contact-intersection-v1"

    def suggest(self, records: list[dict[str, Any]]) -> tuple[DuplicateSuggestion, ...]:
        suggestions: list[DuplicateSuggestion] = []
        for index, left in enumerate(records):
            for right in records[index + 1:]:
                shared = set(left["contact_fingerprints"]) & set(right["contact_fingerprints"])
                if shared:
                    suggestions.append(DuplicateSuggestion(
                        left["customer_id"], right["customer_id"], len(shared),
                        tuple(sorted(set(left["evidence_refs"]) | set(right["evidence_refs"]))),
                    ))
        return tuple(suggestions)


class DuplicateAutomationService:
    """Records untrusted candidate drafts; it has no merge/command authority."""

    def __init__(
        self,
        repository: InMemoryRepository,
        assistant: DuplicateAssistantPort,
        *,
        environment: str = "test",
    ) -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M01 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def suggest(self, context: ActorContext, records: list[dict[str, Any]]) -> AutomationRun:
        if context.principal_type != "staff" or not context.roles & {"cms_operator", "cms_supervisor"}:
            raise ForbiddenError("actor cannot request duplicate suggestions")
        cleaned: list[dict[str, Any]] = []
        source_refs: set[str] = set()
        for record in records:
            if set(record) != {"customer_id", "contact_fingerprints", "evidence_refs"}:
                raise ValidationError("automation input only accepts minimized fingerprint records", code="automation_input_not_minimized")
            customer_id = require_uuid(record["customer_id"], "customer_id")
            fingerprints = record["contact_fingerprints"]
            evidence_refs = record["evidence_refs"]
            if not isinstance(fingerprints, list) or not fingerprints or any(not _HMAC.fullmatch(str(value)) for value in fingerprints):
                raise ValidationError("contact fingerprints must be keyed lowercase SHA-256 values", field_name="contact_fingerprints")
            if not isinstance(evidence_refs, list):
                raise ValidationError("evidence_refs must be an array", field_name="evidence_refs")
            evidence_refs = [require_uuid(value, "evidence_refs") for value in evidence_refs]
            source_refs.update(evidence_refs)
            cleaned.append({"customer_id": customer_id, "contact_fingerprints": sorted(set(fingerprints)), "evidence_refs": evidence_refs})
        suggestions = self.assistant.suggest(cleaned)
        run = AutomationRun(
            id=new_id(), tenant_id=context.tenant_id, capability="duplicate_customer_candidate",
            model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version, source_refs=tuple(sorted(source_refs)),
            input_sha256=canonical_hash(cleaned), suggestions=suggestions, created_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationRun:
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)

    def record_review(
        self,
        context: ActorContext,
        run_id: str,
        *,
        action: str,
        reason_code: str,
        selected_customer_ids: tuple[str, ...] = (),
    ) -> AutomationReview:
        if context.principal_type != "staff" or not context.roles & {"cms_operator", "cms_supervisor"}:
            raise ForbiddenError("actor cannot review duplicate suggestions")
        run_id = require_uuid(run_id, "run_id")
        if action not in {"adopted", "modified", "rejected"}:
            raise ValidationError("automation review action is not supported", field_name="action")
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        selected = tuple(require_uuid(value, "selected_customer_ids") for value in selected_customer_ids)
        if action in {"adopted", "modified"} and len(selected) != 2:
            raise ValidationError("adopted or modified review must identify two customers", field_name="selected_customer_ids")
        review = AutomationReview(
            id=new_id(), tenant_id=context.tenant_id, run_id=run_id, reviewer_id=context.actor_id,
            action=action, reason_code=reason_code, selected_customer_ids=selected, reviewed_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationReview:
            if (context.tenant_id, run_id) not in state.automation_runs:
                raise ValidationError("automation run was not found", code="automation_run_not_found")
            state.automation_reviews[(context.tenant_id, review.id)] = review
            return review

        return self.repository.atomic(save)
