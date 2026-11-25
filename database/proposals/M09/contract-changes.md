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
