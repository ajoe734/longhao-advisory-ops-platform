# M02 · 線上契約、公司接受與服務權限：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

實現客戶先在線上簽申請／契約，再完成 KYC／適合度／AML，由公司評估接受後才建立有效服務。客戶簽署與公司接受、契約生效、付款和權限分開記錄。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 客戶：閱讀與簽立精確版本文件、撤回／終止申請
- 營運審核者：核對資料與法遵 clearance，提出接受建議
- 公司授權接受者：依職務權限決策，與自行製作／更改條款分離
- 權限服務：執行已批准條件，不接受 LLM 指令繞過條件

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M02-001 | 客戶可以在 KYC 前完成簽署 | customer_signature=verified；application 推進；entitlement 仍 locked，UI 導向 KYC | UAT-M02-001 |
| REQ-M02-002 | 未完成審核不可公司接受 | 422 gate_not_satisfied；無 effective/active entitlement；保留拒絕理由 | UAT-M02-002 |
| REQ-M02-003 | 全部門檻通過後只開通一次 | 只有一有效生效決策與權限事件；UTC effective_at 一致 | UAT-M02-003 |
| REQ-M02-004 | 公司拒絕已收款申辦 | 契約不生效、權限維持 locked；建立可追蹤退款工作並交 M14，無直接出金 | UAT-M02-004 |
| REQ-M02-005 | 兩份並行契約權限分開 | 月訂權限到期；年度服務仍 active；客戶未因此被當全部服務中斷 | UAT-M02-005 |
| REQ-M02-006 | 文件改版不得套用舊簽署 | 拒絕 hash/version 不符；建立 v2 新簽署要求並保存 v1 | UAT-M02-006 |
| REQ-M02-007 | 審核快照過期應重新審查 | 412 或 stale_assessment；不得基於 v1直接開通 | UAT-M02-007 |
| REQ-M02-008 | 簽章 callback 不等於完成驗證 | signature=invalid；不發 verified 事件；顯示可重新簽署原因碼 | UAT-M02-008 |
| REQ-M02-009 | 續約預建不提前給權限 | 只有現行期服務生效；續約 entitlement 在生效時點才轉 active | UAT-M02-009 |
| REQ-M02-010 | 公司接受權限需職務且防自審 | 403 或 segregation_conflict；決策沒有落地 | UAT-M02-010 |
| REQ-M02-011 | 權限事件延遲不以網站快取越權 | 由權威 entitlement 再查而拒絕；記錄 denied decision id | UAT-M02-011 |
| REQ-M02-012 | 終止與留存各自生效 | 停止對應服務；仍可稽核契約及簽署原件；不刪除受保全物件 | UAT-M02-012 |

## 4. 主要使用案例

### UC-M02-A · 先簽再評估

以已批准 package 建立簽署 envelope，顯示公司仍須接受及申辦未完成的狀態；客戶完成簽署後開啟線上 KYC 流程，保持 entitlement locked。

### UC-M02-B · 公司接受與開通

審核者讀版本鎖定的簽署、KYC、適合度、AML clearance 與付款條件；授權人作 accept/reject；工作流在全部門檻有效時以原子命令記錄 effective 與 entitlement_decision，並通知 LegendFlow。

### UC-M02-C · 服務變更與退費

續約／升級建立明確 service period、差額與關聯；退款、撤回、到期及法遵限制觸發重新評估權限，保留原契約與已提供服務證據；拒絕時預收款交 M14 退款，不直接修改付款紀錄。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| package | draft → approved → effective → retired | 已使用的條款與內容 hash 不可覆寫；新條款建立新版本 |
| customer signature | requested → received → verified/invalid/expired | 驗證簽署主體、文件版號、簽署時間與簽章證據；收到 callback 不等於 verified |
| contract | pending_acceptance → accepted → effective → suspended/expired/terminated | 公司 acceptance 與 effective_at 分列；未達條款設定生效條件不 effective |
| entitlement | locked → eligible → active → suspended/expired/revoked | 每個 contract_service 分別計算；一服務失效不能使其他合法服務一併停用 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| contract.package_version | approval_status | draft/approved；draft 不得供正式簽署 | CHECK (approval_status IN ('draft','approved','retired')) |
| contract.acceptance_decision | decision | accept/reject/request_changes | CHECK (decision IN ('accept','reject','request_changes')) |
| contract.contract | status | 狀態：pending_company_acceptance / accepted / effective / suspended / expired / terminated | CHECK (status IN ('pending_company_acceptance','accepted','effective','suspended','expired','terminated')) |
| contract.entitlement | status | 狀態：locked / eligible / active / suspended / expired / revoked | CHECK (status IN ('locked','eligible','active','suspended','expired','revoked')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| contract.package_version | 本模組權威寫入 | 申請暨投顧契約及揭露文件完整版本。 | package_code,package_version | internal | contract |
| contract.customer_signature | 本模組權威寫入 | 客戶先簽申請／契約封包的不可變證據。 | provider,provider_transaction_id | internal | contract |
| contract.acceptance_decision | 本模組權威寫入 | 公司接受、拒絕或要求補件的完整決策歷史；accept才另產生company_acceptance。 | tenant_id,id | internal | contract |
| contract.company_acceptance | 本模組權威寫入 | 公司完成必要審查後接受；與客戶簽署分離。 | application_id；decision_id | internal | contract |
| contract.contract | 本模組權威寫入 | 法律關係及服務契約狀態；不把付款或前端權限當成成立證據。 | contract_number；application_id | internal | contract |
| contract.contract_service | 本模組權威寫入 | 一客多契約多服務及續約服務鏈。 | tenant_id,id | internal | contract |
| contract.entitlement | 本模組權威寫入 | 權限來源為有效契約與審查結果；配送時重新檢查。 | contract_service_id,feature_code | internal | contract |
| contract.entitlement_decision | 本模組權威寫入 | 每次啟停權限的決策紀錄。 | tenant_id,id | internal | audit |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_contract_package | POST /contract-packages | PackageCreate | ContractPackage | contract_operator | 依OpenAPI |
| approve_contract_package | POST /contract-packages/{package_version_id}/approval | Decision | ContractPackage | compliance_reviewer | If-Match 必須 |
| get_contract_package | GET /contract-packages/{package_version_id} | — | ContractPackage | contract_operator | 依OpenAPI |
| create_customer_signature | POST /applications/{application_id}/customer-signature | CustomerSignatureRequest | SignatureSession | contract_operator | If-Match 必須 |
| receive_customer_signature | POST /webhooks/customer-signatures | CustomerSignatureCallback | CallbackReceipt | signature_adapter | 依OpenAPI |
| decide_company_acceptance | POST /applications/{application_id}/company-acceptance | CompanyAcceptanceRequest | CompanyAcceptance | company_acceptor | If-Match 必須 |
| get_contract | GET /contracts/{contract_id} | — | Contract | contract_operator | 依OpenAPI |
| create_contract_amendment | POST /contracts/{contract_id}/amendments | AmendmentRequest | Application | contract_operator | If-Match 必須 |
| terminate_contract | POST /contracts/{contract_id}/termination | TerminationRequest | Contract | contract_operator | If-Match 必須 |
| list_customer_entitlements | GET /customers/{customer_id}/entitlements | — | EntitlementPage | contract_operator | 依OpenAPI |
| evaluate_entitlements | POST /contracts/{contract_id}/entitlement-evaluations | EntitlementEvaluationRequest | EntitlementDecision | entitlement_service | If-Match 必須 |
| suspend_entitlement | POST /entitlements/{entitlement_id}/suspension | SuspensionRequest | Entitlement | contract_supervisor | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| application.customer_signed | 發出 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| application.company_decided | 發出 | M02 | M01,M10,M14 | customer_confidential | 1.0.0 |
| contract.effective | 發出 | M02 | M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.terminated | 發出 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| entitlement.changed | 發出 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| contract.service_ended | 發出 | M02 | M11,M10,M14 | customer_confidential | 1.0.0 |
| application.created | ／接收 | M01 | M02,M10 | customer_confidential | 1.0.0 |
| kyc.decision_recorded | ／接收 | M03 | M02,M04 | customer_confidential | 1.0.0 |
| suitability.assessed | ／接收 | M03 | M02 | customer_confidential | 1.0.0 |
| business_clearance.changed | ／接收 | M04 | M02 | customer_confidential | 1.0.0 |
| finance.payment_confirmed | ／接收 | M14 | M02,M10 | customer_confidential | 1.0.0 |
| finance.refund_confirmed | ／接收 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |
| kyc.refresh_required | ／接收 | M03 | M02,M10 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 客戶簽署頁契約介面契約 | 提供文件摘要、完整原件、簽署條件、公司待接受狀態與線上下一步 | LegendFlow 實作畫面；本模組回傳 document hash/version、allowed_actions，不能由前端傳 effective=true |
| 公司接受工作台 | 同屏顯示四項審核狀態、版本、付款條件、異常與決策理由 | 只對符合條件者啟用接受；衝突重載；拒絕需選通知模板並建立款項處理工作 |
| 契約與權限明細 | 每契約、每服務顯示 start/end、pause、source_decision、最新投遞判斷 | 手動例外需獨立權限、期限與原因；不能略過法遵 mandatory gate |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM 可解釋已批准條款、整理待補文件及草擬通知，不得產生客戶尚未同意的新條款。
- 必須受控：文件版本、簽署驗證、公司接受及權限啟用由確定性工作流與授權決策控制。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 簽章 provider callback 重送：驗證 provider reference 與 payload hash；同一 envelope 僅一有效簽署。
- 審核期間資料改版：舊審核 snapshot 失效，重新評估而非套用已失效 clearance。
- 開通事件傳送失敗：權威決策已提交且 outbox 重試；LegendFlow 不可自行開權限。
- 退款成功不意味可刪契約；終止、生效、退款與服務截止各保留實際時間。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M01 application/customer
- M03 KYC 與適合度決策
- M04 不含案情的 business_clearance
- M14 payment/service schedule/refund
- M09 LegendFlow entitlement projection

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M02-001 · 客戶可以在 KYC 前完成簽署

- 前提：有可用契約 package，但無 KYC submission
- 操作：客戶完成合法線上簽署
- 預期：customer_signature=verified；application 推進；entitlement 仍 locked，UI 導向 KYC
- API：`create_customer_signature`, `receive_customer_signature`
- 證據：`contract.customer_signature`、audit/correlation與適用外部receipt。

### UAT-M02-002 · 未完成審核不可公司接受

- 前提：簽署完成但 KYC 或 AML pending
- 操作：公司接受者送 accept
- 預期：422 gate_not_satisfied；無 effective/active entitlement；保留拒絕理由
- API：`decide_company_acceptance`
- 證據：`contract.company_acceptance`、audit/correlation與適用外部receipt。

### UAT-M02-003 · 全部門檻通過後只開通一次

- 前提：簽署、KYC、適合度、AML、付款條件均有效
- 操作：兩個工作者同時要求生效
- 預期：只有一有效生效決策與權限事件；UTC effective_at 一致
- API：`evaluate_entitlements`, `list_customer_entitlements`
- 證據：`contract.entitlement_decision`、audit/correlation與適用外部receipt。

### UAT-M02-004 · 公司拒絕已收款申辦

- 前提：客戶已付款但尚未獲接受
- 操作：授權人 reject
- 預期：契約不生效、權限維持 locked；建立可追蹤退款工作並交 M14，無直接出金
- API：`decide_company_acceptance`
- 證據：`contract.company_acceptance`、audit/correlation與適用外部receipt。

### UAT-M02-005 · 兩份並行契約權限分開

- 前提：年度服務與月訂皆 active
- 操作：月訂到期
- 預期：月訂權限到期；年度服務仍 active；客戶未因此被當全部服務中斷
- API：`get_contract`, `list_customer_entitlements`
- 證據：`contract.contract_service`、audit/correlation與適用外部receipt。

### UAT-M02-006 · 文件改版不得套用舊簽署

- 前提：客戶簽的是 package v1，公司要求 v2
- 操作：嘗試將 v1 signature 掛到 v2
- 預期：拒絕 hash/version 不符；建立 v2 新簽署要求並保存 v1
- API：`create_contract_package`, `approve_contract_package`, `get_contract_package`, `create_customer_signature`
- 證據：`contract.package_version`、audit/correlation與適用外部receipt。

### UAT-M02-007 · 審核快照過期應重新審查

- 前提：公司決策讀 KYC v1，客戶已送 v2
- 操作：以舊版本送 acceptance
- 預期：412 或 stale_assessment；不得基於 v1直接開通
- API：`decide_company_acceptance`
- 證據：`contract.company_acceptance`、audit/correlation與適用外部receipt。

### UAT-M02-008 · 簽章 callback 不等於完成驗證

- 前提：provider 回 received 而驗證失敗
- 操作：處理簽署回呼
- 預期：signature=invalid；不發 verified 事件；顯示可重新簽署原因碼
- API：`receive_customer_signature`
- 證據：`contract.customer_signature`、audit/correlation與適用外部receipt。

### UAT-M02-009 · 續約預建不提前給權限

- 前提：已有效服務到期於下月；續約已簽並付款
- 操作：執行今日權限查詢
- 預期：只有現行期服務生效；續約 entitlement 在生效時點才轉 active
- API：`create_contract_amendment`, `evaluate_entitlements`
- 證據：`contract.contract_service`、audit/correlation與適用外部receipt。

### UAT-M02-010 · 公司接受權限需職務且防自審

- 前提：一般業務或同一條款製作／接受者不符 SoD
- 操作：呼叫 company acceptance
- 預期：403 或 segregation_conflict；決策沒有落地
- API：`decide_company_acceptance`
- 證據：`contract.company_acceptance`、audit/correlation與適用外部receipt。

### UAT-M02-011 · 權限事件延遲不以網站快取越權

- 前提：後端已 suspend，LegendFlow 投影未更新
- 操作：請求受限內容或發訊息
- 預期：由權威 entitlement 再查而拒絕；記錄 denied decision id
- API：`suspend_entitlement`, `evaluate_entitlements`
- 證據：`contract.entitlement_decision`、audit/correlation與適用外部receipt。

### UAT-M02-012 · 終止與留存各自生效

- 前提：有效契約遇授權終止並有 legal hold
- 操作：提交終止
- 預期：停止對應服務；仍可稽核契約及簽署原件；不刪除受保全物件
- API：`terminate_contract`, `get_contract`
- 證據：`contract.contract`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M02-contracts_entitlements.md)
- [GitHub issue #2](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/2)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
