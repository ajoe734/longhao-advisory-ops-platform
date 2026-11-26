# M12 獎金與結算 worker 操作手冊

本手冊只適用本機／測試 adapter（`LocalEventBus`、`FakeRevenueBasis`、`FakeSalesConversion`、
`FakeOwnershipCohort`、`FakeEvidenceStore`）；不得用來驅動真實薪酬匯出或付款接口。

## 兩種 job 與其 202 語意

- `calculate_commission_run`（由 `create_commission_run` 排入）：由 `CommissionJobRunner`
  呼叫 `CommissionService.run_calculation_job`，寫入 `commission.entry` 並把 run 狀態推進到
  `calculated`，發出 `commission.run_calculated`。API 的 202 只代表已持久接收；呼叫端必須
  `get_commission_run` 輪詢直到 `state=calculated` 才能核准。
- `assemble_payout_batch`（由 `create_commission_payout_batch` 排入）：呼叫
  `CommissionService.assemble_payout_batch_job`，組裝未重複支付的 `payout_line`，計算
  `control_sha256`／`total`，把批次推進到 `approval_pending`。同樣需 `get_commission_payout_batch`
  輪詢。

## 告警與判讀

- `m12_job_oldest_age_seconds > 30`：先確認 repository 可用、lease 是否過期、worker clock
  offset，再看 `last_error_code`。禁止更換 Idempotency-Key 盲重送 `create_commission_run`。
- `m12_outbox_oldest_age_seconds > 30`：同上，檢查 `commission.run_calculated` /
  `commission.run_approved` / `commission.adjustment_recorded` 是否卡在 `retry`。
- `m12_inbox_sequence_gap`（M10/M11/M14 來源事件）：保留 retry receipt，等上游補齊或由權威
  API 重建，不可把 gap 直接標為 applied。
- dead letter：人工重驅必填非空原因；重驅計算 job 前先確認原 run 尚未被其他候選覆蓋
  （`run_key` 唯一性避免重複計算同一期間/政策/截止時間）。

## 合成故障恢復演練

1. 建立已核准政策與一個 `create_commission_run`；確認 job 處於 `pending`。
2. 執行 `CommissionJobRunner.run_once("m12-drill")`；確認 run 推進到 `calculated` 且
   `commission.run_calculated` 進入 outbox。
3. 將 `LocalEventBus.failure_mode` 設為 `timeout`，執行 `OutboxPublisher.run_once("m12-drill")`；
   確認事件為 `retry`、attempt 增加，而 `commission.entry` 與 audit 不受影響（已提交的計算不因
   outbox 重試而重算）。
4. 清空 failure mode 並把測試 clock 推進至 `next_attempt_at`，再執行 publisher；確認同一
   `event_id` 只出現一次、狀態為 `published`。
5. 對帳 `commission.entry` 的 `marketing+sales+company=base_pool`、run 的
   `entry_count`/`exception_count`、audit correlation；將演練結果標示為 `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 `CommissionJobRunner` 與 `OutboxPublisher`，核對 inbox/outbox、run/payout batch
狀態與 `payout_line` 是否重複支付同一 entry。只有確認未知結果後才恢復排程；真實財會薪酬接口
與 M10/M11/M14 讀取路徑維持 `NOT_CONNECTED`。
