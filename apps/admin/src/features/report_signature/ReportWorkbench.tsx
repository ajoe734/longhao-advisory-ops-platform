import React from "react";
import type { ReportWorkbench as ReportWorkbenchModel, ViewState } from "./types";
import { isAllowed } from "./viewState";

const STEPS = ["draft", "in_review", "changes_requested", "ready_to_sign", "signature_pending", "signed"] as const;

export function ReportWorkbench({ state, onReload, onOpenVersion, onFreeze }: {
  state: ViewState<ReportWorkbenchModel>;
  onReload: () => void;
  onOpenVersion: (versionId: string) => void;
  onFreeze: (versionId: string) => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正式報告工作台載入中…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此報告不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const model = state.value;
  const latest = model.versions[model.versions.length - 1];
  return <main className="m07-stack">
    {state.kind === "conflict" && <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>}
    {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
    <header className="m07-panel">
      <p className="m07-eyebrow">正式報告工作台</p>
      <h1>{model.report.title}</h1>
      <p>報告編號 {model.report.id} · 目前狀態：{model.report.state}</p>
    </header>
    <section aria-labelledby="version-list" className="m07-panel">
      <h2 id="version-list">版本與來源血緣</h2>
      <ol className="m07-timeline">
        {model.versions.map(version => (
          <li key={version.id} aria-current={version.id === model.report.current_version_id ? "step" : undefined}>
            <strong>v{version.version_number}</strong>
            <span role="status"> 目前階段：{version.state}</span>
            <p>來源候選：{version.source_candidate_id} · 內容雜湊：{version.report_content_sha256.slice(0, 12)}…</p>
            <p>待解決評論：{version.reviews.filter(review => review.decision !== "approve").length} 筆；需求覆核 {version.required_review_ids.length} 筆</p>
            <div className="m07-channel-preview">
              {version.channel_texts.map(text => <article key={text.channel}><h3>{text.channel}</h3><p>{text.body}</p></article>)}
            </div>
            <button onClick={() => onOpenVersion(version.id)}>開啟版本</button>
            <button
              disabled={!isAllowed(version.allowed_actions, "freeze_report_version")}
              title={!isAllowed(version.allowed_actions, "freeze_report_version") ? "目前覆核未完成或狀態不允許凍結" : undefined}
              onClick={() => onFreeze(version.id)}
            >
              凍結此版本
            </button>
          </li>
        ))}
      </ol>
    </section>
    {latest && !STEPS.includes(latest.state as typeof STEPS[number]) && (
      <p role="status">目前版本狀態（{latest.state}）不在一般簽署前流程內，請查看覆核或撤銷紀錄。</p>
    )}
  </main>;
}
