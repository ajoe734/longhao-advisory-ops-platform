from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


@dataclass(frozen=True)
class TriageSuggestion:
    candidate_id: str
    likely_missing_evidence: bool
    summary: str


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
    suggestions: tuple[TriageSuggestion, ...]
    created_at: datetime
    status: str = "untrusted_draft"


class TriageAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def triage(self, records: list[dict[str, Any]]) -> tuple[TriageSuggestion, ...]: ...


class LocalTriageAssistant:
    """Deterministic local fallback; flags only already-known missing-evidence facts."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m05-candidate-triage-v1"
    ruleset_version = "m05-evidence-completeness-v1"

    def triage(self, records: list[dict[str, Any]]) -> tuple[TriageSuggestion, ...]:
        return tuple(
            TriageSuggestion(
                candidate_id=record["candidate_id"],
                likely_missing_evidence="evidence_incomplete" in record["validation_failure_codes"],
                summary="missing_evidence" if "evidence_incomplete" in record["validation_failure_codes"] else "no_flag",
            )
            for record in records
        )


class CandidateTriageAutomationService:
    """Records untrusted LLM/local triage drafts; it has no validate/reject authority."""

    def __init__(self, repository: InMemoryRepository, assistant: TriageAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M05 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def suggest(self, context: ActorContext, records: list[dict[str, Any]]) -> AutomationRun:
        if context.principal_type != "staff" or not context.roles & {"analyst", "analyst_reviewer"}:
            raise ForbiddenError("actor cannot request candidate triage suggestions")
        cleaned: list[dict[str, Any]] = []
        source_refs: set[str] = set()
        for record in records:
            if set(record) != {"candidate_id", "validation_failure_codes", "evidence_refs"}:
                raise ValidationError("automation input only accepts minimized candidate records", code="automation_input_not_minimized")
            candidate_id = require_uuid(record["candidate_id"], "candidate_id")
            codes = record["validation_failure_codes"]
            if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
                raise ValidationError("validation_failure_codes must be an array of strings", field_name="validation_failure_codes")
            refs = record["evidence_refs"]
            if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                raise ValidationError("evidence_refs must be an array of strings", field_name="evidence_refs")
            source_refs.update(refs)
            cleaned.append({"candidate_id": candidate_id, "validation_failure_codes": sorted(set(codes)), "evidence_refs": sorted(set(refs))})
        suggestions = self.assistant.triage(cleaned)
        run = AutomationRun(
            id=new_id(), tenant_id=context.tenant_id, capability="candidate_triage_summary",
            model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version, source_refs=tuple(sorted(source_refs)),
            input_sha256=canonical_hash(cleaned), suggestions=suggestions, created_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationRun:
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)
