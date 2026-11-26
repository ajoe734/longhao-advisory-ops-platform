# M11 維護歸屬 worker 操作手冊

本手冊只適用本機／測試 adapter；不得用來啟動真實客戶通知或薪酬發放。

## 告警與判讀

- `m11_outbox_oldest_age_seconds > 30`：先確認 lease 是否過期、worker clock offset，再看 `last_error_code`；不得更換 idempotency key 盲重送。
- `m11_inbox_sequence_gap`（contract.effective/terminated/service_ended）：保留 retry receipt；在補齊前序事件前，`evaluate_company_pool` 看到的付費服務視窗可能不完整，寧可少判定「全部中斷」，不可假設缺的事件已結束。
- `m11_policy_not_approved`：`evaluate_company_pool` 的 live（`dry_run=false`）呼叫被拒；確認是否誤排程到尚未核准的 policy_version，不要自動切換成另一個已核准政策。
- dead letter：人工重驅必填非空原因；重驅前先確認該 outbox event 的業務效果（assignment/pool_entry 狀態）尚未被其他路徑完成。

## 合成故障恢復演練

1. 將 `LocalEventBus.failure_mode` 設為 `timeout`，透過 `release_maintenance_owner` 建立一筆 pool_entry。
2. 執行 `OutboxPublisher.run_once("m11-drill")`；確認對應 outbox event 為 `retry`、attempt 增加，而 assignment/pool_entry 的業務狀態不受影響。
3. 清空 failure mode 並將測試 clock 推進至 `next_attempt_at`，再執行一次 publisher。
4. 確認同一 event_id 只出現一次、狀態為 `published`；pool_entry 沒有被重建或重複釋出。
5. 對帳 assignment／pool_entry 數量與 audit correlation、outbox payload hash；將演練結果標示為 `FAKE_PROVIDER`。

## 政策批次（policy sweep）

`run_policy_sweep` 以單一 idempotency key（建議每 UTC 日一個）呼叫
`evaluate_company_pool(dry_run=false)`；同一天重跑不會重複執行回收。只有
`policy_version` 對應的 `ownership.policy_version.approval_status == 'approved'`
時才會產生實際效果，否則回 `policy_not_approved`，不會自動改用其他政策或靜默略過。

## 還原後啟動

還原環境先停 publisher 與 policy sweep；核對 inbox/outbox、assignment/pool_entry
狀態與 evaluate_company_pool 最近一次 evaluation 的 evidence_export_asset_id 對帳。
真實 CRM activity／HR adapter 尚未接線時維持 `NOT_CONNECTED`。
