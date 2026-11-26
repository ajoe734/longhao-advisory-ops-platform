import React, { useState } from "react";
import type { PoolEvaluationResult, ViewState } from "./types";

export function PolicyPreview({ state, onPreview }: {
  state: ViewState<PoolEvaluationResult>;
  onPreview: (policyVersion: string, asOf: string) => void;
}) {
  const [policyVersion, setPolicyVersion] = useState("");
  const [asOf, setAsOf] = useState("");
  const submit = (event: React.FormEvent) => { event.preventDefault(); onPreview(policyVersion, asOf); };
  return <section className="m11-panel" aria-labelledby="policy-preview-title">
    <h1 id="policy-preview-title">政策預演</h1>
    <p id="policy-preview-help">只模擬「全部付費服務連續中斷168小時」候選名單；未核定政策不會實際回收客戶。</p>
    <form onSubmit={submit} aria-describedby="policy-preview-help">
      <label>政策版本<input value={policyVersion} onChange={event => setPolicyVersion(event.target.value)} required /></label>
      <label>評估時間<input type="datetime-local" value={asOf} onChange={event => setAsOf(event.target.value)} required /></label>
      <button type="submit">預演（dry_run）</button>
    </form>
    <Result state={state} />
  </section>;
}

function Result({ state }: { state: ViewState<PoolEvaluationResult> }) {
  if (state.kind === "loading") return <p role="status">評估中…</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限執行政策預演。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）{state.problem.code === "policy_not_approved" && "：政策尚未核准，無法正式執行，只能預演。"}</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const result = state.value;
  return <>
    {state.kind === "stale" && <div role="status" className="m11-banner-stale"><p>{state.message}</p></div>}
    {state.kind === "conflict" && <div role="alert"><p>{state.message}</p></div>}
    <dl>
      <dt>規則狀態</dt><dd>{result.rule_status === "approved" ? "已核准" : "待核定（僅供預演）"}</dd>
      <dt>評估人數</dt><dd>{result.evaluated_count}</dd>
      <dt>符合候選</dt><dd>{result.eligible_count}</dd>
      <dt>受限（不列入促銷）</dt><dd>{result.blocked_count}</dd>
    </dl>
  </>;
}
