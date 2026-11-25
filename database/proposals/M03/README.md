# M03 資料庫整合提案

基準：`dev@48ae302`（`database/migrations/0001_domains.sql` 已含 `kyc.submission`、`kyc.submission_document`、`kyc.suitability_assessment`）。本目錄不是已套用 migration；integration/data steward 應配置正式序號、更新 catalog／dictionary／grants，並在 disposable PostgreSQL 16 實跑（本次僅用 `pglast` 做語法解析驗證，未連實際 PostgreSQL）。

`001_kyc_suitability.sql` 補足基線無法表達的 M03 必要證據：

1. **隔離區掃描與人工審核分離**：baseline `kyc.submission_document.verification_state` 只有人工審核結果；新增 `scan_status`／`scan_rejection_code`／`scanned_at`／`classification` 表達防毒/MIME掃描結果，並以 CHECK `m03_submission_document_review_requires_clean_ck` 保證未掃描完成或遭拒文件無法進入人工審核（UAT-M03-004）。
2. **OCR 未信任草稿**：`kyc.document_extraction`／`kyc.document_extraction_review` 保存欄位抽取建議、信心度、是否與客戶原答衝突，以及完整 model/prompt/ruleset 版本與人工採用/拒絕紀錄；不可變；沒有合併/命令權（UAT-M03-003）。
3. **適合度 maker-checker**：`kyc.suitability_override` 記錄規則計算值與人工核定覆核後的結果、核定人與理由；不可變，`computed_decision <> resulting_decision` 保證只有真正的例外才落此表（UAT-M03-010）。
4. **持續 KYC 到期/風險變更排程**：`kyc.refresh_case` 是 `approved -> refresh_required` 轉換背後的排程與 exception queue，含 lease/attempt/next_attempt_at，worker 連續失敗只能轉 `manual_required`，不會產生「通過」事實（UAT-M03-009、UAT-M03-011）；自動複核成功或 `decide_kyc` 核准同客戶的續送件，都會把尚未結案的 `refresh_case` 轉為 `completed`，避免流程卡在 `refresh_required`。
5. **掃描 lease／重試與 maker-checker**：`kyc.submission_document` 新增 `attempt_count`／`lease_owner`／`lease_until`／`next_attempt_at`／`last_error_code`，對應 `DocumentScanWorker` 真實 lease 與有界重試（掃描結果仍只能是 pending 或 worker 回報的結果，失敗不會被誤判為 clean）；`kyc.submission` 新增 `submitted_by`，記錄代客戶送件的 staff actor，`decide_kyc` 用它拒絕同一人既送件又核准（maker-checker，UAT-M03-004b）。

## 整合步驟

1. 先確認既有 synthetic `kyc.submission_document` 列數；回填 `scan_status='clean'`（若已通過人工審核）或 `'pending'`，避免新 CHECK 立即違反既有資料。
2. 驗證 `NOT VALID` constraints 後轉為 `VALIDATE CONSTRAINT`。
3. 由資料 steward 授與 `longhao_m03` 對新表的最小 DML 權限；其他模組只經 port/projection 讀取（AML 需要的 `kyc.submission` 最小安全結果已由既有 grants 處理，不在本提案範圍）。
4. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加：未掃描文件不可進審核、OCR草稿不可變、override 不可覆寫原assessment、refresh_case 連續失敗轉 manual_required 的案例。

回復採 forward-fix。已有 `document_extraction`、`suitability_override` 或 `refresh_case` 證據後不可直接 drop；應先停寫、匯出可驗證證據，再由核准 migration 做相容降級。

## 契約協調項

- `If-Match` 的 pattern（`^"[1-9][0-9]*"$`）不允許 `"0"`，確認「以操作路徑主aggregate版本做CAS」指的是路徑所命名的既有外部聚合（`application_id`／`customer_id`），不是尚未存在的 M03 子資源；`create_kyc_submission`／`assess_suitability` CAS 比對 application 版本，`request_due_diligence_refresh` CAS 比對 customer 版本。本模組 `ApplicationGatewayPort`／`CustomerGatewayPort` 是此版本的唯一來源；真正接線時由 M01/M02 提供權威版本，不由 M03 自建假版本號。
- `request_due_diligence_refresh` 回應型別沿用 `KycSubmission`（OpenAPI 未另訂 RefreshCase 型別）；`refresh_case_id` 只在 `kyc.refresh_required` 事件 payload 曝光，`kyc.refresh_case` 表尚未有對應 wire schema，由 API steward 後續評估是否需要公開查詢端點。
- OCR 衝突投影與適合度 override 證據同樣未有 wire schema；本模組以 `OcrAutomationService`／`get_kyc_submission` 之外的程式介面提供，production route 需等 API steward 合併相容契約。
