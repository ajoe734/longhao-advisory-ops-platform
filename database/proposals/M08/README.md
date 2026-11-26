# M08 資料庫整合提案

基準：`dev` 分支 `31fe1c7`（`database/migrations/0001_domains.sql` 建立的既有 baseline）。本目錄不是已套用
migration；integration/data steward 應配置正式序號、更新 catalog／dictionary／grants，並在 disposable
PostgreSQL 16 實跑。

`001_publication.sql` 補足基線無法表達的 M08 必要證據：`hold` 建議動作、不可變 rationale 物件引用、
Pantheon/Agora 來源候選追溯、超過一筆的支持報告版本、發布與受眾快照的雙向引用、簽章事件追溯，以及列表
讀取用的去正規化受眾人數。既有五張表（`recommendation_series`、`recommendation_version`、`publication`、
`audience_snapshot`、`recipient`）的欄位、CHECK、UNIQUE 與既有歷史保護 trigger 維持不變，只新增欄位與一張
子表。

整合步驟：

1. 先確認既有 synthetic recommendation_version 筆數；回填 `rationale_asset_id`、`source_candidate_id`
   （若合成資料缺少這兩項,由 steward 決定是否可留 NULL 直到下一批合成資料補齊）。
2. 驗證 `NOT VALID` constraints（`m08_series_origin_ref_ck`、`m08_recommendation_version_rationale_fk`、
   `m08_publication_audience_snapshot_fk`）後轉為 validated。
3. 由資料 steward 授與 `longhao_m08` 最小 DML 權限；其他 module 只經 port/projection 讀取
   （`publication.publication`／`publication.recommendation_series` 等權威表仍只由 M08 寫入）。
4. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加本模組
   UAT-M08-001～012 對應的 DB 層案例（見 `database/proposals/M08/contract-changes.md` 第2節的欄位映射）。

本次 agent 執行環境依指示不得啟動 Docker Compose 或正式 PostgreSQL 服務，因此上述 `verify_database.py`
與 disposable PostgreSQL 16 實跑**本次未執行**；`001_publication.sql` 僅完成靜態撰寫與人工核對 baseline
欄位/constraint 名稱（含不可用猜測名稱、改用 `pg_constraint` 動態查找），尚未經真實資料庫驗證。交由
integration steward 或具備 PostgreSQL 服務的 hosted workflow 執行第4步。

回復採 forward-fix。已有 publication、audience_snapshot 或 recipient 證據後不可直接 drop 新欄位；應先停
寫、匯出可驗證證據，再由核准 migration 做相容降級。

契約協調項見 `contract-changes.md`，包含 OpenAPI `supporting_report_version_ids` 的 minItems 缺口與完整
API/資料表 mapper 新增列。
