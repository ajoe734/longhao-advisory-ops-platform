# M04 AML/CFT worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實名單查詢、通報或外部 production 存取。

## 告警與判讀

- `m04_screening_dispatch_manual_required`：`ScreeningDispatchWorker` 呼叫名單 provider 逾時／失敗；screening 已標記 `error`，`manual_required=true`，job 已 dead-letter。禁止直接把 screening 標成 `clear`；officer 必須另外發起新的 `start_aml_screening`。告警只含 provider 名稱，不含客戶個資。
- `m04_outbox_oldest_age_seconds > 30`：先確認 DB 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。`aml.case_changed` 與 `business_clearance.changed` 都只含安全摘要欄位，重送不會洩漏案情。
- `m04_inbox_sequence_gap`：保留 retry receipt，等待前序事件或由權威來源（M01/M02/M03）重建；不可直接把 sequence 標成 applied。
- legal hold：`RetentionSweeper` 對 `legal_hold=true` 的案件一律回報 `skipped_hold`，不嘗試刪除或解除限制；只有新的核定動作可以解除 hold。

## 合成故障恢復演練

1. 呼叫 `start_aml_screening` 建立一筆 screening；將 `FakeScreeningProvider.failure_mode` 設為 `"timeout"`。
2. 執行 `ScreeningDispatchWorker.run_once("m04-drill")`；確認 screening 變為 `state=error`、`manual_required=true`，dispatch job 變為 `dead_letter`。
3. 確認 `get_business_clearance` 回傳 `decision=pending`（不是 `approved`），且 telemetry 的 `dependency_failure("screening_provider")` 計數增加。
4. 清空 failure mode 後，officer 另發一次 `start_aml_screening`（新的 idempotency key），確認新 screening 可正常 dispatch。
5. 對帳 screening／case／clearance 數量與 audit correlation；將演練結果標示為 `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 `OutboxPublisher`／`ScreeningDispatchWorker`，核對 inbox/outbox、screening 狀態與 provider lookup。只有確認未知結果後才恢復排程；真實名單 provider 尚未接線時維持 `NOT_CONNECTED`。
