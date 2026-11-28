# M03 共用契約相容新增提案

本提案的相容 API 增加已在本候選的 `api/openapi.yaml` 實作；需要 API schema steward review。既有欄位、operation 與事件 schema 均未改名、刪除或重新定義。

## GAP-M03-SUITABILITY-READ · 已實作的相容讀取契約

1. `GET /applications/{application_id}/suitability-assessments`（`list_suitability_assessments`）回傳該已驗證 tenant 內、指定 application 的不可變評估歷史，依 assessment version 升冪排序；無資料時回傳空 `items`，不以跨模組 application lookup 洩漏存在性。
2. `GET /suitability-assessments/{assessment_id}`（`get_suitability_assessment`）回傳單筆評估與其 ETag；不同 tenant 對該 id 一律取得 404。
3. 兩端點只接受 `staffBearer` 的 `suitability_reviewer`；`platform_admin`、sales、service agent 與 customer 都不因其他角色取得資料。輸出重用既有 `SuitabilityAssessment` 投影，因此不包含原問卷答案、answers hash、規則計算細節、reason code、service codes 或 override 證據。每次讀取或拒絕都保留 M03 audit。
4. `SuitabilityAssessmentList` 是新增 response wrapper，不變更既有 `SuitabilityAssessment` 欄位，故對既有 POST client 向下相容；不新增 migration 或事件。

## 待 API steward 協調的其他提案

1. `DocumentStatus` 可選新增欄位對應 `scan_rejection_code`/`classification` 已足夠表達目前 UAT；暫不需 wire 變更。
2. 新增查詢型 operation（暫名 `get_kyc_document_conflicts`）：`GET /kyc-submissions/{submission_id}/document-conflicts`，角色 `kyc_reviewer`，回傳 OCR 欄位抽取與客戶原答的差異列表（`question_id`、`ocr_value`、`customer_value`、`confidence`、`conflicts_with_customer_answer`），不包含原始文件內容；供審核工作台取代目前只能透過程式介面（`OcrAutomationService`）查詢的限制。
3. 新增查詢型 operation（暫名 `get_due_diligence_refresh_case`）：`GET /customers/{customer_id}/due-diligence-refreshes/{refresh_case_id}`，角色 `kyc_reviewer`，回傳 `status`（pending/manual_required/completed）、`trigger`、`due_at`、`last_error_code`；目前 `kyc.refresh_required` 事件已攜帶 `refresh_case_id`，但沒有對應查詢端點讓 consumer 或客服覆核案件進度。
4. 註冊事件 `suitability.override_recorded`（暫名）：payload 只含 `assessment_id`、`application_id`、`computed_decision`、`resulting_decision`、`authorizer_id`，不含 reason_code 原文；供 M02/合規稽核在 `suitability.assessed` 之外得知該筆評估是否為人工例外，不必解析 audit log。

在上述契約未合併前，以上三項只在本模組程式介面與測試可用，不掛公開 production route，也不自造隱藏 topic。真實 Pantheon／Agora／LegendFlow／掃毒供應商接線仍是 `NOT_CONNECTED`。
