import { useState } from "react";
import type { Delivery, ViewState } from "./types";
import { isAllowed } from "./viewState";

/**
 * Expiry and live entitlement are not part of the Delivery wire contract
 * (api/openapi.yaml only exposes id/state/attempt/timestamps). The server,
 * not this screen, re-checks both before accepting a reconcile or redrive;
 * this screen only surfaces whatever reason the server returns rather than
 * guessing expiry/entitlement from stale client-side data.
 */
export function RedriveReconciliation({
  state,
  onReconcile,
  onRedrive,
}: {
  state: ViewState<Delivery>;
  onReconcile: (reasonCode: string) => void;
  onRedrive: (reasonCode: string, approvedBy: string, expectedEventId: string) => void;
}) {
  const [reasonCode, setReasonCode] = useState("");
  const [approvedBy, setApprovedBy] = useState("");
  const [expectedEventId, setExpectedEventId] = useState("");

  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入投遞明細…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此資源不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;

  const delivery = state.value;
  const canReconcile = isAllowed(delivery.allowed_actions, "reconcile_delivery");
  const canRedrive = isAllowed(delivery.allowed_actions, "redrive_delivery_event");
  const reasonValid = reasonCode.trim().length > 0 && reasonCode.trim().length <= 80;
  const redriveDisabled = !canRedrive || !reasonValid || !approvedBy || !expectedEventId;
  const redriveDisabledReason = !canRedrive ? "目前狀態或角色不允許重驅" : undefined;

  return (
    <main className="m09-stack">
      {state.kind === "conflict" && (
        <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p></aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>資料可能已過期</strong><p>{state.message}</p></aside>}
      <header className="m09-panel">
        <p className="m09-eyebrow">重驅與收件對帳</p>
        <h1>{delivery.publication_id}</h1>
        <p role="status">目前狀態：{delivery.state}・已嘗試 {delivery.attempt_count} 次・上次 provider ref：{delivery.provider_message_ref ?? "未提供"}</p>
        {delivery.last_error_code && <p role="alert">上次錯誤代碼：{delivery.last_error_code}</p>}
      </header>
      <section aria-labelledby="reconcile-form" className="m09-panel">
        <h2 id="reconcile-form">查詢供應商結果後再判斷是否安全重送</h2>
        <label htmlFor="m09-reason">對帳原因（必填，1–80字）</label>
        <input id="m09-reason" value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} maxLength={80} />
        <button disabled={!canReconcile || !reasonValid} title={!canReconcile ? "目前狀態或角色不允許對帳" : undefined} onClick={() => onReconcile(reasonCode.trim())}>
          查詢供應商並對帳
        </button>
      </section>
      <section aria-labelledby="redrive-form" className="m09-panel">
        <h2 id="redrive-form">重驅死信（僅限已進入 dead letter 的投遞工作）</h2>
        <p>不提供一鍵盲發全部；伺服器會重新核對內容有效期與目前權限，過期或權限未生效會被拒絕並留稽核。</p>
        <label htmlFor="m09-approved-by">核准者 ID</label>
        <input id="m09-approved-by" value={approvedBy} onChange={(event) => setApprovedBy(event.target.value)} />
        <label htmlFor="m09-expected-event">預期 dead-letter 事件 ID</label>
        <input id="m09-expected-event" value={expectedEventId} onChange={(event) => setExpectedEventId(event.target.value)} />
        <button
          disabled={redriveDisabled}
          title={redriveDisabledReason}
          onClick={() => onRedrive(reasonCode.trim(), approvedBy.trim(), expectedEventId.trim())}
        >
          送出重驅申請
        </button>
      </section>
    </main>
  );
}
