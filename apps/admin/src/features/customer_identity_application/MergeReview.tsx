import React, { FormEvent, useState } from "react";
import type { MergeCase, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function MergeReview({ state, onDecision, onReload }: { state: ViewState<MergeCase>; onDecision: (decision: "approve" | "reject", reason: string) => void; onReload: () => void }) {
  const [reason, setReason] = useState("");
  if (state.kind === "loading") return <p role="status">合併證據載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限查看此合併案件。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">覆核處理中，收據：{state.receiptId}</p>;
  const merge = state.value;
  const allowed = isAllowed(merge.allowed_actions, "decide_customer_merge");
  const submit = (decision: "approve" | "reject") => (event: FormEvent) => { event.preventDefault(); if (reason.trim()) onDecision(decision, reason.trim()); };
  return <section className="m01-panel" aria-labelledby="merge-title"><h1 id="merge-title">客戶合併異人覆核</h1>
    {state.kind === "conflict" && <div role="alert"><p>{state.message}</p><button onClick={onReload}>重載版本</button></div>}
    <dl><dt>來源客戶</dt><dd>{merge.source_customer_id}</dd><dt>保留客戶</dt><dd>{merge.target_customer_id}</dd><dt>提案人</dt><dd>{merge.proposer_id}</dd><dt>狀態</dt><dd>{merge.status}</dd></dl>
    <p>核准只建立可追溯 alias；不重新編號契約、付款或報告歷史。</p>
    <form><label>覆核理由<input required maxLength={80} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button disabled={!allowed || !reason.trim()} onClick={submit("approve")}>核准合併</button><button disabled={!allowed || !reason.trim()} onClick={submit("reject")}>拒絕</button>
      {!allowed && <p>不同覆核人且具 cms_supervisor 權限時才可決定。</p>}</form>
  </section>;
}
