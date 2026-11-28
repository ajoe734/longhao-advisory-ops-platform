# M03 共用契約相容新增提案

本提案的相容 API 增加已在本候選的 `api/openapi.yaml` 實作；需要 API schema steward review。既有欄位、operation 與事件 schema 均未改名、刪除或重新定義。

## GAP-M03-SUITABILITY-READ · 已實作的相容讀取契約

1. `GET /applications/{application_id}/suitability-assessments`（`list_suitability_assessments`）回傳該已驗證 tenant 內、指定 application 的不可變評估歷史，依 assessment version 升冪排序；無資料時回傳空 `items`，不以跨模組 application lookup 洩漏存在性。
2. `GET /suitability-assessments/{assessment_id}`（`get_suitability_assessment`）回傳單筆評估與其 ETag；不同 tenant 對該 id 一律取得 404。
3. 兩端點只接受 `staffBearer` 的 `suitability_reviewer`；`platform_admin`、sales、service agent 與 customer 都不因其他角色取得資料。輸出重用既有 `SuitabilityAssessment` 投影，因此不包含原問卷答案、answers hash、規則計算細節、reason code、service codes 或 override 證據。每次讀取或拒絕都保留 M03 audit。
4. `SuitabilityAssessmentList` 是新增 response wrapper，不變更既有 `SuitabilityAssessment` 欄位，故對既有 POST client 向下相容；不新增 migration 或事件。

## GAP-M03-SUITABILITY-READ · 修復與驗證證據

基準為 `origin/dev@d5b707d`；本節記錄本 task 已完成的相容新增，供 API schema steward 及 reviewer 逐項核對。所有動態 probe 使用本模組正式 `M03Controller`／`KycSuitabilityService` 路徑與合成 tenant，未啟動產品開發伺服器，也未使用真實客戶或外部 provider。

| Finding／驗收項 | 原始碼依據與修改位置 | 舊版重現 → 修正版結果 | 命令、結果與證據 | 未驗項與具體限制 |
| --- | --- | --- | --- | --- |
| UI-08 不能取得單筆 assessment | `http.py:M03Controller._dispatch`、`ROUTES`；`service.py:get_suitability_assessment`；`api/openapi.yaml:get_suitability_assessment` | 同一個已驗證 `suitability_reviewer` 對合法 UUID 呼叫正式 controller：`origin/dev@d5b707d` 為 `422 operation_not_found`；本候選進入 tenant-scoped repository lookup，缺失 id 正確為 `404 resource_not_found`。有 assessment 時模組回歸驗證 `200` 與 ETag。 | 暫存 detached worktree 的正式 controller probe：`origin/dev: 422 operation_not_found`、候選：`404 resource_not_found`；`python3 -m pytest tests/modules/kyc_suitability -q`。 | 不啟動產品 runtime；PostgreSQL/RLS integration 由 hosted workflow 或 integration 環境另驗。 |
| UI-08 不能依 application 列出歷史 | `http.py:M03Controller._dispatch`、`ROUTES`；`service.py:list_suitability_assessments`；`api/openapi.yaml:list_suitability_assessments` 與 `SuitabilityAssessmentList` | `origin/dev@d5b707d` 未發布 operation；修正版對同一 application 建兩筆 immutable assessment，回傳兩項且 assessment version 依 `[1, 2]` 遞增。 | `test_suitability_read_queries_are_reviewer_only_tenant_scoped_and_masked` 走完整 KYC → assess → HTTP controller flow；`python3 -m pytest tests/modules/kyc_suitability -q`。 | 與單筆讀取相同：不在此 VM 啟動 runtime／PG。 |
| 資料分級、401／403、tenant isolation | `service.py:_authorize_staff`、`_stored_assessment`、list tenant filter、`SuitabilityAssessment.to_api`；OpenAPI `staffBearer`／`x-allowed-roles` | 成功回應不含 answers、hash、computed decision、reason code、service codes 或 override evidence；`platform_admin`、`kyc_reviewer`、sales、service agent、customer、僅有 authorizer 皆 `403`；未驗證 HTTP request 為 `401`；跨 tenant 單筆為 `404`、列表為空。 | `test_suitability_read_queries_are_reviewer_only_tenant_scoped_and_masked` 與 `test_suitability_read_routes_reject_an_unauthenticated_request`；`python3 -m pytest tests/modules/kyc_suitability -q`。 | HTTP authentication resolver 是 integration composition；本測試只確認正式 router 對 resolver 所產生的 401 正確傳遞。 |
| 契約相容性 | `api/openapi.yaml` 僅新增兩個 GET operation 與 `SuitabilityAssessmentList`，既有 POST/schema field 未變更 | `scripts/validate_contracts.py` 成功驗證 142 operations、191 schemas、47 event contracts；`scripts/validate_docs.py` 成功追蹤 14 modules／182 acceptance cases。 | `python3 scripts/validate_contracts.py` exit 0；`python3 scripts/validate_docs.py` exit 0。 | API schema steward review 仍待 candidate lifecycle；不代表 production provider 或 UI deployment 已驗。 |

## 待 API steward 協調的其他提案

1. `DocumentStatus` 可選新增欄位對應 `scan_rejection_code`/`classification` 已足夠表達目前 UAT；暫不需 wire 變更。
2. 新增查詢型 operation（暫名 `get_kyc_document_conflicts`）：`GET /kyc-submissions/{submission_id}/document-conflicts`，角色 `kyc_reviewer`，回傳 OCR 欄位抽取與客戶原答的差異列表（`question_id`、`ocr_value`、`customer_value`、`confidence`、`conflicts_with_customer_answer`），不包含原始文件內容；供審核工作台取代目前只能透過程式介面（`OcrAutomationService`）查詢的限制。
3. 新增查詢型 operation（暫名 `get_due_diligence_refresh_case`）：`GET /customers/{customer_id}/due-diligence-refreshes/{refresh_case_id}`，角色 `kyc_reviewer`，回傳 `status`（pending/manual_required/completed）、`trigger`、`due_at`、`last_error_code`；目前 `kyc.refresh_required` 事件已攜帶 `refresh_case_id`，但沒有對應查詢端點讓 consumer 或客服覆核案件進度。
4. 註冊事件 `suitability.override_recorded`（暫名）：payload 只含 `assessment_id`、`application_id`、`computed_decision`、`resulting_decision`、`authorizer_id`，不含 reason_code 原文；供 M02/合規稽核在 `suitability.assessed` 之外得知該筆評估是否為人工例外，不必解析 audit log。

在上述契約未合併前，以上三項只在本模組程式介面與測試可用，不掛公開 production route，也不自造隱藏 topic。真實 Pantheon／Agora／LegendFlow／掃毒供應商接線仍是 `NOT_CONNECTED`。
