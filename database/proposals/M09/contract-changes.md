# M09 共用契約相容新增提案

本模組實作完全落在既有 `api/openapi.yaml`（8 個 M09 operationId）與
`events/registry.json`（M09 的 9 個訂閱事件＋1 個發出事件）範圍內，**未**
提出新的 OpenAPI operation 或事件註冊；只新增 DB 層的
`delivery.communication_preference`／`delivery.dispatch_job`／
`delivery.message.replaces_delivery_id`（見 `001_legendflow_delivery.sql`）。

## 與其他模組提案的相依關係

- `database/proposals/M01/contract-changes.md` 提議的
  `customer.identity_revoked` 事件（M09 為預計 consumer，撤銷 channel
  binding）尚未合併進 `events/registry.json`。本模組目前不訂閱該事件；
  待 integration steward 核准並登錄後，由本模組補 consumer 分支，不在此先行
  假設已核准。
- `delivery.message.replaces_delivery_id`、`delivery.dispatch_job` 僅供本模組
  worker 使用；未更動任何其他模組擁有的資料表或唯讀 port 契約
  （`publication.recipient`、`contract.entitlement` 仍為唯讀）。

## 待議：`delivery_supervisor` 的死信觸發查詢入口（未實作，僅記錄缺口）

`api/openapi.yaml` 的 `list_deliveries` x-allowed-roles 僅
`service_agent`、`delivery_operator`；程式已改回完全依照此契約授權，**未**
私自多放 `delivery_supervisor`。這表示目前 `delivery_supervisor` 沒有任何
已核准的 API 可以查詢哪些 `delivery.message`／`DispatchJob` 已進入
`dead_letter`，只能依 `workers/tasks/legendflow_delivery/RUNBOOK.md` 所述，由
告警（`m09_dispatch_job_oldest_age_seconds`）取得 `dead_letter_id` 後直接呼叫
`redrive_delivery_event`。

`apps/api/src/modules/legendflow_delivery/service.py` 的 `_delivery_actions`
已預留：若 `delivery_supervisor` 之後透過已核准的契約變更取得某種查詢入口，
對應 `DispatchJob.status == "dead_letter"` 的項目會回報
`redrive_delivery_event` 於 `allowed_actions`，`RedriveReconciliation.tsx`
已改為以此欄位（而非僅憑表單填寫完整度）決定重驅按鈕是否可用。未核准前，
本模組不新增、不假裝已有這條查詢路徑；若日後需要，提案方向二選一，待
integration steward 核准後再實作：

1. 在 `list_deliveries` 的 `x-allowed-roles` 加入 `delivery_supervisor`（最小
   改動，但等於讓 supervisor 也能看到全部一般投遞列表）。
2. 新增一個僅回傳 `dead_letter` 狀態的唯讀查詢 operation，範圍更小。
