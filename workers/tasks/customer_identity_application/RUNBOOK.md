# M01 身分與申辦 worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實客戶訊息、付款、退款或外部 production 存取。

## 告警與判讀

- `m01_outbox_oldest_age_seconds > 30`：先確認 DB 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。禁止更換 idempotency key 盲重送。
- `m01_identity_provider_unavailable`：API 應回 503/retryable，且 customer/external_identity 數量不變；不得退回未驗證 session。
- `m01_inbox_sequence_gap`：保留 retry receipt，補齊前序事件或由權威來源重建；不可直接把 sequence 標成 applied。
- dead letter：人工重驅必填非空原因，並確認 provider lookup 顯示先前效果不存在。

## 合成故障恢復演練

1. 將 `LocalEventBus.failure_mode` 設為 `timeout`，建立 synthetic customer。
2. 執行 `OutboxPublisher.run_once("m01-drill")`；確認 event 為 `retry`、attempt 增加、business row 與 audit 仍存在。
3. 將 failure mode 清空並把測試 clock 推進至 `next_attempt_at`，再執行 publisher。
4. 確認同一 event_id 只出現一次、狀態為 `published`，沒有重建 customer 或 first-touch attribution。
5. 對帳 customer／identity／application 數量、audit correlation、outbox payload hash；將演練結果標示為 `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 publisher，核對 inbox/outbox、application 狀態與 provider lookup。只有確認未知結果後才恢復排程；真實 LegendFlow 尚未接線時維持 `NOT_CONNECTED`。
