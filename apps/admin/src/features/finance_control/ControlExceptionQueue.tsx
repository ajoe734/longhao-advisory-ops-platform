import React, { FormEvent, useState } from "react";
import type { ControlException, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function ControlExceptionQueue({
  state,
  onAction,
  onReload,
}: {
  state: ViewState<ControlException>;
  onAction: (action: "add_remediation_evidence" | "request_closure" | "approve_closure" | "reject_closure" | "reopen", reasonCode: string) => void;
  onReload: () => void;
}) {
  const [reasonCode, setReasonCode] = useState("");
  if (state.kind === "loading") return <p role="status">內控例外佇列載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限查看此內控例外。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const exception = state.value;
  const canRequestClosure = isAllowed(exception.allowed_actions, "request_closure");
  const canApproveClosure = isAllowed(exception.allowed_actions, "approve_closure");
  const canReopen = isAllowed(exception.allowed_actions, "reopen");
  const submit = (action: "add_remediation_evidence" | "request_closure" | "approve_closure" | "reject_closure" | "reopen") => (event: FormEvent) => {
    event.preventDefault();
    if (reasonCode.trim()) onAction(action, reasonCode.trim());
  };
  return (
    <section className="m14-panel" aria-labelledby="control-exception-title">
      <h1 id="control-exception-title">內控例外：{exception.control_code}</h1>
      {state.kind === "conflict" && (
        <div role="alert">
          <p>{state.message}</p>
          <button onClick={onReload}>重載版本</button>
        </div>
      )}
      <dl>
        <dt>嚴重度</dt>
        <dd>{exception.severity}</dd>
        <dt>狀態</dt>
        <dd>{exception.state}</dd>
        <dt>改善承辦</dt>
        <dd>{exception.remediation_owner_id}</dd>
        <dt>期限</dt>
        <dd>{new Date(exception.due_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</dd>
        <dt>結案人</dt>
        <dd>{exception.closed_by ?? "尚未結案"}</dd>
      </dl>
      <p>結案須由獨立稽核覆核；改善承辦不可自行核准結案，無人工空勾就標結案。</p>
      <form>
        <label>
          理由代碼
          <input required maxLength={80} value={reasonCode} onChange={event => setReasonCode(event.target.value)} />
        </label>
        <button disabled={!canRequestClosure || !reasonCode.trim()} onClick={submit("request_closure")}>送出結案申請</button>
        <button disabled={!canApproveClosure || !reasonCode.trim()} onClick={submit("approve_closure")}>核准結案</button>
        <button disabled={!canApproveClosure || !reasonCode.trim()} onClick={submit("reject_closure")}>退回改善</button>
        <button disabled={!canReopen || !reasonCode.trim()} onClick={submit("reopen")}>重新開啟</button>
      </form>
    </section>
  );
}
