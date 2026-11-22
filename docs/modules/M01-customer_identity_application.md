# M01 · 客戶、身分與申辦核心：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

建立同一客戶的可追溯身分與申辦紀錄，讓 LegendFlow 的匿名流量、已登入會員、線下建檔與後續契約能準確對接；不因信箱相同就合併客戶。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 申請人：僅讀寫本人申辦及已授權資料
- 客服／營運：建立協助申辦、處理身分連結衝突，不取得 AML 案情
- 身分管理者：依證據批准合併／解除連結；不得改寫交易歷史
- LegendFlow service principal：限定 customer/application scopes，不能冒用 staff 權限

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M01-001 | 同一外部身分重送只建立一客戶 | 回同一 customer_id；external_identity 唯一；新增事件不得重複產生行銷首購歸因 | UAT-M01-001 |
| REQ-M01-002 | 不同已驗證 subject 不能依同信箱自動合併 | 建立待人工確認連結候選；原契約及客戶隔離，不把 email 當主鍵 | UAT-M01-002 |
| REQ-M01-003 | 申辦草稿跨會話續填與撤回 | 續填保留同一 application_id、原來源和歷史版本；撤回記withdrawn及原因／時間，不能再當有效申辦；全程無服務權限 | UAT-M01-003 |
| REQ-M01-004 | 未授權跨客戶查詢 | 拒絕且不洩漏存在性；事件不含 B 的明文個資 | UAT-M01-004 |
| REQ-M01-005 | 來源變更不覆寫原始取得來源 | 原始來源不變；新的 touchpoint／transaction attribution 另記錄 | UAT-M01-005 |
| REQ-M01-006 | 同意撤回有範圍與時間 | 停止後續行銷投遞；必要契約通知依法定／契約分類另判斷，不能誤停所有服務 | UAT-M01-006 |
| REQ-M01-007 | 外部 token 偽造遭拒 | 401/403；不產生 linked identity、customer 或申辦 | UAT-M01-007 |
| REQ-M01-008 | 兩筆合併保留可追溯關聯 | 保留來源 id 映射與 reviewer evidence；歷史付款與報告送達紀錄可由舊 id 查回 | UAT-M01-008 |
| REQ-M01-009 | 客戶資料樂觀鎖阻擋覆寫 | 第二筆 412／版本衝突；第一筆資料仍在；UI 提供重載與差異 | UAT-M01-009 |
| REQ-M01-010 | 驗證供應商中斷不建立假身分 | 回可重試外部依賴錯誤；狀態保持未驗證，不使用上次別人的會話 | UAT-M01-010 |
| REQ-M01-011 | 協助建檔不可冒充客戶同意 | API 拒絕不允許欄位；只建立代填者 audit，客戶同意須獨立證據 | UAT-M01-011 |
| REQ-M01-012 | 來源資料故障與空集合不同 | 回 source_status/degraded 與 last_success_at；不能顯示沒有契約或沒有付款 | UAT-M01-012 |

## 4. 主要使用案例

### UC-M01-A · 入口識別與申辦

LegendFlow 驗證登入後以穩定 external_subject 建立或解析客戶；保存來源／同意證據；建立服務申請及 client reference；回傳可續填 application_id。匿名 cookie 只作來源訊號，不能驗證身分。

### UC-M01-B · 跨通路協助

客服按已驗證聯絡資訊查詢遮罩名單，將電話／現場申辦連到既有客戶；有衝突時建立受限處理工作，不直接覆寫姓名或外部主體。

### UC-M01-C · 修正與合併

以 expected_version 修改可改欄位；身分合併保留 surviving_customer_id 與來源關聯歷史，歷史契約／財務不重新編號；誤連結使用可稽核解除流程。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| customer | prospect → applicant → established；另有 restricted/closed | 由權威事件投影狀態，與契約有效性分開；closed 不刪除留存資料 |
| application | draft → customer_signed → due_diligence_pending → review_in_progress → accepted/rejected/withdrawn | customer_signed 由 M02 驗證事件推進；accepted 來自 M02 公司決策，不由前端設定 |
| identity link | unverified → verified → linked；衝突 → quarantined | 須驗證 provider issuer/audience/subject；合併須人工證據及審計 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| customer.customer | status | 狀態：prospect / applicant / active / inactive / restricted / merged | CHECK (status IN ('prospect','applicant','active','inactive','restricted','merged')) |
| customer.application | status | 狀態：draft / customer_signed / due_diligence_pending / review_in_progress / accepted / rejected / withdrawn | CHECK (status IN ('draft','customer_signed','due_diligence_pending','review_in_progress','accepted','rejected','withdrawn')) |
| customer.merge_case | status | 狀態：proposed / approved / rejected / executed / cancelled | CHECK (status IN ('proposed','approved','rejected','executed','cancelled')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| customer.customer | 本模組權威寫入 | 客戶管理主檔；潛在、申辦、已簽與已生效客戶共用身份。 | lookup_hmac | internal／personal | customer |
| customer.external_identity | 本模組權威寫入 | 經驗證外部登入身分；合併須證據與審核。 | provider,subject_hmac | internal | customer |
| customer.application | 本模組權威寫入 | 簽約申辦流程；客戶簽署先於 KYC，不代表公司已接受。 | tenant_id,id | internal | contract |
| customer.consent | 本模組權威寫入 | 個資告知、行銷同意、撤回事件；不能用簽約強迫行銷同意。 | tenant_id,id | internal | customer |
| customer.merge_case | 本模組權威寫入 | 重複客戶合併申請，核准後保留來源 ID alias 與原證據。 | tenant_id,id | internal | customer |
| core.object_asset | 受控共用基礎 | 加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。 | storage_key,object_version | internal | per_asset |
| core.audit_event | 受控共用基礎 | 不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。 | tenant_id,id | internal | audit |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_customer | POST /customers | CustomerCreate | Customer | cms_operator | 依OpenAPI |
| list_customers | GET /customers | — | CustomerPage | cms_operator,sales,service_agent | 依OpenAPI |
| get_customer | GET /customers/{customer_id} | — | Customer | cms_operator,sales,service_agent | 依OpenAPI |
| link_external_identity | POST /customers/{customer_id}/identity-links | IdentityLinkRequest | ExternalIdentity | cms_operator | If-Match 必須 |
| propose_customer_merge | POST /customer-merges | CustomerMergeRequest | MergeCase | cms_operator | 依OpenAPI |
| decide_customer_merge | POST /customer-merges/{merge_case_id}/decision | Decision | MergeCase | cms_supervisor | If-Match 必須 |
| create_application | POST /applications | ApplicationCreate | Application | cms_operator | 依OpenAPI |
| get_application | GET /applications/{application_id} | — | Application | cms_operator | 依OpenAPI |
| withdraw_application | POST /applications/{application_id}/withdrawal | ReasonRequest | Application | cms_operator | If-Match 必須 |
| record_consent | POST /customers/{customer_id}/consents | ConsentRequest | Consent | cms_operator | If-Match 必須 |
| list_customer_merges | GET /customer-merges | — | MergeCasePage | cms_supervisor | 依OpenAPI |
| get_customer_merge | GET /customer-merges/{merge_case_id} | — | MergeCase | cms_operator,cms_supervisor | 依OpenAPI |
| update_customer | PATCH /customers/{customer_id} | CustomerUpdate | Customer | cms_operator,service_agent | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| customer.created | 發出 | M01 | M10,M13 | customer_confidential | 1.0.0 |
| customer.identity_linked | 發出 | M01 | M09 | customer_confidential | 1.0.0 |
| application.created | 發出 | M01 | M02,M10 | customer_confidential | 1.0.0 |
| customer.profile_updated | 發出 | M01 | M03,M04 | customer_confidential | 1.0.0 |
| customer.consent_changed | 發出 | M01 | M09,M10 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| application.company_decided | ／接收 | M02 | M01,M10,M14 | customer_confidential | 1.0.0 |
| ownership.employee_departed | ／接收 | M11 | M01,M10,M11 | internal | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| CMS 客戶搜尋 | 姓名／電話尾碼／客戶編號搜尋，結果先遮罩；跨租戶查無同等回應 | 只允許授權使用者打開詳細頁；匯出另受控，不能用前端遮罩取代 API 過濾 |
| Customer 360 標頭 | 顯示客戶、來源、目前維護人、有效服務、待辦／阻擋摘要，各有來源與更新時間 | AML 只顯示一般阻擋碼，不顯示名單／申報；無資料、載入中、來源故障分別顯示 |
| 申辦時間軸 | 簽署、資料補件、公司接受、付款、權限分列 | 草稿可續填；每一步顯示版本、責任者及下一動作，不把登入當契約生效 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM 可整理客服建檔文字、提出重複紀錄候選；姓名、電話與識別證資料先最小化／遮罩。
- 必須受控：身分驗證、合併決策、同意有效性、跨客戶授權及申辦狀態由規則與授權人員決定。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 登入 token issuer/audience/nonce 不符：拒絕並記 security audit，不建立客戶。
- 同一 Idempotency-Key 不同 payload：409；同 payload 回原識別碼。
- 身分供應商故障：顯示稍後重試，不退回未驗證自動登入。
- 個資更新與合併要求先檢查 legal hold／留存，再選擇遮罩或限制處理；不得直接刪除稽核鏈。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M02 CustomerSignatureVerified/CompanyAcceptance 結果
- M03/M04 安全狀態摘要，不接收完整受限案情
- M10 來源歸因與漏斗投影
- LegendFlow 身分／會話 adapter

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M01-001 · 同一外部身分重送只建立一客戶

- 前提：同 tenant、issuer、subject 已 linked
- 操作：以相同請求及不同重試 key 呼叫解析
- 預期：回同一 customer_id；external_identity 唯一；新增事件不得重複產生行銷首購歸因
- API：`create_customer`, `link_external_identity`
- 證據：`customer.external_identity`、audit/correlation與適用外部receipt。

### UAT-M01-002 · 不同已驗證 subject 不能依同信箱自動合併

- 前提：兩個 provider subject 的 email 相同
- 操作：分別建立申辦
- 預期：建立待人工確認連結候選；原契約及客戶隔離，不把 email 當主鍵
- API：`create_customer`, `link_external_identity`
- 證據：`customer.customer`、audit/correlation與適用外部receipt。

### UAT-M01-003 · 申辦草稿跨會話續填與撤回

- 前提：客戶已有 draft application 及版本
- 操作：合法重新登入讀回並補填，再由本人明確撤回
- 預期：續填保留同一 application_id、原來源和歷史版本；撤回記withdrawn及原因／時間，不能再當有效申辦；全程無服務權限
- API：`create_application`, `get_application`, `withdraw_application`
- 證據：`customer.application`、audit/correlation與適用外部receipt。

### UAT-M01-004 · 未授權跨客戶查詢

- 前提：客戶 A token 與客戶 B id
- 操作：讀取 B 的申辦或客戶資訊
- 預期：拒絕且不洩漏存在性；事件不含 B 的明文個資
- API：`get_customer`, `get_application`
- 證據：`customer.application`、audit/correlation與適用外部receipt。

### UAT-M01-005 · 來源變更不覆寫原始取得來源

- 前提：既有 first-touch attribution 並收到新活動 ref
- 操作：建立新申辦或續約
- 預期：原始來源不變；新的 touchpoint／transaction attribution 另記錄
- API：`create_application`
- 證據：`customer.customer`、audit/correlation與適用外部receipt。

### UAT-M01-006 · 同意撤回有範圍與時間

- 前提：客戶原同意行銷並有有效契約
- 操作：撤回行銷同意
- 預期：停止後續行銷投遞；必要契約通知依法定／契約分類另判斷，不能誤停所有服務
- API：`record_consent`
- 證據：`customer.consent`、audit/correlation與適用外部receipt。

### UAT-M01-007 · 外部 token 偽造遭拒

- 前提：錯誤 issuer/audience 的登入 token
- 操作：請求連結外部身分
- 預期：401/403；不產生 linked identity、customer 或申辦
- API：`link_external_identity`
- 證據：`customer.external_identity`、audit/correlation與適用外部receipt。

### UAT-M01-008 · 兩筆合併保留可追溯關聯

- 前提：經批准且存在兩 customer records
- 操作：執行人工合併
- 預期：保留來源 id 映射與 reviewer evidence；歷史付款與報告送達紀錄可由舊 id 查回
- API：`propose_customer_merge`, `list_customer_merges`, `get_customer_merge`, `decide_customer_merge`
- 證據：`customer.customer`、audit/correlation與適用外部receipt。

### UAT-M01-009 · 客戶資料樂觀鎖阻擋覆寫

- 前提：兩人讀同 version
- 操作：第一人成功更新後第二人送舊 If-Match
- 預期：第二筆 412／版本衝突；第一筆資料仍在；UI 提供重載與差異
- API：`update_customer`
- 證據：`customer.customer`、audit/correlation與適用外部receipt。

### UAT-M01-010 · 驗證供應商中斷不建立假身分

- 前提：OIDC／provider lookup timeout
- 操作：客戶登入／連結
- 預期：回可重試外部依賴錯誤；狀態保持未驗證，不使用上次別人的會話
- API：`link_external_identity`
- 證據：`customer.external_identity`、audit/correlation與適用外部receipt。

### UAT-M01-011 · 協助建檔不可冒充客戶同意

- 前提：staff 代填 draft 並缺客戶簽署
- 操作：送出 company-effective 或 customer-consent flag
- 預期：API 拒絕不允許欄位；只建立代填者 audit，客戶同意須獨立證據
- API：`create_application`, `record_consent`
- 證據：`customer.application`、audit/correlation與適用外部receipt。

### UAT-M01-012 · 來源資料故障與空集合不同

- 前提：Customer 360 某模組暫時失聯
- 操作：讀取摘要
- 預期：回 source_status/degraded 與 last_success_at；不能顯示沒有契約或沒有付款
- API：`get_customer`, `list_customers`
- 證據：`customer.customer`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M01-customer_identity_application.md)
- [GitHub issue #1](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/1)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
