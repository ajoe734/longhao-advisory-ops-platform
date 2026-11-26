# M03 線上 KYC、客戶資料與適合度模組

此目錄實作 OpenAPI 1.0.0 的 8 個 M03 operationId。`domain.py`／`service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app 掛載的 FastAPI router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter。

## 關鍵控制

可信任 context 決定 tenant/actor；自有資源（customer self-service）與 staff ABAC（`kyc_reviewer` / `suitability_reviewer` / `suitability_authorizer` / `sales` / `service_agent`）分流；CAS（If-Match）、payload-bound idempotency、原始答案與審核證據不可覆寫、適合度規則確定性計算、maker-checker override、無 PII 事件、audit/outbox 原子提交。

## 契約協調項（本模組對 OpenAPI 模糊處的落地解讀）

- `create_kyc_submission` 與 `assess_suitability` 的 `If-Match` 路徑為 `application_id`，但該聚合由 M01/M02 擁有。本模組落地為：CAS 比對的是 `ApplicationGatewayPort.get_signed_application()` 回報的 application 目前版本，不新造 M03 自己的版本號。`IfMatch` 契約 pattern（`^"[1-9][0-9]*"$`）不允許 `"0"`；首筆 `create_kyc_submission`／`assess_suitability` 一樣要帶該 application 當前版本（fake gateway 預設為 `"1"`），不是 `"0"`。
- `request_due_diligence_refresh` 路徑為 `customer_id`；CAS 比對的是 `CustomerGatewayPort.get_customer_version()` 回報的客戶目前版本（該客戶聚合由 M01 擁有），不是 `kyc.submission` 自己的版本（與 `database/proposals/M03/README.md` 契約協調項一致）。此端點只對 `staffBearer`＋`kyc_reviewer` 開放（OpenAPI `security` 未列 `customerBearer`），客戶不能自行觸發重評。
- `KycSubmission.state` 的 `submission_version` 在「核准/拒絕後再次 `create_kyc_submission`」或「`submitted`/`under_review`/`incomplete`/`refresh_required` 狀態下再次 `submit_kyc`（代表在審核中修改答案或完成到期重評）」時遞增為新的一行；舊版本的原始答案與審核證據永不覆寫（`decide_kyc` 會先確認自己評的是最新版本，否則回 `stale_submission`）。重送文件只需與目前這筆申請（`application_id`／`customer_id`）相符，不限定同一個 `submission_version` 上傳。
- `get_kyc_submission` 僅 `staffBearer`；`kyc_reviewer` 看完整案件，`sales`/`service_agent` 看欄位遮罩投影（`masked_business_view`：清空答案快照、文件清單與 reviewer_id，保留 `missing_field_codes`/`next_review_due_at`），其餘角色 403。
- `decide_kyc` 核准前會重新檢查 `document_ids` 與 `verification_evidence_ids` 指向的文件皆屬本次送件且 `scan_status=='clean'`（未掃描完成或已被隔離的文件永遠不能核准通過）；`verification_evidence_ids` 必須是本次送件的文件。若該 `submission.submitted_by`（代客戶送件的 staff）與目前決定者 actor 相同則回 403 `maker_checker_violation`——代送件與核准必須分屬不同 staff。核准成功會把該客戶任何 `pending`/`manual_required` 的 due-diligence `refresh_case` 結案，避免 refresh 流程卡死。
- `assess_suitability` 的 `decision`/`conditions`/`reason_code` 是呼叫端對確定性規則結果的**覆核輸入**：伺服器用 `compute_suitability()` 重新計算期望結果；若呼叫端提交值與計算值一致（包含 `conditional` 時的條件集合沒有被削弱）視為確認，只需 `suitability_reviewer`；不一致或**削弱**既有 `conditional` 條件（override）則額外要求 `suitability_authorizer` 角色，且永久記錄 `suitability_overrides`（maker-checker 證據）；override 時若覆核的 actor 與上一筆評估的 `assessed_by` 相同，即使同時持有兩個角色仍回 403 `maker_checker_violation`。每次呼叫都會新增一筆不可變 `SuitabilityAssessment`（新 `id`），絕不覆寫前一筆判斷的證據；回應一律 `201`（OpenAPI 未定義 `200`）。not_suitable 永遠不能被已付費等外部事實繞過；本模組對此沒有、也不會新增旁路端點。

## 自動化

`automation.py` 的 `OcrAutomationService` 只接受已存在的 submission/document，輸出 `untrusted_draft` 的欄位抽取建議與信心度，並標註是否與客戶原始答案衝突；沒有命令權，不會寫回 `submission.answers`。沒有核准 LLM provider 時使用 deterministic local fallback，production 會拒絕 fake。

## 外部狀態（NOT_CONNECTED）

Pantheon／Agora／LegendFlow 的真實身分驗證、文件病毒掃描供應商與 M01/M02 的真實 application/customer gateway 均為 `NOT_CONNECTED`。本模組只有 adapter port（`ApplicationGatewayPort`、`DocumentScannerPort`、`ExternalVerificationPort`）與 local fake；沒有修改或呼叫外部 repository／production endpoint。`request_kyc_upload` 回傳的 `upload_url` 是本平台內部隔離區路徑佔位，不是任何供應商的簽發 URL。

## 整合者需提供

shared PostgreSQL repository/UoW、FastAPI auth context resolver、真實 application/customer gateway、物件儲存＋防毒 adapter、metrics exporter，以及套用 `database/proposals/M03` 後的 migration 編號。共用 OpenAPI 尚未能表達 OCR vs. 客戶答案衝突投影；模組提供 `OcrAutomationService` 與內部查詢路徑，production route 需等 API steward 合併相容契約，不能私設隱藏端點。
