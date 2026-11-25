# M08 發布 worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實客戶訊息或外部 production 存取。

## 告警與判讀

- `m08_publish_job_dead_letter`：先讀 `job.last_error_code`。`bundle_hash_mismatch` 代表簽後內容與簽署時
  不一致，必須由分析師建新版本覆核簽章，不可直接重驅同一 job。`entitlement_resolution_timeout` 達上限才
  可考慮重驅，先確認 M02 依賴已恢復。
- `m08_publication_expired_blocked`：job 處理時發現 `valid_until` 已過，屬正常攔截（REQ-M08-011），不是
  故障；若頻繁出現代表上游簽章延遲過久，需追查 M07 流程時間，不要放寬 `valid_until` 當修復。
- `m08_outbox_oldest_age_seconds` 過高：先確認 DB 可用、lease 是否過期，再看 `last_error_code`；禁止更換
  idempotency key 盲重送。
- `m08_inbox_sequence_gap`：保留 retry receipt，等待缺的 `report.signature_verified`/`report.signature_revoked`
  序號到達；不可直接把 sequence 標成 applied。

## 合成故障恢復演練

1. 建立 series／version，註冊 `FakeReportBundleRegistry` 一致的 bundle，consume
   `report.signature_verified`。
2. 將 `FakeEntitlementAudienceRegistry.failure_mode` 設為 `timeout`，執行
   `PublicationWorker.run_once("m08-drill")`；確認 job 為 `retry`，`publication.publication` 未建立。
3. 重複呼叫 `PublicationService.process_publish_job` 到 `attempt_count` 達上限，確認 job 轉
   `dead_letter`，且沒有半成品 publication／recipient 留下。
4. 清空 failure mode 後用 `PublicationWorker.redrive(tenant_id, job_id, reason)` 重驅；若
   `valid_until` 已過，確認結果是 `expired_blocked` 而非重新發送。
5. 對帳 recommendation_version／publication／audience_snapshot／recipient 數量、audit correlation、
   outbox payload hash；將演練結果標示為 `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 worker，核對 inbox/outbox、publication 狀態與 bundle registry。只有確認未知結果後才恢復
排程；真實 M07/M02/M09 介接尚未接線時維持 `NOT_CONNECTED`。
