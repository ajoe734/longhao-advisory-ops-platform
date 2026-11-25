# M09 · LegendFlow 與訊息交付

依 [docs/modules/M09-legendflow_delivery.md](../../../../../docs/modules/M09-legendflow_delivery.md) 實作。
本模組的 domain + in-memory adapter 可獨立測試；`plugin.py`
（`MODULE_ID = "M09"`、`register(runtime)`）讓 `longhao_platform.app.PlatformRuntime.bootstrap()`
掛上本模組的 FastAPI router（非 production 環境下以本模組自己的 synthetic
provider 組裝 `LegendflowDeliveryService`；production 因尚無真實 channel/entitlement
adapter，`register()` 會因 `LegendflowDeliveryService` 自身的生產環境防呆而失敗，
回報為 `broken`/`MODULE_REGISTRATION_FAILED`，而非悄悄用假 adapter 服務會員資料）。
`http.py` 的 `create_fastapi_router` 不再自帶 `/api/v1` 前綴，交由
`longhao_platform.http.create_app()` 統一掛載，避免路徑重複成
`/api/v1/api/v1/...`。

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
- `plugin.py`：`PlatformRuntime.discover_modules()` 的註冊入口
  （`MODULE_ID`、`register(runtime)`）。

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
