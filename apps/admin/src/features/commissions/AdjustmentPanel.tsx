import React, { FormEvent, useState } from "react";
import type { M12Api } from "./api";
import { failedState, newIdempotencyKey } from "./viewState";
import type { CommissionAdjustment, ViewState } from "./types";

interface AdjustmentForm {
  originalEntryId: string;
  refundId: string;
  attributableRecognitionId: string;
  amount: string;
  currency: string;
  reasonCode: string;
  evidenceAssetId: string;
}

const emptyForm: AdjustmentForm = {
  originalEntryId: "",
  refundId: "",
  attributableRecognitionId: "",
  amount: "",
  currency: "TWD",
  reasonCode: "",
  evidenceAssetId: "",
};

export function AdjustmentPanel({ api }: { api: M12Api }) {
  const [form, setForm] = useState<AdjustmentForm>(emptyForm);
  const [state, setState] = useState<ViewState<CommissionAdjustment>>({ kind: "empty", message: "尚未建立退款調整。退款只反轉原受款人與原政策，不轉嫁新接手人。" });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState({ kind: "loading" });
    try {
      const result = await api.createCommissionAdjustment(
        {
          original_entry_id: form.originalEntryId,
          refund_id: form.refundId,
          attributable_recognition_id: form.attributableRecognitionId,
          adjustment_amount: { amount: form.amount, currency: form.currency },
          reason_code: form.reasonCode,
          evidence_asset_id: form.evidenceAssetId,
        },
        newIdempotencyKey(),
      );
      setState({ kind: "ready", value: result.value, message: result.replayed ? "同一退款已調整過一次（重送被去重）。" : "調整已建立。" });
    } catch (error) {
      setState(failedState<CommissionAdjustment>(error));
    }
  }

  return (
    <section aria-labelledby="m12-adjustment-title" className="m12-panel">
      <h2 id="m12-adjustment-title">退款調整</h2>
      <form onSubmit={submit} className="m12-form-grid" aria-label="建立退款調整">
        <label>original_entry_id<input required value={form.originalEntryId} onChange={e => setForm({ ...form, originalEntryId: e.target.value })} /></label>
        <label>refund_id<input required value={form.refundId} onChange={e => setForm({ ...form, refundId: e.target.value })} /></label>
        <label>attributable_recognition_id<input required value={form.attributableRecognitionId} onChange={e => setForm({ ...form, attributableRecognitionId: e.target.value })} /></label>
        <label>調整金額（可為負值）<input required value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} /></label>
        <label>幣別<input required value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value })} /></label>
        <label>理由代碼<input required value={form.reasonCode} onChange={e => setForm({ ...form, reasonCode: e.target.value })} /></label>
        <label>證據 asset id<input required value={form.evidenceAssetId} onChange={e => setForm({ ...form, evidenceAssetId: e.target.value })} /></label>
        <button type="submit">建立調整</button>
      </form>
      <AdjustmentResult state={state} />
    </section>
  );
}

function AdjustmentResult({ state }: { state: ViewState<CommissionAdjustment> }) {
  if (state.kind === "loading") return <p role="status">送出中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "processing") return <p role="status">處理中…</p>;
  const adjustment = "value" in state ? state.value : undefined;
  if (!adjustment) return null;
  return <div aria-live="polite">
    {"message" in state && state.message && <p>{state.message}</p>}
    <dl className="m12-dl">
      <div><dt>id</dt><dd>{adjustment.id}</dd></div>
      <div><dt>original_entry_id</dt><dd>{adjustment.original_entry_id}</dd></div>
      <div><dt>original_recipient_id</dt><dd>{adjustment.original_recipient_id ?? "（原本未指定受款人）"}</dd></div>
      <div><dt>amount</dt><dd>{adjustment.amount.amount} {adjustment.amount.currency}</dd></div>
      <div><dt>payroll_offset_allowed</dt><dd>{adjustment.payroll_offset_allowed ? "是" : "否（已付款，須人工追索／抵銷覆核，不自動扣薪）"}</dd></div>
    </dl>
  </div>;
}
