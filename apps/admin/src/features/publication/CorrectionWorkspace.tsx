import React, { useState } from "react";
import type { Publication, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function CorrectionWorkspace({
  original,
  replacement,
  onReload,
  onSubmit,
}: {
  original: ViewState<Publication>;
  replacement: ViewState<Publication> | null;
  onReload: () => void;
  onSubmit: (replacementPublicationId: string, replacementReportVersionId: string, reasonCode: string) => void;
}) {
  const [replacementPublicationId, setReplacementPublicationId] = useState("");
  const [reasonCode, setReasonCode] = useState("");

  if (original.kind === "loading") return <main aria-busy="true"><p role="status">正在載入原發布內容…</p></main>;
  if (original.kind === "denied") return <main><h1>無權限</h1><p>此發布不存在或不在你的授權範圍。</p></main>;
  if (original.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{original.problem.code}（{original.problem.correlation_id}）</p></main>;
  if (original.kind === "processing") return <main><p role="status">背景處理中，收據：{original.receiptId}</p></main>;
  if (original.kind === "empty") return <main><p>{original.message}</p></main>;

  const originalPublication = original.value;
  const canCorrect = isAllowed(originalPublication.allowed_actions, "link_publication_correction");
  const replacementPublication =
    replacement && (replacement.kind === "ready" || replacement.kind === "stale" || replacement.kind === "conflict") ? replacement.value : null;
  // The replacement actually looked up must still be the one visible in the
  // input; editing the id after a lookup must not let a stale lookup result
  // (a different, already-validated publication) get submitted alongside it.
  const replacementMatchesInput = replacementPublication !== null && replacementPublicationId.trim() === replacementPublication.id;

  return (
    <main className="m08-stack">
      {original.kind === "conflict" && (
        <aside role="alert"><strong>版本衝突</strong><p>{original.message} 伺服器版本：{original.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>
      )}
      {original.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{original.message}</p><button onClick={onReload}>重新載入</button></aside>}
      <header className="m08-panel">
        <p className="m08-eyebrow">更正工作頁</p>
        <h1>原發布 {originalPublication.id}</h1>
        <p>已送內容永久可稽核；更正不會覆寫原訊息，只會新增關聯。</p>
      </header>
      <section className="m08-correction-grid">
        <article aria-labelledby="original-heading" className="m08-panel">
          <h2 id="original-heading">原內容</h2>
          <p>狀態：{originalPublication.state}</p>
          <p>受眾人數：{originalPublication.recipient_count}</p>
          <p>報告版本：{originalPublication.report_version_id}</p>
          <p>有效期限：{new Date(originalPublication.valid_until).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</p>
        </article>
        <article aria-labelledby="replacement-heading" className="m08-panel">
          <h2 id="replacement-heading">更正內容（需為已發布的新版本）</h2>
          {replacementPublication ? (
            <>
              <p>狀態：{replacementPublication.state}</p>
              <p>報告版本：{replacementPublication.report_version_id}</p>
              {!replacementMatchesInput && (
                <p role="status">輸入的 ID 已變更，請重新查詢後再送出。</p>
              )}
            </>
          ) : (
            <p>請先輸入已簽署且已發布的替代發布 ID。</p>
          )}
        </article>
      </section>
      <form
        className="m08-panel"
        aria-label="建立更正關聯"
        onSubmit={(event) => {
          event.preventDefault();
          if (replacementPublication && replacementMatchesInput && reasonCode.trim()) {
            onSubmit(replacementPublication.id, replacementPublication.report_version_id, reasonCode.trim());
          }
        }}
      >
        <label htmlFor="replacement-publication-id">替代發布 ID</label>
        <input
          id="replacement-publication-id"
          value={replacementPublicationId}
          onChange={(event) => setReplacementPublicationId(event.target.value)}
          disabled={!canCorrect}
        />
        <label htmlFor="correction-reason">更正理由</label>
        <input id="correction-reason" value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} disabled={!canCorrect} maxLength={80} />
        <button type="submit" disabled={!canCorrect || !replacementMatchesInput || !reasonCode.trim()}>
          建立更正連結
        </button>
        {!canCorrect && <p role="status">目前狀態或角色不允許建立更正；只有已發布或部分失敗的訊息可被更正。</p>}
      </form>
    </main>
  );
}
