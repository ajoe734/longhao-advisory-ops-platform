import React from "react";
import type { RecommendationSeries, ViewState } from "./types";
import { isAllowed } from "./viewState";

export function RecommendationSeriesView({
  state,
  onReload,
  onCreateVersion,
}: {
  state: ViewState<RecommendationSeries>;
  onReload: () => void;
  onCreateVersion: () => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入建議序列…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此序列不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;
  const series = state.value;
  const canCreateVersion = isAllowed(series.allowed_actions, "create_recommendation_version");
  return (
    <main className="m08-stack">
      {state.kind === "conflict" && (
        <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>部分資料已過期</strong><p>{state.message}</p></aside>}
      <header className="m08-panel">
        <p className="m08-eyebrow">建議序列 · {series.state === "open" ? "進行中" : series.state === "closed" ? "已結束" : "已撤回"}</p>
        <h1>{series.instrument_ref.market} {series.instrument_ref.instrument_code}</h1>
        <p>{series.id} · 策略範圍：{series.strategy_scope_ref}</p>
        <button
          disabled={!canCreateVersion}
          title={!canCreateVersion ? "目前狀態或角色不允許新增建議版次" : undefined}
          onClick={onCreateVersion}
        >
          新增建議版次（entry / update / exit）
        </button>
      </header>
      <section aria-labelledby="series-latest" className="m08-panel">
        <h2 id="series-latest">最新版次</h2>
        {series.latest_version_id ? (
          <p>最新版次 ID：{series.latest_version_id}（進場、更新、出場各版次的簽章與發布狀態見發布監控畫面）</p>
        ) : (
          <p>尚無任何建議版次。</p>
        )}
      </section>
      <section aria-labelledby="series-notice" className="m08-panel notice">
        <h2 id="series-notice">時序提醒</h2>
        <p>本畫面只顯示研究依據建立時間，不代表客戶已收到或成交；實際建議以簽章後自動發布時間為準。</p>
      </section>
    </main>
  );
}
