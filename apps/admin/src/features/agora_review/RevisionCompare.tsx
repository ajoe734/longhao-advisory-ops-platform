import React from "react";
import type { ViewState, WorkspaceRevision } from "./types";

export function RevisionCompare({ state }: { state: ViewState<WorkspaceRevision> }) {
  if (state.kind === "loading") return <p role="status">版本比較載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限查看此修訂。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">修訂處理中，收據：{state.receiptId}</p>;
  const revision = state.value;
  return <section className="m06-panel" aria-labelledby="revision-compare-title"><h1 id="revision-compare-title">版本比較</h1>
    <p role="status">修訂狀態：{revision.state}</p>
    <dl><dt>基準版本</dt><dd>{revision.base_artifact_ref.external_id}</dd>
      <dt>新版本</dt><dd>{revision.result_artifact_ref ? revision.result_artifact_ref.external_id : "尚未產生"}</dd></dl>
    {revision.state === "completed" && revision.significant_diff && (
      <div role="alert" className="m06-diff-banner">
        <strong>重大差異，待人工接受</strong>
        <p>以下差異不會默默替換既有預覽內容，需分析師明確接受後才能視為新版本的審查依據：</p>
        <ul>{revision.diff_summary.map(line => <li key={line}>{line}</li>)}</ul>
      </div>
    )}
    {revision.state === "completed" && !revision.significant_diff && <p>內容與既有版本一致，無重大差異。</p>}
    {revision.state === "failed" && <p role="alert">本次修訂失敗；原始失敗紀錄保留，可重新送出修訂請求。</p>}
    {revision.state === "stale" && <p role="status">此修訂結果所依據的候選已被新版本取代，不會自動視為新版覆核。</p>}
    <p>接受修訂需人工在正式報告流程中明確動作；修訂完成不自動沿用舊審查或簽章。</p>
  </section>;
}
