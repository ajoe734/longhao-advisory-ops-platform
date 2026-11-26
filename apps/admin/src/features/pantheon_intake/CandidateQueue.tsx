import React, { FormEvent, useState } from "react";
import { isAllowed } from "./viewState";
import type { ResearchCandidate, ViewState } from "./types";

const STATE_LABEL: Record<string, string> = {
  received: "已收件",
  validating: "驗證中",
  accepted: "通過（待覆核，非正式對客）",
  quarantined: "隔離（缺證據／衝突）",
  rejected: "已拒絕",
};

export interface QueueFilters {
  state: string;
  cursor: string;
}

export function CandidateQueue({ state, onSearch, onOpen, onReject }: {
  state: ViewState<ResearchCandidate[]>;
  onSearch: (filters: QueueFilters) => void;
  onOpen: (candidateId: string) => void;
  onReject: (candidate: ResearchCandidate) => void;
}) {
  const [filters, setFilters] = useState<QueueFilters>({ state: "", cursor: "" });
  const submit = (event: FormEvent) => { event.preventDefault(); onSearch(filters); };
  return <section aria-labelledby="pantheon-queue-title" className="m05-panel">
    <h1 id="pantheon-queue-title">候選收件佇列</h1>
    <form onSubmit={submit} className="m05-filter-grid" aria-describedby="pantheon-queue-help">
      <p id="pantheon-queue-help">本畫面沒有對客戶發布按鈕；通過只表示可進入分析師覆核。</p>
      <label>狀態
        <select value={filters.state} onChange={event => setFilters({ ...filters, state: event.target.value })}>
          <option value="">全部</option>
          {Object.entries(STATE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <button type="submit">重新查詢</button>
    </form>
    <Results state={state} onOpen={onOpen} onReject={onReject} />
  </section>;
}

function Results({ state, onOpen, onReject }: {
  state: ViewState<ResearchCandidate[]>;
  onOpen: (id: string) => void;
  onReject: (candidate: ResearchCandidate) => void;
}) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看候選佇列。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const rows = state.value;
  const banner = state.kind === "stale"
    ? <p role="status" className="m05-banner-stale">資料可能已過期：{state.message}</p>
    : state.kind === "conflict"
      ? <p role="alert" className="m05-banner-conflict">{state.message}（目前版本 {state.currentVersion}）</p>
      : null;
  return <div className="m05-stack">
    {banner}
    <div className="m05-table-scroll">
      <table>
        <caption>授權範圍內的候選研究（不含已核准發布內容）</caption>
        <thead><tr>
          <th scope="col">候選</th><th scope="col">來源</th><th scope="col">狀態</th>
          <th scope="col">證據／授權旗標</th><th scope="col">收到時間</th><th scope="col">操作</th>
        </tr></thead>
        <tbody>{rows.map(candidate => <tr key={candidate.id}>
          <th scope="row">{candidate.artifact_ref.external_id}<small>{candidate.id}</small></th>
          <td>{candidate.origin === "persona_autonomous" ? "Persona 自主" : "分析師發起"}</td>
          <td>{STATE_LABEL[candidate.state] ?? candidate.state}</td>
          <td>{candidate.validation_failure_codes.length > 0
            ? <span className="m05-flag-text">⚠ {candidate.validation_failure_codes.join("、")}</span>
            : <span className="m05-flag-text">—</span>}</td>
          <td>{new Date(candidate.received_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</td>
          <td>
            <button onClick={() => onOpen(candidate.id)}>開啟</button>
            {isAllowed(candidate.allowed_actions, "reject_research_candidate") && (
              <button onClick={() => onReject(candidate)}>拒絕</button>
            )}
          </td>
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}
