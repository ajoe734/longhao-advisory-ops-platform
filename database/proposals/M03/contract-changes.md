# M03 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. `DocumentStatus` 可選新增欄位對應 `scan_rejection_code`/`classification` 已足夠表達目前 UAT；暫不需 wire 變更。
2. 新增查詢型 operation（暫名 `get_kyc_document_conflicts`）：`GET /kyc-submissions/{submission_id}/document-conflicts`，角色 `kyc_reviewer`，回傳 OCR 欄位抽取與客戶原答的差異列表（`question_id`、`ocr_value`、`customer_value`、`confidence`、`conflicts_with_customer_answer`），不包含原始文件內容；供審核工作台取代目前只能透過程式介面（`OcrAutomationService`）查詢的限制。
3. 新增查詢型 operation（暫名 `get_due_diligence_refresh_case`）：`GET /customers/{customer_id}/due-diligence-refreshes/{refresh_case_id}`，角色 `kyc_reviewer`，回傳 `status`（pending/manual_required/completed）、`trigger`、`due_at`、`last_error_code`；目前 `kyc.refresh_required` 事件已攜帶 `refresh_case_id`，但沒有對應查詢端點讓 consumer 或客服覆核案件進度。
4. 註冊事件 `suitability.override_recorded`（暫名）：payload 只含 `assessment_id`、`application_id`、`computed_decision`、`resulting_decision`、`authorizer_id`，不含 reason_code 原文；供 M02/合規稽核在 `suitability.assessed` 之外得知該筆評估是否為人工例外，不必解析 audit log。

在上述契約未合併前，以上三項只在本模組程式介面與測試可用，不掛公開 production route，也不自造隱藏 topic。真實 Pantheon／Agora／LegendFlow／掃毒供應商接線仍是 `NOT_CONNECTED`。
