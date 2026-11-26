import { useState } from "react";
import type {
  Conversion,
  ConversionClaim,
  ConversionDecision,
  NextActionRequest,
  Opportunity,
  SalesActivityCreate,
  ViewState,
} from "./types";
import { isAllowed } from "./viewState";

const STATE_LABELS: Record<Opportunity["state"], string> = {
  open: "開放",
  qualified: "已篩選",
  proposal: "已提案",
  awaiting_customer: "待客戶回覆",
  converted: "已轉換",
  lost: "已流失",
  suppressed: "已抑制／待重評",
};

function formatTime(value: string | null): string {
  if (!value) return "未提供";
  return new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" });
}

export function OpportunityDetail({
  state,
  conversion,
  onReload,
  onRecordActivity,
  onSetNextAction,
  onClaimConversion,
  onDecideConversion,
}: {
  state: ViewState<Opportunity>;
  conversion: Conversion | null;
  onReload: () => void;
  onRecordActivity: (body: SalesActivityCreate) => Promise<void>;
  onSetNextAction: (body: NextActionRequest) => Promise<void>;
  onClaimConversion: (body: ConversionClaim) => Promise<void>;
  onDecideConversion: (body: ConversionDecision) => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入業務機會…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此機會不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;

  const opportunity = state.value;
  const isTerminal = opportunity.state === "converted" || opportunity.state === "lost";

  async function withBusy(key: string, action: () => Promise<void>) {
    setBusy(key);
    try {
      await action();
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="m10-stack">
      {state.kind === "conflict" && (
        <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>資料可能已過期</strong><p>{state.message}</p></aside>}
      <header className="m10-panel">
        <p className="m10-eyebrow">業務機會・{opportunity.type}</p>
        <h1>{opportunity.service_code}</h1>
        <p>
          客戶（遮罩）：{opportunity.customer_id.slice(0, 8)}…・
          <span className={`m10-state m10-state-${opportunity.state}`} role="status">{STATE_LABELS[opportunity.state]}</span>
          ・到期：{formatTime(opportunity.due_at)}
          {opportunity.converted_at && <>・轉換時間：{formatTime(opportunity.converted_at)}</>}
        </p>
      </header>

      <section aria-labelledby="activity-form" className="m10-panel">
        <h2 id="activity-form">記錄業務活動</h2>
        {!isAllowed(opportunity.allowed_actions, "record_sales_activity") ? (
          <p>目前狀態或角色不允許記錄活動。</p>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void withBusy("activity", () =>
                onRecordActivity({
                  activity_type: form.get("activity_type") as SalesActivityCreate["activity_type"],
                  occurred_at: new Date(String(form.get("occurred_at"))).toISOString(),
                  activity_evidence_asset_id: String(form.get("activity_evidence_asset_id")),
                  customer_response_evidence_id: String(form.get("customer_response_evidence_id") || "") || null,
                  needs_summary: String(form.get("needs_summary")),
                  next_action: String(form.get("next_action")),
                  next_action_due_at: new Date(String(form.get("next_action_due_at"))).toISOString(),
                  outcome: form.get("outcome") as SalesActivityCreate["outcome"],
                })
              );
            }}
          >
            <label>
              活動類型
              <select name="activity_type" required defaultValue="completed_call">
                <option value="two_way_message">雙向訊息</option>
                <option value="completed_call">完成通話</option>
                <option value="meeting">會談</option>
                <option value="needs_resolution">需求處理</option>
                <option value="proposal">提案（單向）</option>
                <option value="follow_up">追蹤（單向）</option>
              </select>
            </label>
            <label>發生時間 <input name="occurred_at" type="datetime-local" required /></label>
            <label>活動證據 asset id <input name="activity_evidence_asset_id" required /></label>
            <label>客戶回應證據 asset id（無則留空，將標記證據不足） <input name="customer_response_evidence_id" /></label>
            <label>客戶需求摘要 <textarea name="needs_summary" required maxLength={2000} /></label>
            <label>下一步 <input name="next_action" required maxLength={500} /></label>
            <label>下一步到期 <input name="next_action_due_at" type="datetime-local" required /></label>
            <label>
              結果
              <select name="outcome" required defaultValue="contacted">
                <option value="contacted">已聯繫</option>
                <option value="needs_identified">已確認需求</option>
                <option value="proposal_requested">客戶要求提案</option>
                <option value="follow_up">需再追蹤</option>
                <option value="declined">客戶拒絕</option>
                <option value="converted">已成交</option>
              </select>
            </label>
            <button type="submit" disabled={busy === "activity"}>{busy === "activity" ? "送出中…" : "記錄活動"}</button>
          </form>
        )}
      </section>

      <section aria-labelledby="next-action-form" className="m10-panel">
        <h2 id="next-action-form">指定下一步</h2>
        {!isAllowed(opportunity.allowed_actions, "set_next_action") ? (
          <p>目前狀態或角色不允許指定下一步。</p>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void withBusy("next_action", () =>
                onSetNextAction({
                  owner_id: String(form.get("owner_id")),
                  action_type: form.get("action_type") as NextActionRequest["action_type"],
                  due_at: new Date(String(form.get("due_at"))).toISOString(),
                  reason_code: String(form.get("reason_code")),
                  note: String(form.get("note") || "") || undefined,
                })
              );
            }}
          >
            <label>負責人 actor id <input name="owner_id" required /></label>
            <label>
              動作
              <select name="action_type" required defaultValue="call">
                <option value="call">致電</option>
                <option value="message">傳訊息</option>
                <option value="meeting">安排會談</option>
                <option value="complete_documents">協助補件</option>
                <option value="renewal_offer">提供續約方案</option>
                <option value="upgrade_assessment">升級適合度重評</option>
                <option value="resolve_issue">處理客服問題（抑制期間唯一允許）</option>
              </select>
            </label>
            <label>到期時間 <input name="due_at" type="datetime-local" required /></label>
            <label>原因代碼 <input name="reason_code" required maxLength={80} /></label>
            <label>備註 <textarea name="note" maxLength={2000} /></label>
            <button type="submit" disabled={busy === "next_action"}>{busy === "next_action" ? "送出中…" : "指定下一步"}</button>
          </form>
        )}
      </section>

      <section aria-labelledby="claim-form" className="m10-panel">
        <h2 id="claim-form">提出轉換認領</h2>
        {!isAllowed(opportunity.allowed_actions, "claim_conversion") ? (
          <p>{isTerminal ? "此機會已結案，不可再提出認領。" : "目前狀態或角色不允許提出認領。"}</p>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void withBusy("claim", () =>
                onClaimConversion({
                  application_id: String(form.get("application_id")),
                  contract_id: String(form.get("contract_id")),
                  finance_transaction_ref: String(form.get("finance_transaction_ref")),
                  sales_activity_ids: String(form.get("sales_activity_ids")).split(",").map((v) => v.trim()).filter(Boolean),
                  conversion_type: form.get("conversion_type") as ConversionClaim["conversion_type"],
                  claimed_salesperson_id: String(form.get("claimed_salesperson_id")),
                  reason_code: String(form.get("reason_code")),
                })
              );
            }}
          >
            <label>Application id <input name="application_id" required /></label>
            <label>交易／服務線 id（contract_id） <input name="contract_id" required /></label>
            <label>財務交易參照 <input name="finance_transaction_ref" required /></label>
            <label>引用活動 id（逗號分隔） <input name="sales_activity_ids" required /></label>
            <label>
              轉換類型
              <select name="conversion_type" required defaultValue="renewal">
                <option value="new_subscription">新申辦</option>
                <option value="renewal">續約</option>
                <option value="upgrade">升級</option>
                <option value="reactivation">重新開發</option>
              </select>
            </label>
            <label>認領業務 actor id <input name="claimed_salesperson_id" required /></label>
            <label>原因代碼 <input name="reason_code" required maxLength={80} /></label>
            <button type="submit" disabled={busy === "claim"}>{busy === "claim" ? "送出中…" : "提出認領"}</button>
          </form>
        )}
      </section>

      {conversion && (
        <section aria-labelledby="decision-form" className="m10-panel">
          <h2 id="decision-form">轉換歸因裁定（主管）</h2>
          <p>
            狀態：<strong>{conversion.state}</strong>・認領人：{conversion.claimed_salesperson_id.slice(0, 8)}…
            {conversion.approved_salesperson_id && <>・核准歸因：{conversion.approved_salesperson_id.slice(0, 8)}…</>}
          </p>
          {!isAllowed(conversion.allowed_actions, "decide_conversion") ? (
            <p>目前狀態或角色不允許裁定（可能是你本人提出的認領，需迴避）。</p>
          ) : (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                const approvedSalespersonId = String(form.get("approved_salesperson_id") || "");
                void withBusy("decide", () =>
                  onDecideConversion({
                    decision: form.get("decision") as ConversionDecision["decision"],
                    reason_code: String(form.get("reason_code")),
                    approved_salesperson_id: approvedSalespersonId || null,
                    causality_evidence_asset_id: String(form.get("causality_evidence_asset_id")),
                  })
                );
              }}
            >
              <label>
                裁定
                <select name="decision" required defaultValue="approve">
                  <option value="approve">核准（可留空核准歸因人＝自助續約）</option>
                  <option value="reject">駁回</option>
                  <option value="dispute">列爭議（待進一步處理）</option>
                </select>
              </label>
              <label>核准歸因人 actor id（留空＝self_service，不計業務獎金） <input name="approved_salesperson_id" /></label>
              <label>因果證據 asset id <input name="causality_evidence_asset_id" required /></label>
              <label>原因代碼 <input name="reason_code" required maxLength={80} /></label>
              <button type="submit" disabled={busy === "decide"}>{busy === "decide" ? "送出中…" : "送出裁定"}</button>
            </form>
          )}
        </section>
      )}
    </main>
  );
}
