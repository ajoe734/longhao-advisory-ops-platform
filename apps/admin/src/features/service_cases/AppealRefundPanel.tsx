import React from "react";
import type { ResponseDraft, ServiceCase, ServiceResponse, ViewState } from "./types";
import { isAllowed } from "./viewState";

export interface AppealRefundModel {
  case: ServiceCase;
  draft: ResponseDraft | null;
  response: ServiceResponse | null;
}

export function AppealRefundPanel({
  state, onReload, onApproveDraft, onSendResponse,
}: {
  state: ViewState<AppealRefundModel>;
  onReload: () => void;
  onApproveDraft: () => void;
  onSendResponse: () => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入申訴與退款面板…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此資源不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const model = state.value;
  return <main className="m13-stack">
    {state.kind === "conflict" && <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>}
    {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
    <header className="m13-panel">
      <p className="m13-eyebrow">申訴與退款面板</p>
      <h1>{model.case.subject}</h1>
      <p>分類：{model.case.category} · 優先：{model.case.priority}</p>
    </header>
    <section aria-labelledby="refund-status" className="m13-panel">
      <h2 id="refund-status">退款工作狀態</h2>
      {model.case.linked_refund_id
        ? <p role="status">已連結 M14 退款請求：{model.case.linked_refund_id}（狀態由 M14 權威回報）</p>
        : <p role="status">尚未建立退款請求；客服僅可提出，不能自行核准或出金。</p>}
      <p role="alert" className="m13-warning">客服不可自行承諾未核定退款金額或投資收益；任何金額須待 M14／授權流程核准後才可視為確定。</p>
    </section>
    <section aria-labelledby="draft-status" className="m13-panel">
      <h2 id="draft-status">回覆草稿</h2>
      {!model.draft && <p role="status">尚未建立回覆草稿。</p>}
      {model.draft && (
        <div>
          <p role="status">草稿狀態：{model.draft.state}</p>
          {model.draft.contains_new_investment_judgment && (
            <p role="alert" className="m13-warning">此草稿包含新的投資判斷，不得核定發送；請轉交分析師並回正式報告流程。</p>
          )}
          <p>{model.draft.text ?? "（此狀態尚無可顯示內容）"}</p>
          <button
            disabled={!isAllowed(model.draft.allowed_actions, "approve_service_response_draft")}
            title={!isAllowed(model.draft.allowed_actions, "approve_service_response_draft") ? "目前狀態或資格不允許核定" : undefined}
            onClick={onApproveDraft}
          >
            核定草稿
          </button>
        </div>
      )}
    </section>
    <section aria-labelledby="response-status" className="m13-panel">
      <h2 id="response-status">客服回覆送達狀態</h2>
      {!model.response && <p role="status">尚未送出回覆。</p>}
      {model.response && (
        <p role="status">
          {model.response.state === "queued" && "已受理，尚未送出（queued）"}
          {model.response.state === "accepted" && "供應商已接受，尚未確認送達（accepted）"}
          {model.response.state === "sent" && "已送出，尚未確認送達（sent）"}
          {model.response.state === "delivered" && "已確認送達（delivered）"}
          {model.response.state === "unknown" && "送達結果未知，正在查詢外部狀態，不視為已送達"}
          {(model.response.state === "failed" || model.response.state === "cancelled" || model.response.state === "expired") && `未送達：${model.response.state}`}
        </p>
      )}
      <button
        disabled={!isAllowed(model.case.allowed_actions, "send_service_response")}
        title={!isAllowed(model.case.allowed_actions, "send_service_response") ? "目前狀態不允許送出回覆" : undefined}
        onClick={onSendResponse}
      >
        送出回覆
      </button>
    </section>
  </main>;
}
