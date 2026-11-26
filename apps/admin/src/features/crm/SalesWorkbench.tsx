import { useState } from "react";
import type { CrmWorkItem, NextActionRequest, Page, ViewState } from "./types";
import { isAllowed, newIdempotencyKey } from "./viewState";

const WORKLIST_LABELS: Record<CrmWorkItem["worklist"], string> = {
  incomplete_application: "未完成申辦",
  pending_review: "待公司審核",
  unpaid: "付款失敗",
  renewal: "續約到期",
  upgrade: "升級機會",
  retention: "服務風險／留存",
  reactivation: "重新開發",
  company_pool: "公司 Pool",
};

const STATE_LABELS: Record<CrmWorkItem["state"], string> = {
  open: "待處理",
  in_progress: "處理中",
  completed: "已完成",
  suppressed: "已抑制聯絡",
  cancelled: "已取消",
};

function formatTime(value: string | null): string {
  if (!value) return "未提供";
  return new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" });
}

export function SalesWorkbench({
  state,
  currentActorId,
  onReload,
  onAssign,
}: {
  state: ViewState<Page<CrmWorkItem>>;
  /** The signed-in actor claiming an unassigned item claims it for themselves. */
  currentActorId: string;
  onReload: () => void;
  onAssign: (item: CrmWorkItem, body: NextActionRequest) => Promise<void>;
}) {
  const [pendingId, setPendingId] = useState<string | null>(null);

  if (state.kind === "loading") return <main aria-busy="true"><p role="status">正在載入業務今日工作台…</p></main>;
  if (state.kind === "denied") return <main><h1>無權限</h1><p>此資源不存在或不在你的授權範圍。</p></main>;
  if (state.kind === "error") return <main><h1>資料載入失敗</h1><p role="alert">{state.problem.code}（{state.problem.correlation_id}）</p><button onClick={onReload}>重試</button></main>;
  if (state.kind === "empty") return <main><p>{state.message}</p></main>;
  if (state.kind === "processing") return <main><p role="status">背景處理中，收據：{state.receiptId}</p></main>;

  const page = state.value;

  async function claim(item: CrmWorkItem) {
    setPendingId(item.id);
    try {
      await onAssign(item, {
        // An unassigned item ("認領") is claimed by the current actor, not
        // sent as an empty string -- owner_id must be a UUID (422 otherwise).
        owner_id: item.owner_id ?? currentActorId,
        action_type: item.action_type as NextActionRequest["action_type"],
        due_at: item.due_at,
        reason_code: "claimed_from_workbench",
      });
    } finally {
      setPendingId(null);
    }
  }

  return (
    <main className="m10-stack">
      {state.kind === "conflict" && (
        <aside role="alert"><strong>版本衝突</strong><p>{state.message} 伺服器版本：{state.currentVersion}</p><button onClick={onReload}>重載並比較</button></aside>
      )}
      {state.kind === "stale" && <aside role="status"><strong>資料可能已過期</strong><p>{state.message}</p></aside>}
      <header className="m10-panel">
        <p className="m10-eyebrow">業務今日工作台</p>
        <h1>CRM 工作清單</h1>
        <p>快照時間：{formatTime(page.as_of)}・共 {page.total_estimate ?? page.items.length} 筆</p>
      </header>
      <section aria-labelledby="worklist-table" className="m10-panel">
        <h2 id="worklist-table">按優先序列的名單</h2>
        {page.items.length === 0 ? (
          <p>目前沒有符合條件的待辦。</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">名單</th>
                <th scope="col">客戶（遮罩）</th>
                <th scope="col">原因</th>
                <th scope="col">下一步</th>
                <th scope="col">到期</th>
                <th scope="col">Owner</th>
                <th scope="col">狀態</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => {
                // set_next_action is published under
                // /crm/opportunities/{opportunity_id}/next-actions -- a
                // rule-generated work item with no linked opportunity
                // (opportunity_id is null) has no path to call it through.
                const hasOpportunity = item.opportunity_id !== null;
                const canAssign = hasOpportunity && isAllowed(item.allowed_actions, "set_next_action");
                return (
                  <tr key={item.id}>
                    <td>{WORKLIST_LABELS[item.worklist]}</td>
                    <td>{item.customer_id.slice(0, 8)}…</td>
                    <td>{item.reason_codes.join("、") || "unknown"}</td>
                    <td>{item.action_type}</td>
                    <td>{formatTime(item.due_at)}</td>
                    <td>{item.owner_id ? `${item.owner_id.slice(0, 8)}…` : "未指派"}</td>
                    <td>
                      <span className={`m10-state m10-state-${item.state}`} role="status">
                        {STATE_LABELS[item.state]}
                        {item.suppression_applied && item.state !== "suppressed" ? "（受抑制範圍限制）" : ""}
                      </span>
                    </td>
                    <td>
                      <button
                        disabled={!canAssign || pendingId === item.id}
                        title={
                          !hasOpportunity
                            ? "此待辦未關聯業務機會，請先建立機會後再指派"
                            : !canAssign
                              ? "目前狀態或角色不允許指派下一步"
                              : undefined
                        }
                        onClick={() => claim(item)}
                      >
                        {pendingId === item.id ? "處理中…" : item.owner_id ? "更新下一步" : "認領"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
      <section className="m10-panel notice">
        <h3>安全提示</h3>
        <p>「已抑制聯絡」代表客戶拒絕行銷或有未結抱怨；僅能執行客服必要的下一步（resolve_issue），不得主動行銷或升級催促。</p>
      </section>
    </main>
  );
}

export function newIdempotencyKeyForAssign(): string {
  return newIdempotencyKey("set_next_action");
}
