# M03 · 線上 KYC、客戶資料與適合度：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

在客戶已簽申請後完成線上身分與資料蒐集、完整性檢核、適合度評估及持續更新；以版本化資料和人工例外處理降低重複行政作業。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 客戶：提交本人資料、文件與適合度問卷；修改形成新版本
- KYC 審核者：核對證據、補件與決策；不可自動接受自己代填之敏感例外
- 適合度授權人：確認規則結果與例外，記錄適用服務限制
- 客服／業務：只看補件清單與一般狀態；不看完整證件內容

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M03-001 | 簽署後可開始線上 KYC | 保存答案、問卷版號、檔案 refs；status submitted；未直接開通服務 | UAT-M03-001 |
| REQ-M03-002 | 缺必填欄位不得完成審核 | 回明確欄位 errors 或 incomplete；保留已填內容，無 approved | UAT-M03-002 |
| REQ-M03-003 | OCR 輔助不覆寫客戶原始答案 | 顯示兩個來源與信心；等待確認；原始答案可查 | UAT-M03-003 |
| REQ-M03-004 | 文件惡意內容在隔離區遭阻擋 | 不可讀取／進審核；保存安全原因碼，無外洩 URL | UAT-M03-004 |
| REQ-M03-005 | 適合度由版本化規則計算 | 得到相同結果與因素；不使用隨機 LLM 輸出取代評分 | UAT-M03-005 |
| REQ-M03-006 | 不適合服務不能因已付費通過 | gate 拒絕；可改服務或走拒絕退款流程；留存拒絕證據 | UAT-M03-006 |
| REQ-M03-007 | 新版本提交使舊審核過時 | 回 stale_submission；v1舊答案仍在，須評估v2才可更新clearance | UAT-M03-007 |
| REQ-M03-008 | 業務僅能看到補件狀態 | 403/欄位遮罩；可看缺件項目與到期，不能取得 object download link | UAT-M03-008 |
| REQ-M03-009 | 風險變更觸發重評 | 建立一筆 refresh 工作與到期規則；不要自動沿用舊 suitable | UAT-M03-009 |
| REQ-M03-010 | 人工例外需權限與獨立覆核 | 拒絕；授權角色附證據及核定者才可形成有效例外 | UAT-M03-010 |
| REQ-M03-011 | 到期查核不被失敗重試當通過 | 進exception queue並告警，狀態pending或manual_required；無pass事件 | UAT-M03-011 |
| REQ-M03-012 | 同一提交重放不產生多筆評估 | 只有一有效 assessment及 outbox事件；結果可用原request id查詢 | UAT-M03-012 |

## 4. 主要使用案例

### UC-M03-A · 線上填答與補件

依 customer_signed application 提供版本化問卷；格式檢核、OCR 輔助填寫、文件有效期與前後不一致檢查；客戶確認 OCR 結果後提交；可離線續填但提交需已驗證身份。

### UC-M03-B · 適合度與例外

確定性規則計算風險/服務適合度初步結果；有不一致、條件式或不適合時轉授權人；規則輸出不是 LLM 判斷，override需理由、權限與影響服務。

### UC-M03-C · 持續 KYC 更新

以到期、風險改變、聯絡資料變更或法遵事件觸發 refresh；清楚區分補件期限與權限限制規則；更新結果推送 M02，舊資料保留作歷史決策證據。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| submission | draft → submitted → incomplete/under_review → approved/rejected；approved → refresh_required | 新資料建立新版本；原始答案與審核證據不可覆寫 |
| suitability | pending → assessed → suitable/conditional/not_suitable；另可 reassessment_due | ruleset_version 與答案版本鎖定，不能以行銷或付費壓過 not_suitable |
| upload | requested → quarantined → scanned → accepted/rejected | 物件傳輸與防毒完成前不供審核，禁止公網 URL 直接引用敏感原件 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| kyc.submission | status | 狀態：not_started / in_progress / submitted / incomplete / under_review / approved / rejected / refresh_required | CHECK (status IN ('not_started','in_progress','submitted','incomplete','under_review','approved','rejected','refresh_required')) |
| kyc.suitability_assessment | status | 狀態：pending / suitable / conditional / not_suitable / reassessment_due | CHECK (status IN ('pending','suitable','conditional','not_suitable','reassessment_due')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| kyc.submission | 本模組權威寫入 | 版本化線上 KYC 回答與證據；先保留客戶原始填答。 | application_id,submission_version | internal／personal | kyc |
| kyc.submission_document | 本模組權威寫入 | 線上補件、驗證用途與結果；保留原件版本。 | tenant_id,id | internal | kyc |
| kyc.suitability_assessment | 本模組權威寫入 | 版本化適合度評估，條件適合必須附限制。 | tenant_id,id | internal | kyc |
| core.object_asset | 受控共用基礎 | 加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。 | storage_key,object_version | internal | per_asset |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_kyc_submission | POST /applications/{application_id}/kyc-submissions | KycDraftRequest | KycSubmission | kyc_reviewer | If-Match 必須 |
| request_kyc_upload | POST /kyc-submissions/{submission_id}/document-uploads | DocumentUploadRequest | UploadTicket | kyc_reviewer | If-Match 必須 |
| submit_kyc | POST /kyc-submissions/{submission_id}/submit | KycSubmitRequest | KycSubmission | kyc_reviewer | If-Match 必須 |
| get_kyc_submission | GET /kyc-submissions/{submission_id} | — | KycSubmission | kyc_reviewer | 依OpenAPI |
| decide_kyc | POST /kyc-submissions/{submission_id}/decision | KycDecisionRequest | KycSubmission | kyc_reviewer | If-Match 必須 |
| assess_suitability | POST /applications/{application_id}/suitability-assessments | SuitabilityRequest | SuitabilityAssessment | suitability_reviewer | If-Match 必須 |
| request_due_diligence_refresh | POST /customers/{customer_id}/due-diligence-refreshes | RefreshRequest | KycSubmission | kyc_reviewer | If-Match 必須 |
| get_document_status | GET /documents/{asset_id}/status | — | DocumentStatus | kyc_reviewer | 依OpenAPI |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| kyc.decision_recorded | 發出 | M03 | M02,M04 | customer_confidential | 1.0.0 |
| suitability.assessed | 發出 | M03 | M02 | customer_confidential | 1.0.0 |
| kyc.refresh_required | 發出 | M03 | M02,M10 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| customer.profile_updated | ／接收 | M01 | M03,M04 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 線上問卷 API 的 UX 規約 | 分段自存、必填與說明、檔案限制、可見補件原因、版本與提交確認 | 不要要求客戶重填未變欄位；不接受前端直接傳 approved |
| KYC 審核工作台 | 左側提交原始答案、右側規則差異／證據，中間補件與決策 | 身分文件點擊經臨時授權 URL 讀取；審核逾時與版本改變需提示 |
| 適合度詳情 | 顯示 ruleset、命中條件、客戶風險偏好、可用服務與限制 | 強制例外理由；已付費也不能直接解除不適合限制 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM/OCR 可抽取欄位、找出缺件或矛盾、草擬補件訊息；原件引用與低信心欄位必須顯示並由客戶／審核者確認。
- 必須受控：問卷版本、規則評分、有效期、限制與審核批准由受控規則與人員執行。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 文件上傳未掃毒／存取失敗：保持待檢查，不能顯示已審核完成。
- OCR 與本人填答衝突：保留兩者、標注衝突與證據；不靜默以 AI 改答案。
- 規則版本切換：在 submission snapshot 固定版本，明確重評才變更，不回溯改已決策結果。
- KYC 外部驗證 timeout：進 retry/exception queue，不當通過。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M01 客戶及申辦
- M02 customer signature／company acceptance gate
- M04 AML 查核所需資料經最小化受限交換
- M10 CRM 只接補件狀態及到期，不接證件

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M03-001 · 簽署後可開始線上 KYC

- 前提：application customer_signed 且本人登入
- 操作：建立並提交完整 KYC
- 預期：保存答案、問卷版號、檔案 refs；status submitted；未直接開通服務
- API：`create_kyc_submission`, `submit_kyc`
- 證據：`kyc.submission`、audit/correlation與適用外部receipt。

### UAT-M03-002 · 缺必填欄位不得完成審核

- 前提：缺職業／身分驗證必要項目
- 操作：送出 submission
- 預期：回明確欄位 errors 或 incomplete；保留已填內容，無 approved
- API：`submit_kyc`
- 證據：`kyc.submission`、audit/correlation與適用外部receipt。

### UAT-M03-003 · OCR 輔助不覆寫客戶原始答案

- 前提：OCR 姓名與客戶填答不同
- 操作：執行抽取與審核
- 預期：顯示兩個來源與信心；等待確認；原始答案可查
- API：`get_kyc_submission`
- 證據：`kyc.submission_document`、audit/correlation與適用外部receipt。

### UAT-M03-004 · 文件惡意內容在隔離區遭阻擋

- 前提：測試文件被 scanner 標記危險
- 操作：完成 upload callback
- 預期：不可讀取／進審核；保存安全原因碼，無外洩 URL
- API：`request_kyc_upload`, `get_document_status`
- 證據：`kyc.submission_document`、audit/correlation與適用外部receipt。

### UAT-M03-005 · 適合度由版本化規則計算

- 前提：同答案與同 ruleset
- 操作：重跑 assessment
- 預期：得到相同結果與因素；不使用隨機 LLM 輸出取代評分
- API：`assess_suitability`
- 證據：`kyc.suitability_assessment`、audit/correlation與適用外部receipt。

### UAT-M03-006 · 不適合服務不能因已付費通過

- 前提：assessment not_suitable 且 finance paid
- 操作：要求接受／啟用該服務
- 預期：gate 拒絕；可改服務或走拒絕退款流程；留存拒絕證據
- API：`assess_suitability`, `decide_company_acceptance`
- 證據：`kyc.suitability_assessment`、audit/correlation與適用外部receipt。

### UAT-M03-007 · 新版本提交使舊審核過時

- 前提：reviewer 開啟 submission v1
- 操作：客戶送 v2後 reviewer批准v1
- 預期：回 stale_submission；v1舊答案仍在，須評估v2才可更新clearance
- API：`decide_kyc`
- 證據：`kyc.submission`、audit/correlation與適用外部receipt。

### UAT-M03-008 · 業務僅能看到補件狀態

- 前提：sales role 查 KYC 詳情
- 操作：請求證件或原始收入／財富資料
- 預期：403/欄位遮罩；可看缺件項目與到期，不能取得 object download link
- API：`get_kyc_submission`
- 證據：`kyc.submission_document`、audit/correlation與適用外部receipt。

### UAT-M03-009 · 風險變更觸發重評

- 前提：客戶資料事件顯示重要風險改變
- 操作：處理事件並跑 refresh rule
- 預期：建立一筆 refresh 工作與到期規則；不要自動沿用舊 suitable
- API：`request_due_diligence_refresh`
- 證據：`kyc.suitability_assessment`、audit/correlation與適用外部receipt。

### UAT-M03-010 · 人工例外需權限與獨立覆核

- 前提：conditional 需要額外證據
- 操作：一般客服嘗試override為suitable
- 預期：拒絕；授權角色附證據及核定者才可形成有效例外
- API：`assess_suitability`
- 證據：`kyc.suitability_assessment`、audit/correlation與適用外部receipt。

### UAT-M03-011 · 到期查核不被失敗重試當通過

- 前提：外部身分驗證連續 timeout
- 操作：工作者重試後超過閾值
- 預期：進exception queue並告警，狀態pending或manual_required；無pass事件
- API：`decide_kyc`
- 證據：`kyc.submission`、audit/correlation與適用外部receipt。

### UAT-M03-012 · 同一提交重放不產生多筆評估

- 前提：相同 submission/version/idempotency key
- 操作：送出兩次或事件重送
- 預期：只有一有效 assessment及 outbox事件；結果可用原request id查詢
- API：`submit_kyc`
- 證據：`kyc.suitability_assessment`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M03-kyc_suitability.md)
- [GitHub issue #3](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/3)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
