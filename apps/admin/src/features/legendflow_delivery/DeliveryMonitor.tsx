import type { Delivery, Page, ViewState } from "./types";
import { isAllowed } from "./viewState";

const STATE_LABELS: Record<Delivery["state"], string> = {
  queued: "已排隊",
  accepted: "供應商已受理",
  sent: "已送出",
  delivered: "已送達",
  failed: "失敗（可重試）",
  expired: "已過期（不可重送）",
  suppressed: "已抑制（權限/偏好）",
  unknown: "結果未知（待對帳）",
};

function formatTime(value: string | null): string {
  if (!value) return "未提供";
  return new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" });
}

export function DeliveryMonitor({
  state,
  onReload,
  onSelect,
}: {
  state: ViewState<Page<Delivery>>;
  onReload: () => void;
  onSelect: (delivery: Delivery) => void;
}) {
  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入投遞監控表…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此資源不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;

  const page = state.value;
  return (
    <main className="m09-stack">
      {state.kind === "conflict" && (
        <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>資料可能已過期</strong><p>{state.message}</p></aside>}
      <header className="m09-panel">
        <p className="m09-eyebrow">投遞監控表</p>
        <h1>通路交付</h1>
        <p>快照時間：{new Date(page.as_of).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}・共 {page.total_estimate ?? page.items.length} 筆</p>
      </header>
      <section aria-labelledby="delivery-table" className="m09-panel">
        <h2 id="delivery-table">逐客戶、逐渠道投遞狀態</h2>
        {page.items.length === 0 ? (
          <p>目前沒有符合條件的投遞紀錄。</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">Publication</th>
                <th scope="col">客戶（遮罩）</th>
                <th scope="col">渠道</th>
                <th scope="col">狀態</th>
                <th scope="col">accepted_at</th>
                <th scope="col">sent_at</th>
                <th scope="col">delivered_at</th>
                <th scope="col">read_at</th>
                <th scope="col">Provider ref</th>
                <th scope="col">失敗/重試原因</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((delivery) => (
                <tr key={delivery.id}>
                  <td>{delivery.publication_id}</td>
                  <td>{delivery.customer_id.slice(0, 8)}…</td>
                  <td>{delivery.channel}</td>
                  <td>
                    <span className={`m09-state m09-state-${delivery.state}`} role="status">
                      {STATE_LABELS[delivery.state]}
                    </span>
                  </td>
                  <td>{formatTime(delivery.accepted_at)}</td>
                  <td>{formatTime(delivery.sent_at)}</td>
                  <td>{formatTime(delivery.delivered_at)}</td>
                  <td>
                    {delivery.read_capability === "unsupported"
                      ? "此渠道不支援已讀回報"
                      : formatTime(delivery.read_at)}
                  </td>
                  <td>{delivery.provider_message_ref ?? "未提供"}</td>
                  <td>{delivery.last_error_code ?? "—"}</td>
                  <td>
                    <button
                      disabled={!isAllowed(delivery.allowed_actions, "reconcile_delivery")}
                      title={!isAllowed(delivery.allowed_actions, "reconcile_delivery") ? "目前狀態或角色不允許對帳" : undefined}
                      onClick={() => onSelect(delivery)}
                    >
                      查看對帳/重驅
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
