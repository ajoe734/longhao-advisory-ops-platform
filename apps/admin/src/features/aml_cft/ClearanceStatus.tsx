import React from "react";
import type { BusinessClearance, ViewState } from "./types";

const DECISION_LABEL: Record<BusinessClearance["decision"], string> = {
  pending: "待處理",
  approved: "通過",
  denied: "不通過",
  expired: "已過期，需重新查核",
};

// General-platform widget only: the server already restricts this response
// to decision/policy/expiry fields, so no raw AML content ever reaches here
// even if a future bug tried to pass it through — there is nothing in this
// component that reads list hits, filing notes or case ids.
export function ClearanceStatus({ state, onReload }: { state: ViewState<BusinessClearance>; onReload: () => void }) {
  if (state.kind === "loading") return <p role="status">安全摘要載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限檢視此安全摘要。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（correlation：{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">查核處理中，收據：{state.receiptId}</p>;
  const clearance = state.value;
  return (
    <section className="m04-panel" aria-labelledby="clearance-status-title">
      <h1 id="clearance-status-title">安全摘要</h1>
      {state.kind === "stale" && <p role="alert">{state.message}{state.lastSuccessAt ? `（最後成功：${state.lastSuccessAt}）` : ""}</p>}
      {state.kind === "conflict" && (
        <div role="alert">
          <p>{state.message}（目前版本：{state.currentVersion}）</p>
          <button onClick={onReload}>重新查詢</button>
        </div>
      )}
      <p role="status" className="m04-clearance-chip">目前狀態：{DECISION_LABEL[clearance.decision]}</p>
      {clearance.decision === "pending" && <p>公司尚不能以此建立或維持服務關係；請留意後續補件通知。</p>}
      {clearance.decision === "expired" && <p>需要重新查核才能建立關係；不公開內部案件詳細原因。</p>}
      <dl>
        <dt>有效期限</dt><dd>{clearance.expires_at ?? "尚未核定"}</dd>
        <dt>依據政策</dt><dd>{clearance.policy_version}</dd>
      </dl>
    </section>
  );
}
