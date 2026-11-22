# M11 · 維護歸屬、離職移交與公司 Pool：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

保存客戶維護責任的有效期間與實質接手證據；離職即回公司，重新開發者在實際促成轉換後取得該筆業務獎金。維護assignment不是獎金憑證。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 業務主管：按政策分派／移交與處理Pool競爭認領
- 業務：認領待開發客戶、記錄實質聯絡與後續機會
- HR或授權人：送出有效離職事件，不自行重分已核定歷史獎金
- Pool worker：按核定服務中斷政策釋放責任，不能採每週未聯絡規則

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M11-001 | 自助客戶無維護人也能續訂 | 服務正常延續；無sales conversion；marketing/company依核定policy計算 | UAT-M11-001 |
| REQ-M11-002 | 離職立即回公司不等七天 | 結束assignment並建company pool entry；撤銷離職者未來操作權 | UAT-M11-002 |
| REQ-M11-003 | 新指派不是新獎金 | 不產生新業務sales獎金；保留company retention邏輯 | UAT-M11-003 |
| REQ-M11-004 | 新業務實際再開發才可領後續獎金 | 該transaction指向新業務；舊已核定獎金與來源不改 | UAT-M11-004 |
| REQ-M11-005 | 七日規則不是七天沒聯絡 | 不因未聯絡回Pool；最多依內部維護SLA提醒，不能移獎金 | UAT-M11-005 |
| REQ-M11-006 | 全部服務中斷七日才符合提案 | 前者不符合、後者符合候選；正式執行仍須approved policy | UAT-M11-006 |
| REQ-M11-007 | 單一月訂到期不抵銷年度有效服務 | 不回Pool；all_paid_services_lapsed=false | UAT-M11-007 |
| REQ-M11-008 | 未核定七日定義禁止自動回收 | policy_not_approved；preview可回受影響清單，assignment不變 | UAT-M11-008 |
| REQ-M11-009 | 法遵停權不被當可銷售流失名單 | 列restricted，不派續約催促；完整AML理由不外洩 | UAT-M11-009 |
| REQ-M11-010 | 兩人同時認領只有一人成功 | 一成功一409/412；無重疊active assignment；失敗者不能記成owner | UAT-M11-010 |
| REQ-M11-011 | 歷史佣金不因移交改歸屬 | 歷史entry仍原recipient；新的轉換依新證據另定 | UAT-M11-011 |
| REQ-M11-012 | 服務恢復可中止尚未成立中斷條件 | 中斷clock重設／結束；不沿用舊T在隔日回Pool | UAT-M11-012 |

## 4. 主要使用案例

### UC-M11-A · 官網自助與維護指派

網路自行申辦未有維護人時company/marketing pool保有歸屬；日後指派業務維護只建立assignment；客戶自行續訂不因assignment自動產生sales award。

### UC-M11-B · 離職與接手

接收有效employee departure，立即終止未來assignment並移回company pool；歷史conversion與已核定獎金保留；新業務接手記錄實質活動，成功續約／升級交M10核定。

### UC-M11-C · 服務中斷七日提案

以「全部付費服務連續中斷168小時」作待確認的規則參數，排除仍有效年度服務；不是七天沒聯絡。規則未正式核定可模擬，不自動上線變更獎金歸屬。到期、停權、法遵限制與行政故障要分原因判斷。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| assignment | pending → active → ended/released | 同一客戶同一責任類型不可有重疊active期間；明確start/end與理由 |
| pool entry | available → claimed → redeveloping → converted/released/blocked | claim僅工作責任，不是成功conversion；法遵／抱怨限制另標 |
| policy | draft → approved → effective → retired | 「斷掉一周」的系統定義必須版本化；目前服務中斷解釋為待確認實作提案 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| ownership.policy_version | approval_status | draft/approved/retired | CHECK (approval_status IN ('draft','approved','retired')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| ownership.policy_version | 本模組權威寫入 | Pool 判定政策；七天未聯絡不是既定規則。 | policy_code,policy_version | internal | policy |
| ownership.assignment | 本模組權威寫入 | 維護關係的有效時間；不直接決定所有交易佣金。 | tenant_id,id | internal | commission |
| ownership.pool_entry | 本模組權威寫入 | 客戶回公司 Pool 的證據與重新開發狀態。 | tenant_id,id | internal | commission |
| crm.activity | 唯讀／port，寫入由M10 | 真實業務行為及客戶回應證據；AI 建議、群發或分配名字不能單獨計獎。 | tenant_id,id | internal | crm |
| crm.conversion | 唯讀／port，寫入由M10 | 單筆交易銷售歸因；不是單純維護人欄位。 | contract_service_id | internal | commission |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| list_company_pool | GET /ownership/company-pool | — | PoolEntryPage | sales,sales_supervisor | 依OpenAPI |
| assign_maintenance_owner | POST /customers/{customer_id}/maintenance-assignments | MaintenanceAssignmentRequest | MaintenanceAssignment | sales_supervisor | If-Match 必須 |
| claim_pool_customer | POST /ownership/pool-entries/{pool_entry_id}/claim | PoolClaimRequest | MaintenanceAssignment | sales | If-Match 必須 |
| release_maintenance_owner | POST /ownership/assignments/{assignment_id}/release | ReasonRequest | PoolEntry | sales_supervisor | If-Match 必須 |
| record_employee_departure | POST /ownership/departures | EmployeeDepartureRequest | DepartureReceipt | hr_operator | 依OpenAPI |
| evaluate_company_pool | POST /ownership/pool-evaluations | PoolEvaluationRequest | PoolEvaluationResult | ownership_service | 依OpenAPI |
| get_ownership_history | GET /customers/{customer_id}/ownership-history | — | MaintenanceAssignmentPage | sales,sales_supervisor | 依OpenAPI |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| ownership.assignment_changed | 發出 | M11 | M10,M12,M13 | customer_confidential | 1.0.0 |
| ownership.pool_entered | 發出 | M11 | M10 | customer_confidential | 1.0.0 |
| ownership.employee_departed | 發出／接收 | M11 | M01,M10,M11 | internal | 1.0.0 |
| contract.effective | ／接收 | M02 | M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.terminated | ／接收 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.service_ended | ／接收 | M02 | M11,M10,M14 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 維護責任時間軸 | 每assignment的owner、開始／結束、移交原因、實際聯絡與交易 | 清楚分目前維護人和歷史計獎人；禁止覆寫歷史owner |
| 公司Pool工作台 | 可開發／受限分組，來源、全部服務中斷時長、同意聯絡、上次實質活動、claim狀態 | 同時認領用CAS；先顯示責任與抑制，不能誰點快就拿獎金 |
| 政策預演 | 七日定義、時間區、服務集合、排除原因、受影響客戶清單 | 可比較規則結果；未核定policy禁止批量實際回收 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM可整理接手摘要與建議聯絡重點；不能決定owner、製造活動證據或覆寫歷史獎金。
- 必須受控：assignment唯一性、離職時點、服務中斷時計與claim競爭由交易和核定政策控制。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 同時兩業務claim：單一交易與unique active assignment；loser回409並取得最新狀態。
- 事件延遲：以真實effective_at記離職／服務中斷，收到時間另外保存；不回溯撤銷已實際完成且核定的轉換。
- 自動回Pool規則尚未核定：只preview，正式job回policy_not_approved。
- 法遵停權不是普通流失；不得自動當可以促銷的Pool客戶。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M02 all contract_service states
- M10 activities/conversion causal evidence
- M12 historical commission snapshots
- M13/04 safe restriction flags
- HR departure adapter

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M11-001 · 自助客戶無維護人也能續訂

- 前提：網路來源且owner為company
- 操作：客戶自助續訂
- 預期：服務正常延續；無sales conversion；marketing/company依核定policy計算
- API：`list_company_pool`
- 證據：`ownership.assignment`、audit/correlation與適用外部receipt。

### UAT-M11-002 · 離職立即回公司不等七天

- 前提：owner已離職且effective_at到達
- 操作：處理departure
- 預期：結束assignment並建company pool entry；撤銷離職者未來操作權
- API：`record_employee_departure`, `release_maintenance_owner`
- 證據：`ownership.assignment`、audit/correlation與適用外部receipt。

### UAT-M11-003 · 新指派不是新獎金

- 前提：公司Pool客戶分派新業務但無聯絡
- 操作：客戶自己續訂
- 預期：不產生新業務sales獎金；保留company retention邏輯
- API：`assign_maintenance_owner`
- 證據：`ownership.assignment`、audit/correlation與適用外部receipt。

### UAT-M11-004 · 新業務實際再開發才可領後續獎金

- 前提：接手業務有可驗證聯絡並成功續約
- 操作：M10核定conversion
- 預期：該transaction指向新業務；舊已核定獎金與來源不改
- API：`claim_pool_customer`
- 證據：`ownership.assignment`、audit/correlation與適用外部receipt。

### UAT-M11-005 · 七日規則不是七天沒聯絡

- 前提：客戶年度服務active、七天無聯絡
- 操作：執行Pool預演
- 預期：不因未聯絡回Pool；最多依內部維護SLA提醒，不能移獎金
- API：`evaluate_company_pool`
- 證據：`ownership.policy_version`、audit/correlation與適用外部receipt。

### UAT-M11-006 · 全部服務中斷七日才符合提案

- 前提：所有服務end_at=T，無其他有效服務
- 操作：在T+167h及T+168h預演
- 預期：前者不符合、後者符合候選；正式執行仍須approved policy
- API：`evaluate_company_pool`
- 證據：`ownership.pool_entry`、audit/correlation與適用外部receipt。

### UAT-M11-007 · 單一月訂到期不抵銷年度有效服務

- 前提：月訂expired、年度active
- 操作：evaluate pool
- 預期：不回Pool；all_paid_services_lapsed=false
- API：`evaluate_company_pool`
- 證據：`ownership.pool_entry`、audit/correlation與適用外部receipt。

### UAT-M11-008 · 未核定七日定義禁止自動回收

- 前提：policy draft且規則已完成
- 操作：執行live evaluate
- 預期：policy_not_approved；preview可回受影響清單，assignment不變
- API：`evaluate_company_pool`
- 證據：`ownership.policy_version`、audit/correlation與適用外部receipt。

### UAT-M11-009 · 法遵停權不被當可銷售流失名單

- 前提：contract suspended原因compliance
- 操作：evaluate pool/worklist
- 預期：列restricted，不派續約催促；完整AML理由不外洩
- API：`evaluate_company_pool`
- 證據：`ownership.pool_entry`、audit/correlation與適用外部receipt。

### UAT-M11-010 · 兩人同時認領只有一人成功

- 前提：Pool entry available同version
- 操作：兩sales同時claim
- 預期：一成功一409/412；無重疊active assignment；失敗者不能記成owner
- API：`claim_pool_customer`
- 證據：`ownership.assignment`、audit/correlation與適用外部receipt。

### UAT-M11-011 · 歷史佣金不因移交改歸屬

- 前提：舊業務已核定conversion與commission
- 操作：assignment改新業務
- 預期：歷史entry仍原recipient；新的轉換依新證據另定
- API：`assign_maintenance_owner`, `get_ownership_history`
- 證據：`ownership.assignment`、audit/correlation與適用外部receipt。

### UAT-M11-012 · 服務恢復可中止尚未成立中斷條件

- 前提：全部中斷第6天恢復一項active服務
- 操作：evaluate七日窗口
- 預期：中斷clock重設／結束；不沿用舊T在隔日回Pool
- API：`evaluate_company_pool`
- 證據：`ownership.pool_entry`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M11-ownership_pool.md)
- [GitHub issue #11](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/11)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
