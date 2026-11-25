# M09 · LegendFlow 與訊息交付

依 [docs/modules/M09-legendflow_delivery.md](../../../../../docs/modules/M09-legendflow_delivery.md) 實作。
與 M01 相同，本模組目前是可獨立測試的 domain + in-memory adapter，尚未經由
`plugin.py` 掛上共用 `longhao_platform` runtime（該 runtime 本身也尚未要求模組掛載）；
`http.py` 的 `create_fastapi_router` 可在 FastAPI 可用時供 build steward 整合。

## 模組結構

- `domain.py`：`Delivery`/`ChannelBinding`/`CommunicationPreference`/`DeliveryAttempt`/
  `DeliveryReceiptRecord`/`DispatchJob` 等實體、狀態機（`apply_receipt`）、
  `resolve_delivery_permission` 純函式。
- `ports.py`：M08 publication／recipient、M02 entitlement、M07 report signing、
  LINE/SMS/email/LegendFlow channel proof／send、webhook integration key 的
  protocol 與 deterministic fake adapter。
- `repository.py`：copy-on-write in-memory store（同一交易內 state＋audit＋
  outbox＋idempotency 原子提交）。
- `service.py`：8 個 operationId 的 command/query，以及 worker 用的
  `dispatch_publication` / `lease_jobs` / `process_job`。
- `automation.py`：`delivery_exception_summary`（唯一核准 LLM 能力）——只整理
  DLQ/timeout 例外為非投資判斷的摘要草稿，不可自行核准重驅。
- `http.py`：framework-neutral controller；`create_fastapi_router` 延遲匯入
  FastAPI。

## 已知限制與 NOT_CONNECTED

- `ChannelBindingRequest`/`CommunicationPreferenceRequest` 的 `web` channel 在
  `delivery.message` 的 DB CHECK（`legendflow/line/sms/email`）中不存在，因此
  worker 目前不會對 `web` binding 派送；僅保留綁定/偏好資源本身符合契約。
- LegendFlow member BFF、LINE/SMS/email provider、M08 publication/recipient、
  M02 entitlement 均為本平台 adapter + 測試替身；真實串接標記 `NOT_CONNECTED`，
  見 [workers/tasks/legendflow_delivery/RUNBOOK.md](../../../../../workers/tasks/legendflow_delivery/RUNBOOK.md)。
- `delivery.communication_preference` 與 dispatch job 佇列資料表由
  [database/proposals/M09](../../../../../database/proposals/M09) 提出，尚待資料治理者併入
  `database/migrations`。
