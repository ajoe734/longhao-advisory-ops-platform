import React, { FormEvent, useEffect, useState } from "react";
import type { M12Api } from "./api";
import { failedState, isAllowed, newIdempotencyKey } from "./viewState";
import type { PayoutBatch, ViewState } from "./types";

interface CreateForm {
  approvedRunIds: string;
  payoutPeriod: string;
  payrollMappingVersion: string;
}

const emptyCreate: CreateForm = { approvedRunIds: "", payoutPeriod: "", payrollMappingVersion: "" };

export function PayoutPanel({ api, prefillRunId }: { api: M12Api; prefillRunId?: string }) {
  const [form, setForm] = useState<CreateForm>(emptyCreate);
  const [batchId, setBatchId] = useState("");
  const [batchState, setBatchState] = useState<ViewState<PayoutBatch>>({ kind: "empty", message: "尚未建立發放批次。試算結果不可付款；只有已核准批次能入批次。" });
  const [decisionForm, setDecisionForm] = useState<{ decision: "approve" | "reject"; reasonCode: string } | null>(null);

  const currentBatch = "value" in batchState ? batchState.value : undefined;

  useEffect(() => {
    if (prefillRunId) setForm(f => ({ ...f, approvedRunIds: prefillRunId }));
  }, [prefillRunId]);

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    setBatchState({ kind: "loading" });
    try {
      const runIds = form.approvedRunIds.split(",").map(v => v.trim()).filter(Boolean);
      const result = await api.createCommissionPayoutBatch(
        { approved_run_ids: runIds, payout_period: form.payoutPeriod, payroll_mapping_version: form.payrollMappingVersion },
        newIdempotencyKey(),
      );
      setBatchId(result.value.id);
      setBatchState({ kind: "processing", value: result.value, receiptId: result.value.id });
    } catch (error) {
      setBatchState(failedState(error, currentBatch));
    }
  }

  async function refresh(id: string) {
    setBatchState({ kind: "loading" });
    try {
      const result = await api.getCommissionPayoutBatch(id);
      setBatchId(id);
      setBatchState({ kind: "ready", value: result.value });
    } catch (error) {
      setBatchState(failedState(error, currentBatch));
    }
  }

  async function submitDecision(event: FormEvent) {
    event.preventDefault();
    if (!currentBatch || !decisionForm) return;
    setBatchState({ kind: "processing", value: currentBatch, receiptId: currentBatch.id });
    try {
      const result = await api.approveCommissionPayoutBatch(
        currentBatch.id,
        `"${currentBatch.version}"`,
        { decision: decisionForm.decision, reason_code: decisionForm.reasonCode },
        newIdempotencyKey(),
      );
      setBatchState({ kind: "ready", value: result.value, message: decisionForm.decision === "approve" ? "批次已核准，待匯出薪酬介接。" : "批次已拒絕。" });
      setDecisionForm(null);
    } catch (error) {
      setBatchState(failedState(error, currentBatch));
    }
  }

  return (
    <section aria-labelledby="m12-payout-title" className="m12-panel">
      <h2 id="m12-payout-title">發放批次（薪酬輸出由不同人核准，與原 run 控制總額對比）</h2>
      <form onSubmit={submitCreate} className="m12-form-grid" aria-label="建立發放批次">
        <label>已核准 run_id（逗號分隔）<input required value={form.approvedRunIds} onChange={e => setForm({ ...form, approvedRunIds: e.target.value })} /></label>
        <label>發放期間（YYYY-MM）<input required placeholder="2026-11" value={form.payoutPeriod} onChange={e => setForm({ ...form, payoutPeriod: e.target.value })} /></label>
        <label>薪酬對映版本<input required value={form.payrollMappingVersion} onChange={e => setForm({ ...form, payrollMappingVersion: e.target.value })} /></label>
        <button type="submit">建立批次（202 受理）</button>
      </form>
      <label className="m12-inline-query">查詢既有批次 id<input value={batchId} onChange={e => setBatchId(e.target.value)} /><button type="button" onClick={() => batchId && refresh(batchId)}>查詢</button></label>
      <PayoutResult
        state={batchState}
        onRefresh={refresh}
        canApprove={!!currentBatch && isAllowed(currentBatch.allowed_actions, "approve_commission_payout_batch")}
        onStartDecision={() => setDecisionForm({ decision: "approve", reasonCode: "" })}
      />
      {decisionForm && currentBatch && (
        <form onSubmit={submitDecision} className="m12-form-grid" aria-label="核定發放批次">
          <h3>核定（製作人不可自核）</h3>
          <label>決定<select value={decisionForm.decision} onChange={e => setDecisionForm({ ...decisionForm, decision: e.target.value as "approve" | "reject" })}><option value="approve">核准</option><option value="reject">拒絕</option></select></label>
          <label>理由代碼<input required value={decisionForm.reasonCode} onChange={e => setDecisionForm({ ...decisionForm, reasonCode: e.target.value })} /></label>
          <button type="submit">送出</button>
          <button type="button" onClick={() => setDecisionForm(null)}>取消</button>
        </form>
      )}
    </section>
  );
}

function PayoutResult({ state, onRefresh, canApprove, onStartDecision }: {
  state: ViewState<PayoutBatch>;
  onRefresh: (id: string) => void;
  canApprove: boolean;
  onStartDecision: () => void;
}) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  const batch = state.kind === "ready" || state.kind === "conflict" || state.kind === "stale"
    ? state.value
    : state.kind === "processing"
    ? state.value
    : undefined;
  if (!batch) return null;
  return <div aria-live="polite">
    {state.kind === "processing" && <p role="status">處理中（組裝中），收據：{state.receiptId}</p>}
    {"message" in state && state.message && <p>{state.message}</p>}
    {state.kind === "conflict" && <p role="alert">版本衝突（目前版本 {state.currentVersion}），請重新讀取。</p>}
    <dl className="m12-dl">
      <div><dt>id</dt><dd>{batch.id}</dd></div>
      <div><dt>state</dt><dd>{batch.state}</dd></div>
      <div><dt>total</dt><dd>{batch.total.amount} {batch.total.currency}</dd></div>
      <div><dt>line_count</dt><dd>{batch.line_count}</dd></div>
      <div><dt>control_sha256</dt><dd>{batch.control_sha256}</dd></div>
    </dl>
    <button onClick={() => onRefresh(batch.id)}>重新查詢批次</button>
    {canApprove && <button onClick={onStartDecision}>開始核定</button>}
  </div>;
}
