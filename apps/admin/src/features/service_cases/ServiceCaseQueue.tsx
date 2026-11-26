import React from "react";
import type { Page, ServiceCase, ViewState } from "./types";

function maskedCustomer(customerId: string | null): string {
  if (!customerId) return "未驗證／未連結客戶";
  return `客戶 …${customerId.slice(-6)}`;
}

function dispatchHint(state: ServiceCase["state"]): string {
  if (state === "resolved") return "已結案待通知確認";
  if (state === "closed") return "已關閉";
  if (state === "waiting_internal") return "等待內部處理（不暫停SLA）";
  if (state === "waiting_customer") return "等待客戶回覆";
  return "待受理";
}

export function ServiceCaseQueue({
  state, onReload, onOpenCase,
}: {
  state: ViewState<Page<ServiceCase>>;
  onReload: () => void;
  onOpenCase: (caseId: string) => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入客服工作台…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此佇列不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const page = state.value;
  return <main className="m13-stack">
    {state.kind === "conflict" && <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>}
    {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
    <header className="m13-panel"><p className="m13-eyebrow">客服工作台</p><h1>統一案件佇列</h1><p>依 SLA 優先順序排序；{page.total_estimate ?? page.items.length} 筆（{page.as_of} 快照）</p></header>
    <table className="m13-table">
      <thead>
        <tr><th>案件</th><th>通路</th><th>客戶</th><th>分類</th><th>優先</th><th>SLA</th><th>承辦人</th><th>下一步</th></tr>
      </thead>
      <tbody>
        {page.items.map(item => (
          <tr key={item.id}>
            <td><button onClick={() => onOpenCase(item.id)}>{item.subject}</button></td>
            <td>{item.intake_channel}</td>
            <td>{maskedCustomer(item.customer_id)}</td>
            <td>{item.category}</td>
            <td>{item.priority}</td>
            <td>
              <span role="status">{item.resolution_due_at ?? "無期限"}</span>
              {item.sla_paused_reason && <span className="m13-pill" aria-label="SLA暫停理由">暫停：{item.sla_paused_reason}</span>}
            </td>
            <td>{item.assignee_id ? `承辦 …${item.assignee_id.slice(-6)}` : "未指派"}</td>
            <td>{dispatchHint(item.state)}</td>
          </tr>
        ))}
      </tbody>
    </table>
    {page.items.length === 0 && <p role="status">目前沒有符合條件的案件。</p>}
  </main>;
}
