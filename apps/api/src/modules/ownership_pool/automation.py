"""M11 LLM handoff assistant: summary and contact-point suggestions only.

docs/modules/M11-ownership_pool.md §11 — LLM 可以整理接手摘要與建議聯絡重點，但不能
決定 owner、製造活動證據或覆寫歷史獎金；assignment 唯一性、離職時點、服務中斷時計與
claim 競爭一律留在 ``OwnershipPoolService`` 的交易與核定政策。這個 service 完全不
import ``OwnershipPoolService``，也沒有拿到任何 ``StoreState.assignments`` /
``pool_entries`` / ``departures`` / ``policies`` 的寫入權——它只透過同一個
``InMemoryRepository`` 寫自己的 ``automation_runs`` / ``automation_reviews``
collections，物理上無法指派 owner、無法製造活動證據，也碰不到任何 commission 紀錄。

Mirrors ``modules/customer_identity_application/automation.py``'s shape: an
untrusted draft output, a deterministic local fallback (no live LLM provider is
wired in this repository), minimized input only (never raw PII/transcripts —
callers pass structured signal records, not free text), a production fake
guard, and a mandatory human ``adopted``/``modified``/``rejected`` review record
before any suggestion is ever acted on by a human outside this service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, require_uuid, utc_now
from .repository import InMemoryRepository, StoreState


CAPABILITY = "handoff_summary_and_contact_points"
_SIGNAL_TYPES = {"contact_attempt", "meeting", "service_question", "complaint", "renewal_interest"}
_STAFF_ROLES = {"sales", "sales_supervisor"}


@dataclass(frozen=True)
class HandoffSuggestion:
    summary: str
    contact_points: tuple[str, ...]


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
    suggestion: HandoffSuggestion
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
    adopted_summary: str | None
    reviewed_at: datetime


class HandoffAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def summarize(self, predecessor: dict[str, Any], signals: list[dict[str, Any]]) -> HandoffSuggestion: ...


class LocalHandoffAssistant:
    """Deterministic local fallback; no live LLM provider is wired in this repo."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m11-handoff-summary-v1"
    ruleset_version = "m11-contact-point-frequency-v1"

    def summarize(self, predecessor: dict[str, Any], signals: list[dict[str, Any]]) -> HandoffSuggestion:
        reason_code = predecessor["reason_code"]
        valid_from = predecessor["valid_from"]
        valid_until = predecessor.get("valid_until")
        span = f"{valid_from} 起，至 {valid_until}" if valid_until else f"{valid_from} 起，進行中"
        summary = f"前任維護因「{reason_code}」結束（{span}）；已知 {len(signals)} 筆聯絡/活動訊號。"

        counts: dict[str, int] = {}
        latest: dict[str, str] = {}
        for signal in signals:
            kind = signal["signal_type"]
            counts[kind] = counts.get(kind, 0) + 1
            if kind not in latest or signal["occurred_at"] > latest[kind]:
                latest[kind] = signal["occurred_at"]
        ordered = sorted(counts, key=lambda kind: (-counts[kind], kind))
        contact_points = tuple(f"{kind}：{counts[kind]} 次，最近一次 {latest[kind]}" for kind in ordered)
        return HandoffSuggestion(summary=summary, contact_points=contact_points)


class HandoffAutomationService:
    """Records untrusted handoff-summary drafts; it has no ownership/activity/commission write authority."""

    def __init__(self, repository: InMemoryRepository, assistant: HandoffAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M11 handoff assistant adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def summarize(self, context: ActorContext, predecessor: dict[str, Any], signals: list[dict[str, Any]]) -> AutomationRun:
        if context.principal_type != "staff" or not context.roles & _STAFF_ROLES:
            raise ForbiddenError("actor cannot request a handoff summary")
        predecessor_clean = self._clean_predecessor(predecessor)
        cleaned_signals, source_refs = self._clean_signals(signals)
        suggestion = self.assistant.summarize(predecessor_clean, cleaned_signals)
        run = AutomationRun(
            id=new_id(), tenant_id=context.tenant_id, capability=CAPABILITY,
            model_version=self.assistant.model_version, prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version, source_refs=tuple(sorted(source_refs)),
            input_sha256=canonical_hash({"predecessor": predecessor_clean, "signals": cleaned_signals}),
            suggestion=suggestion, created_at=utc_now(),
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
        adopted_summary: str | None = None,
    ) -> AutomationReview:
        if context.principal_type != "staff" or not context.roles & _STAFF_ROLES:
            raise ForbiddenError("actor cannot review a handoff summary")
        run_id = require_uuid(run_id, "run_id")
        if action not in {"adopted", "modified", "rejected"}:
            raise ValidationError("automation review action is not supported", field_name="action")
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        if action == "modified":
            if not isinstance(adopted_summary, str) or not 1 <= len(adopted_summary) <= 4000:
                raise ValidationError("a modified review requires adopted_summary", field_name="adopted_summary")
        elif adopted_summary is not None:
            raise ValidationError("adopted_summary is only accepted for a modified review", field_name="adopted_summary")
        review = AutomationReview(
            id=new_id(), tenant_id=context.tenant_id, run_id=run_id, reviewer_id=context.actor_id,
            action=action, reason_code=reason_code, adopted_summary=adopted_summary, reviewed_at=utc_now(),
        )

        def save(state: StoreState) -> AutomationReview:
            if (context.tenant_id, run_id) not in state.automation_runs:
                raise ValidationError("automation run was not found", code="automation_run_not_found")
            state.automation_reviews[(context.tenant_id, review.id)] = review
            return review

        return self.repository.atomic(save)

    @staticmethod
    def _clean_predecessor(predecessor: dict[str, Any]) -> dict[str, Any]:
        if set(predecessor) != {"reason_code", "valid_from", "valid_until"}:
            raise ValidationError("predecessor summary only accepts minimized fields", code="automation_input_not_minimized")
        reason_code = predecessor["reason_code"]
        if not isinstance(reason_code, str) or not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        valid_from = predecessor["valid_from"]
        if not isinstance(valid_from, str):
            raise ValidationError("valid_from must be an RFC3339 timestamp", field_name="valid_from")
        valid_until = predecessor["valid_until"]
        if valid_until is not None and not isinstance(valid_until, str):
            raise ValidationError("valid_until must be an RFC3339 timestamp", field_name="valid_until")
        return {"reason_code": reason_code, "valid_from": valid_from, "valid_until": valid_until}

    @staticmethod
    def _clean_signals(signals: Any) -> tuple[list[dict[str, Any]], set[str]]:
        if not isinstance(signals, list) or len(signals) > 200:
            raise ValidationError("signals must contain at most 200 items", field_name="signals")
        cleaned: list[dict[str, Any]] = []
        source_refs: set[str] = set()
        for signal in signals:
            if not isinstance(signal, dict) or set(signal) != {"signal_type", "occurred_at", "evidence_asset_id"}:
                raise ValidationError("automation input only accepts minimized signal records", code="automation_input_not_minimized")
            signal_type = signal["signal_type"]
            if signal_type not in _SIGNAL_TYPES:
                raise ValidationError("signal_type is not supported", field_name="signal_type")
            occurred_at = signal["occurred_at"]
            if not isinstance(occurred_at, str):
                raise ValidationError("occurred_at must be an RFC3339 timestamp", field_name="occurred_at")
            evidence_asset_id = require_uuid(signal["evidence_asset_id"], "evidence_asset_id")
            source_refs.add(evidence_asset_id)
            cleaned.append({"signal_type": signal_type, "occurred_at": occurred_at, "evidence_asset_id": evidence_asset_id})
        return cleaned, source_refs
