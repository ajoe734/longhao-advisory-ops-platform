import React from "react";
import type { Customer360 as Customer360Model, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function Customer360({ state, onReload, onEdit }: { state: ViewState<Customer360Model>; onReload: () => void; onEdit: () => void }) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入客戶 360…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此資源不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const model = state.value;
  return <main className="m01-stack">
    {state.kind === "conflict" && <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>}
    {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
    <header className="m01-panel"><p className="m01-eyebrow">Customer 360</p><h1>{model.customer.display_name}</h1><p>{model.customer.id} · {model.customer.status}</p>
      <button disabled={!isAllowed(model.customer.allowed_actions, "update_customer")} title={!isAllowed(model.customer.allowed_actions, "update_customer") ? "目前角色或狀態不允許更正" : undefined} onClick={onEdit}>更正白名單資料</button>
    </header>
    <section aria-labelledby="source-summary" className="m01-panel"><h2 id="source-summary">服務與案件摘要</h2><div className="m01-source-grid">
      {model.sources.map(source => <article key={source.source} aria-label={source.source}><h3>{source.source}</h3><p>來源狀態：{source.source_status}</p><p>上次成功：{source.last_success_at ? new Date(source.last_success_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" }) : "尚無"}</p>
        {source.source_status === "available" ? <p>{source.items?.length ?? 0} 筆</p> : <p role="alert">來源暫時失聯；不可解讀為零筆資料。{source.error_code && `（${source.error_code}）`}</p>}</article>)}
    </div></section>
  </main>;
}
