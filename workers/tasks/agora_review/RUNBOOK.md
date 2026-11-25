# M06 Agora/Servant worker 操作手冊

本手冊只適用本機／測試 `FakeServant` adapter；不得用來啟動真實 Agora/Pantheon 對話、修訂任務或外部網路存取。

## 告警與判讀

- `m06_outbox_oldest_age_seconds > 30`：先確認 repository 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。禁止更換 idempotency key 或 revision id 盲重送（會建立第二個外部 job）。
- `m06_servant_dependency_unavailable`：`open_servant_conversation` 應回 503/retryable；`get_review_case_workspace` 的 `servant_health.status` 應為 `degraded` 且保留 `last_success_at`，不可顯示空修訂清單或誤報完成。
- `m06_inbox_sequence_gap`：保留 `retry` receipt，等待缺的 `research.candidate_*` 事件或由 M05 重建；不可直接把 sequence 標成 applied。
- dead letter：人工重驅（`OutboxPublisher.redrive`）必填非空原因；重驅前先用 `servant.get_revision_status(job_ref)` 確認外部是否已經有 job，避免建立第二個 Servant 任務。

## 合成故障恢復演練（UAT-M06-007 對應）

1. 分析師呼叫 `request_servant_revision`，建立 `review.revision_requested` outbox 事件與一筆 `requested` revision。
2. 對 `FakeServant.fail_job(job_ref, "timeout_after_register")` 設定一次性故障，執行 `OutboxPublisher.run_once("m06-drill")`；確認 outbox 事件為 `retry`、`attempt_count` 增加，但 revision 仍是 `requested`，且 `FakeServant.submitted_job_count(job_ref) == 1`（server 端已經收到，只是回應逾時）。
3. 把 outbox event 的 `next_attempt_at` 往前推進並再次執行 `run_once`；worker 會先查 `get_revision_status(job_ref)`，發現任務已存在，不重新送出，直接將 revision 轉為 `running` 並完成 outbox 發送。
4. 驗證全程只有一個外部 Servant job（`submitted_job_count` 仍為 1）、沒有重複建立 conversation 或 revision row。

## 還原後啟動

還原環境先停 `OutboxPublisher`，核對 inbox（`research.candidate_*`）、outbox（`review.revision_requested` / `review.revision_received`）與 revision 狀態。只有確認外部任務的真實狀態（而非假設 timeout=failure）後才恢復排程；真實 Agora/Servant 尚未接線時維持 `NOT_CONNECTED`，`review.revision_received` 對 M07 的轉送目前只進入本機 `LocalEventBus`。
