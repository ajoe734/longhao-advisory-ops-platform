import React, { FormEvent, useState } from "react";
import type { Refund, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function RefundApproval({
  state,
  onDecision,
  onReload,
}: {
  state: ViewState<Refund>;
  onDecision: (decision: "approve" | "reject" | "request_changes", reasonCode: string) => void;
  onReload: () => void;
}) {
  const [reasonCode, setReasonCode] = useState("");
  if (state.kind === "loading") return <p role="status">退款核准頁載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限查看此退款，或退款不存在。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">退款處理中，收據：{state.receiptId}</p>;
  const refund = state.value;
  const allowed = isAllowed(refund.allowed_actions, "approve_refund");
  const needsReconciliation = refund.state === "reconciliation_required";
  const submit = (decision: "approve" | "reject" | "request_changes") => (event: FormEvent) => {
    event.preventDefault();
    if (reasonCode.trim()) onDecision(decision, reasonCode.trim());
  };
  return (
    <section className="m14-panel" aria-labelledby="refund-title">
      <h1 id="refund-title">退款核准：{refund.id}</h1>
      {state.kind === "conflict" && (
        <div role="alert">
          <p>{state.message}</p>
          <button onClick={onReload}>重載版本</button>
        </div>
      )}
      {needsReconciliation && (
        <p role="alert">此退款結果未知，請先查 provider 狀態或等待回呼；系統不會因逾時自動視為成功或重複出金。</p>
      )}
      <dl>
        <dt>原付款</dt>
        <dd>{refund.original_payment_id}</dd>
        <dt>申請金額</dt>
        <dd>{refund.requested_amount.amount} {refund.requested_amount.currency}</dd>
        <dt>狀態</dt>
        <dd>{refund.state}</dd>
        <dt>製作人</dt>
        <dd>{refund.prepared_by}</dd>
        <dt>核准人</dt>
        <dd>{refund.approved_by ?? "尚未核准"}</dd>
      </dl>
      <h2>已履約／未履約分配</h2>
      <table className="m14-table-scroll">
        <thead><tr><th>帳款明細</th><th>未履約沖還</th><th>已認列沖回</th><th>稅額調整</th></tr></thead>
        <tbody>
          {refund.allocations.map(allocation => (
            <tr key={allocation.invoice_line_ref}>
              <td>{allocation.invoice_line_ref}</td>
              <td>{allocation.unearned_amount.amount} {allocation.unearned_amount.currency}</td>
              <td>{allocation.earned_reversal_amount.amount} {allocation.earned_reversal_amount.currency}</td>
              <td>{allocation.tax_adjustment_amount.amount} {allocation.tax_adjustment_amount.currency}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <form>
        <label>
          核准理由
          <input required maxLength={80} value={reasonCode} onChange={event => setReasonCode(event.target.value)} />
        </label>
        <button disabled={!allowed || !reasonCode.trim()} onClick={submit("approve")}>核准退款</button>
        <button disabled={!allowed || !reasonCode.trim()} onClick={submit("reject")}>拒絕</button>
        {!allowed && <p>不同製作人且具 finance_checker 權限時才可核准；受限服務要等核准後才可執行。</p>}
      </form>
    </section>
  );
}
