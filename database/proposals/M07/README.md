# M07 資料庫整合提案

基準：`48ae302`（`dev` 於本任務開工時的 head）。本目錄不是已套用 migration；integration/data steward 應配置正式序號、更新 catalog／dictionary／grants，並在 disposable PostgreSQL 16 實跑。

`report.report`／`report.report_version`／`report.review_decision`／`report.signature_request`／`report.signature`／`report.revocation` 六張表已由 `database/migrations/0001_domains.sql`（及 `0002_isolation_indexes.sql`、`0003_business_guards.sql` 的 RLS、索引、`report.guard_frozen_version`／`report.guard_signature`／`report.require_signature_outbox` trigger）建立。`001_report_signature.sql` 只補足本模組 API 契約需要、而 baseline 尚未表達的欄位：

- `report.report.report_type` CHECK 擴充含 `correction`（OpenAPI `ReportCreate.report_type` 已有此值，baseline CHECK 尚未跟上）；新增 `preceding_report_id`（更正／接續同系列報告的穩定連結）。
- `report.report_version` 新增 `supersedes_version_id`（REQ-M07-005 簽後修訂建立新版本時記錄取代關係）與 `required_review_ids`（凍結時綁定的 review id 清單，凍結後與其他 frozen 欄位一樣不可變）。
- `report.review_decision` 新增 `reason_code`（`ReportReviewRequest.reason_code` 的持久化欄位）；`evidence_asset_id` 加上 `NOT VALID` CHECK，分階段收斂為必填（本模組每筆覆核皆要求 checklist 證據，baseline 原允許 NULL 過寬）。
- `report.signature_request` 新增 `redirect_url`（provider 會話導向位址）與 `last_receipt_id`（provider 重送同一 request 的 callback 時回同一 receipt，對應 UAT-M07-008）。
- `report.revocation` 新增 `publication_withdrawal_event_id`（追溯撤銷同交易發出的 `report.signature_revoked` outbox event）。

新欄位沿用既有風格：只記錄同 tenant 的 uuid 參考並以 `COMMENT` 說明語意，不另加跨 aggregate 的 `FOREIGN KEY`（baseline 本身對 `review_case_id`／`candidate_id`／`reviewer_id` 等欄位也只用註解，referential 正確性由應用層與既有 `report.guard_*` trigger 負責；`report.report`／`report.report_version` 的主鍵是 `(tenant_id, id)` 複合鍵，無法對單一 `id` 建立標準 FK）。

## 整合步驟

1. 先確認既有 `report.review_decision` 筆數與 `evidence_asset_id` 是否均非空；若有既存合成資料缺此欄位，先回填再執行 `VALIDATE CONSTRAINT`，最後 `SET NOT NULL`（檔內已標註對應的三個待執行陳述）。
2. 確認 `DO $$ ... $$` 區塊真的只找到一條 `report_type` CHECK（`\d report.report` 核對）後才執行替換；多條時先手動處理，不要盲目執行。
3. 由資料 steward 授與 `longhao_m07`（若尚未建立對應角色，先比對 `0004_database_roles.sql` 既有角色慣例）最小 DML 權限；其他 module 只經本模組 API／事件讀取，不直接存取 `report.*`。
4. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加本模組跨 tenant、frozen 後內容不可變（`report.guard_frozen_version`）、簽章 gate（`report.guard_signature`）、同交易 outbox（`report.require_signature_outbox`）案例；三者已由 baseline trigger 實作，本提案的 ALTER 不得破壞既有 trigger 相依欄位。

回復採 forward-fix。已有 `report.signature`／`report.revocation` 證據後不可直接 drop 新欄位；應先停寫、匯出可驗證證據，再由核准 migration 做相容降級。

## 本模組實作與 DB trigger 的一致性

本模組應用層（`apps/api/src/modules/report_signature/service.py`）目前以 in-memory `InMemoryRepository` 運作，尚未接到真實 PostgreSQL；為降低日後接線風險，已刻意對齊 baseline 既有 `report.guard_signature` 的關鍵邊界：

- 簽章驗證時重驗 `signed_at` 必須在 `frozen_at` 之後、`signature_request.expires_at` 之前、`report_version.valid_until` 之前（否則回 `quarantined`，frozen package 不變）。
- 簽章驗證失敗或 payload hash 不符時一律把 `report_version` 退回 `ready_to_sign`、`signature_request` 標 `failed`，不嘗試盲目重簽。

`report.guard_signature` 另外重驗 `core.staff_credential.credential_type='analyst'` 及撤銷歷史（`core.credential_revocation`）；本模組的 `SignerQualificationPort`/`FakeQualificationRegistry` 是本地測試替身，真正接線需由 steward 提供對應 PostgreSQL-backed adapter，目前標記 `NOT_CONNECTED`。
