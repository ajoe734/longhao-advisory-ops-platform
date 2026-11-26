import React, { useState } from "react";
import type { PoolEntry, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function CompanyPoolWorkbench({ state, onClaim, onReload }: {
  state: ViewState<PoolEntry[]>;
  onClaim: (entry: PoolEntry, firstContactDueAt: string) => void;
  onReload: () => void;
}) {
  if (state.kind === "loading") return <p role="status">公司Pool工作台載入中…</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看公司Pool。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const rows = state.value;
  return <section className="m11-panel" aria-labelledby="company-pool-title">
    <h1 id="company-pool-title">公司Pool工作台</h1>
    {state.kind === "conflict" && <div role="alert"><p>{state.message}</p><button onClick={onReload}>重載與檢視差異</button></div>}
    <p id="company-pool-help">同時認領以版本鎖保證只有一位新維護人；先點不代表先得獎金。</p>
    <div className="m11-table-scroll"><table aria-describedby="company-pool-help">
      <caption>可重新開發的公司客戶池</caption>
      <thead><tr><th scope="col">客戶</th><th scope="col">來源</th><th scope="col">狀態</th><th scope="col">中斷起算</th><th scope="col">政策版本</th><th scope="col">操作</th></tr></thead>
      <tbody>{rows.map(entry => <PoolRow key={entry.id} entry={entry} onClaim={onClaim} />)}</tbody>
    </table></div>
  </section>;
}

function PoolRow({ entry, onClaim }: { entry: PoolEntry; onClaim: (entry: PoolEntry, firstContactDueAt: string) => void }) {
  const [dueAt, setDueAt] = useState("");
  const claimable = isAllowed(entry.allowed_actions, "claim_pool_customer");
  return <tr>
    <th scope="row">{entry.customer_id}</th>
    <td>{entry.entry_reason}</td>
    <td>{entry.state === "suppressed" ? "受限（不可促銷）" : entry.state}</td>
    <td>{entry.paid_service_lapse_started_at ?? "—"}</td>
    <td>{entry.policy_version}</td>
    <td>
      <label>
        首次聯絡期限
        <input type="datetime-local" value={dueAt} onChange={event => setDueAt(event.target.value)} disabled={!claimable} />
      </label>
      <button disabled={!claimable || !dueAt} title={!claimable ? "目前狀態不可認領" : undefined} onClick={() => onClaim(entry, dueAt)}>
        認領
      </button>
    </td>
  </tr>;
}
