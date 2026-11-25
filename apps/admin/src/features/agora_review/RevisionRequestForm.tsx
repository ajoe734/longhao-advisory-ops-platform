import React, { FormEvent, useState } from "react";
import type { ReviewCase, ViewState } from "./types";
import { isAllowed } from "./viewState";

export interface RevisionDraft { instructions: string; requestedOutput: string; }

const STORAGE_PREFIX = "m06-revision-draft:";

export function RevisionRequestForm({ state, onSubmit }: {
  state: ViewState<ReviewCase>;
  onSubmit: (draft: RevisionDraft) => void;
}) {
  const reviewCaseId = state.kind === "ready" || state.kind === "conflict" || state.kind === "stale" ? state.value.id : "";
  const storageKey = `${STORAGE_PREFIX}${reviewCaseId}`;
  const [draft, setDraft] = useState<RevisionDraft>(() => {
    try {
      const saved = reviewCaseId && window?.localStorage?.getItem(storageKey);
      return saved ? JSON.parse(saved) : { instructions: "", requestedOutput: "revised_report" };
    } catch {
      return { instructions: "", requestedOutput: "revised_report" };
    }
  });
  if (state.kind === "loading") return <p role="status">修訂請求表單載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限或案件不存在。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">送出中，收據：{state.receiptId}</p>;
  const reviewCase = state.value;
  const allowed = isAllowed(reviewCase.allowed_actions, "request_servant_revision");

  const saveDraft = () => { try { window?.localStorage?.setItem(storageKey, JSON.stringify(draft)); } catch { /* local-only convenience */ } };
  const cancelDraft = () => { try { window?.localStorage?.removeItem(storageKey); } catch { /* local-only convenience */ } setDraft({ instructions: "", requestedOutput: "revised_report" }); };
  const submit = (event: FormEvent) => { event.preventDefault(); if (draft.instructions.trim()) onSubmit(draft); };

  return <section className="m06-panel" aria-labelledby="revision-request-title"><h1 id="revision-request-title">修正請求編輯</h1>
    <p>尚未送出的草稿只存在本機；一旦送出，取消需要外部確認，畫面會顯示 cancel_requested，不代表已終止。</p>
    <form onSubmit={submit}>
      <label>需修改的結論／風險／計算／證據
        <textarea required maxLength={12000} value={draft.instructions} onChange={event => setDraft({ ...draft, instructions: event.target.value })} />
      </label>
      <label>期望輸出
        <select value={draft.requestedOutput} onChange={event => setDraft({ ...draft, requestedOutput: event.target.value })}>
          <option value="revised_report">修訂報告</option>
          <option value="additional_evidence">補充證據</option>
          <option value="counter_analysis">對照分析</option>
          <option value="message_draft">訊息草稿</option>
        </select>
      </label>
      <button type="button" onClick={saveDraft}>儲存草稿</button>
      <button type="button" onClick={cancelDraft}>取消本地草稿</button>
      <button type="submit" disabled={!allowed || !draft.instructions.trim()} title={!allowed ? "目前角色或案件狀態不允許送出修訂" : undefined}>送出修訂請求</button>
    </form>
  </section>;
}
