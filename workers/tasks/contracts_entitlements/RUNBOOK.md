# M02 契約與權限 worker 操作手冊

本手冊只適用本機／測試 provider；不得用來啟動真實客戶通知、金流或 LegendFlow production 存取。

## 告警與判讀

- `m02_outbox_oldest_age_seconds > 30`：先確認 DB 可用、lease 是否過期、worker clock offset，再看 `last_error_code`。禁止更換 idempotency key 盲重送。
- `m02_gate_port_unavailable`：KYC/適合度/AML/付款 port 回 503/retryable 時，`decide_company_acceptance`／`evaluate_entitlements` 必須保持原狀態，不得以逾時推定通過或失敗。
- `m02_inbox_sequence_gap`：保留 retry receipt，待補齊前序事件或由權威來源重建；不可直接把 sequence 標成 applied。
- `m02_entitlement_recheck_dead_letter`：`EntitlementRecheckWorker` 的 job 進 dead_letter 時，人工重驅必填非空原因，並先確認對應 contract 目前狀態與 gate port 快照，避免對已終止或已暫停的 entitlement 盲目重算。
- dead letter 一般原則：人工重驅必填非空原因，並確認 provider lookup 顯示先前效果不存在。

## 事件消費邊界

`M02EventConsumer` 只做 inbox 去重、payload hash／schema 檢查與 audit；收到 `kyc.decision_recorded`、`suitability.assessed`、`business_clearance.changed`、`finance.payment_confirmed` 只會建立一筆 `gate_changed_recheck`／`payment_confirmed_recheck` job，不直接改 entitlement 狀態——真正的授權判斷在 `evaluate_entitlements` 執行時，由各自 port 即時重查權威狀態完成。`kyc.refresh_required` 只建立 `kyc_refresh_pending_review` job 供人工複核，不自動 suspend；`LegendFlow` 或任何下游都不可憑事件自行開通或撤銷權限。

## 合成故障恢復演練

1. 將 `LocalEventBus.failure_mode` 設為 `timeout`，對已 accept 的 application 呼叫 `evaluate_entitlements` 使其產生 `contract.effective`／`entitlement.changed` outbox 項目。
2. 執行 `OutboxPublisher.run_once("m02-drill")`；確認 event 為 `retry`、attempt 增加、`contract`／`entitlement` 資料列與 audit 仍存在且未被重覆建立。
3. 將 failure mode 清空並把測試 clock 推進至 `next_attempt_at`，再執行 publisher。
4. 確認同一 event_id 只出現一次、狀態為 `published`，且 `contract.entitlement_decision` 沒有因重試而重覆產生生效決策。
5. 對帳 contract／entitlement／entitlement_decision 數量、audit correlation、outbox payload hash；將演練結果標示為 `FAKE_PROVIDER`。

## 還原後啟動

還原環境先停 publisher 與 `EntitlementRecheckWorker`，核對 inbox/outbox、contract/entitlement 狀態與各 gate port 快照。只有確認未知結果後才恢復排程；真實 LegendFlow／簽章供應商／KYC／AML／金流連線仍維持 `NOT_CONNECTED`。
