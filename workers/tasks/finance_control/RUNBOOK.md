# M14 帳務／內控 worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實金流、退款或外部 production 存取。

## 告警與判讀

- `m14_outbox_oldest_age_seconds > 30`：先確認 DB 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。禁止更換 idempotency key 盲重送（尤其 `finance.refund_confirmed`／`finance.payment_confirmed`）。
- `m14_refund_provider_unavailable`：`execute_refund` 應回 503/retryable，refund 狀態停在 `submitted`／`reconciliation_required`；不得推定成功或重複送出第二筆對外退款。下一次重試前一律先 `RefundProviderPort.query()`。
- `m14_inbox_sequence_gap`：保留 `retry` receipt（`contract.*`／`commission.*` consumer），補齊前序事件或由權威來源重建；不可直接把 sequence 標成 applied。
- `m14_journal_unbalanced_rejected`：`_post_journal`／`create_accounting_export` 拒絕了不平衡分錄；不得繞過，回溯原始計算來源，必要時以反向分錄更正，絕不 UPDATE 已過帳金額。
- dead letter：人工重驅必填非空原因，並確認 provider lookup 顯示先前效果不存在。

## 合成故障恢復演練（UAT-M14-012 對應）

1. 將 `LocalEventBus.failure_mode` 設為 `timeout`，完成一筆 `run_revenue_recognition`／`approve_revenue_run` 產生的 `finance.revenue_recognized` outbox event。
2. 執行 `OutboxPublisher.run_once("m14-drill")`；確認 event 為 `retry`、attempt 增加、`finance.revenue_run`／`finance.revenue_recognition` 與 audit 仍存在、未重複過帳。
3. 將 failure mode 清空並把測試 clock 推進至 `next_attempt_at`，再執行 publisher。
4. 確認同一 event_id 只出現一次、狀態為 `published`；對帳 payment/refund/journal 數量與 outbox payload hash，沒有第二筆扣款或退款。
5. 將演練結果標示為 `FAKE_PROVIDER`；restore drill 差異清單寫入 `record_control_exception`（control_code=`restore_drill`），並由 `act_on_control_exception` 走 `request_closure` → `approve_closure`（獨立稽核覆核）結案。

## 排程 purge 前的 legal hold 檢查（UAT-M14-011 對應）

`ScheduledPurgeWorker.evaluate()` 在實際刪除前對每個候選 `(scope, subject_id)` 呼叫 `service.evaluate_purge_candidate()`；回傳 `action="skip"` 時 purge job 必須整筆跳過並保留 `reason_code`，不得局部刪除或靜默略過理由；只有 `action="eligible_for_delete"` 才可交由下游刪除流程處理。`core.legal_hold` 解除一律是新增一筆事實（`release_legal_hold`），不會覆寫或刪除原 hold。

## 還原後啟動

還原環境先停 publisher，核對 inbox/outbox、payment/refund/journal 狀態與 provider lookup。只有確認未知結果（`reconciliation_required`／`submitted`）後才恢復排程；真實金流／退款／會計 ERP 尚未接線時維持 `NOT_CONNECTED`。
