# M13 資料庫整合提案

基準：`dev@48ae302308de64159ee25ed506fb6f2e548cf886`。本目錄不是已套用
migration；integration/data steward 應配置正式序號、更新
catalog.json／data-dictionary.md／grants，並在 disposable PostgreSQL 16 實跑。

`001_service_cases.sql` 補足基線無法表達的 M13 必要項：

1. `support.case_file` 缺少業務維護人（與客服承辦人分離，REQ-M13-012）、
   退款連結、SLA 暫停理由與升級安全 link/status 欄位；category/priority 的
   CHECK 補上使其與 `api/openapi.yaml` 的凍結 enum 一致（基線僅有文件註解，
   未實際約束）。
2. 新增 `support.case_resolution` 不可變子表，保存每次結案的完整證據，讓
   reopen 後再次結案不會覆寫或刪除原始結案紀錄（REQ-M13-011）；其
   `id` 對應 `support.case_resolved` 事件 payload 的 `resolution_record_id`。
3. `support.response_dispatch` 補上 worker lease 欄位（`lease_owner` /
   `lease_until` / `next_attempt_at` / `attempt_count` / `last_error_code`），
   讓 `ResponseDispatchSender` 能獨立於通用 outbox 對外送出通路做
   lease／bounded retry／dead-letter。
4. 基線 migration 定義了 `core.touch_version()` 與
   `core.reject_evidence_mutation()`，但未對任何 `support.*` 表接線；本提案
   替三個可變狀態表（case_file／response_draft／response_dispatch）接上
   CAS version-bump trigger，替六個證據表（interaction／sla_event／
   response_approval／response_attempt／response_receipt／webhook_nonce）與
   新表（case_resolution）接上 append-only guard，並對全部十個表加上
   tenant_isolation RLS policy。
5. 新增 `support.webhook_nonce`：`receive_service_response_receipt` 的
   `integrationSignature` 安全機制（`api/openapi.yaml` 的 `WebhookNonce`
   參數）要求拒絕重放的 `X-Webhook-Nonce`；此表與
   `support.response_receipt` 的 event_id/payload 去重邏輯分離（防重放
   ≠ 應用層 idempotency），`(tenant_id, provider, nonce)` 唯一。

## 整合步驟

1. 確認既有 synthetic case/draft/dispatch 筆數；回填
   `maintenance_owner_id`（可留 NULL，等 `ownership.assignment_changed`
   投影補上）。
2. 驗證 `NOT VALID` constraints 後轉為 validated
   （`ALTER TABLE ... VALIDATE CONSTRAINT ...`）。
3. 由資料 steward 授與 `longhao_m13` 最小 DML 權限；對
   `finance.refund`、`core.actor`、`core.object_asset`、`delivery.channel_binding`
   僅唯讀，不可寫入其他模組權威表。
4. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`
   （或 `--standalone`），另加本提案的 RLS／append-only／CAS trigger 案例。

回復採 forward-fix。已有 `support.case_resolution`、`support.response_dispatch`
attempt 歷史或 `support.interaction` 之後不可直接 drop 新欄位／表；應先停寫、
匯出可驗證證據，再由核准 migration 做相容降級。

## 驗證證據

本提案 SQL（含新增 `support.webhook_nonce`）已在本機 disposable PostgreSQL 16
（`pgserver` standalone single-user 引擎，PostgreSQL 16.2）依序套用
`database/migrations/0001_domains.sql` → `0004_database_roles.sql` →
`database/seeds/001_synthetic.sql` → 本檔，全部無 `ERROR`/`FATAL`/`PANIC`。
standalone 模式以 superuser 執行、RLS 策略不會真正生效，完整 RLS／並行測試
仍須 `--dsn-env` 或 `--ephemeral` 對一般使用者連線執行；本次僅確認
DDL／FK／CHECK／trigger 語法與相容性，不代表已完成正式 RLS 執行期驗收。
