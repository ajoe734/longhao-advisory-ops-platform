from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, utc_now
from .repository import InMemoryRepository, StoreState


@dataclass(frozen=True)
class ChecklistFinding:
    channel: str
    issue_code: str
    detail: str


@dataclass(frozen=True)
class DraftAssistRun:
    id: str
    tenant_id: str
    capability: str
    model_version: str
    prompt_version: str
    ruleset_version: str
    source_refs: tuple[str, ...]
    input_sha256: str
    findings: tuple[ChecklistFinding, ...]
    created_at: datetime
    status: str = "untrusted_draft"


class DraftAssistPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def check(self, report_content_sha256: str, channel_texts: list[dict[str, Any]]) -> tuple[ChecklistFinding, ...]: ...


class LocalDraftAssistant:
    """Deterministic local fallback; flags inconsistency, never approves anything."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m07-channel-consistency-v1"
    ruleset_version = "m07-numeric-mismatch-v1"

    def check(self, report_content_sha256: str, channel_texts: list[dict[str, Any]]) -> tuple[ChecklistFinding, ...]:
        findings: list[ChecklistFinding] = []
        lengths = {item["channel"]: len(item["body"]) for item in channel_texts}
        if lengths and max(lengths.values()) > 20 * max(1, min(lengths.values())):
            longest = max(lengths, key=lengths.get)
            findings.append(ChecklistFinding(longest, "channel_length_outlier", "通路文案長度與其他通路差異過大，建議人工覆核。"))
        return tuple(findings)


class ReportDraftAssistService:
    """Produces untrusted draft-checklist findings; it has no review or signing authority."""

    def __init__(self, repository: InMemoryRepository, assistant: DraftAssistPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M07 automation adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def suggest(self, context: ActorContext, report_content_sha256: str, channel_texts: list[dict[str, Any]], source_refs: list[str]) -> DraftAssistRun:
        if context.principal_type != "staff" or not context.roles & {"analyst", "analyst_reviewer", "compliance_reviewer"}:
            raise ForbiddenError("actor cannot request draft-assist checks")
        if not isinstance(channel_texts, list) or not channel_texts:
            raise ValidationError("channel_texts must be a non-empty array", field_name="channel_texts")
        findings = self.assistant.check(report_content_sha256, channel_texts)
        run = DraftAssistRun(
            id=new_id(), tenant_id=context.tenant_id, capability="report_channel_consistency_check",
            model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version, source_refs=tuple(sorted(set(source_refs))),
            input_sha256=canonical_hash({"report_content_sha256": report_content_sha256, "channel_texts": channel_texts}),
            findings=findings, created_at=utc_now(),
        )

        def save(state: StoreState) -> DraftAssistRun:
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)
