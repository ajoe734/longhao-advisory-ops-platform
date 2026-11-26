# M14 資料庫整合提案

基準：`48ae302308de64159ee25ed506fb6f2e548cf886`（dev）。本目錄不是已套用 migration；integration/data steward 應配置正式序號、更新 `catalog.json`／`data-dictionary.md`／grants，並在 disposable PostgreSQL 16 實跑。本次未在本機啟動 PostgreSQL（VM 限制不得啟動 runtime／DB 基礎設施）；以下 SQL 僅完成括號與 dollar-quote 的靜態檢查，尚未經 `python database/tools/verify_database.py` 實際驗證，狀態為 `NOT_RUN`。

`database/migrations/0001_domains.sql` 已包含全部 14 張 `finance.*` 表與共用 `core.legal_hold`／`core.audit_event`；`001_finance_control.sql` **不建立或重建**這些表，只 ALTER 補上 OpenAPI 1.0.0（`api/openapi.yaml`）wire contract 需要、但目前欄位集缺少的部分，並新增兩張 M14 自己的未核准 automation 證據表（`finance.reconciliation_automation_run`／`_review`，與 M01 的 `identity_automation_run`／`_review` 同構）。

## 本提案涵蓋

- `finance.payment`：補 `method`／`return_route`／`checkout_url`／`expires_at`，承接 pre-capture 的 PaymentIntent 投影。
- `finance.invoice`／`invoice_line`：補業務 `invoice_date`／`due_date`（與既有事件時間 `issued_at`／`due_at` 分開），以及每行 `service_period_start/end`／`quantity`／`tax_code`／`tax_mapping_version`／`service_code`。
- `finance.reconciliation_item`：補 `settlement_id`（`uuid`；永遠是真實 `finance.settlement.id`，或在 unmatched／duplicate 無對應 settlement 列時由應用層產生的合成 uuid，不再是 `batch_ref:transaction_ref` 這種非 uuid 字串）／`difference_type`／`expected_amount`／`actual_amount`，與既有 `source_type`／`source_ref`／`difference_amount` 並存供 mapper 轉換；`status` CHECK 改為應用層實際使用的 `open`／`investigating`／`resolved`／`escalated`（取代基線的 `unmatched`／`matched`／`exception`），預設值同步改為 `open`。
- `finance.revenue_run`：補 `calculation_detail jsonb`，持久化計算時included／exception schedule id，讓 `approve_revenue_run` 核准時過帳內容可與 `calculation_manifest_sha256` 獨立重算核對。
- `finance.revenue_recognition`：補 `contract_id`／`contract_service_id`，讓 `finance.revenue_recognized` 事件可從單一列重建而不需每次 replay 都重新 join。
- `finance.refund`：補 `contract_id`／`customer_request_evidence_id`／`calculation_policy_version`／`approved_amount`／`provider_idempotency_key`（含 tenant 範圍唯一索引），確保 execute_refund 重試永遠使用同一組對外 idempotency key；`status` CHECK 加入 `accounting_adjusted`（確認退款過完反向分錄後的終態，基線原本缺這個值）。
- `finance.refund_allocation`：補 `invoice_line_ref`，讓既有一列一 `allocation_type` 的紀錄可被 mapper 重新分組回 wire 的單一 allocation 物件。
- `finance.accounting_export`：補 `period_start/end`／`account_mapping_version`／`tax_mapping_version`／`debit_total`／`credit_total`／`entry_count`／`accepted_journal_ids`／`rejected_journal_ids`／`applied_ack_event_ids`，並放寬 `status` CHECK 以涵蓋 wire 的 `exported`／`partially_acknowledged`／`failed`。
- `finance.control_exception`：補 `description`／`evidence_asset_ids`（複數）／`closed_by`／`closure_evidence_id`，並放寬 `status` CHECK 以涵蓋 `ready_for_review`／`reopened`。
- 新表 `finance.reconciliation_automation_run`／`_review`：未核准 LLM 對帳說明草稿與人工採用紀錄，無解決權限；已含 RLS、guard trigger。

## 整合步驟

1. 先確認既有 synthetic `finance.*` 筆數；新欄位均為 nullable 或有安全 default，回填不阻塞現有資料。
2. 驗證 `NOT VALID` constraints 後轉為 `VALIDATE CONSTRAINT`，確認回填完成（特別是 `finance.refund.contract_id`、`finance.invoice.invoice_date`／`due_date`）。
3. 由資料 steward 更新 `longhao_m14` 角色授權（`database/migrations/0004_database_roles.sql`）以涵蓋新表；其他模組只經事件/API 讀取，不直接查 `finance.*`。
4. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加本模組 12 項 UAT 對應的 constraint 測試（不平衡分錄拒絕、SoD、legal hold 保全、export ack 去重）。

回復採 forward-fix。已有 `finance.refund`／`finance.journal`／`finance.accounting_export` 證據後不可直接 drop 新欄位；應先停寫、匯出可驗證證據，再由核准 migration 做相容降級。
