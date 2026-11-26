from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DraftResult:
    text: str | None
    contains_new_investment_judgment: bool
    requires_human: bool


class DraftingAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str

    def draft(self, minimized_input: dict[str, Any]) -> DraftResult: ...


class LocalDraftingAssistant:
    """Deterministic local fallback. Never receives raw case description or
    AML-flagged content; it only sees category, intent, tone and approved
    knowledge references supplied by the service layer.
    """

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m13-response-draft-v1"

    def draft(self, minimized_input: dict[str, Any]) -> DraftResult:
        expected = {"category", "intent", "requested_tone", "knowledge_version", "approved_source_refs"}
        if set(minimized_input) != expected:
            raise ValueError("automation input only accepts the minimized drafting record")
        if minimized_input["category"] == "research_question":
            # New buy/sell or performance judgment must route to an analyst and
            # a formal M07/M08 publication, never an ad hoc drafted answer.
            return DraftResult(text=None, contains_new_investment_judgment=True, requires_human=True)
        intent = minimized_input["intent"]
        refs = minimized_input["approved_source_refs"]
        text = {
            "status": f"您的案件目前處理中，將依核准知識庫（版本 {minimized_input['knowledge_version']}）回覆最新狀態。",
            "procedure": "以下為核准作業流程說明，若需進一步協助請回覆本訊息。",
            "approved_report_explanation": f"以下說明依據已發布報告（引用 {len(refs)} 筆核准來源），不包含新的投資建議。",
            "billing_explanation": "以下為帳務與費用說明，詳細明細請參閱對應憑證。",
        }[intent]
        return DraftResult(text=text, contains_new_investment_judgment=False, requires_human=False)
