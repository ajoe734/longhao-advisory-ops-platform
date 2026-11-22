# M10 · 客戶漏斗、業務維護與轉換歸因：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

從未簽約、申辦中及已訂閱客戶找出可完成申辦、續約、留存、升級與重新開發的名單，具體分派下一步業務工作；用真實業務活動與成功交易建立獎金歸因證據。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 業務：處理所屬名單、記錄有內容的聯絡、設定下一步、提出轉換認領
- 業務主管：分派與覆核轉換因果證據，處理爭議及客群策略
- 行銷：查看取得來源與漏斗，不越權查看KYC/AML細節
- 客服／分析師：提供服務問題與需求訊號，不能僅憑客服接案取得業務獎金

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M10-001 | 未完成申辦產生可執行待辦 | 只一待辦含缺件原因、安全摘要、owner、next_action及due_at | UAT-M10-001 |
| REQ-M10-002 | 自助續約不計業務轉換 | conversion type self_serve；不因有maintenance owner就給sales獎金 | UAT-M10-002 |
| REQ-M10-003 | 業務續約需事前活動和交易 | 保存活動／交易／因果與核定者；核定後M12才可用sales attribution | UAT-M10-003 |
| REQ-M10-004 | 空白聯絡或自動訊息不能搶獎金 | evidence_insufficient；不能approve為業務介入 | UAT-M10-004 |
| REQ-M10-005 | 沒有讀研究報告不單獨判流失 | 不因report未讀標必然流失；以可觀測服務與續約訊號附原因 | UAT-M10-005 |
| REQ-M10-006 | 禁止聯絡或未結抱怨抑制銷售 | 不派行銷／升級催促；保留必要客服工作與抑制原因碼 | UAT-M10-006 |
| REQ-M10-007 | 升級機會須服務適合度允許 | 規則阻擋轉為可聯絡升級名單；可提示需要重評，不能直接推銷 | UAT-M10-007 |
| REQ-M10-008 | 四種owner不互相覆寫 | 保留各自欄位與有效時間；計獎只用核定transaction owner | UAT-M10-008 |
| REQ-M10-009 | 爭議認領不能重複計獎 | 只一有效歸因或明確批准分配；pending/disputed不進正式payout | UAT-M10-009 |
| REQ-M10-010 | 轉換後補寫活動不能回填搶獎 | 標late_entry需獨立核定，不改created_at；不自動獲獎金 | UAT-M10-010 |
| REQ-M10-011 | 漏斗統計重放與退出正確 | 按distinct application/customer定義去重；withdraw另列，不把累計簽署當有效訂閱 | UAT-M10-011 |
| REQ-M10-012 | 同一待辦不因日批次無限重複 | 回原work_item或更新優先；保留owner與已完成結果 | UAT-M10-012 |

## 4. 主要使用案例

### UC-M10-A · 漏斗與名單

收申辦、契約、付款、使用與客服事件，按原始來源及服務關係生成名單：未完成簽署、待補件、付款失敗、到期續約、適合升級、服務問題、流失與Pool；每筆給具體原因與建議下一動作。

### UC-M10-B · 業務維護

業務記錄雙向對話／會談／實際需求處理的證據、同意聯絡範圍與下一步；AI可摘要、提醒與草擬，業務確認後執行；CRM不能用報告未讀率單獨認定流失。

### UC-M10-C · 轉換認領與歸因

成功續約／升級後，檢查在轉換前的可驗證活動及因果關聯；主管核定transaction conversion owner；原始來源、當前維護人、機會owner與計獎人分開保存；無實質介入為self-serve。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| opportunity | open → contacting → qualified → application/renewal/upgrade_pending → won/lost/suppressed | won由成功轉換證據確認，不由業務按鈕直接產生計獎收入 |
| conversion claim | proposed → evidence_pending → approved/rejected/disputed | 關聯交易、業務活動、owner snapshot及policy；獨立覆核，不能自核獎金 |
| work item | ready → assigned → in_progress → waiting_customer/completed/expired | 每筆owner、next_action、due_at、reason、source event與結果可追溯 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| crm.opportunity | status | 狀態：open / qualifying / proposed / awaiting_customer / converted / lost / cancelled | CHECK (status IN ('open','qualifying','proposed','awaiting_customer','converted','lost','cancelled')) |
| crm.work_item | status | 狀態：open / assigned / in_progress / done / snoozed / cancelled | CHECK (status IN ('open','assigned','in_progress','done','snoozed','cancelled')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| crm.marketing_attribution | 本模組權威寫入 | 客戶最初來源及後續可稽核 attribution；不因換業務清除。 | tenant_id,id | internal | crm |
| crm.opportunity | 本模組權威寫入 | 首次申辦、續訂、升級或重新開發機會。 | tenant_id,id | internal | crm |
| crm.activity | 本模組權威寫入 | 真實業務行為及客戶回應證據；AI 建議、群發或分配名字不能單獨計獎。 | tenant_id,id | internal | crm |
| crm.conversion | 本模組權威寫入 | 單筆交易銷售歸因；不是單純維護人欄位。 | contract_service_id | internal | commission |
| crm.work_item | 本模組權威寫入 | 業務要執行的具體名單工作；不只顯示分數。 | dedupe_key | internal | crm |
| crm.suppression | 本模組權威寫入 | 限制招攬但不停止必要服務通知。 | tenant_id,id | internal | crm |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| list_crm_worklists | GET /crm/worklists | — | CrmWorkItemPage | sales,sales_supervisor,marketing | 依OpenAPI |
| create_opportunity | POST /crm/opportunities | OpportunityCreate | Opportunity | sales | 依OpenAPI |
| record_sales_activity | POST /crm/opportunities/{opportunity_id}/activities | SalesActivityCreate | SalesActivity | sales | If-Match 必須 |
| set_next_action | POST /crm/opportunities/{opportunity_id}/next-actions | NextActionRequest | CrmWorkItem | sales,sales_supervisor | If-Match 必須 |
| claim_conversion | POST /crm/opportunities/{opportunity_id}/conversion-claims | ConversionClaim | Conversion | sales | If-Match 必須 |
| decide_conversion | POST /crm/conversions/{conversion_id}/decision | ConversionDecision | Conversion | sales_supervisor | If-Match 必須 |
| create_crm_suppression | POST /crm/suppressions | SuppressionRequest | Suppression | service_agent,sales_supervisor | 依OpenAPI |
| get_funnel_metrics | GET /crm/funnel-metrics | — | FunnelMetrics | sales_supervisor,marketing | 依OpenAPI |
| list_opportunities | GET /crm/opportunities | — | OpportunityPage | sales,sales_supervisor | 依OpenAPI |
| get_opportunity | GET /crm/opportunities/{opportunity_id} | — | Opportunity | sales,sales_supervisor | 依OpenAPI |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| crm.opportunity_changed | 發出／接收 | M10 | M10 | customer_confidential | 1.0.0 |
| crm.conversion_decided | 發出 | M10 | M12 | customer_confidential | 1.0.0 |
| crm.suppression_changed | 發出／接收 | M10 | M09,M10 | customer_confidential | 1.0.0 |
| customer.created | ／接收 | M01 | M10,M13 | customer_confidential | 1.0.0 |
| application.created | ／接收 | M01 | M02,M10 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| application.company_decided | ／接收 | M02 | M01,M10,M14 | customer_confidential | 1.0.0 |
| contract.effective | ／接收 | M02 | M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.terminated | ／接收 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| entitlement.changed | ／接收 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| ownership.assignment_changed | ／接收 | M11 | M10,M12,M13 | customer_confidential | 1.0.0 |
| ownership.pool_entered | ／接收 | M11 | M10 | customer_confidential | 1.0.0 |
| ownership.employee_departed | ／接收 | M11 | M01,M10,M11 | internal | 1.0.0 |
| support.case_opened | ／接收 | M13 | M10,M13 | customer_confidential | 1.0.0 |
| support.case_resolved | ／接收 | M13 | M10 | customer_confidential | 1.0.0 |
| finance.payment_confirmed | ／接收 | M14 | M02,M10 | customer_confidential | 1.0.0 |
| finance.refund_confirmed | ／接收 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |
| customer.consent_changed | ／接收 | M01 | M09,M10 | customer_confidential | 1.0.0 |
| kyc.refresh_required | ／接收 | M03 | M02,M10 | customer_confidential | 1.0.0 |
| finance.payment_failed | ／接收 | M14 | M10 | customer_confidential | 1.0.0 |
| contract.service_ended | ／接收 | M02 | M11,M10,M14 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 業務今日工作台 | 按優先序列名單、客戶原因、服務、到期、上次實質互動、下一步與期限 | 每列有聯絡／記錄／補件協助／建立升級機會，禁止只有無法執行的AI分數 |
| Customer 360 關係分析 | 來源、契約、服務實際使用、已知偏好、服務問題、互動、轉換歷史 | 讀取即時訊息行為和服務需求；研究報告閱讀不當必要留存指標 |
| 轉換認領頁 | 交易、事前聯絡證據、參與人、有效窗口、維護assignment與核定結果 | 顯示自助／業務介入差異；不能補寫虛假聯絡時間以搶獎金 |
| 漏斗看板 | 訪客→註冊→客戶簽署→補件→公司接受→付款／有效服務→續約／升級 | 不同事件時點可交叉，不能假設付款一定在接受之後；指標定義與分母顯示 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM可摘要客戶互動、提出可解釋的留存／升級候選與聯絡草稿、每日待辦；保留rule/model version與原因。
- 必須受控：名單資格、聯絡抑制、歸因、成功交易、計獎輸入與業務資格由規則／獨立核定控制。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 相同事件／窗口生成同一待辦：unique work item key去重，更新原因而非每日堆疊。
- 客戶拒絕行銷、未結重大抱怨、法遵限制：依核定政策suppress sales，保留必要客服追蹤。
- UI錄音轉文字未確認：記draft activity，不能直接作正式conversion evidence。
- 原始來源缺失：標unknown而非任意給當前業務；可補證但audit記修正。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M01 acquisition/customer/application
- M02 contract/service/entitlement
- M09可用的服務互動而非假設read
- M11 maintenance/Pool
- M12 approved conversion evidence
- M13 unresolved complaint/suppression
- M14 successful transaction/refund

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M10-001 · 未完成申辦產生可執行待辦

- 前提：客戶簽後KYC缺件且同意可聯絡
- 操作：定時名單計算
- 預期：只一待辦含缺件原因、安全摘要、owner、next_action及due_at
- API：`list_crm_worklists`, `set_next_action`
- 證據：`crm.work_item`、audit/correlation與適用外部receipt。

### UAT-M10-002 · 自助續約不計業務轉換

- 前提：網路來源持續auto renew且無sales evidence
- 操作：finance確認續約成功
- 預期：conversion type self_serve；不因有maintenance owner就給sales獎金
- API：`claim_conversion`, `decide_conversion`
- 證據：`crm.conversion`、audit/correlation與適用外部receipt。

### UAT-M10-003 · 業務續約需事前活動和交易

- 前提：有可驗證聯絡且客戶完成續約
- 操作：提出並覆核conversion claim
- 預期：保存活動／交易／因果與核定者；核定後M12才可用sales attribution
- API：`record_sales_activity`, `claim_conversion`, `decide_conversion`, `get_opportunity`
- 證據：`crm.conversion`、audit/correlation與適用外部receipt。

### UAT-M10-004 · 空白聯絡或自動訊息不能搶獎金

- 前提：只有自動群發、頁面瀏覽或空筆記
- 操作：提出sales claim
- 預期：evidence_insufficient；不能approve為業務介入
- API：`record_sales_activity`, `claim_conversion`
- 證據：`crm.activity`、audit/correlation與適用外部receipt。

### UAT-M10-005 · 沒有讀研究報告不單獨判流失

- 前提：客戶經常收即時訊息但report read未知
- 操作：計算retention list
- 預期：不因report未讀標必然流失；以可觀測服務與續約訊號附原因
- API：`list_crm_worklists`
- 證據：`crm.work_item`、audit/correlation與適用外部receipt。

### UAT-M10-006 · 禁止聯絡或未結抱怨抑制銷售

- 前提：customer marketing optout或重大open complaint
- 操作：產生升級名單
- 預期：不派行銷／升級催促；保留必要客服工作與抑制原因碼
- API：`create_crm_suppression`
- 證據：`crm.suppression`、audit/correlation與適用外部receipt。

### UAT-M10-007 · 升級機會須服務適合度允許

- 前提：風險／服務不適合高階服務
- 操作：AI推薦升級
- 預期：規則阻擋轉為可聯絡升級名單；可提示需要重評，不能直接推銷
- API：`create_opportunity`, `list_opportunities`
- 證據：`crm.opportunity`、audit/correlation與適用外部receipt。

### UAT-M10-008 · 四種owner不互相覆寫

- 前提：marketing source A、maintenance B、opportunity C
- 操作：D經核定促成transaction
- 預期：保留各自欄位與有效時間；計獎只用核定transaction owner
- API：`decide_conversion`
- 證據：`crm.conversion`、audit/correlation與適用外部receipt。

### UAT-M10-009 · 爭議認領不能重複計獎

- 前提：兩業務認領同renewal交易
- 操作：主管處理claims
- 預期：只一有效歸因或明確批准分配；pending/disputed不進正式payout
- API：`decide_conversion`
- 證據：`crm.conversion`、audit/correlation與適用外部receipt。

### UAT-M10-010 · 轉換後補寫活動不能回填搶獎

- 前提：成功交易發生於T，活動實際建立於T+1
- 操作：業務填更早happened_at且無證據
- 預期：標late_entry需獨立核定，不改created_at；不自動獲獎金
- API：`record_sales_activity`
- 證據：`crm.activity`、audit/correlation與適用外部receipt。

### UAT-M10-011 · 漏斗統計重放與退出正確

- 前提：同application events重播，客戶後withdraw
- 操作：計算期間漏斗
- 預期：按distinct application/customer定義去重；withdraw另列，不把累計簽署當有效訂閱
- API：`get_funnel_metrics`
- 證據：`crm.work_item`、audit/correlation與適用外部receipt。

### UAT-M10-012 · 同一待辦不因日批次無限重複

- 前提：相同customer/action/window已open
- 操作：再次重跑名單
- 預期：回原work_item或更新優先；保留owner與已完成結果
- API：`list_crm_worklists`, `set_next_action`
- 證據：`crm.work_item`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M10-crm.md)
- [GitHub issue #10](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/10)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
