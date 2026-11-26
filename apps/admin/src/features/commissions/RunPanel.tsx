import React, { FormEvent, useState } from "react";
import type { M12Api } from "./api";
import { failedState, isAllowed, newIdempotencyKey } from "./viewState";
import type { CommissionRun, ViewState } from "./types";

interface RunForm {
  policyId: string;
  periodStart: string;
  periodEnd: string;
  revenueCutoffAt: string;
}

const emptyForm: RunForm = { policyId: "", periodStart: "", periodEnd: "", revenueCutoffAt: "" };

export function RunPanel({ api, onRunApproved }: { api: M12Api; onRunApproved?: (runId: string) => void }) {
  const [form, setForm] = useState<RunForm>(emptyForm);
  const [runState, setRunState] = useState<ViewState<CommissionRun>>({ kind: "empty", message: "尚未建立正式結算批次。" });
  const [decisionForm, setDecisionForm] = useState<{ decision: "approve" | "reject"; reasonCode: string } | null>(null);

  const currentRun = runState.kind === "ready" || runState.kind === "conflict" || runState.kind === "stale" ? runState.value : undefined;

  async function submitRun(event: FormEvent) {
    event.preventDefault();
    setRunState({ kind: "loading" });
    try {
      const result = await api.createCommissionRun(
        {
          policy_id: form.policyId,
          period_start: form.periodStart,
          period_end: form.periodEnd,
          revenue_cutoff_at: form.revenueCutoffAt ? new Date(form.revenueCutoffAt).toISOString() : "",
        },
        newIdempotencyKey(),
      );
      setRunState({ kind: "processing", value: result.value, receiptId: result.value.id });
    } catch (error) {
      setRunState(failedState(error, currentRun));
    }
  }

  async function refresh(runId: string) {
    setRunState({ kind: "loading" });
    try {
      const result = await api.getCommissionRun(runId);
      setRunState({ kind: "ready", value: result.value });
    } catch (error) {
      setRunState(failedState(error, currentRun));
    }
  }

  async function submitDecision(event: FormEvent) {
    event.preventDefault();
    if (!currentRun || !decisionForm) return;
    setRunState({ kind: "processing", value: currentRun, receiptId: currentRun.id });
    try {
      const result = await api.approveCommissionRun(
        currentRun.id,
        `"${currentRun.version}"`,
        { decision: decisionForm.decision, reason_code: decisionForm.reasonCode },
        newIdempotencyKey(),
      );
      setRunState({ kind: "ready", value: result.value, message: decisionForm.decision === "approve" ? "批次已核准，可建立發放批次。" : "批次已拒絕。" });
      setDecisionForm(null);
      if (decisionForm.decision === "approve") onRunApproved?.(result.value.id);
    } catch (error) {
      setRunState(failedState(error, currentRun));
    }
  }

  return (
    <section aria-labelledby="m12-run-title" className="m12-panel">
      <h2 id="m12-run-title">月結批次（僅核准政策與已結算財務基礎可入正式台帳）</h2>
      <form onSubmit={submitRun} className="m12-form-grid" aria-label="建立結算批次">
        <label>policy_id（必須已核准）<input required value={form.policyId} onChange={e => setForm({ ...form, policyId: e.target.value })} /></label>
        <label>期間起<input required type="date" value={form.periodStart} onChange={e => setForm({ ...form, periodStart: e.target.value })} /></label>
        <label>期間迄<input required type="date" value={form.periodEnd} onChange={e => setForm({ ...form, periodEnd: e.target.value })} /></label>
        <label>收入截止時間<input required type="datetime-local" value={form.revenueCutoffAt} onChange={e => setForm({ ...form, revenueCutoffAt: e.target.value })} /></label>
        <button type="submit">建立批次（202 受理，非完成）</button>
      </form>
      <RunResult
        state={runState}
        onRefresh={refresh}
        canApprove={!!currentRun && isAllowed(currentRun.allowed_actions, "approve_commission_run")}
        onStartDecision={() => setDecisionForm({ decision: "approve", reasonCode: "" })}
      />
      {decisionForm && currentRun && (
        <form onSubmit={submitDecision} className="m12-form-grid" aria-label="核定結算批次">
          <h3>核定（製作人不可自核，將回 segregation_conflict）</h3>
          <label>決定<select value={decisionForm.decision} onChange={e => setDecisionForm({ ...decisionForm, decision: e.target.value as "approve" | "reject" })}><option value="approve">核准</option><option value="reject">拒絕</option></select></label>
          <label>理由代碼<input required value={decisionForm.reasonCode} onChange={e => setDecisionForm({ ...decisionForm, reasonCode: e.target.value })} /></label>
          <button type="submit">送出</button>
          <button type="button" onClick={() => setDecisionForm(null)}>取消</button>
        </form>
      )}
    </section>
  );
}

function RunResult({ state, onRefresh, canApprove, onStartDecision }: {
  state: ViewState<CommissionRun>;
  onRefresh: (runId: string) => void;
  canApprove: boolean;
  onStartDecision: () => void;
}) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  const run = state.kind === "ready" || state.kind === "conflict" || state.kind === "stale"
    ? state.value
    : state.kind === "processing"
    ? state.value
    : undefined;
  if (!run) return null;
  return <div aria-live="polite">
    {state.kind === "processing" && <p role="status">處理中（queued/calculating），收據：{state.receiptId}</p>}
    {"message" in state && state.message && <p>{state.message}</p>}
    {state.kind === "conflict" && <p role="alert">版本衝突（目前版本 {state.currentVersion}），請重新讀取。</p>}
    <dl className="m12-dl">
      <div><dt>id</dt><dd>{run.id}</dd></div>
      <div><dt>state</dt><dd>{run.state}</dd></div>
      <div><dt>basis_total</dt><dd>{run.basis_total.amount} {run.basis_total.currency}</dd></div>
      <div><dt>marketing_total</dt><dd>{run.marketing_total.amount}</dd></div>
      <div><dt>sales_total</dt><dd>{run.sales_total.amount}</dd></div>
      <div><dt>company_retained_total</dt><dd>{run.company_retained_total.amount}</dd></div>
      <div><dt>entry_count / exception_count</dt><dd>{run.entry_count} / {run.exception_count}</dd></div>
      <div><dt>prepared_by</dt><dd>{run.prepared_by}</dd></div>
    </dl>
    <button onClick={() => onRefresh(run.id)}>重新查詢批次</button>
    {canApprove && <button onClick={onStartDecision}>開始核定</button>}
  </div>;
}
