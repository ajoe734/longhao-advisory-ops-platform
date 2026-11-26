# M09 LegendFlow／訊息交付 worker 操作手冊

本手冊只適用本機／測試 provider（`FakeChannelSendPort`、`LocalEventBus` 等）；
不得用來啟動真實 LINE/SMS/email 發送、真實 LegendFlow 連線或真實金流／簽章。
LegendFlow member BFF、LINE/SMS/email provider、M08 publication/recipient 讀取、
M02 entitlement 讀取、M07 report signing 皆標記 `NOT_CONNECTED`；通過本機 mock
不代表已與對方系統打通。

## 告警與判讀

- `m09_dispatch_job_oldest_age_seconds` 偏高：先確認 `DispatchWorker.run_once`
  是否仍在排程、`lease_until` 是否過期，再看 `last_error_code`；禁止更換
  `provider_idempotency_key` 盲重送（會造成同一 publication/recipient/channel
  出現第二個邏輯訊息）。
- `m09_entitlement_lookup_unavailable`：`EntitlementPort` 應回
  `DependencyUnavailableError`／503，且不得把 `unknown` 當 `active` 放行；
  `dispatch_publication`／`process_job` 會把對應收件人標記 `suppressed` 並記錄
  `last_entitlement_decision_id`，待依賴恢復後由 reconcile 或下一輪事件補送。
- `m09_inbox_sequence_gap`：保留 `retry` receipt，等待前序事件或由 M08/M02 等
  producer 重建；不可直接把 sequence 標成 `applied`。
- dead letter（`DispatchJob.status == "dead_letter"`）：`redrive_delivery_event`
  必填非空 `reason_code`、`approved_by`，且會重新核對 `expires_at` 與目前
  entitlement；兩者任一不通過會回 409 並記 audit，**不會**對 provider 送出任何
  請求。

## 合成故障恢復演練

1. 用 `FakeChannelSendPort.queue(key, SendOutcome("timeout"))` 模擬本地逾時，
   接著 `FakeChannelSendPort.seed_accepted(key, "provider-ref-1")` 模擬供應商
   實際已收下。
2. 執行 `DispatchWorker.run_once("m09-drill", tenant_id)`；確認
   `process_job` 透過 `lookup` 解析為 `accepted`，只建立一筆
   `delivery.attempt`，且 `delivery.message` 只有一筆邏輯訊息（同一
   `provider_idempotency_key`）。
3. 將 `FakeChannelSendPort` 改為持續回傳 `unknown` 並重複呼叫
   `DispatchWorker.run_once` 直到 `attempt_count` 達到上限；確認
   `DispatchJob.status` 轉為 `dead_letter` 且 `delivery.message.status`
   停在 `unknown`（不得偽稱 `delivered`）。
4. 以 `delivery_supervisor` 呼叫 `redrive_delivery_event`；確認過期或
   entitlement 未生效時回 409 且未呼叫 provider；核准後 `DispatchJob` 轉
   `retry`，下一輪 `DispatchWorker.run_once` 才真正重試。
5. 對帳 `delivery.message`／`delivery.attempt`／`delivery.receipt` 筆數、
   outbox `delivery.status_changed` payload hash 與 audit correlation；將
   演練結果標示為 `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 `DispatchWorker` 與 `OutboxPublisher`，核對 inbox/outbox、
`delivery.message` 狀態與 provider lookup。只有確認未知結果後才恢復排程；
真實 LegendFlow／LINE／SMS／email 尚未接線時維持 `NOT_CONNECTED`。
