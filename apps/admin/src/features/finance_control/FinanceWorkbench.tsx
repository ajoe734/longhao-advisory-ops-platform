import React, { FormEvent, useState } from "react";
import type { Page, ReconciliationItem, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function FinanceWorkbench({
  state,
  onResolve,
  onReload,
}: {
  state: ViewState<Page<ReconciliationItem>>;
  onResolve: (itemId: string, decision: "match" | "correct_mapping" | "accept_timing" | "reject" | "escalate", reasonCode: string) => void;
  onReload: () => void;
}) {
  const [reasonCodes, setReasonCodes] = useState<Record<string, string>>({});
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">財會工作台載入中…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此資源不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const page = state.value;
  const submit = (itemId: string, decision: "match" | "correct_mapping" | "accept_timing" | "reject" | "escalate") => (event: FormEvent) => {
    event.preventDefault();
    const reasonCode = (reasonCodes[itemId] ?? "").trim();
    if (reasonCode) onResolve(itemId, decision, reasonCode);
  };
  return (
    <main className="m14-stack">
      {state.kind === "conflict" && (
        <aside role="alert">
          <strong>版本衝突</strong>
          <p>{state.message} 伺服器版本：{state.currentVersion}</p>
          <button onClick={onReload}>重載並比較</button>
        </aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
      <header className="m14-panel">
        <p className="m14-eyebrow">財會工作台</p>
        <h1>收款、對帳與差異</h1>
        <p>金額以定點小數顯示；不同幣別不可直接加總。截至 {new Date(page.as_of).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}。</p>
      </header>
      <section className="m14-panel m14-table-scroll" aria-labelledby="reconciliation-title">
        <h2 id="reconciliation-title">對帳差異（{page.total_estimate ?? page.items.length} 筆）</h2>
        <table>
          <thead>
            <tr>
              <th>差異類型</th>
              <th>預期金額</th>
              <th>實際金額</th>
              <th>狀態</th>
              <th>理由代碼</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map(item => {
              const allowed = isAllowed(item.allowed_actions, "resolve_reconciliation_item");
              const formId = `resolve-form-${item.id}`;
              return (
                <tr key={item.id}>
                  <td>{item.difference_type}</td>
                  <td>{item.expected_amount.amount} {item.expected_amount.currency}</td>
                  <td>{item.actual_amount.amount} {item.actual_amount.currency}</td>
                  <td>{item.state}</td>
                  <td>
                    <form id={formId} onSubmit={submit(item.id, "match")}>
                      <label>
                        理由代碼
                        <input
                          required
                          maxLength={80}
                          value={reasonCodes[item.id] ?? ""}
                          onChange={event => setReasonCodes(previous => ({ ...previous, [item.id]: event.target.value }))}
                        />
                      </label>
                    </form>
                  </td>
                  <td>
                    <button type="submit" form={formId} disabled={!allowed} title={!allowed ? "目前角色或狀態不允許覆核" : undefined}>核對一致</button>
                    <button type="button" disabled={!allowed} onClick={submit(item.id, "escalate")}>升級</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </main>
  );
}
