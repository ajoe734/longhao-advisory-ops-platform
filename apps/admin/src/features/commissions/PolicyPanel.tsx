import React, { FormEvent, useState } from "react";
import type { M12Api } from "./api";
import { failedState, isAllowed, newIdempotencyKey } from "./viewState";
import type { CommissionPolicy, ViewState } from "./types";

const ALLOCATION_OPTIONS = ["unresolved", "marketing_decay_only", "whole_pool_decay"] as const;
const AGE_ANCHOR_OPTIONS = ["unresolved", "first_paid_effective_start"] as const;
const LINEAGE_OPTIONS = ["unresolved", "sticky_assisted_share", "transaction_only_share"] as const;
const YEAR3_OPTIONS = ["unresolved", "zero_unless_continuous_auto", "approved_custom_rule"] as const;
const ROUNDING_OPTIONS = ["ROUND_HALF_UP", "ROUND_HALF_EVEN"] as const;

interface DraftForm {
  policyCode: string;
  effectiveFrom: string;
  basePoolRate: string;
  allocationInterpretation: string;
  ageAnchor: string;
  serviceLineageRule: string;
  year3Rule: string;
  poolPolicyVersion: string;
  recognitionPolicyVersion: string;
  roundingMode: string;
  roundingScale: string;
  notes: string;
}

const emptyDraft: DraftForm = {
  policyCode: "",
  effectiveFrom: "",
  basePoolRate: "",
  allocationInterpretation: "unresolved",
  ageAnchor: "unresolved",
  serviceLineageRule: "unresolved",
  year3Rule: "unresolved",
  poolPolicyVersion: "",
  recognitionPolicyVersion: "",
  roundingMode: "ROUND_HALF_UP",
  roundingScale: "2",
  notes: "",
};

interface ApprovalForm {
  decision: "approve" | "reject";
  policySha256: string;
  approvedBasePoolRate: string;
  parameterDecisionEvidenceAssetId: string;
  effectiveFrom: string;
  reasonCode: string;
}

export function PolicyPanel({ api }: { api: M12Api }) {
  const [draft, setDraft] = useState<DraftForm>(emptyDraft);
  const [policyState, setPolicyState] = useState<ViewState<CommissionPolicy>>({ kind: "empty", message: "尚未建立政策版本。" });
  const [approval, setApproval] = useState<ApprovalForm | null>(null);

  const currentPolicy = policyState.kind === "ready" || policyState.kind === "conflict" || policyState.kind === "stale" ? policyState.value : null;

  async function submitDraft(event: FormEvent) {
    event.preventDefault();
    setPolicyState({ kind: "loading" });
    try {
      const result = await api.createCommissionPolicy(
        {
          policy_code: draft.policyCode,
          effective_from: draft.effectiveFrom ? new Date(draft.effectiveFrom).toISOString() : "",
          base_pool_rate: draft.basePoolRate || null,
          marketing_year_1: "1.0",
          marketing_year_2: "0.5",
          marketing_year_3_auto: "0.25",
          assisted_marketing_share: "0.3",
          assisted_sales_share: "0.7",
          allocation_interpretation: draft.allocationInterpretation,
          age_anchor: draft.ageAnchor,
          service_lineage_rule: draft.serviceLineageRule,
          year3_assisted_marketing_rule: draft.year3Rule,
          pool_policy_version: draft.poolPolicyVersion,
          recognition_policy_version: draft.recognitionPolicyVersion,
          rounding_mode: draft.roundingMode,
          rounding_scale: Number(draft.roundingScale),
          notes: draft.notes,
        },
        newIdempotencyKey(),
      );
      setPolicyState({ kind: "ready", value: result.value, message: result.replayed ? "此提案先前已建立（重送被去重）。" : "草案已建立，尚未核准。" });
    } catch (error) {
      setPolicyState(failedState(error, currentPolicy ?? undefined));
    }
  }

  async function submitApproval(event: FormEvent) {
    event.preventDefault();
    if (!currentPolicy || !approval) return;
    setPolicyState({ kind: "processing", value: currentPolicy, receiptId: currentPolicy.id });
    try {
      const result = await api.approveCommissionPolicy(
        currentPolicy.id,
        `"${currentPolicy.version}"`,
        {
          decision: approval.decision,
          policy_sha256: approval.policySha256,
          approved_base_pool_rate: approval.approvedBasePoolRate,
          parameter_decision_evidence_asset_id: approval.parameterDecisionEvidenceAssetId,
          effective_from: approval.effectiveFrom ? new Date(approval.effectiveFrom).toISOString() : "",
          reason_code: approval.reasonCode,
        },
        newIdempotencyKey(),
      );
      setPolicyState({ kind: "ready", value: result.value, message: approval.decision === "approve" ? "政策已核准。" : "政策已拒絕並停用。" });
      setApproval(null);
    } catch (error) {
      setPolicyState(failedState(error, currentPolicy));
    }
  }

  return (
    <section aria-labelledby="m12-policy-title" className="m12-panel">
      <h2 id="m12-policy-title">政策版本</h2>
      <p>100/50/25 行銷年係數與 30/70 業務介入分配由契約固定；缺任何關鍵核定參數（<code>base_pool_rate</code>、衰減與對照規則）只能停留在 draft / simulation，不可正式計獎。</p>
      <form onSubmit={submitDraft} className="m12-form-grid" aria-label="建立政策草案">
        <label>政策代碼<input required value={draft.policyCode} onChange={e => setDraft({ ...draft, policyCode: e.target.value })} /></label>
        <label>生效起始<input required type="datetime-local" value={draft.effectiveFrom} onChange={e => setDraft({ ...draft, effectiveFrom: e.target.value })} /></label>
        <label>base_pool_rate（留空＝未核定，僅可 simulation）<input placeholder="例如 0.03" value={draft.basePoolRate} onChange={e => setDraft({ ...draft, basePoolRate: e.target.value })} /></label>
        <label>allocation_interpretation<select value={draft.allocationInterpretation} onChange={e => setDraft({ ...draft, allocationInterpretation: e.target.value })}>{ALLOCATION_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}</select></label>
        <label>age_anchor<select value={draft.ageAnchor} onChange={e => setDraft({ ...draft, ageAnchor: e.target.value })}>{AGE_ANCHOR_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}</select></label>
        <label>service_lineage_rule<select value={draft.serviceLineageRule} onChange={e => setDraft({ ...draft, serviceLineageRule: e.target.value })}>{LINEAGE_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}</select></label>
        <label>year3_assisted_marketing_rule<select value={draft.year3Rule} onChange={e => setDraft({ ...draft, year3Rule: e.target.value })}>{YEAR3_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}</select></label>
        <label>pool_policy_version<input required value={draft.poolPolicyVersion} onChange={e => setDraft({ ...draft, poolPolicyVersion: e.target.value })} /></label>
        <label>recognition_policy_version<input required value={draft.recognitionPolicyVersion} onChange={e => setDraft({ ...draft, recognitionPolicyVersion: e.target.value })} /></label>
        <label>rounding_mode<select value={draft.roundingMode} onChange={e => setDraft({ ...draft, roundingMode: e.target.value })}>{ROUNDING_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}</select></label>
        <label>rounding_scale<input required type="number" min={0} max={6} value={draft.roundingScale} onChange={e => setDraft({ ...draft, roundingScale: e.target.value })} /></label>
        <label>備註<input value={draft.notes} onChange={e => setDraft({ ...draft, notes: e.target.value })} /></label>
        <button type="submit">建立草案</button>
      </form>
      <PolicyResult state={policyState} onStartApproval={() => currentPolicy && setApproval({ decision: "approve", policySha256: currentPolicy.policy_sha256, approvedBasePoolRate: currentPolicy.base_pool_rate ?? "", parameterDecisionEvidenceAssetId: "", effectiveFrom: "", reasonCode: "" })} canApprove={!!currentPolicy && isAllowed(currentPolicy.allowed_actions, "approve_commission_policy")} />
      {approval && currentPolicy && (
        <form onSubmit={submitApproval} className="m12-form-grid" aria-label="核准或拒絕政策">
          <h3>核定（異人覆核，不可由製作人本人核准）</h3>
          <label>決定<select value={approval.decision} onChange={e => setApproval({ ...approval, decision: e.target.value as "approve" | "reject" })}><option value="approve">核准</option><option value="reject">拒絕</option></select></label>
          <label>policy_sha256（核對版本未被變更）<input required value={approval.policySha256} onChange={e => setApproval({ ...approval, policySha256: e.target.value })} /></label>
          <label>核定 base_pool_rate<input required value={approval.approvedBasePoolRate} onChange={e => setApproval({ ...approval, approvedBasePoolRate: e.target.value })} /></label>
          <label>核定證據 asset id<input required value={approval.parameterDecisionEvidenceAssetId} onChange={e => setApproval({ ...approval, parameterDecisionEvidenceAssetId: e.target.value })} /></label>
          <label>生效起始<input required type="datetime-local" value={approval.effectiveFrom} onChange={e => setApproval({ ...approval, effectiveFrom: e.target.value })} /></label>
          <label>理由代碼<input required value={approval.reasonCode} onChange={e => setApproval({ ...approval, reasonCode: e.target.value })} /></label>
          <button type="submit">送出核定</button>
          <button type="button" onClick={() => setApproval(null)}>取消</button>
        </form>
      )}
    </section>
  );
}

function PolicyResult({ state, onStartApproval, canApprove }: { state: ViewState<CommissionPolicy>; onStartApproval: () => void; canApprove: boolean }) {
  if (state.kind === "loading") return <p role="status">送出中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限執行此操作。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">失敗：{state.problem.code}（{state.problem.correlation_id}）{state.problem.retryable ? "，可重試" : ""}</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const policy = state.value;
  return <div className="m12-result" aria-live="polite">
    {"message" in state && state.message && <p>{state.message}</p>}
    {state.kind === "conflict" && <p role="alert">版本衝突（目前版本 {state.currentVersion}），請重新讀取後再核定。</p>}
    <dl className="m12-dl">
      <div><dt>id</dt><dd>{policy.id}</dd></div>
      <div><dt>state</dt><dd>{policy.state}</dd></div>
      <div><dt>policy_sha256</dt><dd>{policy.policy_sha256}</dd></div>
      <div><dt>base_pool_rate</dt><dd>{policy.base_pool_rate ?? "未核定"}</dd></div>
      <div><dt>unresolved_parameters</dt><dd>{policy.unresolved_parameters.length ? policy.unresolved_parameters.join(", ") : "無"}</dd></div>
    </dl>
    {canApprove && <button onClick={onStartApproval}>開始核定</button>}
  </div>;
}
