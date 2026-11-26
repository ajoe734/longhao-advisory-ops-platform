import React from "react";
import type { MaintenanceAssignment, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function MaintenanceTimeline({ state, onRelease, onReload }: {
  state: ViewState<MaintenanceAssignment[]>;
  onRelease: (assignmentId: string, etag: string) => void;
  onReload: () => void;
}) {
  if (state.kind === "loading") return <p role="status">維護責任時間軸載入中…</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看此客戶的維護歸屬。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const rows = state.value;
  const current = rows.find(row => row.state === "active");
  return <section className="m11-panel" aria-labelledby="ownership-history-title">
    <h1 id="ownership-history-title">維護責任時間軸</h1>
    {state.kind === "conflict" && <div role="alert"><p>{state.message}</p><button onClick={onReload}>重載與檢視差異</button></div>}
    <p>目前維護人：{current ? current.salesperson_id : "無（在公司Pool）"}</p>
    <ol className="m11-timeline">
      {rows.map(row => (
        <li key={row.id} aria-current={row.state === "active" ? "step" : undefined}>
          <strong>{row.salesperson_id}</strong>
          <span>{row.state}（{row.reason_code}）</span>
          <small>{row.valid_from} ～ {row.valid_until ?? "進行中"}</small>
          <small>此筆計獎歸屬不因後續移交改變——commission_entitlement_granted: {String(row.commission_entitlement_granted)}</small>
          {row.state === "active" && (
            <button
              disabled={!isAllowed(row.allowed_actions, "release_maintenance_owner")}
              title={!isAllowed(row.allowed_actions, "release_maintenance_owner") ? "目前狀態或權限不可釋出" : undefined}
              onClick={() => onRelease(row.id, `"${row.version}"`)}
            >
              釋出維護關係
            </button>
          )}
        </li>
      ))}
    </ol>
  </section>;
}
