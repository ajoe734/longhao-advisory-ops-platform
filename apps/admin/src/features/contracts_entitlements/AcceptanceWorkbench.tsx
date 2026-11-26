import React, { useState } from "react";
import type { CompanyAcceptance, ViewState } from "./types";
import { isAllowed } from "./viewState";

export interface GateSummary {
  kyc: "pending" | "approved" | "rejected" | "refresh_required";
  suitability: "pending" | "suitable" | "conditional" | "not_suitable";
  clearance: "pending" | "approved" | "denied" | "expired";
  payment: "not_required" | "pending" | "authorized" | "paid" | "refunded" | "disputed";
}

const GATE_LABELS: Record<keyof GateSummary, string> = { kyc: "KYC", suitability: "適合度", clearance: "AML 審查", payment: "付款條件" };
const PASSING = new Set(["approved", "suitable", "conditional", "authorized", "paid"]);

export function AcceptanceWorkbench({
  state, gates, onDecide, onReload,
}: {
  state: ViewState<CompanyAcceptance>;
  gates: GateSummary;
  onDecide: (decision: "accept" | "reject" | "request_changes", reasonCode: string) => void;
  onReload: () => void;
}) {
  const [reasonCode, setReasonCode] = useState("");
  if (state.kind === "loading") return <p role="status">公司接受工作台載入中…</p>;
  if (state.kind === "denied") return <p role="alert">您無權限處理此公司接受決策（需 company_acceptor 職務，且不可為本契約條款製作／核准者）。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;

  const allGatesPass = (Object.keys(gates) as Array<keyof GateSummary>).every((key) => key === "payment" ? true : PASSING.has(gates[key]));
  const acceptance = state.kind === "conflict" || state.kind === "ready" || state.kind === "stale" ? state.value : undefined;
  const canDecide = acceptance ? isAllowed(acceptance.allowed_actions, "decide_company_acceptance") : true;
  const submit = (decision: "accept" | "reject" | "request_changes") => (event: React.FormEvent) => {
    event.preventDefault();
    if (reasonCode.trim()) onDecide(decision, reasonCode.trim());
  };

  return (
    <section className="m02-panel" aria-labelledby="acceptance-title">
      <h1 id="acceptance-title">公司接受工作台</h1>
      {state.kind === "conflict" && <div role="alert"><p>{state.message}</p><button onClick={onReload}>重載版本</button></div>}
      {state.kind === "processing" && <p role="status">決策處理中，收據：{state.receiptId}</p>}
      <table>
        <caption>四項審核狀態（全部通過才可接受；付款不是必要前提，拒絕可在收款後發生）</caption>
        <thead><tr><th scope="col">項目</th><th scope="col">狀態</th></tr></thead>
        <tbody>
          {(Object.keys(gates) as Array<keyof GateSummary>).map((key) => (
            <tr key={key}><th scope="row">{GATE_LABELS[key]}</th><td>{gates[key]}{key !== "payment" && !PASSING.has(gates[key]) ? "（未通過）" : ""}</td></tr>
          ))}
        </tbody>
      </table>
      <form>
        <label>
          理由代碼
          <input required maxLength={80} value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} />
        </label>
        <button disabled={!canDecide || !allGatesPass || !reasonCode.trim()} onClick={submit("accept")}>接受</button>
        <button disabled={!canDecide || !reasonCode.trim()} onClick={submit("reject")}>拒絕（含已收款需觸發退款工作）</button>
        <button disabled={!canDecide || !reasonCode.trim()} onClick={submit("request_changes")}>要求補件</button>
        {!allGatesPass && <p>尚有審核項目未通過，無法接受；仍可拒絕或要求補件。</p>}
        {!canDecide && <p>依職務分離規則，製作或核准本方案條款者不可同時擔任本次公司接受決策。</p>}
      </form>
    </section>
  );
}
