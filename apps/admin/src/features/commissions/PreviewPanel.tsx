import React, { FormEvent, useState } from "react";
import type { M12Api } from "./api";
import { failedState, newIdempotencyKey } from "./viewState";
import type { CommissionPreview, ViewState } from "./types";

interface PreviewForm {
  policyId: string;
  periodStart: string;
  periodEnd: string;
  revenueCutoffAt: string;
  recognitionIds: string;
}

const emptyForm: PreviewForm = { policyId: "", periodStart: "", periodEnd: "", revenueCutoffAt: "", recognitionIds: "" };

export function PreviewPanel({ api }: { api: M12Api }) {
  const [form, setForm] = useState<PreviewForm>(emptyForm);
  const [state, setState] = useState<ViewState<CommissionPreview>>({ kind: "empty", message: "尚未試算。試算結果永遠不可直接付款。" });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState({ kind: "loading" });
    try {
      const recognitionIds = form.recognitionIds.split(",").map(v => v.trim()).filter(Boolean);
      const result = await api.previewCommissions(
        {
          policy_id: form.policyId,
          period_start: form.periodStart,
          period_end: form.periodEnd,
          revenue_cutoff_at: form.revenueCutoffAt ? new Date(form.revenueCutoffAt).toISOString() : "",
          recognition_ids: recognitionIds,
        },
        newIdempotencyKey(),
      );
      setState({ kind: "ready", value: result.value });
    } catch (error) {
      setState(failedState<CommissionPreview>(error));
    }
  }

  return (
    <section aria-labelledby="m12-preview-title" className="m12-panel">
      <h2 id="m12-preview-title">試算（純比較，不入正式台帳）</h2>
      <form onSubmit={submit} className="m12-form-grid" aria-label="執行試算">
        <label>policy_id<input required value={form.policyId} onChange={e => setForm({ ...form, policyId: e.target.value })} /></label>
        <label>期間起<input required type="date" value={form.periodStart} onChange={e => setForm({ ...form, periodStart: e.target.value })} /></label>
        <label>期間迄<input required type="date" value={form.periodEnd} onChange={e => setForm({ ...form, periodEnd: e.target.value })} /></label>
        <label>收入截止時間<input required type="datetime-local" value={form.revenueCutoffAt} onChange={e => setForm({ ...form, revenueCutoffAt: e.target.value })} /></label>
        <label>recognition_ids（逗號分隔，留空＝全部）<input value={form.recognitionIds} onChange={e => setForm({ ...form, recognitionIds: e.target.value })} /></label>
        <button type="submit">試算</button>
      </form>
      <PreviewResult state={state} />
    </section>
  );
}

function PreviewResult({ state }: { state: ViewState<CommissionPreview> }) {
  if (state.kind === "loading") return <p role="status">試算中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限執行試算。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">試算失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "processing") return <p role="status">處理中…</p>;
  const preview = "value" in state ? state.value : undefined;
  if (!preview) return null;
  return <div aria-live="polite">
    <p role={preview.unapproved_assumptions.length ? "alert" : undefined}>
      live_payable：{preview.live_payable ? "是" : "否（試算不可付款）"}
      {preview.unapproved_assumptions.length > 0 && ` ・ 含未核定假設：${preview.unapproved_assumptions.join(", ")}`}
    </p>
    <p>control_total：{preview.control_total.amount} {preview.control_total.currency}</p>
    <div className="m12-table-scroll">
      <table>
        <caption>逐筆拆分（marketing + sales + company = base_pool，無 float 誤差）</caption>
        <thead><tr>
          <th scope="col">recognition</th><th scope="col">basis</th><th scope="col">base_pool</th>
          <th scope="col">marketing</th><th scope="col">sales</th><th scope="col">company</th><th scope="col">規則軌跡</th>
        </tr></thead>
        <tbody>{preview.lines.map(line => <tr key={line.recognition_id}>
          <th scope="row">{line.recognition_id}</th>
          <td>{line.basis.amount} {line.basis.currency}</td>
          <td>{line.base_pool.amount}（rate {line.base_pool_rate}）</td>
          <td>{line.marketing_award.amount}（係數 {line.marketing_age_coefficient}）</td>
          <td>{line.sales_award.amount}</td>
          <td>{line.company_retained.amount}</td>
          <td>{line.rule_trace_codes.join(", ")}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}
