# M12 資料庫整合提案

基準：`dev@48ae302308de64159ee25ed506fb6f2e548cf886`。本目錄不是已套用 migration；integration/data steward 應配置正式序號、更新 catalog／dictionary，並在 disposable PostgreSQL 16 實跑。

`001_commissions.sql` 補足 baseline `commission.*`（0001_domains.sql）無法表達、但本模組實作（`apps/api/src/modules/commissions/{domain.py,service.py}`）實際讀寫的欄位與兩個新增 append-only 子表，並修正兩個與已核准業務流程衝突的既有 trigger 參數。詳見 [contract-changes.md](contract-changes.md) 的逐項依據與未決事項。

## 整合步驟

1. 先在空白測試資料庫套用 `migrations/0001-0004` 基線，再套用本提案。
2. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加本提案新增的 CHECK／trigger 測試案例（approve_commission_policy 設定 base_pool_rate、run worker 寫入 input_sha256、acknowledge 去重、adjustment 原受款人沖回）。
3. `NOT VALID` constraints 由 steward 在確認既有 synthetic 列已回填（`pool_policy_version`、`recognition_policy_version`、`policy_sha256` 等）後 `VALIDATE CONSTRAINT`；本提案刻意不自動轉為 validated，避免對未知既有列資料做未經覆核的假設。
4. 由資料 steward 授與 `longhao_m12` 最小 DML 權限；本模組目前以 `InMemoryRepository`（見模組 README「架構選擇」一節）開發與測試，尚未接上本提案的 PostgreSQL schema，因此未執行的 SQL 驗證項目在交付時標記 NOT_RUN，不得宣稱已過。
5. ENG-COM-01（adjustment maker/checker 簡化）與 refund_id／refund_allocation_id 語意對齊兩項未決事項見 [contract-changes.md](contract-changes.md) 與 [docs/18-decision-register.md](../../../docs/18-decision-register.md)；本提案刻意不自行決定，只記錄衝突與選項供 steward 核定。

回復採 forward-fix；已有 adjustment、payout 回執或 run 計算證據後不可直接 drop 新增欄位，應先停寫、匯出可驗證證據，再由核准 migration 做相容降級。
