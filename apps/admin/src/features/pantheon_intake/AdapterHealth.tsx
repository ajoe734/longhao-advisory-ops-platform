import React from "react";
import type { AdapterHealthSnapshot, ViewState } from "./types";

// 真實 Pantheon/Agora 延遲、重複率與 DLQ 指標匯出尚未接線（NOT_CONNECTED）；
// 本面板只渲染已注入的快照，不自行呼叫外部監控或假裝已打通。
export function AdapterHealth({ state }: { state: ViewState<AdapterHealthSnapshot> }) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看對接健康。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const snapshot = state.value;
  return <section aria-labelledby="pantheon-health-title" className="m05-panel">
    <h2 id="pantheon-health-title">對接健康</h2>
    <p className="m05-eyebrow">只含來源／ID 層級統計，不含研究全文或客戶資料</p>
    <dl className="m05-health-grid">
      <div><dt>來源延遲 P95</dt><dd>{snapshot.source_latency_p95_ms !== null ? `${snapshot.source_latency_p95_ms} ms` : "無資料"}</dd></div>
      <div><dt>Schema 版本拒絕數</dt><dd>{snapshot.schema_rejection_count}</dd></div>
      <div><dt>重複率</dt><dd>{snapshot.duplicate_rate !== null ? `${(snapshot.duplicate_rate * 100).toFixed(1)}%` : "無資料"}</dd></div>
      <div><dt>失敗佇列（DLQ）</dt><dd aria-label={snapshot.dead_letter_count > 0 ? "需要處理" : "正常"}>{snapshot.dead_letter_count}</dd></div>
    </dl>
  </section>;
}
