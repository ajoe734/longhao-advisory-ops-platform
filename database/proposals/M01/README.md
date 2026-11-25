# M01 資料庫整合提案

基準：`4b9adeb09485386c04d6ce38d789e5058ebe2082`。本目錄不是已套用 migration；integration/data steward 應配置正式序號、更新 catalog／dictionary／grants，並在 disposable PostgreSQL 16 實跑。

`001_customer_identity_application.sql` 補足基線無法表達的 M01 必要證據：identity revocation、申辦撤回時間與理由、不可覆寫 first-touch 與後續 touch、同聯絡 HMAC 的非唯一人工候選、多筆 merge evidence、歷史 customer alias、consent 文件 digest，以及最小化 automation run／人工採用修改紀錄。新表均有 composite tenant FK、RLS、索引及 immutable/version guard。

整合步驟：

1. 先確認既有 synthetic consent/application 筆數；回填 `document_sha256`、`original_acquisition_ref`、已撤回列的時間／理由。
2. 驗證 `NOT VALID` constraints 後轉為 validated，最後把 `customer.consent.document_sha256` 設為 `NOT NULL`。
3. 由資料 steward 授與 `longhao_m01` 最小 DML 權限；其他 module 只經 port/projection 讀取。
4. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加跨 tenant、不可變 evidence、同 contact HMAC 可多 customer、同 alias 不可重複案例。

回復採 forward-fix。已有 customer alias、merge evidence 或 withdrawal facts 後不可直接 drop；應先停寫、匯出可驗證證據，再由核准 migration 做相容降級。

契約協調項：OpenAPI `Customer.status=blocked` 對 DB `restricted`、`MergeCase.status=applied` 對 DB `executed` 已由 mapper 處理。Customer 360 的 `source_status/last_success_at` 尚未在 shared wire schema，應由 API steward做 optional 相容新增；在此之前 UI projection不得把 degraded 顯示為空集合。
