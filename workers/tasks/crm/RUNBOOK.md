# M10 CRM worker 操作手冊

本手冊只適用本機／測試 provider（`LocalEventBus` 等）；不得用來啟動真實
Pantheon/Agora/LegendFlow 連線或真實金流。`core.object_asset` 存在性檢查、
M01/M02/M03/M11/M13/M14 的上游事件均標記 `NOT_CONNECTED`；通過本機 mock 不代表
已與對方系統打通。

## 三個worker

1. `M10EventConsumer.consume(envelope)`（`consumer.py`）：inbox 去重消費
   customer/application/contract/entitlement/ownership/support/finance/kyc
   事件，更新本模組 projection；`finance.refund_confirmed` 會連動
   `reverse_conversion_for_refund` 回正已核准的歸因。
2. `OutboxPublisher.run_once(worker_id)`（`tasks.py`）：發布
   `crm.opportunity_changed`／`crm.conversion_decided`／
   `crm.suppression_changed` 到共用bus；lease/attempt/next_attempt_at／
   dead_letter 機制與 M09 的 `OutboxPublisher` 相同模式。
3. `WorklistSweepWorker.run_once(tenant_ids)`（`tasks.py`）：
   `CrmService.sweep_worklists` 的排程入口，對應 REQ-M10-001/005/012「定時名單
   計算」；冪等（`_upsert_work_item` 以 `dedupe_key` upsert），重跑不產生重複
   待辦。

## 告警與判讀

- `m10_outbox_oldest_age_seconds` 偏高：先確認 `OutboxPublisher.run_once` 是否
  仍在排程、`lease_until` 是否過期，再看 `last_error_code`；禁止更換
  `idempotency_key` 盲重送（會造成下游 M09/M12 收到第二個邏輯事件）。
- `m10_inbox_sequence_gap`：保留 `retry` receipt，等待前序事件或由
  M01/M02/M03/M11/M13/M14 重建投影，不可直接把 sequence 標成 `applied`。
- `m10_conversion_reversed_total` 突增：對照 `finance.refund_confirmed` 來源與
  `crm.conversion_decided(decision=reversed)` 的 outbox payload，確認
  `approved_salesperson_id` 保留原核准業務（供 M12 核對獎金回收），不是誤設為
  null。
- dead letter（`core.outbox.status == 'dead_letter'`）：目前尚無對應 API
  公開重驅入口（見
  [database/proposals/M10/contract-changes.md](../../../database/proposals/M10/contract-changes.md)
  的待議事項）；需人工核對 payload 後，由資料治理者協助重置
  `status`/`attempt_count`，不得略過 payload 核對直接改資料庫。

## 合成故障恢復演練

1. 用 `LocalEventBus.failure_mode = "unknown"` 模擬本地已送出但結果不明的
   `crm.conversion_decided` 發布。
2. 執行 `OutboxPublisher.run_once("m10-drill")`；確認透過 `lookup` 解析為
   `accepted` 後只標記一次 `published`，不重複呼叫 `publish`。
3. 將 `LocalEventBus.failure_mode = "timeout"` 並重複呼叫
   `OutboxPublisher.run_once` 直到 `attempt_count` 達到 `max_attempts`；確認
   outbox row 轉為 `dead_letter`，`crm.conversion`／`crm.opportunity` 本身的
   業務狀態不受影響（outbox 失敗不回滾已提交的業務交易）。
4. 對帳 `crm.opportunity`/`crm.conversion`/`crm.work_item`/`crm.suppression`
   筆數、outbox payload hash 與 audit correlation；將演練結果標示為
   `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 `OutboxPublisher`、`WorklistSweepWorker` 與事件 consumer 排程，
核對 inbox/outbox 與 `crm.*` 投影狀態。只有確認未知結果後才恢復排程；真實
Pantheon/Agora/LegendFlow／金流／簽章尚未接線時維持 `NOT_CONNECTED`。
