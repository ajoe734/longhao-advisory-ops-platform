import React, { FormEvent, useState } from "react";
import type { ReviewCase, ViewState } from "./types";

export interface QueueFilters { state: string; }

export function ReviewQueue({ state, onSearch, onOpen }: {
  state: ViewState<ReviewCase[]>;
  onSearch: (filters: QueueFilters) => void;
  onOpen: (reviewCaseId: string) => void;
}) {
  const [filters, setFilters] = useState<QueueFilters>({ state: "" });
  const submit = (event: FormEvent) => { event.preventDefault(); onSearch(filters); };
  return <section aria-labelledby="review-queue-title" className="m06-panel">
    <h1 id="review-queue-title">研究覆核工作佇列</h1>
    <form onSubmit={submit} className="m06-search-grid" aria-describedby="review-queue-help">
      <p id="review-queue-help">僅顯示授權範圍內的候選；可用操作由伺服器決定。</p>
      <label>狀態
        <select value={filters.state} onChange={event => setFilters({ state: event.target.value })}>
          <option value="">全部</option>
          <option value="queued">待接案</option>
          <option value="assigned">已指派</option>
          <option value="under_review">審查中</option>
          <option value="revision_requested">待修訂</option>
          <option value="stale">來源已過時</option>
        </select>
      </label>
      <button type="submit">查詢</button>
    </form>
    <Results state={state} onOpen={onOpen} />
  </section>;
}

function Results({ state, onOpen }: { state: ViewState<ReviewCase[]>; onOpen: (id: string) => void }) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看此佇列。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const rows = state.value;
  return <div className="m06-table-scroll"><table><caption>授權範圍內的覆核案件</caption>
    <thead><tr><th scope="col">案件</th><th scope="col">狀態</th><th scope="col">指派人</th><th scope="col">更新時間</th><th scope="col">操作</th></tr></thead>
    <tbody>{rows.map(item => <tr key={item.id}>
      <th scope="row">{item.id}<small>candidate: {item.candidate_id}</small></th>
      <td>{item.state}{item.source_version_stale && <strong> · 來源已過時</strong>}</td>
      <td>{item.assignee_id ?? "未指派"}</td>
      <td>{new Date(item.updated_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</td>
      <td><button onClick={() => onOpen(item.id)}>開啟</button></td>
    </tr>)}</tbody>
  </table></div>;
}
