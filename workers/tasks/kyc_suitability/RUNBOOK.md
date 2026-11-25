# M03 KYC／適合度 worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實客戶文件掃描、身分驗證或外部 production 存取。

## 告警與判讀

- `m03_outbox_oldest_age_seconds > 30`：先確認 DB 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。禁止更換 idempotency key 盲重送。
- `m03_document_scanner_unavailable`：文件維持 `scan_status=pending`，不得顯示已審核／已通過；等掃描器恢復後重跑 `DocumentScanWorker`。
- `m03_due_diligence_manual_required`：`DueDiligenceWorker` 連續 timeout 達 `max_attempts` 或掃描回 `flagged` 時觸發；`refresh_case.status` 進 `manual_required`，不產生任何「通過」事件。人工覆核後才可能重置為 `pending` 由 worker 再次嘗試，或由權威流程直接結案。
- `m03_inbox_sequence_gap`：保留 retry receipt，補齊前序事件或由權威來源重建；不可直接把 sequence 標成 applied。
- dead letter（outbox）：人工重驅必填非空原因，並確認 provider lookup 顯示先前效果不存在。

## 合成故障恢復演練（文件掃描）

1. 建立 synthetic submission 並呼叫 `request_kyc_upload`；文件初始 `scan_status=pending`。
2. 將 `FakeDocumentScanner.register(asset_id, ScanOutcome("rejected", "malware"))`，執行 `DocumentScanWorker.run_once("m03-drill")`。
3. 確認 `get_document_status` 回 `scan_status=rejected`、`rejection_code=malware`，且無任何下載 URL；`submit_kyc` 引用該文件須回 `document_quarantined` 422。

## 合成故障恢復演練（到期查核）

1. 核准一筆 submission 後呼叫 `request_due_diligence_refresh`，建立 `refresh_case`。
2. 將 `FakeExternalVerification.fail(tenant_id, customer_id, "timeout")`，連續執行 `DueDiligenceWorker.run_once` 達 `max_attempts` 次。
3. 確認 `refresh_case.status == "manual_required"`、`alerts` 有對應告警字串，且過程中沒有任何「通過」或 suitability 自動維持 `suitable` 的副作用。

## 還原後啟動

還原環境先停 publisher／scan worker／due-diligence worker，核對 inbox/outbox、submission／refresh_case 狀態與 provider lookup。只有確認未知結果後才恢復排程；真實 LegendFlow／掃毒供應商／身分驗證尚未接線時維持 `NOT_CONNECTED`。
