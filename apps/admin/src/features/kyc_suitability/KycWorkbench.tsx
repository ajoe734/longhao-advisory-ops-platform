import React from "react";
import type { DocumentStatus, KycSubmission, ViewState } from "./types";
import { isAllowed } from "./viewState";

const STATE_LABEL: Record<KycSubmission["state"], string> = {
  not_started: "尚未開始",
  in_progress: "填寫中",
  submitted: "已送出，待審核",
  incomplete: "缺件，待補齊",
  under_review: "審核中",
  approved: "已核准",
  rejected: "已拒絕",
  refresh_required: "需重新審核",
};

export function KycWorkbench({
  state,
  documents,
  onReload,
  onDecide,
}: {
  state: ViewState<KycSubmission>;
  documents: DocumentStatus[];
  onReload: () => void;
  onDecide: (decision: "approve" | "reject" | "request_information") => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入 KYC 案件…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此案件不存在或不在你的授權範圍；業務角色只能查看補件狀態。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;

  const submission = state.value;
  const canDecide = isAllowed(submission.allowed_actions, "decide_kyc");
  return (
    <main className="m03-stack">
      {state.kind === "conflict" && (
        <aside role="alert">
          <strong>版本衝突</strong>
          <p>{state.message} 伺服器版本：{state.currentVersion}</p>
          <button onClick={onReload}>重載並比較</button>
        </aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
      <header className="m03-panel">
        <p className="m03-eyebrow">KYC 審核工作台</p>
        <h1>{STATE_LABEL[submission.state]}</h1>
        <p>案件 {submission.id} · 申請 {submission.application_id}</p>
        {submission.next_review_due_at && <p>下次覆核到期：{new Date(submission.next_review_due_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</p>}
      </header>
      <section aria-labelledby="missing-items" className="m03-panel">
        <h2 id="missing-items">缺件項目</h2>
        {submission.missing_field_codes.length === 0 ? <p>無缺件。</p> : (
          <ul>{submission.missing_field_codes.map(code => <li key={code}>{code}</li>)}</ul>
        )}
      </section>
      <section aria-labelledby="documents" className="m03-panel">
        <h2 id="documents">證明文件隔離區掃描狀態</h2>
        {documents.length === 0 ? <p>尚無上傳文件。</p> : (
          <div className="m03-table-scroll">
            <table>
              <thead><tr><th>文件</th><th>掃描狀態</th><th>原因</th></tr></thead>
              <tbody>
                {documents.map(doc => (
                  <tr key={doc.asset_id}>
                    <td>{doc.asset_id}</td>
                    <td>{doc.scan_status === "pending" ? "掃描中，尚不可審核" : doc.scan_status === "clean" ? "已通過" : "已阻擋"}</td>
                    <td>{doc.rejection_code ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section aria-labelledby="decision" className="m03-panel">
        <h2 id="decision">審核決定</h2>
        <div className="m03-actions">
          <button disabled={!canDecide} title={!canDecide ? "目前狀態或角色不允許審核" : undefined} onClick={() => onDecide("approve")}>核准</button>
          <button disabled={!canDecide} title={!canDecide ? "目前狀態或角色不允許審核" : undefined} onClick={() => onDecide("reject")}>拒絕</button>
          <button disabled={!canDecide} title={!canDecide ? "目前狀態或角色不允許審核" : undefined} onClick={() => onDecide("request_information")}>要求補件</button>
        </div>
      </section>
    </main>
  );
}
