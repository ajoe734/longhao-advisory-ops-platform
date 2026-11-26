# M04 資料庫整合提案

基準：dev 分支共用平台 migrations `0001`-`0004`（PostgreSQL 16）。本目錄不是已套用 migration；integration/data steward 應配置正式序號、更新 catalog／dictionary／grants，並在 disposable PostgreSQL 16 實跑。

`001_aml_cft.sql` 補足基線無法表達的 M04 必要證據與契約落差：`risk_level` 列舉校正為 OpenAPI 的 `low/standard/high`、`screening_ids`／`restricted_evidence_ids` 的完整陣列關聯表、`AmlDeclarationReceipt` 的持久化表、內稽限定時效 grant，以及 `aml.screening` 補上 `scope`／`reason_code`／`manual_required`。新表均有 composite tenant FK、RLS 與 immutable/version guard；既有 `aml.screening`／`case_file`／`case_action`／`business_clearance` 基線表不變更其核心語意。

整合步驟：

1. 先在 disposable DB 確認基線 `aml.*` 表已存在（由共用 migrations 0001 建立）；若 `risk_level` 已有既有合成資料為 `medium`/`undetermined`，先執行本提案的 `UPDATE` 回填，再驗證 `NOT VALID` constraints 轉為 validated。
2. 由資料 steward 授與 `longhao_m04` 對新四張表的最小 DML 權限；`longhao_aml_audit` 只讀 `aml.case_read_grant`／`aml.case_file`（依 `database/README.md` 既有 AML 稽核角色規則），一般 `longhao_audit` 不可讀。
3. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加本模組跨 tenant、legal hold 阻擋刪除、maker-checker（透過 `case_action.actor_id` 與核定 actor 比對）、過期 clearance 投影為 `expired` 的案例。
4. 確認 `contract-changes.md` 列出的契約協調項（risk_level 列舉、陣列關聯表、宣告收據表、內稽 grant operationId、legal hold 掛靠既有 `core.legal_hold`）已交 API／資料字典 steward 排期。

回復採 forward-fix。已有 `declaration_receipt`、`case_screening_link`、`case_evidence_link` 或 `case_read_grant` 資料後不可直接 drop；應先停寫、匯出可驗證證據，再由核准 migration 做相容降級。
