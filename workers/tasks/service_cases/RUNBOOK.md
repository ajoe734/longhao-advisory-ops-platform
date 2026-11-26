# M13 客服客訴 Worker Runbook

本文件涵蓋 `workers/tasks/service_cases/` 三類背景工作：`OutboxPublisher`（發布
`support.case_opened` / `support.case_escalated` / `support.case_resolved` /
`support.response_status_changed`）、`ResponseDispatchSender`（實際發送已核准
客服回覆並寫入 `support.response_attempt`）、`SuppressionJobRunner`（將重大申訴
的行銷抑制請求轉交 M10，M10 端點目前 NOT_CONNECTED，僅有 contract mock）。

## Metrics 與告警門檻

- `m13_outbox_oldest_pending_age_seconds`：`pending`/`retry` 事件中最舊
  `emitted_at` 距今秒數。超過 300 秒觸發 warning，超過 1800 秒觸發 page。
- `m13_response_dispatch_oldest_queued_age_seconds`：`queued`/`retry` 的
  `support.response_dispatch` 中最舊 `created_at` 距今秒數；超過 180 秒 warning。
- `m13_dead_letter_count{queue="outbox"|"response_dispatch"|"suppression_job"}`：
  任一非零即建立人工覆核工單，不自動重送。
- `m13_sla_breach_without_pause_count`：`waiting_internal` 案件中
  `resolution_due_at` 已過且無對應 `sla_event(action=paused)` 的筆數；非零觸發
  SLA 稽核告警（REQ-M13-008）。

所有 metrics 以 `module="M13"` 與 error class 標籤呈現，不得以 `customer_id`
或明文 PII 作為標籤基數。

## 失敗恢復演練（必跑一次並記錄於驗收證據）

1. 建一個案件並送出一則已核准回覆（`send_service_response`），使
   `support.response_dispatch` 進入 `queued`。
2. 將 `LocalMessagingProvider.failure_mode` 設為 `"timeout"`，呼叫
   `ResponseDispatchSender.run_once`；確認 `attempt_count` 遞增、
   `support.response_attempt` 新增一筆 `outcome="timeout"`、狀態轉
   `retry` 且 `next_attempt_at` 依指數退避設定。
3. 將 `failure_mode` 設為 `None`，手動把 `next_attempt_at` 往前移後再跑一次；
   確認同一 dispatch 只送出一次（不重複發送）、狀態轉 `accepted`。
4. 將 `failure_mode` 設為 `"unknown"`，確認 worker 呼叫 `provider.lookup(...)`
   查詢外部真實狀態後才決定 retry 或視為已送達，不盲目重送。
5. 連續失敗超過 `max_attempts` 的 dispatch 必須進入 `failed`
   （OutboxPublisher/SuppressionJobRunner 的等價任務則進入 `dead_letter`）；
   `TaskRunner.redrive(tenant_id, job_id, reason)` 只接受非空理由字串，且只能
   重驅 `dead_letter` 狀態的任務。

## 重啟後檢查清單

1. 停止所有 publisher/sender 前先確認目前沒有 `leased` 狀態卻無對應 worker
   存活的紀錄（lease 逾時會自然可再被租用，不需手動清除)。
2. 重啟後先核對 `support.response_dispatch`、`support.response_attempt`、
   `support.response_receipt` 三表狀態是否一致：任何 `attempt.outcome="unknown"`
   且沒有對應 receipt 的 dispatch，必須先呼叫 provider 查詢而非直接判定失敗。
3. 核對 outbox 與 inbox：任何 `dead_letter` 事件需人工覆核理由後才能
   `redrive`；不得為了清空佇列就批量重驅。
