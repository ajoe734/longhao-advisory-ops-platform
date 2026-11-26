import React from "react";
import type { CaseInteraction, ServiceCase, ViewState } from "./types";
import { isAllowed } from "./viewState";

export interface CaseDetailModel {
  case: ServiceCase;
  interactions: CaseInteraction[];
}

export function CaseDetail({
  state, onReload, onEscalate, onResolve, onDraftResponse,
}: {
  state: ViewState<CaseDetailModel>;
  onReload: () => void;
  onEscalate: () => void;
  onResolve: () => void;
  onDraftResponse: () => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入案件詳情…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此案件不存在或尚未完成身份驗證，無法顯示契約／付款細節。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const model = state.value;
  const unverified = model.case.customer_id === null;
  return <main className="m13-stack">
    {state.kind === "conflict" && <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>}
    {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
    <header className="m13-panel">
      <p className="m13-eyebrow">案件詳情</p>
      <h1>{model.case.subject}</h1>
      <p>{model.case.id} · {model.case.state} · {model.case.priority}</p>
      {unverified && <p role="alert" className="m13-warning">尚未完成客戶身份驗證：契約、付款與研究引用暫不顯示。</p>}
      <div className="m13-actions">
        <button disabled={!isAllowed(model.case.allowed_actions, "escalate_service_case")} title={!isAllowed(model.case.allowed_actions, "escalate_service_case") ? "目前角色或狀態不允許升級" : undefined} onClick={onEscalate}>升級案件</button>
        <button disabled={!isAllowed(model.case.allowed_actions, "draft_service_response")} title={!isAllowed(model.case.allowed_actions, "draft_service_response") ? "目前角色或狀態不允許起草回覆" : undefined} onClick={onDraftResponse}>起草回覆</button>
        <button disabled={!isAllowed(model.case.allowed_actions, "resolve_service_case")} title={!isAllowed(model.case.allowed_actions, "resolve_service_case") ? "目前角色或狀態不允許結案" : undefined} onClick={onResolve}>結案</button>
      </div>
    </header>
    <section aria-labelledby="contract-refs" className="m13-panel">
      <h2 id="contract-refs">關聯契約／研究／退款</h2>
      <p>契約：{model.case.linked_contract_id ?? "（未揭露或未連結）"}</p>
      <p>研究引用：{model.case.linked_publication_id ?? "（未揭露或未連結）"}</p>
      <p>退款：{model.case.linked_refund_id ?? "（尚未由 M14 確認）"}</p>
    </section>
    <section aria-labelledby="timeline" className="m13-panel">
      <h2 id="timeline">跨通路時間軸</h2>
      <ol className="m13-timeline">
        {model.interactions.map(interaction => (
          <li key={interaction.id} data-direction={interaction.direction}>
            <span role="status">{interaction.channel} · {interaction.direction} · {interaction.occurred_at}</span>
            <p>{interaction.direction === "internal" ? `內部筆記：${interaction.summary}` : interaction.summary}</p>
          </li>
        ))}
      </ol>
      {model.interactions.length === 0 && <p role="status">尚無互動紀錄。</p>}
    </section>
  </main>;
}
