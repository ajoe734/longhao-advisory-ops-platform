import React from "react";
import type { ReportVersion, ViewState } from "./types";
import { isAllowed } from "./viewState";

export interface SigningCredential {
  signerDisplayName: string;
  credentialId: string;
  qualificationStatus: "qualified" | "unknown";
}

export function SignatureConfirmation({ state, signer, onConfirmSign, onReload }: {
  state: ViewState<ReportVersion>;
  signer: SigningCredential;
  onConfirmSign: () => void;
  onReload: () => void;
}) {
  if (state.kind === "loading") return <p role="status">簽署確認頁載入中…</p>;
  if (state.kind === "denied") return <p role="alert">無權限或版本不存在。</p>;
  if (state.kind === "error") return <p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "processing") return <p role="status">簽署請求處理中，收據：{state.receiptId}</p>;
  const version = state.value;
  const allowed = isAllowed(version.allowed_actions, "request_analyst_signature");
  // A stale read must never let signing proceed on data that may no longer match the
  // server's current version; the signer has to reload and re-confirm the latest content.
  const isStale = state.kind === "stale";
  const missingReason = isStale
    ? "顯示資料可能已過期，請重載後再簽署"
    : version.state !== "ready_to_sign"
      ? "缺任一必要覆核或凍結尚未完成，暫不能簽署"
      : !allowed
        ? "目前角色或資格不允許簽署"
        : signer.qualificationStatus !== "qualified"
          ? "目前簽署者資格未確認，暫不能簽署"
          : undefined;
  return <section className="m07-panel" aria-labelledby="sign-confirm-title">
    <h1 id="sign-confirm-title">簽署確認</h1>
    {state.kind === "conflict" && <div role="alert"><p>{state.message}</p><button onClick={onReload}>重載版本</button></div>}
    {isStale && <aside role="status"><strong>資料可能已過期</strong><p>{state.message}</p><button onClick={onReload}>重載版本</button></aside>}
    <dl>
      <dt>將簽的精確版本</dt><dd>{version.id}（v{version.version_number}）</dd>
      <dt>內容雜湊</dt><dd>{version.report_content_sha256}</dd>
      <dt>訊息包雜湊</dt><dd>{version.bundle_manifest_sha256}</dd>
      <dt>有效期限</dt><dd>{version.valid_from} — {version.valid_until}</dd>
      <dt>簽署者</dt><dd>{signer.signerDisplayName}</dd>
      <dt>有效資格</dt><dd>{signer.credentialId}（{signer.qualificationStatus === "qualified" ? "資格有效" : "資格未確認"}）</dd>
    </dl>
    <section aria-labelledby="sign-channels-title">
      <h2 id="sign-channels-title">所有渠道文案與受眾規則</h2>
      <ul>
        {version.channel_texts.map(text => <li key={text.channel}><strong>{text.channel}</strong>：{text.body}</li>)}
      </ul>
      <p>受眾服務範圍：{version.audience_policy.service_codes.join("、")}（需 entitlement 狀態：{version.audience_policy.required_entitlement_state}）</p>
    </section>
    <p role="status" className="m07-auto-publish-notice">簽署後自動發布；成功驗證簽章後系統會自動依上列通路與受眾規則發布，沒有另外的人工發布步驟。</p>
    <button disabled={!allowed || version.state !== "ready_to_sign" || isStale || signer.qualificationStatus !== "qualified"} title={missingReason} onClick={onConfirmSign}>
      確認簽署（簽署後自動發布）
    </button>
  </section>;
}
