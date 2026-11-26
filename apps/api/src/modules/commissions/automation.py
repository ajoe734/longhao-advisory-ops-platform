"""Explain-only commission variance assistant.

This capability may summarize *already-computed* decimal results and point at
which rule_trace_codes produced them. It has no command authority: it cannot
create, approve or resize a policy, run, adjustment or payout, and it never
decides an amount, rate or beneficiary. Low-confidence or policy-unresolved
situations are surfaced as plain narrative lines for a human to route, not
silently resolved.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .domain import ActorContext, ForbiddenError, ValidationError, canonical_hash, new_id, utc_now


TRACE_EXPLANATIONS = {
    "age:year1": "客戶為合作第一年，行銷係數 1.00。",
    "age:year2": "客戶為合作第二年，行銷係數 0.50。",
    "age:year3_continuous_auto": "客戶進入第三年且持續自動續約，適用尾佣係數 0.25。",
    "age:year3_manual_restart_zero": "客戶進入第三年但曾人工重啟續約，尾佣係數為 0（不因重新打開自動續約即恢復資格）。",
    "age:year3_custom_fallback_auto": "政策的 year3_assisted_marketing_rule 尚待核定自訂規則；暫以持續自動續約尾佣係數模擬。",
    "age:year3_custom_fallback_manual": "政策的 year3_assisted_marketing_rule 尚待核定自訂規則；暫以人工重啟零係數模擬。",
    "split:organic_no_sales_share": "本筆為自助續約，業務分配為 0，其餘保留公司，不歸屬現任維護人。",
    "split:assisted_30_70": "本筆有業務介入核定證據，獎金池先依 30/70 分配行銷與業務。",
    "decay:whole_pool_decay": "行銷與業務兩部分皆依客戶年齡係數衰減（whole_pool_decay）。",
    "decay:marketing_decay_only": "僅行銷分配依客戶年齡係數衰減；業務分配維持介入當期全額（marketing_decay_only）。",
}


@dataclass(frozen=True)
class VarianceNote:
    recognition_id: str
    summary: str
    trace_codes: tuple[str, ...]


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
    notes: tuple[VarianceNote, ...]
    created_at: datetime
    status: str = "untrusted_draft"


class ExplainerPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def explain(self, lines: list[dict[str, Any]]) -> tuple[VarianceNote, ...]: ...


class LocalRuleExplainer:
    """Deterministic local fallback; narrates ``rule_trace_codes`` only, never amounts' meaning beyond what the trace says."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m12-commission-variance-v1"
    ruleset_version = "m12-trace-code-glossary-v1"

    def explain(self, lines: list[dict[str, Any]]) -> tuple[VarianceNote, ...]:
        notes: list[VarianceNote] = []
        for line in lines:
            codes = tuple(line.get("rule_trace_codes") or ())
            sentences = [TRACE_EXPLANATIONS.get(code, f"未知規則代碼：{code}") for code in codes]
            notes.append(VarianceNote(line["recognition_id"], " ".join(sentences) or "無規則追蹤代碼。", codes))
        return tuple(notes)


class CommissionExplainerService:
    """Records an untrusted, human-reviewable narrative draft; it has no merge/command authority."""

    def __init__(self, assistant: ExplainerPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M12 automation adapter is forbidden in production")
        self.assistant = assistant

    def explain_lines(self, context: ActorContext, lines: list[dict[str, Any]]) -> AutomationRun:
        if context.roles.isdisjoint({"commission_operator", "commission_approver"}):
            raise ForbiddenError("actor cannot request a commission variance explanation")
        if not isinstance(lines, list) or not lines:
            raise ValidationError("lines must be a non-empty array of computed CommissionLine objects", field_name="lines")
        for line in lines:
            if "recognition_id" not in line:
                raise ValidationError("each line requires recognition_id to be explainable", field_name="lines")
        notes = self.assistant.explain(lines)
        return AutomationRun(
            id=new_id(),
            tenant_id=context.tenant_id,
            capability="commission_variance_explanation",
            model_version=self.assistant.model_version,
            prompt_version=self.assistant.prompt_version,
            ruleset_version=self.assistant.ruleset_version,
            source_refs=tuple(sorted({line["recognition_id"] for line in lines})),
            input_sha256=canonical_hash(lines),
            notes=notes,
            created_at=utc_now(),
        )
