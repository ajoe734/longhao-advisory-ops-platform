import React, { useState } from "react";
import type { AmlCase, Page, ViewState } from "./types";
import { isAllowed } from "./viewState";

const CASE_STATE_LABEL: Record<AmlCase["state"], string> = {
  open: "待分派",
  under_review: "審查中",
  escalated: "已升級",
  cleared: "已解除",
  rejected: "已拒絕",
  closed: "已結案",
};

// This workbench is a separate, permission-gated navigation surface: it is
// never indexed by platform-wide search, and every view/decision here is
// audited server-side. No raw hit detail ever leaves this component.
export function AmlCaseList({ state, onSelect, onReload }: { state: ViewState<Page<AmlCase>>; onSelect: (caseId: string) => void; onReload: () => void }) {
  if (state.kind === "loading") return <p role="status">受限案件佇列載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限檢視 AML 受限案件；本畫面禁止全平台搜尋索引。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（correlation：{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const page = state.value;
  return (
    <section className="m04-panel" aria-labelledby="aml-case-queue-title">
      <h1 id="aml-case-queue-title">AML 受限案件工作佇列</h1>
      {state.kind === "stale" && <p role="alert">{state.message}{state.lastSuccessAt ? `（最後成功：${state.lastSuccessAt}）` : ""}</p>}
      <div className="m04-table-scroll">
        <table>
          <thead><tr><th>案件</th><th>目前階段</th><th>風險等級</th><th>下次覆核</th></tr></thead>
          <tbody>
            {page.items.map((item) => (
              <tr key={item.id} className="m04-case-row" data-state={item.state}>
                <td><button onClick={() => onSelect(item.id)}>{item.id}</button></td>
                <td>目前階段：{CASE_STATE_LABEL[item.state]}</td>
                <td>{item.risk_level}</td>
                <td>{item.next_review_due_at ?? "未排定"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button onClick={onReload}>重新整理</button>
    </section>
  );
}

export function AmlCaseDetail({
  state,
  onRequestEdd,
  onApprove,
  onDeny,
  onReload,
}: {
  state: ViewState<AmlCase>;
  onRequestEdd: (reasonCode: string) => void;
  onApprove: (reasonCode: string) => void;
  onDeny: (reasonCode: string) => void;
  onReload: () => void;
}) {
  const [eddReason, setEddReason] = useState("");
  const [decisionReason, setDecisionReason] = useState("");

  if (state.kind === "loading") return <p role="status">案件載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限檢視此案件，或案件不存在；本次存取已記錄 audit。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（correlation：{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">決策處理中，收據：{state.receiptId}</p>;
  const item = state.value;
  const canAct = isAllowed(item.allowed_actions, "add_aml_case_action");
  const canDecide = isAllowed(item.allowed_actions, "decide_aml_case");
  const trimmedEddReason = eddReason.trim();
  const trimmedDecisionReason = decisionReason.trim();
  const submitEdd = () => {
    if (!trimmedEddReason) return;
    onRequestEdd(trimmedEddReason);
    setEddReason("");
  };
  return (
    <section className="m04-panel" aria-labelledby="aml-case-detail-title">
      <h1 id="aml-case-detail-title">案件 {item.id}</h1>
      <p role="status">目前階段：{CASE_STATE_LABEL[item.state]}（版本 {item.version}）</p>
      {state.kind === "stale" && (
        <p role="alert">{state.message}{state.lastSuccessAt ? `（最後成功：${state.lastSuccessAt}）` : ""}</p>
      )}
      {state.kind === "conflict" && (
        <div role="alert">
          <p>{state.message}（目前版本：{state.currentVersion}）</p>
          <button onClick={onReload}>重載並檢視差異</button>
        </div>
      )}
      <dl>
        <dt>風險等級</dt><dd>{item.risk_level}</dd>
        <dt>比對次數</dt><dd>{item.screening_ids.length}</dd>
        <dt>受限證據件數</dt><dd>{item.restricted_evidence_ids.length}</dd>
        <dt>下次覆核</dt><dd>{item.next_review_due_at ?? "未排定"}</dd>
      </dl>
      <p className="m04-restricted-banner">受限工作台：命中解除與核定須雙人；不可同一人完成受限例外製作與核定。</p>
      <div className="m04-decision-grid">
        <label>
          補件理由
          <input
            disabled={!canAct}
            title={!canAct ? "目前狀態或角色不可要求補件" : undefined}
            aria-label="補件理由"
            aria-describedby={!canAct ? "aml-case-action-disabled-reason" : undefined}
            value={eddReason}
            onChange={(event) => setEddReason(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                submitEdd();
              }
            }}
          />
        </label>
        <button disabled={!canAct || !trimmedEddReason} onClick={submitEdd}>
          送出補件要求
        </button>
        {!canAct && <p id="aml-case-action-disabled-reason" role="note">目前狀態或角色不可要求補件</p>}
        <label>
          核定理由
          <input
            disabled={!canDecide}
            title={!canDecide ? "目前狀態、角色或 maker-checker 規則不可核定" : undefined}
            aria-label="核定理由"
            aria-describedby={!canDecide ? "aml-case-decision-disabled-reason" : undefined}
            value={decisionReason}
            onChange={(event) => setDecisionReason(event.target.value)}
          />
        </label>
        <button disabled={!canDecide || !trimmedDecisionReason} onClick={() => onApprove(trimmedDecisionReason)}>
          核定解除
        </button>
        <button disabled={!canDecide || !trimmedDecisionReason} onClick={() => onDeny(trimmedDecisionReason)}>
          核定限制
        </button>
        {!canDecide && <p id="aml-case-decision-disabled-reason" role="note">目前狀態、角色或 maker-checker 規則不可核定</p>}
      </div>
    </section>
  );
}
