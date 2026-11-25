# M07 報告簽章 worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實簽章、憑證或外部 production 存取。

## 告警與判讀

- `m07_outbox_oldest_age_seconds > 30`：先確認 DB 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。禁止更換 idempotency key 盲重送 `report.signature_verified`/`report.signature_revoked`。
- `m07_signature_payload_mismatch` / `m07_qualification_invalid_at_verification`：代表 webhook 收到的 callback 與 frozen 版本 hash 不符或簽署人資格在驗證時失效；receipt 已回 `quarantined`，frozen package 原樣保留，不得由 worker 自動重簽。轉人工覆核並視需要通知分析師改用新的 `request_analyst_signature`。
- `m07_requires_manual_reconciliation`（來自 `SignatureExpiryReconciler`）：request 已過期但 provider lookup 回報非 `pending`/`not_found`，代表「簽章成功但 callback 遺失」風險；必須以 `signature_request_id`／`provider_transaction_id` 向 provider 查證，不可盲目要求分析師重簽，也不可自動標記 verified。
- `m07_inbox_sequence_gap`（`research.candidate_superseded` / `review.revision_received`）：保留 retry receipt，補齊前序事件或由權威來源重建；不可直接把 sequence 標成 applied，否則 stale 來源可能被誤判為未過期。

## 合成故障恢復演練

1. 將 `LocalEventBus.failure_mode` 設為 `timeout`，對一個已 `ready_to_sign` 的 report version 呼叫 `request_analyst_signature` 並 `freeze_report_version` 產生的 outbox 事件。
2. 執行 `OutboxPublisher.run_once("m07-drill")`；確認 event 為 `retry`、attempt 增加、`report_version`／`signature_request` 狀態與 audit 仍存在、沒有重複發布。
3. 將 failure mode 清空並把測試 clock 推進至 `next_attempt_at`，再執行 publisher；確認同一 `event_id` 只出現一次、狀態為 `published`。
4. 以 `SignatureExpiryReconciler.run_once("m07-drill")` 對一個逾時仍 `pending` 的 signature request 執行；確認 request 轉 `expired`、對應 `report_version` 退回 `ready_to_sign`，可重新送簽而不遺失 frozen 內容。
5. 對帳 `report_version`／`signature_request`／`signature`／`revocation` 數量、audit correlation、outbox payload hash；將演練結果標示為 `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 publisher 與 reconciler，核對 inbox/outbox、`report_version.status` 與 provider lookup。只有確認未知結果後才恢復排程；真實簽章 provider、M14 物件儲存尚未接線時維持 `NOT_CONNECTED`。
