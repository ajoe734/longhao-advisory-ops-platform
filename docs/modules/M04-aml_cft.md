# M04 · AML/CFT 查核、風險與受限案件：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

提供身分與風險導向查核、可能命中處理、加強審查、持續監控及受限調查流程。一般營運只取得可否建立／維持關係的安全摘要，不取得敏感案情。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- AML 操作人：處理 screening、比對證據與 EDD
- AML 核定者／負責人：獨立決策、案件覆核與依法處理通報
- 一般營運：只讀 business_clearance 狀態與補件指令
- 內稽：在經授權且必要範圍讀證據，與案件經辦分離

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M04-001 | 可能命中建立受限案件 | 建立 case、clearance pending/restricted；公司接受 gate不能通過 | UAT-M04-001 |
| REQ-M04-002 | LLM 無權解除命中 | 403；案件不變；安全審計記錄拒絕，不把 AI summary當核定 | UAT-M04-002 |
| REQ-M04-003 | 一般業務不可讀 AML 案情 | 403/不存在等值回應；一般摘要只顯示pending/restricted狀態 | UAT-M04-003 |
| REQ-M04-004 | 名單 provider timeout 不視為無命中 | error/retry/manual_required，clearance不轉permitted；告警含provider不含PII | UAT-M04-004 |
| REQ-M04-005 | 誤命中解除需核定與證據 | 自核拒絕；合法checker核定後產生case action與新clearance版本 | UAT-M04-005 |
| REQ-M04-006 | 名單版本更新可再查 | 新增screening保留v1；新的possiblematch阻擋後續操作，不能覆寫為原通過 | UAT-M04-006 |
| REQ-M04-007 | 宣告簽名不能替代查核 | 拒絕；簽署文件僅證據，必須完成指定查核與決策 | UAT-M04-007 |
| REQ-M04-008 | 受限事件只輸出安全摘要 | payload只含customer id/status/version/policy ref；禁止raw案情與PII | UAT-M04-008 |
| REQ-M04-009 | EDD 補件可追蹤且不洩密 | 一般平台取得中性待補清單；完整研判僅AML工作台可見 | UAT-M04-009 |
| REQ-M04-010 | 內稽存取有目的與時限 | 只有授權範圍可讀；每次讀取audit；不能修改決策 | UAT-M04-010 |
| REQ-M04-011 | 未結案件受 legal hold | 物件仍保留且記錄hold；刪除作業報skipped_hold，不解除限制 | UAT-M04-011 |
| REQ-M04-012 | 過期clearance不能被公司接受重用 | 回不可建立關係及需更新的安全原因；不公開內部詳細案情 | UAT-M04-012 |

## 4. 主要使用案例

### UC-M04-A · 首次查核

接收已簽申請的必要 KYC 欄位，在受限 adapter 使用經核定名單與篩檢 policy；記錄 provider、名單版本、查詢與結果摘要；具體命中轉 case。

### UC-M04-B · 可能命中與 EDD

人員核對身份、別名、關係及必要文件，提出判斷與 evidence；另一授權人按 policy核定解除／限制；非判定為洗錢、非自動通報。

### UC-M04-C · 持續監控與處置

名單更新、風險資料變更及異常金流事件觸發再查；受限調查／通報遵循核定流程；只向一般模組發布不洩漏原因的 clearance change，必要時保全資料。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| screening | queued → running → clear/possible_match/error | clear 代表該查核沒有命中，不等同整體 KYC 或公司接受 |
| case | open → investigating → edd_required/decision_pending → resolved/escalated | possible match 不能由 LLM 或一般 sales 清除；保留名單版本與比對條件 |
| business clearance | pending → permitted/restricted/blocked/refresh_required | 對外安全 projection 不包含名單、疑似交易或申報細節；與 case 實體分開 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| aml.screening | status | 狀態：pending / clear / possible_match / confirmed_match / error | CHECK (status IN ('pending','clear','possible_match','confirmed_match','error')) |
| aml.case_file | status | 狀態：open / under_review / escalated / cleared / rejected / closed | CHECK (status IN ('open','under_review','escalated','cleared','rejected','closed')) |
| aml.business_clearance | decision | approved / pending / denied / expired | CHECK (decision IN ('approved','pending','denied','expired')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| aml.screening | 本模組權威寫入 | 名單比對供應商查核；模糊命中不是自動認定或自動清除。 | provider,provider_request_id | internal | aml |
| aml.case_file | 本模組權威寫入 | AML/CFT 受限覆核案件，禁止 CRM 讀內情。 | tenant_id,id | internal／restricted_aml | aml |
| aml.case_action | 本模組權威寫入 | AML 調查、EDD、申報與決定之受限軌跡。 | tenant_id,id | internal／restricted_aml | aml |
| aml.business_clearance | 本模組權威寫入 | 供公司接受服務查核的最小結果；不暴露比對名單、申報或案件細節。 | tenant_id,id | internal | aml |
| core.audit_event | 受控共用基礎 | 不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。 | tenant_id,id | internal | audit |
| core.legal_hold | 受控共用基礎 | 司法、爭議或稽核保存指示；解除以新事件表達。 | tenant_id,id | internal | audit |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| submit_aml_declaration | POST /applications/{application_id}/aml-declarations | AmlDeclarationRequest | AmlDeclarationReceipt | aml_officer | If-Match 必須 |
| start_aml_screening | POST /aml/screenings | ScreeningRequest | AmlScreening | aml_officer | 依OpenAPI |
| receive_aml_screening | POST /webhooks/aml-screenings | ScreeningCallback | CallbackReceipt | aml_adapter | 依OpenAPI |
| list_aml_cases | GET /aml/cases | — | AmlCasePage | aml_officer,aml_supervisor | 依OpenAPI |
| get_aml_case | GET /aml/cases/{aml_case_id} | — | AmlCase | aml_officer,aml_supervisor | 依OpenAPI |
| add_aml_case_action | POST /aml/cases/{aml_case_id}/actions | AmlActionRequest | AmlCase | aml_officer | If-Match 必須 |
| decide_aml_case | POST /aml/cases/{aml_case_id}/decision | AmlDecisionRequest | BusinessClearance | aml_supervisor | If-Match 必須 |
| get_business_clearance | GET /applications/{application_id}/business-clearance | — | BusinessClearance | company_acceptor | 依OpenAPI |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| aml.case_changed | 發出／接收 | M04 | M04 | aml_restricted | 1.0.0 |
| business_clearance.changed | 發出 | M04 | M02 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| kyc.decision_recorded | ／接收 | M03 | M02,M04 | customer_confidential | 1.0.0 |
| customer.profile_updated | ／接收 | M01 | M03,M04 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| AML 受限工作台 | 獨立導覽與權限；顯示可能命中、名單版本、查核時間、原始受控 evidence、核定鏈 | 禁止全平台搜尋索引內容；所有看案／下載需audit |
| EDD 補件 | 一般平台只看核定的中性補件要求與期限 | 禁止用聊天回覆洩漏可疑交易申報或內部研判 |
| 核定操作 | 顯示 maker/checker、decision rationale、effective range、重新查核條件 | 不可同一人完成受控例外製作與核定；無高權限 blanket clear |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM 只可在核准的受限環境整理可用證據與製作工作摘要；預設不向通用 LLM 傳送 AML 案情。
- 必須受控：命中解除、關係建立決策、EDD 充分性、通報與解除凍結由授權人員與受控規則處理，禁止 AI 自動核准。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 名單 provider 失聯、版本不明或結果過期：保持 pending/manual review，不以無結果等同 clear。
- 同名誤判的解除保留證據和雙人核定；後續名單版號更新可再查，不刪舊紀錄。
- 一般 event bus、CRM、客服、Pantheon prompt 禁止出現 raw PII、命中詳情或通報內容。
- 對外通知模板不能透露調查敏感資訊；notify event 只放一般原因碼與受控 template_id。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M03 最小必要且受限 KYC 資料
- M02 business_clearance gating
- M14 可疑／異常付款事件安全引用
- M13 專案移交採受限連結，不把案情複製到客服

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M04-001 · 可能命中建立受限案件

- 前提：名單 adapter 回 possible_match
- 操作：處理 screening response
- 預期：建立 case、clearance pending/restricted；公司接受 gate不能通過
- API：`start_aml_screening`, `receive_aml_screening`
- 證據：`aml.case_file`、audit/correlation與適用外部receipt。

### UAT-M04-002 · LLM 無權解除命中

- 前提：AI service principal 僅有 summary scope
- 操作：呼叫 clear/decision
- 預期：403；案件不變；安全審計記錄拒絕，不把 AI summary當核定
- API：`decide_aml_case`
- 證據：`aml.case_action`、audit/correlation與適用外部receipt。

### UAT-M04-003 · 一般業務不可讀 AML 案情

- 前提：sales 已知 customer_id
- 操作：查 restricted case 或事件payload
- 預期：403/不存在等值回應；一般摘要只顯示pending/restricted狀態
- API：`get_aml_case`, `get_business_clearance`
- 證據：`aml.business_clearance`、audit/correlation與適用外部receipt。

### UAT-M04-004 · 名單 provider timeout 不視為無命中

- 前提：screening服務故障
- 操作：執行查核
- 預期：error/retry/manual_required，clearance不轉permitted；告警含provider不含PII
- API：`start_aml_screening`
- 證據：`aml.screening`、audit/correlation與適用外部receipt。

### UAT-M04-005 · 誤命中解除需核定與證據

- 前提：maker提出false_positive證據
- 操作：同一人自核或另一人核定
- 預期：自核拒絕；合法checker核定後產生case action與新clearance版本
- API：`add_aml_case_action`, `decide_aml_case`
- 證據：`aml.case_action`、audit/correlation與適用外部receipt。

### UAT-M04-006 · 名單版本更新可再查

- 前提：客戶曾在list-v1通過
- 操作：list-v2事件觸發重查
- 預期：新增screening保留v1；新的possiblematch阻擋後續操作，不能覆寫為原通過
- API：`start_aml_screening`, `receive_aml_screening`
- 證據：`aml.screening`、audit/correlation與適用外部receipt。

### UAT-M04-007 · 宣告簽名不能替代查核

- 前提：客戶已簽 AML/CFT聲明但provider未查
- 操作：要求business clearance permitted
- 預期：拒絕；簽署文件僅證據，必須完成指定查核與決策
- API：`submit_aml_declaration`, `get_business_clearance`
- 證據：`aml.business_clearance`、audit/correlation與適用外部receipt。

### UAT-M04-008 · 受限事件只輸出安全摘要

- 前提：案件包含名單與申報備註
- 操作：發clearance_changed到一般outbox
- 預期：payload只含customer id/status/version/policy ref；禁止raw案情與PII
- API：`get_business_clearance`
- 證據：`aml.business_clearance`、audit/correlation與適用外部receipt。

### UAT-M04-009 · EDD 補件可追蹤且不洩密

- 前提：case要求EDD且需客戶文件
- 操作：核定補件通知
- 預期：一般平台取得中性待補清單；完整研判僅AML工作台可見
- API：`add_aml_case_action`
- 證據：`aml.case_action`、audit/correlation與適用外部receipt。

### UAT-M04-010 · 內稽存取有目的與時限

- 前提：auditor獲限定案件read grant
- 操作：查閱／下載案證
- 預期：只有授權範圍可讀；每次讀取audit；不能修改決策
- API：`list_aml_cases`, `get_aml_case`
- 證據：`aml.case_file`、audit/correlation與適用外部receipt。

### UAT-M04-011 · 未結案件受 legal hold

- 前提：依法／公司核定保全中的case
- 操作：到達常規留存刪除時間
- 預期：物件仍保留且記錄hold；刪除作業報skipped_hold，不解除限制
- API：`create_legal_hold`
- 證據：`aml.case_file`、audit/correlation與適用外部receipt。

### UAT-M04-012 · 過期clearance不能被公司接受重用

- 前提：permitted版本已expired或refresh_required
- 操作：M02檢查接受條件
- 預期：回不可建立關係及需更新的安全原因；不公開內部詳細案情
- API：`get_business_clearance`
- 證據：`aml.business_clearance`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M04-aml_cft.md)
- [GitHub issue #4](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/4)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
