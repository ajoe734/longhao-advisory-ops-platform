import React from "react";
import type { Page, Publication, ViewState } from "./types";
import { isAllowed } from "./viewState";

const STATE_LABEL: Record<Publication["state"], string> = {
  queued: "排隊中",
  publishing: "發布中",
  published: "已發布",
  partially_failed: "部分失敗",
  expired: "已過期",
  corrected: "已更正",
  withdrawn: "已撤回",
};

export function PublicationMonitor({
  state,
  onReload,
  onWithdraw,
  onLinkCorrection,
}: {
  state: ViewState<Page<Publication>>;
  onReload: () => void;
  onWithdraw: (publication: Publication) => void;
  onLinkCorrection: (publication: Publication) => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入發布監控…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此清單不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.value.items.length === 0) {
    return <main><p>目前沒有符合條件的發布紀錄；候選或未簽稿不會出現在此清單。</p></main>;
  }
  const page = state.value;
  return (
    <main className="m08-stack">
      {state.kind === "conflict" && (
        <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
      <header className="m08-panel">
        <p className="m08-eyebrow">發布監控</p>
        <h1>由簽章到各通路結果</h1>
        <p>快照時間（as_of）：{new Date(page.as_of).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</p>
      </header>
      <table className="m08-table" aria-label="發布清單">
        <thead>
          <tr>
            <th scope="col">發布 ID</th>
            <th scope="col">狀態</th>
            <th scope="col">受眾人數</th>
            <th scope="col">發布時間</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          {page.items.map((publication) => {
            const canWithdraw = isAllowed(publication.allowed_actions, "withdraw_publication");
            const canCorrect = isAllowed(publication.allowed_actions, "link_publication_correction");
            return (
              <tr key={publication.id}>
                <td>{publication.id}</td>
                <td>
                  <span className={`m08-badge ${publication.state}`}>{STATE_LABEL[publication.state]}</span>
                  {publication.withdrawal_reason_code && <span> · 理由：{publication.withdrawal_reason_code}</span>}
                </td>
                <td>{publication.recipient_count}</td>
                <td>{publication.published_at ? new Date(publication.published_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" }) : "尚未發布"}</td>
                <td>
                  <button disabled={!canWithdraw} title={!canWithdraw ? "目前狀態或角色不允許撤回" : undefined} onClick={() => onWithdraw(publication)}>
                    撤回
                  </button>{" "}
                  <button disabled={!canCorrect} title={!canCorrect ? "目前狀態或角色不允許建立更正連結" : undefined} onClick={() => onLinkCorrection(publication)}>
                    建立更正
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <section className="m08-panel notice">
        <p>發布代表平台已有效建立訊息，不等同各通路實際送達或已讀；通路結果另見交付模組。</p>
      </section>
    </main>
  );
}
