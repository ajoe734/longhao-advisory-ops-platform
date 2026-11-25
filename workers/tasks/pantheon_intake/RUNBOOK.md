# M05 Pantheon 候選接收 worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實 Pantheon 連線、金流或對客發布。

## 告警與判讀

- `m05_verify_job_dead_letter_count > 0`：先查 `last_error_code`；`evidence_url_*` 系列是 SSRF 阻擋（不可重試，需更換來源 URL 並由研究營運補正，不得放寬 allowlist 繞過）；`pantheon_source_unavailable`／`evidence_source_timeout` 是暫時性，可人工 redrive。
- `m05_inbox_sequence_gap`：保留 retry receipt，補齊前序 `research.candidate_received` 事件或由權威來源重建；不可直接把 sequence 標成 applied。
- `m05_outbox_oldest_age_seconds > 30`：先確認 DB 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。禁止更換 idempotency key 盲重送。
- dead letter：人工重驅必填非空原因；SSRF 阻擋的 job 重驅前必須先確認來源 URL 已落在核准 allowlist，不能直接重試原阻擋目標。

## 合成故障恢復演練（REQ-M05-012）

1. 建立 candidate（`ingest_research_candidate`），不註冊 `FakeEvidenceSource` 的 URL，讓 `CandidateVerificationWorker.run_once()` 因 `pantheon_source_unavailable` 失敗。
2. 確認 job 狀態為 `retry`、`attempt_count` 增加、candidate 仍可用 `get_research_candidate` 查到（`status=received`，`received_at` 不變）。
3. 重複執行直到 `attempt_count >= max_attempts`，確認 job 進入 `dead_letter`；candidate 仍非空、未被重建（同一 `id`／`received_at`）。
4. 補上合法 URL 後用 `TaskRunner.redrive()` 附非空原因重新排入；執行 worker 一次成功後確認 `research.candidate_validated` 只發出一次、`received_at` 與原值相同。

## SSRF 阻擋演練（REQ-M05-009）

1. 將 `FakeEvidenceSource` 的 URL 指向非 allowlist host 或 metadata/link-local 位址（如 `169.254.169.254`）。
2. 執行 worker；確認 `SsrfSafeEvidenceFetcher` 拒絕且未呼叫 `FakeEvidenceTransport.get()` 取得 body，job 直接進 `dead_letter`，candidate 轉 `quarantined` 並帶 `evidence_access_unconfirmed`／`license_scope_unconfirmed`。
3. 確認將目標改回 allowlist host 並清掉惡意 redirect 後，才可人工 redrive；不得放寬 `ALLOWED_EVIDENCE_HOSTS`。

## 還原後啟動

還原環境先停 publisher／verification worker，核對 inbox/outbox、candidate 狀態與 job 佇列。只有確認未知結果後才恢復排程；真實 Pantheon／Agora 尚未接線時維持 `NOT_CONNECTED`。
