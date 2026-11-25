import React from "react";
import type { ReviewCaseWorkspaceModel, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function ReviewCaseWorkspace({ state, onOpenConversation, onRequestRevision, onReload }: {
  state: ViewState<ReviewCaseWorkspaceModel>;
  onOpenConversation: () => void;
  onRequestRevision: () => void;
  onReload: () => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">案件工作台載入中…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此案件不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const model = state.value;
  const reviewCase = model.case;
  const canOpenConversation = isAllowed(reviewCase.allowed_actions, "open_servant_conversation");
  const canRequestRevision = isAllowed(reviewCase.allowed_actions, "request_servant_revision");
  return <main className="m06-stack">
    {state.kind === "conflict" && <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>}
    {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
    {reviewCase.source_version_stale && <aside role="alert" className="m06-stale-banner"><strong>來源已有新版本</strong>
      <p>此案件基於的研究候選已被取代；目前審查結論標示為過時，不會自動沿用為新版本的核准。</p></aside>}
    <header className="m06-panel"><p className="m06-eyebrow">研究覆核工作台</p><h1>案件 {reviewCase.id}</h1>
      <p role="status">目前階段：{reviewCase.state}</p>
      <dl><dt>候選</dt><dd>{reviewCase.candidate_id}</dd><dt>指派分析師</dt><dd>{reviewCase.assignee_id ?? "未指派"}</dd>
        <dt>複核人員</dt><dd>{reviewCase.professional_reviewer_id ?? "未指派"}</dd>
        <dt>法遵複核</dt><dd>{reviewCase.compliance_review_required ? "需要" : "不需要"}</dd></dl>
      <button disabled={!canOpenConversation} title={!canOpenConversation ? "目前角色或狀態不允許開啟對話" : undefined} onClick={onOpenConversation}>開啟 Servant 對話</button>
      <button disabled={!canRequestRevision} title={!canRequestRevision ? "目前角色或狀態不允許送出修訂" : undefined} onClick={onRequestRevision}>送出修訂請求</button>
    </header>
    <section aria-labelledby="servant-health-title" className="m06-panel">
      <h2 id="servant-health-title">Agora／Servant 連線狀態</h2>
      {model.servant_health.status === "available"
        ? <p>連線正常。</p>
        : <p role="alert">Agora／Servant 目前無法連線；以下仍為最後已知且真實的案件與修訂資料，不代表無修訂或已完成，不可解讀為零筆資料。
            {model.servant_health.last_success_at && ` 上次成功：${new Date(model.servant_health.last_success_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}`}</p>}
    </section>
    <section aria-labelledby="conversations-title" className="m06-panel">
      <h2 id="conversations-title">Servant 對話</h2>
      {model.conversations.length === 0 ? <p>尚無對話。</p> : <ul>{model.conversations.map(item =>
        <li key={item.id}>{item.external_conversation_ref.external_id} · {item.status}</li>)}</ul>}
    </section>
    <section aria-labelledby="revisions-title" className="m06-panel">
      <h2 id="revisions-title">修訂請求</h2>
      {model.revisions.length === 0 ? <p>尚無修訂請求。</p> : <ul>{model.revisions.map(item =>
        <li key={item.id} aria-current={reviewCase.pending_revision_ids.includes(item.id) ? "true" : undefined}>
          {item.id} · {item.state}{item.state === "failed" && <span role="alert"> · 失敗，保留證據並可重新送出</span>}
        </li>)}</ul>}
    </section>
  </main>;
}
