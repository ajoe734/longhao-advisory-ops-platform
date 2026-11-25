import React, { FormEvent, useState } from "react";
import type { Customer, ViewState } from "./types";

export interface SearchFilters { customerNumber: string; name: string; phoneTail: string; }

export function CustomerSearch({ state, onSearch, onOpen }: {
  state: ViewState<Customer[]>;
  onSearch: (filters: SearchFilters) => void;
  onOpen: (customerId: string) => void;
}) {
  const [filters, setFilters] = useState<SearchFilters>({ customerNumber: "", name: "", phoneTail: "" });
  const submit = (event: FormEvent) => { event.preventDefault(); onSearch(filters); };
  return <section aria-labelledby="customer-search-title" className="m01-panel">
    <h1 id="customer-search-title">客戶搜尋</h1>
    <form onSubmit={submit} className="m01-search-grid" aria-describedby="customer-search-help">
      <p id="customer-search-help">結果只顯示遮罩資料；搜尋範圍由伺服器授權決定。</p>
      <label>客戶編號<input value={filters.customerNumber} onChange={event => setFilters({ ...filters, customerNumber: event.target.value })} /></label>
      <label>姓名<input value={filters.name} onChange={event => setFilters({ ...filters, name: event.target.value })} /></label>
      <label>電話末四碼<input inputMode="numeric" maxLength={4} value={filters.phoneTail} onChange={event => setFilters({ ...filters, phoneTail: event.target.value.replace(/\D/g, "") })} /></label>
      <button type="submit">搜尋</button>
    </form>
    <Results state={state} onOpen={onOpen} />
  </section>;
}

function Results({ state, onOpen }: { state: ViewState<Customer[]>; onOpen: (id: string) => void }) {
  if (state.kind === "loading") return <p role="status">載入中…</p>;
  if (state.kind === "empty") return <p>{state.message}</p>;
  if (state.kind === "denied") return <p role="alert">沒有權限查看搜尋結果。{state.correlationId && ` 追蹤碼：${state.correlationId}`}</p>;
  if (state.kind === "error") return <p role="alert">讀取失敗：{state.problem.code}（{state.problem.correlation_id}）</p>;
  if (state.kind === "processing") return <p role="status">處理中，收據：{state.receiptId}</p>;
  const rows = state.value;
  return <div className="m01-table-scroll"><table><caption>授權範圍內的客戶</caption><thead><tr><th scope="col">客戶</th><th scope="col">狀態</th><th scope="col">聯絡</th><th scope="col">更新時間</th><th scope="col">操作</th></tr></thead>
    <tbody>{rows.map(customer => <tr key={customer.id}><th scope="row">{customer.display_name}<small>{customer.id}</small></th><td>{customer.status}</td><td>{customer.masked_email ?? customer.masked_phone ?? "未提供"}</td><td>{new Date(customer.updated_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</td><td><button onClick={() => onOpen(customer.id)}>開啟</button></td></tr>)}</tbody></table></div>;
}
