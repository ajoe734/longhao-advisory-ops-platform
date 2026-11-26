from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


@dataclass(frozen=True)
class CaseSummaryDraft:
    case_id: str
    restricted_evidence_count: int
    headline: str


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
    draft: CaseSummaryDraft
    created_at: datetime
    status: str = "untrusted_draft"


class CaseSummaryAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def summarize(self, case_id: str, restricted_evidence_ids: tuple[str, ...]) -> CaseSummaryDraft: ...


class LocalCaseSummaryAssistant:
    """Deterministic local fallback; consumes only keyed evidence asset refs, never raw case content."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m04-case-summary-v1"
    ruleset_version = "m04-restricted-evidence-count-v1"

    def summarize(self, case_id: str, restricted_evidence_ids: tuple[str, ...]) -> CaseSummaryDraft:
        return CaseSummaryDraft(
            case_id=case_id,
            restricted_evidence_count=len(restricted_evidence_ids),
            headline=f"{len(restricted_evidence_ids)} 筆受限證據待覆核；本摘要非核定。",
        )


class CaseSummaryAutomationService:
    """Produces untrusted work-summary drafts; it has no clear/decide authority.

    Service identities with a ``m04:case_summary`` scope may request a draft
    (the real operating mode for an approved LLM assistant), but there is no
    method anywhere in this class, or reachable from it, that mutates case
    state or business clearance. Summaries never carry raw PII or hit detail,
    only a count derived from already-keyed restricted evidence asset ids.
    """

    def __init__(self, repository: InMemoryRepository, assistant: CaseSummaryAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M04 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def summarize(self, context: ActorContext, case_id: str) -> AutomationRun:
        if not self._authorized(context):
            raise ForbiddenError("actor cannot request an AML case summary")
        case_id = require_uuid(case_id, "case_id")

        def build(state: StoreState) -> AutomationRun:
            case = state.cases.get((context.tenant_id, case_id))
            if not case:
                raise ValidationError("AML case was not found", code="aml_case_not_found")
            evidence_ids = tuple(sorted(case.restricted_evidence_ids))
            draft = self.assistant.summarize(case_id, evidence_ids)
            run = AutomationRun(
                id=new_id(), tenant_id=context.tenant_id, capability="aml_case_summary",
                model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
                ruleset_version=self.assistant.ruleset_version, source_refs=evidence_ids,
                input_sha256=canonical_hash(list(evidence_ids)), draft=draft, created_at=utc_now(),
            )
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(build)

    @staticmethod
    def _authorized(context: ActorContext) -> bool:
        if context.principal_type == "staff" and bool(context.roles & {"aml_officer", "aml_supervisor"}):
            return True
        if context.principal_type == "service" and "m04:case_summary" in context.scopes:
            return True
        return False
