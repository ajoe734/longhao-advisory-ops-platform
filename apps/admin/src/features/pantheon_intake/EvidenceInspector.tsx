import React from "react";
import type { ResearchCandidate, ViewState } from "./types";

const DISTRIBUTION_DENIED_CODE = "customer_distribution_denied";
const INCOMPLETE_CODE = "evidence_incomplete";

export function EvidenceInspector({ state }: { state: ViewState<ResearchCandidate> }) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看此候選的證據。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const candidate = state.value;
  const distributionDenied = candidate.validation_failure_codes.includes(DISTRIBUTION_DENIED_CODE);
  const incomplete = candidate.validation_failure_codes.includes(INCOMPLETE_CODE);
  return <section aria-labelledby="pantheon-evidence-title" className="m05-panel">
    <h2 id="pantheon-evidence-title">證據檢視：{candidate.artifact_ref.external_id}</h2>
    {state.kind === "conflict" && <p role="alert" className="m05-banner-conflict">{state.message}（目前版本 {state.currentVersion}）</p>}
    {state.kind === "stale" && <p role="status" className="m05-banner-stale">資料可能已過期：{state.message}</p>}
    <p>
      <strong>客戶發布：</strong>
      {distributionDenied ? "不允許（授權範圍僅供內部研究，需替換來源或取得權利）" : "尚需分析師覆核決定"}
    </p>
    {incomplete && <p role="alert" className="m05-flag-text">⚠ 缺少必要證據雜湊；仍保留原始接收紀錄供追查，不會進入簽章佇列。</p>}
    <div className="m05-table-scroll">
      <table>
        <caption>每一來源引用、授權範圍與雜湊驗證</caption>
        <thead><tr><th scope="col">來源系統</th><th scope="col">識別碼</th><th scope="col">類型</th><th scope="col">雜湊驗證</th></tr></thead>
        <tbody>{candidate.evidence_bundle_refs.map((ref, index) => <tr key={`${ref.system}:${ref.external_id}:${index}`}>
          <td>{ref.system}</td>
          <td>{ref.external_id}</td>
          <td>{ref.kind}</td>
          <td>{ref.checksum_sha256 ? "已提供" : "缺失（待補）"}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}
