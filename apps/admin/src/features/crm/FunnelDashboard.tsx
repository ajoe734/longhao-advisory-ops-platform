import type { FunnelMetrics, ViewState } from "./types";

const STAGE_LABELS: Record<FunnelMetrics["items"][number]["stage"], string> = {
  lead: "訪客/潛客",
  application_started: "開始申辦",
  customer_signed: "客戶已簽署",
  due_diligence: "審核中",
  accepted: "公司已接受",
  paid: "已付款",
  active: "有效服務中",
  renewal: "續約中",
  upgrade: "升級中",
  churn: "流失／終止",
};

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" });
}

export function FunnelDashboard({
  state,
  onReload,
}: {
  state: ViewState<FunnelMetrics>;
  onReload: () => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入漏斗看板…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此資源不在你的授權範圍（僅 sales_supervisor／marketing 可查看）。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;

  const metrics = state.value;

  return (
    <main className="m10-stack">
      {state.kind === "stale" && <aside role="status"><strong>資料可能已過期</strong><p>{state.message}</p></aside>}
      <header className="m10-panel">
        <p className="m10-eyebrow">漏斗看板</p>
        <h1>訪客 → 簽署 → 補件 → 接受 → 付款／有效 → 續約／升級</h1>
        <p>
          快照時間：{formatTime(metrics.as_of)}・歸因政策版本：{metrics.attribution_policy_version}・
          資料完整至：{formatTime(metrics.data_complete_through)}
        </p>
      </header>
      <section aria-labelledby="funnel-table" className="m10-panel">
        <h2 id="funnel-table">各階段計數（依 service_code／cohort 分組）</h2>
        {metrics.items.length === 0 ? (
          <p>目前沒有可呈現的漏斗資料。</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">階段</th>
                <th scope="col">Service code</th>
                <th scope="col">Cohort</th>
                <th scope="col">期間</th>
                <th scope="col">人數</th>
              </tr>
            </thead>
            <tbody>
              {metrics.items.map((item, index) => (
                <tr key={`${item.stage}-${item.service_code}-${index}`}>
                  <td>{STAGE_LABELS[item.stage]}</td>
                  <td>{item.service_code}</td>
                  <td>{item.cohort_key}</td>
                  <td>{item.period_start} ~ {item.period_end}</td>
                  <td>{item.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="m10-panel notice">
        <h3>解讀提示</h3>
        <p>
          不同事件時點可交叉（例如付款不一定在公司接受之後）；各階段按 distinct application/customer 去重，
          <code>churn</code> 另列、不併入 <code>active</code>；此看板不代表已與金流／簽章系統打通。
        </p>
      </section>
    </main>
  );
}
