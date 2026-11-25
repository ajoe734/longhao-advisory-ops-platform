import React, { useState } from "react";
import type { Contract, Entitlement, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function ContractEntitlementDetail({
  contractState, entitlementsState, onSuspend, onTerminate, onReload,
}: {
  contractState: ViewState<Contract>;
  entitlementsState: ViewState<Entitlement[]>;
  onSuspend: (entitlementId: string, etag: string, reasonCode: string, evidenceRef: string) => void;
  onTerminate: (reasonCode: string, evidenceId: string) => void;
  onReload: () => void;
}) {
  const [reasonCode, setReasonCode] = useState("");
  const [evidenceRef, setEvidenceRef] = useState("");
  if (contractState.kind === "loading") return <p role="status">契約與權限明細載入中…</p>;
  if (contractState.kind === "denied") return <p role="alert">無權限查看此契約。</p>;
  if (contractState.kind === "error") return <p role="alert">{contractState.problem.code}（{contractState.problem.correlation_id}）</p>;
  if (contractState.kind === "empty") return <p>{contractState.message}</p>;

  const contract = contractState.value;
  const canTerminate = isAllowed(contract.allowed_actions, "terminate_contract");

  return (
    <section className="m02-panel" aria-labelledby="contract-detail-title">
      <h1 id="contract-detail-title">契約與權限明細</h1>
      {contractState.kind === "conflict" && <div role="alert"><p>{contractState.message}</p><button onClick={onReload}>重載版本</button></div>}
      {contractState.kind === "processing" && <p role="status">處理中，收據：{contractState.receiptId}；長任務不代表已完成，請以查詢結果為準。</p>}
      <dl>
        <dt>契約編號</dt><dd>{contract.id}</dd>
        <dt>狀態</dt><dd>{contract.state}</dd>
        <dt>生效時間</dt><dd>{contract.effective_at ?? "尚未生效"}</dd>
        <dt>終止時間</dt><dd>{contract.terminated_at ?? "未終止"}</dd>
        <dt>服務期間</dt><dd>{contract.service_starts_at ?? "—"} ～ {contract.service_ends_at ?? "—"}</dd>
      </dl>

      <h2>服務權限明細</h2>
      {entitlementsState.kind === "loading" && <p role="status">權限資料載入中…</p>}
      {entitlementsState.kind === "denied" && <p role="alert">無權限查看此客戶之服務權限。</p>}
      {entitlementsState.kind === "error" && <p role="alert">{entitlementsState.problem.code}（{entitlementsState.problem.correlation_id}）</p>}
      {entitlementsState.kind === "empty" && <p>{entitlementsState.message}</p>}
      {(entitlementsState.kind === "ready" || entitlementsState.kind === "stale") && (
        <div className="m02-table-scroll">
          {entitlementsState.kind === "stale" && <p role="status">{entitlementsState.message}</p>}
          <table>
            <caption>每服務獨立計算；一服務失效不會使其他合法服務一併停用</caption>
            <thead><tr><th scope="col">服務</th><th scope="col">狀態</th><th scope="col">有效區間</th><th scope="col">最新決策</th><th scope="col">操作</th></tr></thead>
            <tbody>
              {entitlementsState.value.map((entitlement) => {
                const canSuspend = isAllowed(entitlement.allowed_actions, "suspend_entitlement");
                return (
                  <tr key={entitlement.id}>
                    <td>{entitlement.service_code}</td>
                    <td>{entitlement.state}{entitlement.restriction_codes.length > 0 ? `（${entitlement.restriction_codes.join("、")}）` : ""}</td>
                    <td>{entitlement.valid_from} ～ {entitlement.valid_until}</td>
                    <td>{entitlement.last_decision_id}</td>
                    <td>
                      <button
                        disabled={!canSuspend || !reasonCode.trim() || !evidenceRef.trim()}
                        onClick={() => onSuspend(entitlement.id, `"${entitlement.version}"`, reasonCode.trim(), evidenceRef.trim())}
                      >
                        暫停
                      </button>
                      {!canSuspend && <small>不具暫停權限的原因由伺服器決定，按鈕不可操作。</small>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <form aria-label="手動例外操作">
        <label>原因代碼<input required maxLength={80} value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} /></label>
        <label>證據 ID<input required value={evidenceRef} onChange={(event) => setEvidenceRef(event.target.value)} /></label>
        <button
          disabled={!canTerminate || !reasonCode.trim() || !evidenceRef.trim()}
          onClick={(event) => { event.preventDefault(); onTerminate(reasonCode.trim(), evidenceRef.trim()); }}
        >
          終止契約
        </button>
        <p>終止僅停止對應服務；契約與簽署原件仍可稽核，受保全物件不會被刪除。手動暫停／終止需獨立權限、理由與證據，不能略過法遵 mandatory gate。</p>
      </form>
    </section>
  );
}
