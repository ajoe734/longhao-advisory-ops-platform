import React, { useCallback, useEffect, useState } from "react";
import type { M12Api } from "./api";
import { M12ApiError } from "./api";
import { failedState } from "./viewState";
import type { CommissionEntry, Page, ViewState } from "./types";

const STATE_OPTIONS = ["", "calculated", "approved", "exported", "adjusted"] as const;

/** UI-25 `/commissions/:id` — 個人獎金明細：逐筆可追算，維護 owner 不是自動領款人。 */
export function EntriesView({ api, focusEntryId }: { api: M12Api; focusEntryId?: string }) {
  const [stateFilter, setStateFilter] = useState<string>("");
  const [limit, setLimit] = useState(50);
  const [page, setPage] = useState<ViewState<Page<CommissionEntry>>>({ kind: "loading" });

  const load = useCallback(async (cursor?: string) => {
    setPage(prev => (prev.kind === "ready" && cursor ? prev : { kind: "loading" }));
    try {
      const result = await api.listCommissionEntries({ state: stateFilter || undefined, cursor, limit });
      setPage(prevState => {
        if (cursor && prevState.kind === "ready") {
          return { kind: "ready", value: { ...result, items: [...prevState.value.items, ...result.items] } };
        }
        return result.items.length === 0
          ? { kind: "empty", message: "此授權範圍目前沒有符合條件的獎金明細。" }
          : { kind: "ready", value: result };
      });
    } catch (error) {
      if (error instanceof M12ApiError && error.problem.status === 403) {
        setPage({ kind: "denied", correlationId: error.problem.correlation_id });
        return;
      }
      setPage(failedState<Page<CommissionEntry>>(error));
    }
  }, [api, stateFilter, limit]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section aria-labelledby="m12-entries-title" className="m12-panel">
      <h2 id="m12-entries-title">獎金明細</h2>
      <p>收入／退款、原來源、policy 與 marketing/sales/company 分配逐筆可追算；維護 owner 不是自動領款人。授權範圍由伺服器決定——業務角色僅見自己的明細。</p>
      <form className="m12-form-grid" onSubmit={e => { e.preventDefault(); load(); }} aria-label="篩選獎金明細">
        <label>狀態<select value={stateFilter} onChange={e => setStateFilter(e.target.value)}>
          {STATE_OPTIONS.map(option => <option key={option} value={option}>{option || "全部"}</option>)}
        </select></label>
        <label>每頁筆數<input type="number" min={1} max={200} value={limit} onChange={e => setLimit(Number(e.target.value) || 50)} /></label>
        <button type="submit">重新查詢</button>
      </form>
      <EntriesResult state={page} focusEntryId={focusEntryId} onLoadMore={cursor => load(cursor)} />
    </section>
  );
}

function EntriesResult({ state, focusEntryId, onLoadMore }: {
  state: ViewState<Page<CommissionEntry>>;
  focusEntryId?: string;
  onLoadMore: (cursor: string) => void;
}) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看獎金明細。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）{state.problem.retryable ? "，可重試" : ""}</p>;
  const page = state.kind === "ready" || state.kind === "conflict" || state.kind === "stale"
    ? state.value
    : state.kind === "processing"
    ? state.value
    : undefined;
  if (!page) return null;
  return <div>
    <p>as_of：{new Date(page.as_of).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}{page.total_estimate != null && ` ・ 估計筆數：${page.total_estimate}`}</p>
    <div className="m12-table-scroll">
      <table>
        <caption>授權範圍內的獎金明細（原始列不可改，退款以 linked adjustment 沖回）</caption>
        <thead><tr>
          <th scope="col">id</th><th scope="col">狀態</th><th scope="col">受款類型</th><th scope="col">受款人</th>
          <th scope="col">金額</th><th scope="col">policy_id</th><th scope="col">規則軌跡</th>
        </tr></thead>
        <tbody>{page.items.map(entry => <tr key={entry.id} data-focused={entry.id === focusEntryId ? "true" : undefined}>
          <th scope="row">{entry.id}<small>run {entry.run_id}</small></th>
          <td>{entry.state}</td>
          <td>{entry.recipient_type}</td>
          <td>{entry.recipient_id ?? "（公司留存）"}</td>
          <td>{entry.amount.amount} {entry.amount.currency}</td>
          <td>{entry.calculation.policy_id}</td>
          <td>{entry.calculation.rule_trace_codes.join(", ")}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {page.next_cursor && <button onClick={() => onLoadMore(page.next_cursor as string)}>載入更多</button>}
  </div>;
}
