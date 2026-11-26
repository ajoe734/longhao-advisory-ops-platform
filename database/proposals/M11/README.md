# M11 資料庫整合提案

基準：`48ae302308de64159ee25ed506fb6f2e548cf886`。本目錄不是已套用 migration；integration/data
steward 應配置正式序號、更新 catalog／dictionary／grants，並在 disposable PostgreSQL 16 實跑。

`database/migrations/0001_domains.sql` 與 `0003_business_guards.sql` 已建立
`ownership.policy_version` / `ownership.assignment` / `ownership.pool_entry` 的權威欄位、
CHECK 與 trigger（`guard_assignment_interval`、`guard_pool_entry`）。本次 M11 應用層實作（
`apps/api/src/modules/ownership_pool/`）直接依這三張表的欄位語意建模，**沒有**另造平行
schema。`001_ownership_pool_reason_code_reconciliation.sql` 只補兩項既有基線與較新 wire
契約（`api/openapi.yaml`、`events/schemas/ownership.*.v1.schema.json`）之間的落差：

1. **`ownership.pool_entry.reason_code` 的列舉落後於 wire 契約。** 基線 CHECK 只允許
   `staff_departure` / `all_paid_services_lapsed` 兩值；但 OpenAPI 的 `PoolEntry.entry_reason`
   與事件 `ownership.pool_entered` 的 `entry_reason` 都是四值列舉：
   `unassigned_web` / `employee_departure` / `paid_service_lapse` / `manual_release`。
   OpenAPI／事件 schema 是較新、已合併到 `dev` 的 wire 契約（見本任務 task brief 的交付範圍），
   本提案將 DB CHECK 擴充對齊，而不是在應用層另造一套未經資料庫驗證的列舉
   （違反 AI_COLLABORATION_GUIDE §0.7「不能在本模組私增列舉」的精神——這裡是把 DB 補齊到契約，
   不是繞過契約）。`staff_departure`／`all_paid_services_lapsed` 兩個舊值保留相容，供既有
   seed／測試資料沿用；新寫入一律使用四值列舉。
2. **`ownership.assignment` 缺少「這段關係如何結束」的欄位。** 基線的 `reason_code` 語意是
   「這段 assignment 為什�么建立」（`assignment`/`departure`/`service_lapse`/`redevelopment`），
   但 `MaintenanceAssignment.state`（`active`/`released`/`departure`/`superseded`）描述的是
   「這段 assignment 如何結束」——兩者是同一列的不同維度，不能共用一個欄位又不互相覆蓋。
   本提案新增 `ownership.assignment.end_state`（nullable text，`valid_until IS NOT NULL` 時才
   允許非 null），應用層（`apps/api/src/modules/ownership_pool/domain.py::Assignment.wire_state`）
   目前以記憶體欄位模擬這個欄位；正式接線時應改為直接讀寫這張新欄位。

## 整合步驟

1. 先在 disposable DB 套用本提案（`ALTER TABLE ... DROP CONSTRAINT` + `ADD CONSTRAINT`、
   `ADD COLUMN end_state`），確認既有 seed 資料（`database/seeds/001_synthetic.sql`）不違反
   新 CHECK。
2. 由資料 steward 核對 `database/catalog.json`、`database/data-dictionary.md` 的
   `reason_code`／新增 `end_state` 欄位描述與本提案一致後再合併進基線 migration 序號。
3. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，
   另外針對新 CHECK／欄位補 `database/tests/001_invariants.sql` 的對應正反案例
   （四值列舉各一筆成功、舊兩值仍可寫入、`end_state` 在 `valid_until IS NULL` 時必須是 null）。

回復採 forward-fix；已核定 policy 或已發布 pool_entry 不可直接降級欄位。

## 契約協調項（非 DB 變更，供 reviewer 核對）

- `MaintenanceAssignment.salesperson_id` 在 OpenAPI 是必填 UUID（不可 null），但
  `ownership.assignment` 的 `company_pool` 列 `owner_actor_id` 是 null——應用層
  （`Assignment.to_api`）只對 `owner_type == "sales"` 的列產生 wire 資源，`company_pool`
  列永不透過 `MaintenanceAssignment` API 曝光，只在 `ownership.assignment_changed` 事件
  （payload 允許 `salesperson_id: null`）與 `ownership.pool_entry` 曝光。此行為與第 10 節
  「公司Pool工作台」UI 的「清楚分目前維護人和歷史計獎人」一致，不需要契約變更。
