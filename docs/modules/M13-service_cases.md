# M13 · 線上線下客服、申訴與跨部門案件：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

將網站、官方訊息、電子郵件、電話與現場案件匯成同一可追蹤服務歷程；快速分流與回覆，重要投資判斷、申訴、退款與AML事項交授權流程。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 客戶：提交／回覆本人案件與查看可公開狀態
- 客服：受理、補件、協調、使用核定知識與草稿
- 分析師：處理投顧內容問題，必要時回到正式報告／更正流程
- 財會／法遵：受理各自授權事項；不因被指派客服案就獲得全部客戶敏感資訊
- 客服主管：SLA、申訴覆核與品質抽查

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M13-001 | 同案跨通路持續追蹤 | 同case timeline有兩channel及各時間；不重設SLA | UAT-M13-001 |
| REQ-M13-002 | 外部訊息重送只建一互動 | 一interaction；案件數與待辦不重複 | UAT-M13-002 |
| REQ-M13-003 | 未驗證身份不得看到契約細節 | 可記unlinked case但不揭露合同／付款；先完成適當驗證 | UAT-M13-003 |
| REQ-M13-004 | AI草稿須核定發送且狀態真實 | 草稿無sent_at；送出綁精確hash與server產的approval_id，queued/accepted不當sent/delivered；重複receipt去重，unknown先查狀態 | UAT-M13-004 |
| REQ-M13-005 | 投資問題需分析師與報告依據 | 轉分析師；不得即席生成對客戶的新建議；正式建議回M07/M08 | UAT-M13-005 |
| REQ-M13-006 | 客服退款申請不能直接出金 | 建立M14 request與link；客服無approve/execute權限 | UAT-M13-006 |
| REQ-M13-007 | 重大申訴抑制不當促銷 | 發安全suppression給M10；仍可必要服務回覆，不發催升級名單 | UAT-M13-007 |
| REQ-M13-008 | 內部等待不任意停SLA | 按policy持續計時或合規pause；不可轉派即清零；逾期告警 | UAT-M13-008 |
| REQ-M13-009 | AML移交不複製案情 | 一般case只留安全link/status；AML內容不進普通timeline或LLM context | UAT-M13-009 |
| REQ-M13-010 | 結案必須有結果與通知 | 422缺必要資訊；填妥核定結果才resolved，保留通知delivery狀態 | UAT-M13-010 |
| REQ-M13-011 | 重開案件保留原歷史 | 同case新增reopen事件與時間，不刪原resolution；SLA重啟方式按policy | UAT-M13-011 |
| REQ-M13-012 | 客服owner不自動成業務獎金人 | maintenance與conversion owner不因case owner改變；若有真實sales另走M10核定 | UAT-M13-012 |

## 4. 主要使用案例

### UC-M13-A · 跨通路受理

以provider message id、customer identity及case ref去重／對案；電話與現場由staff記錄真實時間和摘要，附件／錄音依同意與留存政策；不可只憑同電話就把敏感案件合併。

### UC-M13-B · AI協作與部門分工

分類、摘要、檢索核定知識、提出回覆草稿和路由；低風險庶務可依核定模板自動回覆並記錄；投資判斷／抱怨／退款／AML需對應授權人或系統流程。

### UC-M13-C · 解決與留存

問題解決需附處理結果、通知證據與SLA；退費案只提出M14 request，不直接出金；報告錯誤回M07/M08更正；維護／轉換owner不因客服接案而轉移。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| case | new → triaged → assigned → in_progress → waiting_customer/waiting_internal → resolved → closed；closed → reopened | resolve需resolution與客戶通知證據；重開沿用或關聯原case，不刪歷史 |
| interaction | received/draft → approved → sent → delivery_confirmed/failed | LLM草稿不當已寄；通路原文與附件refs保留 |
| SLA | running → paused/resumed → breached/completed | pause原因與允許類型版本化；waiting_internal不可任意停鐘隱藏逾期 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| support.case_file | status | 狀態：new / triaged / assigned / waiting_customer / waiting_internal / resolved / closed / reopened | CHECK (status IN ('new','triaged','assigned','waiting_customer','waiting_internal','resolved','closed','reopened')) |
| support.response_draft | status | 狀態：queued / drafted / requires_human / blocked / failed | CHECK (status IN ('queued','drafted','requires_human','blocked','failed')) |
| support.response_approval | decision | approve/reject | CHECK (decision IN ('approve','reject')) |
| support.response_dispatch | status | 狀態：queued / accepted / sent / delivered / failed / unknown / cancelled / expired | CHECK (status IN ('queued','accepted','sent','delivered','failed','unknown','cancelled','expired')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| support.case_file | 本模組權威寫入 | 線上線下統一客服案件；客服 owner 不等於業務歸屬。 | case_number | internal | support |
| support.interaction | 本模組權威寫入 | 官網、訊息、郵件、電話、現場之原始接觸與人工/AI摘要來源。 | channel,external_message_id | internal | support |
| support.sla_event | 本模組權威寫入 | SLA 暫停、恢復、升級的 append-only 事件。 | tenant_id,id | internal | support |
| support.response_draft | 本模組權威寫入 | 客服回覆草稿與來源版本；草稿永遠不代表可直接對外發送。 | tenant_id,id | internal | support |
| support.response_approval | 本模組權威寫入 | 精確草稿的核准或駁回證據；low-risk template由受控policy service記錄。 | tenant_id,id | internal | support |
| support.response_dispatch | 本模組權威寫入 | 已核准客服回覆的邏輯發送，與人工記錄interaction分開。 | idempotency_key | internal | support |
| support.response_attempt | 本模組權威寫入 | 客服外送provider各次呼叫結果；未知狀態先查詢再重試。 | response_id,attempt_no | internal | support |
| support.response_receipt | 本模組權威寫入 | 客服發送回條不可變；accepted/sent/delivered/read 分開。 | provider,event_id | internal | support |
| crm.suppression | 唯讀／port，寫入由M10 | 限制招攬但不停止必要服務通知。 | tenant_id,id | internal | crm |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_service_case | POST /service-cases | ServiceCaseCreate | ServiceCase | service_agent | 依OpenAPI |
| list_service_cases | GET /service-cases | — | ServiceCasePage | service_agent,service_supervisor | 依OpenAPI |
| get_service_case | GET /service-cases/{case_id} | — | ServiceCase | service_agent | 依OpenAPI |
| add_case_interaction | POST /service-cases/{case_id}/interactions | CaseInteractionCreate | CaseInteraction | service_agent | If-Match 必須 |
| assign_service_case | POST /service-cases/{case_id}/assignment | AssignmentRequest | ServiceCase | service_supervisor | If-Match 必須 |
| escalate_service_case | POST /service-cases/{case_id}/escalations | CaseEscalationRequest | ServiceCase | service_agent | If-Match 必須 |
| draft_service_response | POST /service-cases/{case_id}/response-drafts | ResponseDraftRequest | ResponseDraft | service_agent | If-Match 必須 |
| resolve_service_case | POST /service-cases/{case_id}/resolution | CaseResolutionRequest | ServiceCase | service_agent | If-Match 必須 |
| reopen_service_case | POST /service-cases/{case_id}/reopen | ReasonRequest | ServiceCase | service_agent | If-Match 必須 |
| get_service_response_draft | GET /service-response-drafts/{draft_id} | — | ResponseDraft | service_agent | 依OpenAPI |
| send_service_response | POST /service-cases/{case_id}/responses | ServiceResponseRequest | ServiceResponse | service_agent,service_supervisor,service_response_service | If-Match 必須 |
| get_service_response | GET /service-responses/{response_id} | — | ServiceResponse | service_agent,service_supervisor | 依OpenAPI |
| receive_service_response_receipt | POST /webhooks/service-response-receipts | ServiceResponseReceipt | CallbackReceipt | service_messaging_adapter | 依OpenAPI |
| approve_service_response_draft | POST /service-response-drafts/{draft_id}/approval | ServiceResponseApprovalRequest | ServiceResponseApproval | service_agent,service_supervisor,service_response_service | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| support.case_opened | 發出／接收 | M13 | M10,M13 | customer_confidential | 1.0.0 |
| support.case_escalated | 發出／接收 | M13 | M13 | customer_confidential | 1.0.0 |
| support.case_resolved | 發出 | M13 | M10 | customer_confidential | 1.0.0 |
| support.response_status_changed | 發出／接收 | M13 | M13 | customer_confidential | 1.0.0 |
| customer.created | ／接收 | M01 | M10,M13 | customer_confidential | 1.0.0 |
| publication.withdrawn | ／接收 | M08 | M09,M13 | research_confidential | 1.0.0 |
| publication.corrected | ／接收 | M08 | M09,M13 | research_confidential | 1.0.0 |
| delivery.status_changed | ／接收 | M09 | M08,M13 | customer_confidential | 1.0.0 |
| ownership.assignment_changed | ／接收 | M11 | M10,M12,M13 | customer_confidential | 1.0.0 |
| finance.refund_confirmed | ／接收 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 客服工作台 | 統一案件列表，channel、客戶遮罩、分類、優先、SLA、owner、下一步 | 同一案件可有多通路timeline；明顯區分草稿、已送、送達未知 |
| 案件詳情 | 原文、互動、契約／付款／report引用、內部筆記與可客戶見回覆分區 | 內部AML移交只露安全狀態；防止把internal note貼成對外訊息 |
| 申訴與退款面板 | 申訴理由、證據、回覆期限、授權處理者、退款工作狀態 | 客服不可自行承諾未核定退款金額或投資收益；需待核准顯示清楚 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM可分類、摘要、檢索核定知識、草擬回覆與列下一步；經核定低風險模板可自動送，但需完整audit與退回人工條件。
- 必須受控：退款、投顧判斷、AML處理、承諾／權限變更與SLA規則由授權workflow決定；AI不能直接結案重要申訴。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 同provider message重送：只一interaction，回原receipt。
- 找不到正確客戶：unlinked case先受理，但敏感資料不顯示給未驗證者。
- AI無支持知識：說明需人工處理並轉案，不能編造公司政策。
- SLA責任轉派不重新歸零計時；明確保留原case open時間。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- LegendFlow/官方訊息/email/電話/現場 adapters
- M01 verified customer
- M02 contract entitlements
- M07/M08研究內容更正
- M10 marketing suppression
- M14 refund workflow
- M04受限移交link

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M13-001 · 同案跨通路持續追蹤

- 前提：客戶網站開案後電話補充並驗證身份
- 操作：客服連結interaction
- 預期：同case timeline有兩channel及各時間；不重設SLA
- API：`create_service_case`, `add_case_interaction`
- 證據：`support.interaction`、audit/correlation與適用外部receipt。

### UAT-M13-002 · 外部訊息重送只建一互動

- 前提：同provider message id重送
- 操作：處理intake
- 預期：一interaction；案件數與待辦不重複
- API：`add_case_interaction`
- 證據：`support.interaction`、audit/correlation與適用外部receipt。

### UAT-M13-003 · 未驗證身份不得看到契約細節

- 前提：電話聲稱某客戶但未驗證
- 操作：客服查／回覆
- 預期：可記unlinked case但不揭露合同／付款；先完成適當驗證
- API：`get_service_case`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

### UAT-M13-004 · AI草稿須核定發送且狀態真實

- 前提：LLM完成draft response但尚未核定
- 操作：讀草稿與case timeline；完成授權人覆核或核定低風險template後送出，再接收provider回執
- 預期：草稿無sent_at；送出綁精確hash與server產的approval_id，queued/accepted不當sent/delivered；重複receipt去重，unknown先查狀態
- API：`draft_service_response`, `get_service_response_draft`, `approve_service_response_draft`, `send_service_response`, `get_service_response`, `receive_service_response_receipt`
- 證據：`support.response_dispatch`、audit/correlation與適用外部receipt。

### UAT-M13-005 · 投資問題需分析師與報告依據

- 前提：客戶問新買賣建議超出已簽報告
- 操作：AI／客服回覆流程
- 預期：轉分析師；不得即席生成對客戶的新建議；正式建議回M07/M08
- API：`escalate_service_case`, `send_service_response`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

### UAT-M13-006 · 客服退款申請不能直接出金

- 前提：客戶提出退款且客服建案
- 操作：點提出退款
- 預期：建立M14 request與link；客服無approve/execute權限
- API：`escalate_service_case`, `request_refund`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

### UAT-M13-007 · 重大申訴抑制不當促銷

- 前提：case category complaint重大未解
- 操作：建立／升級案件
- 預期：發安全suppression給M10；仍可必要服務回覆，不發催升級名單
- API：`escalate_service_case`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

### UAT-M13-008 · 內部等待不任意停SLA

- 前提：case waiting_internal已到閾值
- 操作：SLA job評估
- 預期：按policy持續計時或合規pause；不可轉派即清零；逾期告警
- API：`assign_service_case`, `list_service_cases`
- 證據：`support.sla_event`、audit/correlation與適用外部receipt。

### UAT-M13-009 · AML移交不複製案情

- 前提：客服問題需AML處理
- 操作：escalate restricted
- 預期：一般case只留安全link/status；AML內容不進普通timeline或LLM context
- API：`escalate_service_case`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

### UAT-M13-010 · 結案必須有結果與通知

- 前提：處理者未填resolution/evidence
- 操作：resolve case
- 預期：422缺必要資訊；填妥核定結果才resolved，保留通知delivery狀態
- API：`resolve_service_case`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

### UAT-M13-011 · 重開案件保留原歷史

- 前提：closed case客戶再回報同問題
- 操作：reopen
- 預期：同case新增reopen事件與時間，不刪原resolution；SLA重啟方式按policy
- API：`reopen_service_case`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

### UAT-M13-012 · 客服owner不自動成業務獎金人

- 前提：客服解決問題，客戶自助續約
- 操作：結案並續約
- 預期：maintenance與conversion owner不因case owner改變；若有真實sales另走M10核定
- API：`resolve_service_case`
- 證據：`support.case_file`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M13-service_cases.md)
- [GitHub issue #13](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/13)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
