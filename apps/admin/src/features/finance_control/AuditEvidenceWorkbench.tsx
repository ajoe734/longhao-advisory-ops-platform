import React, { FormEvent, useState } from "react";
import type { AuditEventRow, EvidenceExport, Page, ViewState } from "./types";

export function AuditEvidenceWorkbench({
  auditState,
  exportState,
  onCreateExport,
  onReload,
}: {
  auditState: ViewState<Page<AuditEventRow>>;
  exportState: ViewState<EvidenceExport> | null;
  onCreateExport: (scope: string, subjectId: string, purpose: string) => void;
  onReload: () => void;
}) {
  const [scope, setScope] = useState("customer");
  const [subjectId, setSubjectId] = useState("");
  const [purpose, setPurpose] = useState("internal_audit");
  if (auditState.kind === "loading") return <main aria-busy="true"><p role="status">稽核證據工作台載入中…</p></main>;
  if (auditState.kind === "denied") return <main><h1>無權限</h1><p>此資源不在你的授權範圍。</p></main>;
  if (auditState.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{auditState.problem.code}（{auditState.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (auditState.kind === "empty") return <main><p>{auditState.message}</p></main>;
  if (auditState.kind === "processing") return <main><p role="status">背景處理中，收據：{auditState.receiptId}</p></main>;
  const page = auditState.value;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (subjectId.trim()) onCreateExport(scope, subjectId.trim(), purpose);
  };
  return (
    <main className="m14-stack">
      <header className="m14-panel">
        <p className="m14-eyebrow">稽核證據工作台</p>
        <h1>依客戶／報告／訊息／付款／獎金追溯</h1>
        <p>下載包含 manifest、雜湊、來源版本與匯出者審計；受限案情依角色遮罩。截至 {new Date(page.as_of).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}。</p>
      </header>
      <section className="m14-panel m14-table-scroll" aria-labelledby="audit-events-title">
        <h2 id="audit-events-title">稽核事件（{page.total_estimate ?? page.items.length} 筆）</h2>
        <table>
          <thead><tr><th>動作</th><th>資源類型</th><th>結果</th><th>分級</th><th>時間</th></tr></thead>
          <tbody>
            {page.items.map(item => (
              <tr key={item.id}>
                <td>{item.action === "restricted" ? <span role="status">受限（已遮罩）</span> : item.action}</td>
                <td>{item.aggregate_type}</td>
                <td>{item.outcome}</td>
                <td>{item.classification}</td>
                <td>{new Date(item.occurred_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="m14-panel" aria-labelledby="evidence-export-title">
        <h2 id="evidence-export-title">建立證據匯出</h2>
        <form onSubmit={submit}>
          <label>
            範圍
            <select value={scope} onChange={event => setScope(event.target.value)}>
              <option value="customer">customer</option>
              <option value="application">application</option>
              <option value="contract">contract</option>
              <option value="publication">publication</option>
              <option value="refund">refund</option>
              <option value="commission_run">commission_run</option>
            </select>
          </label>
          <label>
            主體 ID
            <input required value={subjectId} onChange={event => setSubjectId(event.target.value)} />
          </label>
          <label>
            用途
            <select value={purpose} onChange={event => setPurpose(event.target.value)}>
              <option value="internal_audit">internal_audit</option>
              <option value="regulatory_exam">regulatory_exam</option>
              <option value="complaint_investigation">complaint_investigation</option>
              <option value="litigation">litigation</option>
            </select>
          </label>
          <button type="submit" disabled={!subjectId.trim()}>建立匯出</button>
        </form>
        {exportState?.kind === "ready" && (
          <dl>
            <dt>狀態</dt>
            <dd>{exportState.value.state}</dd>
            <dt>Manifest Hash</dt>
            <dd>{exportState.value.manifest_sha256}</dd>
            <dt>到期</dt>
            <dd>{exportState.value.expires_at}</dd>
          </dl>
        )}
        {exportState?.kind === "error" && <p role="alert">{exportState.problem.code}（{exportState.problem.correlation_id}）</p>}
      </section>
    </main>
  );
}
