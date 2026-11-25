import React from "react";
import type { ContractPackage, SignatureSession, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function SignaturePanel({
  packageState, sessionState, onStartSigning, onReload,
}: {
  packageState: ViewState<ContractPackage>;
  sessionState: ViewState<SignatureSession> | null;
  onStartSigning: () => void;
  onReload: () => void;
}) {
  if (packageState.kind === "loading") return <p role="status">契約文件載入中…</p>;
  if (packageState.kind === "denied") return <p role="alert">無權限查看此契約文件；請聯絡業務單位。</p>;
  if (packageState.kind === "error") return <p role="alert">{packageState.problem.code}（{packageState.problem.correlation_id}）</p>;
  if (packageState.kind === "empty") return <p>{packageState.message}</p>;

  const contractPackage = packageState.value;
  const canSign = isAllowed(contractPackage.allowed_actions, "create_customer_signature") && contractPackage.state === "approved";

  return (
    <section className="m02-panel" aria-labelledby="signature-title">
      <h1 id="signature-title">客戶簽署頁</h1>
      {packageState.kind === "conflict" && (
        <div role="alert"><p>{packageState.message}</p><button onClick={onReload}>重載版本</button></div>
      )}
      {packageState.kind === "stale" && <p role="status">{packageState.message}</p>}
      <dl>
        <dt>方案代碼</dt><dd>{contractPackage.package_code}（{contractPackage.version_label}）</dd>
        <dt>文件版本狀態</dt><dd>{contractPackage.state === "approved" ? "已核准，可供簽署" : contractPackage.state === "retired" ? "已停用，不可簽署" : "尚未核准"}</dd>
        <dt>生效起始</dt><dd>{contractPackage.effective_from}</dd>
      </dl>
      <h2>完整原件</h2>
      <ul>
        {contractPackage.documents.map((doc) => (
          <li key={doc.asset_id}>
            {doc.media_type}（{doc.classification}）— 掃描狀態：{doc.scan_status === "clean" ? "乾淨" : doc.scan_status === "rejected" ? "已拒絕" : "待掃描"}
          </li>
        ))}
      </ul>
      <p>完成本頁簽署後，申辦將進入 KYC；公司尚未完成接受前，服務權限維持鎖定（locked），不會提前開通。</p>
      {!sessionState && (
        <button disabled={!canSign} onClick={onStartSigning}>開始線上簽署</button>
      )}
      {!canSign && !sessionState && <p>目前方案尚未核准完成或您不具簽署資格，無法開始簽署。</p>}
      {sessionState && sessionState.kind === "processing" && <p role="status">簽署受理中，收據：{sessionState.receiptId}（202 僅表示已受理，請等待驗證完成）</p>}
      {sessionState && sessionState.kind === "ready" && sessionState.value.status === "pending" && (
        <p role="status">已建立簽署連結，請前往 <a href={sessionState.value.redirect_url}>簽署頁面</a> 完成簽署。</p>
      )}
      {sessionState && sessionState.kind === "ready" && sessionState.value.status === "verified" && <p role="status">簽署已驗證成功，申辦已推進，請繼續完成 KYC。</p>}
      {sessionState && sessionState.kind === "ready" && (sessionState.value.status === "failed" || sessionState.value.status === "expired") && (
        <p role="alert">簽署驗證未成功（{sessionState.value.status}），可重新發起簽署。</p>
      )}
      {sessionState && sessionState.kind === "error" && <p role="alert">{sessionState.problem.code}（{sessionState.problem.correlation_id}）</p>}
    </section>
  );
}
