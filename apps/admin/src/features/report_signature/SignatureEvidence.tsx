import React from "react";
import type { ReportVersion, SignatureRevocation, ViewState } from "./types";
import { isAllowed } from "./viewState";

export interface SignatureEvidenceModel {
  version: ReportVersion;
  revocations: SignatureRevocation[];
}

export function SignatureEvidence({ state, onRetrySignature }: {
  state: ViewState<SignatureEvidenceModel>;
  onRetrySignature: () => void;
}) {
  if (state.kind === "loading") return <p role="status">簽章證據頁載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限或版本不存在。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">簽章驗證處理中，收據：{state.receiptId}</p>;
  const { version, revocations } = state.value;
  // Receiving a provider callback is not the same as a verified signature; only
  // state "signed" may ever claim the report has been published downstream.
  const isPublished = version.state === "signed" && revocations.length === 0;
  // The backend rejects a blind re-sign while a provider callback is still outstanding
  // (signature_pending); a resend is only ever legitimate once the server has actually
  // put the version back on "ready_to_sign" and granted request_analyst_signature again.
  const canRetry = version.state === "ready_to_sign" && isAllowed(version.allowed_actions, "request_analyst_signature");
  return <section className="m07-panel" aria-labelledby="signature-evidence-title">
    <h1 id="signature-evidence-title">簽章證據</h1>
    {state.kind === "stale" && <aside role="status"><strong>資料可能已過期</strong><p>{state.message}</p></aside>}
    <dl>
      <dt>content hash</dt><dd>{version.report_content_sha256}</dd>
      <dt>signed-file hash</dt><dd>{version.signed_file_sha256 ?? "尚未產生"}</dd>
      <dt>package（bundle manifest）hash</dt><dd>{version.bundle_manifest_sha256}</dd>
      <dt>provider ref</dt><dd>{version.signature_id ?? "尚無已驗證簽章"}</dd>
    </dl>
    {isPublished
      ? <p role="status">已驗證簽章並已發布。</p>
      : <p role="alert">尚未發布：收到簽章 callback 不代表已驗證或已發布，以 {version.state} 狀態為準。</p>}
    {version.state === "signature_pending" && (
      <p role="status">簽章驗證處理中，provider 回呼尚未完成；逾時或失敗後伺服器會自動將版本釋回可再送簽狀態，此處不提供手動重送。</p>
    )}
    {canRetry && (
      <div>
        <p role="status">簽章驗證失敗或逾時可查原因並重新送簽，frozen 內容不會因此被覆寫。</p>
        <button disabled={state.kind === "stale"} onClick={onRetrySignature}>重新送簽</button>
      </div>
    )}
    <section aria-labelledby="revocation-history-title">
      <h2 id="revocation-history-title">撤銷歷史</h2>
      {revocations.length === 0
        ? <p>尚無撤銷紀錄。</p>
        : <ul>
            {revocations.map(revocation => (
              <li key={revocation.id}>
                {revocation.effective_at} · {revocation.reason_code} · 撤銷人 {revocation.revoked_by} · 下游撤回事件 {revocation.publication_withdrawal_event_id}
              </li>
            ))}
          </ul>}
    </section>
  </section>;
}
