import React from "react";
import type { SuitabilityAssessment, ViewState } from "./types";
import { isAllowed } from "./viewState";

const STATE_LABEL: Record<SuitabilityAssessment["state"], string> = {
  pending: "尚未評估",
  suitable: "適合",
  conditional: "條件適合",
  not_suitable: "不適合",
  reassessment_due: "待重新評估",
};

export function SuitabilityDetail({
  state,
  onReload,
  onReassess,
}: {
  state: ViewState<SuitabilityAssessment>;
  onReload: () => void;
  onReassess: () => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入適合度評估…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此評估不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;

  const assessment = state.value;
  const isNotSuitable = assessment.state === "not_suitable";
  return (
    <main className="m03-stack">
      {state.kind === "conflict" && (
        <aside role="alert">
          <strong>版本衝突</strong>
          <p>{state.message} 伺服器版本：{state.currentVersion}</p>
          <button onClick={onReload}>重載並比較</button>
        </aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
      <header className="m03-panel">
        <p className="m03-eyebrow">適合度詳情</p>
        <h1>{STATE_LABEL[assessment.state]}</h1>
        <p>問卷版本 {assessment.questionnaire_version} · 規則版本 {assessment.rule_version}</p>
        <p>核定人：{assessment.assessed_by ?? "—"}</p>
      </header>
      {isNotSuitable && (
        <aside role="alert" className="m03-panel">
          <strong>此客戶目前不適合所選服務</strong>
          <p>即使已付費，仍不得逕行開通；需改選服務或走拒絕退款流程。例外覆核需授權角色與獨立核定人。</p>
        </aside>
      )}
      <section aria-labelledby="conditions" className="m03-panel">
        <h2 id="conditions">條件限制</h2>
        {assessment.conditions.length === 0 ? <p>無限制條件。</p> : (
          <ul>{assessment.conditions.map(condition => <li key={condition}>{condition}</li>)}</ul>
        )}
      </section>
      <div className="m03-actions">
        <button
          disabled={!isAllowed(assessment.allowed_actions, "assess_suitability")}
          title={!isAllowed(assessment.allowed_actions, "assess_suitability") ? "目前狀態或角色不允許重新評估" : undefined}
          onClick={onReassess}
        >
          重新評估
        </button>
      </div>
    </main>
  );
}
