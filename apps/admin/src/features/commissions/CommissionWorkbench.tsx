import React, { useState } from "react";
import type { M12Api } from "./api";
import { PolicyPanel } from "./PolicyPanel";
import { PreviewPanel } from "./PreviewPanel";
import { RunPanel } from "./RunPanel";
import { AdjustmentPanel } from "./AdjustmentPanel";
import { PayoutPanel } from "./PayoutPanel";

/** UI-24 `/commissions` — 政策與試算頁 + 月結與差異，依 SA/SD 第10節合併為單一工作台。 */
export function CommissionWorkbench({ api }: { api: M12Api }) {
  const [approvedRunId, setApprovedRunId] = useState<string | undefined>(undefined);
  return (
    <div className="m12-stack">
      <header>
        <p className="m12-eyebrow">M12 · 獎金試算與結算</p>
        <h1>獎金政策、試算與月結</h1>
        <p>允許動作由伺服器 <code>allowed_actions</code> 決定；禁用的操作不在畫面顯示。202 僅代表受理，不代表已完成。</p>
      </header>
      <PolicyPanel api={api} />
      <PreviewPanel api={api} />
      <RunPanel api={api} onRunApproved={setApprovedRunId} />
      <AdjustmentPanel api={api} />
      <PayoutPanel api={api} prefillRunId={approvedRunId} />
    </div>
  );
}
