import React from "react";
import type { Application, ViewState } from "./types";
import { isAllowed } from "./viewState";

const steps = ["draft", "customer_signed", "due_diligence_pending", "review_in_progress", "accepted"] as const;

export function ApplicationTimeline({ state, onWithdraw, onReload }: { state: ViewState<Application>; onWithdraw: () => void; onReload: () => void }) {
  if (state.kind === "loading") return <p role="status">申辦時間軸載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限或申辦不存在。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">申辦處理中，收據：{state.receiptId}</p>;
  const application = state.value;
  return <section className="m01-panel" aria-labelledby="application-title">
    <h1 id="application-title">申辦 {application.id}</h1>
    {state.kind === "conflict" && <div role="alert"><p>{state.message}</p><button onClick={onReload}>重載與檢視差異</button></div>}
    <ol className="m01-timeline">{steps.map(step => <li key={step} aria-current={application.state === step ? "step" : undefined}><strong>{step}</strong><span>{application.state === step ? "目前階段" : ""}</span></li>)}</ol>
    {(application.state === "rejected" || application.state === "withdrawn") && <p role="status">案件已{application.state === "withdrawn" ? "撤回" : "拒絕"}；沒有服務權限。</p>}
    <dl><dt>KYC</dt><dd>{application.kyc_status}</dd><dt>適合度</dt><dd>{application.suitability_status}</dd><dt>業務放行</dt><dd>{application.business_clearance}</dd><dt>付款</dt><dd>{application.payment_status}</dd></dl>
    <button disabled={!isAllowed(application.allowed_actions, "withdraw_application")} title={!isAllowed(application.allowed_actions, "withdraw_application") ? "目前狀態不可撤回" : undefined} onClick={onWithdraw}>撤回申辦</button>
  </section>;
}
