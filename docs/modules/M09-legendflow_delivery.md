# M09 · LegendFlow、會員服務與即時通路投遞：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

以正式API整合既有LegendFlow客戶端；提供符合授權的會員feed、報告連結與LINE／簡訊等訊息投遞，精確保存accepted、sent、delivered、read及未知狀態。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 客戶：讀本人有權限的feed、簽署報告、綁定通路及偏好
- LegendFlow adapter：使用受限member scopes，不直連營運資料庫
- 投遞worker：按publication與即時授權發已核定文案
- 客服／營運：查投遞摘要、處理例外，不能改已送文字

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M09-001 | LegendFlow feed只顯示本人權限內容 | 僅回X可見內容；Y不可由id猜到／直接下載 | UAT-M09-001 |
| REQ-M09-002 | 報告下載授權需當前有效 | 依核定存取政策拒絕新授權；短效token不可越權讀其他客戶資料 | UAT-M09-002 |
| REQ-M09-003 | 未驗證通路不能綁成active | 要求challenge/provider proof；未驗證不成active收件端 | UAT-M09-003 |
| REQ-M09-004 | 發送前再查權限 | 不送；記suppressed及當時entitlement decision id | UAT-M09-004 |
| REQ-M09-005 | accepted不誤顯示已送達 | accepted_at存在但delivered_at/read_at缺；不偽造送達率 | UAT-M09-005 |
| REQ-M09-006 | 未知已讀維持unknown | read_state=unknown；不把無read事件認定未讀或流失 | UAT-M09-006 |
| REQ-M09-007 | timeout先查結果避免重複 | 先查provider request id或同key安全重試；只一logical message | UAT-M09-007 |
| REQ-M09-008 | 亂序receipt不倒退 | 保存兩時間與接收時間；最終狀態不從read倒退 | UAT-M09-008 |
| REQ-M09-009 | 過期DLQ不得重送舊建議 | 回不可重發原因並稽核；不對provider送請求 | UAT-M09-009 |
| REQ-M09-010 | 撤回／更正保留原文 | feed標示更正，新message引用原；原body/hash/attempt仍可查 | UAT-M09-010 |
| REQ-M09-011 | 通路偏好與必要服務分類分開 | 行銷被抑制；必要通知依已核定分類與偏好處理，禁止把行銷偽裝服務 | UAT-M09-011 |
| REQ-M09-012 | provider webhook驗證與重放防護 | 401/422且不更新message；合法重送去重並回原receipt | UAT-M09-012 |

## 4. 主要使用案例

### UC-M09-A · 會員feed與報告下載

LegendFlow以會員身份交換受限token，query時驗證customer/tenant及當前權限；只回已發布／可用資料；報告用短效簽名URL或受權代理讀取，不暴露bucket永久位置。

### UC-M09-B · 即時推播

consume publication.created，查publication狀態、expiry、客戶權限與通路偏好；依frozen channeltext產生request，保存attempt後對provider提交；處理receipt與對帳。

### UC-M09-C · 例外與更正

provider timeout保留unknown並查狀態；可重送的失敗按相同邏輯訊息id處理；撤回／更正事件更新feed並通知受影響客戶，原文不覆寫。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| message | queued → dispatching → accepted/sent/delivered/read；可failed/unknown/suppressed/expired | 狀態證據各自存在；accepted不當sent，未知read保持unknown |
| attempt | created → provider_submitted → confirmed/timeout/reconciled/failed | 每attempt有request/provider id；未確認結果先查再重送 |
| channel binding | unverified → verified → active/revoked | 以provider身分與challenge確認，不只收一個自填電話號碼 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| delivery.message | status | 狀態：queued / accepted / sent / delivered / failed / expired / suppressed / unknown | CHECK (status IN ('queued','accepted','sent','delivered','failed','expired','suppressed','unknown')) |
| delivery.channel_binding | status | 狀態：verified / unsubscribed / blocked / revoked | CHECK (status IN ('verified','unsubscribed','blocked','revoked')) |
| contract.entitlement | status | 狀態：locked / eligible / active / suspended / expired / revoked | CHECK (status IN ('locked','eligible','active','suspended','expired','revoked')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| delivery.message | 本模組權威寫入 | 每 recipient/channel 一個邏輯消息；供應商重試另建 attempt。 | publication_id,recipient_id,channel；provider_idempotency_key | internal | report |
| delivery.channel_binding | 本模組權威寫入 | 經驗證會員/LINE/手機/郵件收件身分；路由不可使用模型產生的新地址。 | provider,channel,destination_hmac | internal／personal | customer |
| delivery.attempt | 本模組權威寫入 | 各次對 provider 實際傳送嘗試。 | message_id,attempt_no | internal | report |
| delivery.receipt | 本模組權威寫入 | 回呼事件不可變，亂序回呼不可把 delivered 降級為 sent。 | provider,provider_event_id | internal | report |
| publication.recipient | 唯讀／port，寫入由M08 | 逐收件人權益證據；不存姓名或手機。 | snapshot_id,customer_id,entitlement_id | internal | report |
| contract.entitlement | 唯讀／port，寫入由M02 | 權限來源為有效契約與審查結果；配送時重新檢查。 | contract_service_id,feature_code | internal | contract |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| get_member_feed | GET /member/feed | — | MemberFeedPage | member | 依OpenAPI |
| request_member_report_access | GET /member/reports/{report_version_id}/access | — | AccessTicket | member | 依OpenAPI |
| bind_member_channel | POST /member/channel-bindings | ChannelBindingRequest | ChannelBinding | member | 依OpenAPI |
| set_communication_preference | POST /member/communication-preferences | CommunicationPreferenceRequest | CommunicationPreference | member | 依OpenAPI |
| list_deliveries | GET /deliveries | — | DeliveryPage | service_agent,delivery_operator | 依OpenAPI |
| receive_message_receipt | POST /webhooks/message-receipts | DeliveryReceiptRequest | CallbackReceipt | messaging_adapter | 依OpenAPI |
| reconcile_delivery | POST /deliveries/{delivery_id}/reconciliation | ReconciliationRequest | Delivery | delivery_operator | If-Match 必須 |
| redrive_delivery_event | POST /dead-letters/{dead_letter_id}/redrive | RedriveRequest | CallbackReceipt | delivery_supervisor | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| delivery.status_changed | 發出 | M09 | M08,M13 | customer_confidential | 1.0.0 |
| customer.identity_linked | ／接收 | M01 | M09 | customer_confidential | 1.0.0 |
| contract.terminated | ／接收 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| entitlement.changed | ／接收 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| report.signature_revoked | ／接收 | M07 | M08,M09 | research_confidential | 1.0.0 |
| publication.created | ／接收 | M08 | M09 | research_confidential | 1.0.0 |
| publication.withdrawn | ／接收 | M08 | M09,M13 | research_confidential | 1.0.0 |
| publication.corrected | ／接收 | M08 | M09,M13 | research_confidential | 1.0.0 |
| crm.suppression_changed | ／接收 | M10 | M09,M10 | customer_confidential | 1.0.0 |
| customer.consent_changed | ／接收 | M01 | M09,M10 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| LegendFlow介面邊界 | 網站保有導覽／品牌／會員UI，本平台供身份、申辦、feed、報告、客服之API | 合約／KYC互動由網站呼叫API，不重建第二個客戶網站 |
| 投遞監控表 | publication、客戶遮罩、channel、每階段時間、provider id、失敗／重試原因 | 讀取未知顯示「未提供」，不能顯示未讀或已讀的假結論 |
| 重驅與收件對帳 | 顯示上次attempt、provider query結果、expiry、現在權限與操作者原因 | 重驅前檢核重複風險與有效性，不提供一鍵盲發全部 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM只可在事前核定的模板中填非投資判斷欄位或整理失敗摘要；不能簽後重新摘要成不同交易指令。
- 必須受控：內容、收件人、通路、時效、去重、權限與狀態轉移全部由受控邏輯決定。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 供應商不支援idempotency/status lookup：無法保證exactly-once；unknown須受控人工對帳或採明示重複風險策略。
- receipt亂序：保存所有原始狀態時間並以狀態偏序合併，不從read倒退sent。
- 短網址或report token撤權後仍需origin authorizer再驗，不可只靠長期有效CDN快取。
- provider rate limit：有上限重試、jitter、deadletter，仍每次檢查expires_at。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- 外部LegendFlow member BFF contract
- 外部LINE/SMS/email adapter與receipt
- M08 immutable publication
- M02 entitlement authoritative evaluation
- M13 delivery related service cases

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M09-001 · LegendFlow feed只顯示本人權限內容

- 前提：會員A只有服務X
- 操作：查詢member feed含X/Y publication
- 預期：僅回X可見內容；Y不可由id猜到／直接下載
- API：`get_member_feed`
- 證據：`delivery.message`、audit/correlation與適用外部receipt。

### UAT-M09-002 · 報告下載授權需當前有效

- 前提：A曾有權限後被撤銷
- 操作：使用先前report link或請求access
- 預期：依核定存取政策拒絕新授權；短效token不可越權讀其他客戶資料
- API：`request_member_report_access`
- 證據：`delivery.message`、audit/correlation與適用外部receipt。

### UAT-M09-003 · 未驗證通路不能綁成active

- 前提：客戶只填別人電話／LINE id
- 操作：bind channel
- 預期：要求challenge/provider proof；未驗證不成active收件端
- API：`bind_member_channel`
- 證據：`delivery.channel_binding`、audit/correlation與適用外部receipt。

### UAT-M09-004 · 發送前再查權限

- 前提：recipient snapshot時active但現在revoked
- 操作：dispatch worker執行
- 預期：不送；記suppressed及當時entitlement decision id
- API：`list_deliveries`, `reconcile_delivery`
- 證據：`delivery.attempt`、audit/correlation與適用外部receipt。

### UAT-M09-005 · accepted不誤顯示已送達

- 前提：provider只回202 accepted
- 操作：投遞狀態畫面與API
- 預期：accepted_at存在但delivered_at/read_at缺；不偽造送達率
- API：`receive_message_receipt`, `list_deliveries`
- 證據：`delivery.receipt`、audit/correlation與適用外部receipt。

### UAT-M09-006 · 未知已讀維持unknown

- 前提：SMS provider無read receipt
- 操作：查狀態與CRM活動
- 預期：read_state=unknown；不把無read事件認定未讀或流失
- API：`receive_message_receipt`
- 證據：`delivery.receipt`、audit/correlation與適用外部receipt。

### UAT-M09-007 · timeout先查結果避免重複

- 前提：provider已收但本地timeout
- 操作：worker重啟並重試
- 預期：先查provider request id或同key安全重試；只一logical message
- API：`reconcile_delivery`
- 證據：`delivery.attempt`、audit/correlation與適用外部receipt。

### UAT-M09-008 · 亂序receipt不倒退

- 前提：read receipt先於delivered receipt到達
- 操作：處理兩events
- 預期：保存兩時間與接收時間；最終狀態不從read倒退
- API：`receive_message_receipt`
- 證據：`delivery.receipt`、audit/correlation與適用外部receipt。

### UAT-M09-009 · 過期DLQ不得重送舊建議

- 前提：訊息expiry已過
- 操作：操作人redrive
- 預期：回不可重發原因並稽核；不對provider送請求
- API：`redrive_delivery_event`
- 證據：`delivery.message`、audit/correlation與適用外部receipt。

### UAT-M09-010 · 撤回／更正保留原文

- 前提：原message已sent，新publication更正
- 操作：接收correction事件
- 預期：feed標示更正，新message引用原；原body/hash/attempt仍可查
- API：`get_member_feed`
- 證據：`delivery.message`、audit/correlation與適用外部receipt。

### UAT-M09-011 · 通路偏好與必要服務分類分開

- 前提：客戶關閉行銷SMS但保留契約通知
- 操作：處理行銷及服務訊息
- 預期：行銷被抑制；必要通知依已核定分類與偏好處理，禁止把行銷偽裝服務
- API：`set_communication_preference`
- 證據：`delivery.message`、audit/correlation與適用外部receipt。

### UAT-M09-012 · provider webhook驗證與重放防護

- 前提：未簽名或超出允許時間窗的receipt
- 操作：呼叫webhook
- 預期：401/422且不更新message；合法重送去重並回原receipt
- API：`receive_message_receipt`
- 證據：`delivery.receipt`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M09-legendflow_delivery.md)
- [GitHub issue #9](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/9)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
