# M06 · 研究覆核工作台與 Agora／Servant 協作：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

讓分析師在候選佇列閱讀證據、要求 Servant 修改或補證，接受可追溯的新版本後再進正式報告與簽章。Servant 是外部研究協作入口，不能代替分析師簽章。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 分析師：接案、對話、要求修正、比較版本、接受修訂提交M07
- 研究主管：指派與處理逾時、利益衝突及替代審核者
- Agora/Servant adapter：僅可回傳所屬conversation/request成果
- LLM service：沒有report-sign、publish、entitlement或payment capability

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M06-001 | 合法候選自動進分析師佇列 | 建立一case並保留來源版本；分析師可接案而不需重新發起研究 | UAT-M06-001 |
| REQ-M06-002 | 修正請求綁定確切版本 | 記錄v3/hash、scope、request id與接受條件；外部回呼可對應原請求 | UAT-M06-002 |
| REQ-M06-003 | 回傳修訂只建立新版本 | 保留v3、建立v4 revision result與差異；舊review不視為批准v4 | UAT-M06-003 |
| REQ-M06-004 | 舊版本操作遇衝突重載 | 412；UI顯示新的candidate version，不覆蓋更新 | UAT-M06-004 |
| REQ-M06-005 | Servant不能代簽或直接發布 | 403，無signature或publication事件 | UAT-M06-005 |
| REQ-M06-006 | 對話scope不得跨客戶或tenant | 拒絕scope violation；A畫面不顯示B內容 | UAT-M06-006 |
| REQ-M06-007 | 外部timeout不重開多任務 | 以原request id查狀態或同key重送；只有一外部revision job | UAT-M06-007 |
| REQ-M06-008 | 新來源到達時標示review過時 | case標stale並撤銷基於v3的ready標記；不自動把評論當v4覆核 | UAT-M06-008 |
| REQ-M06-009 | 修訂錯誤可重送並保留證據 | 新attempt連原request且原失敗保留；不修改舊artifact | UAT-M06-009 |
| REQ-M06-010 | 來源故障不產生虛假空研究 | 顯示degraded及最後成功狀態；不能顯示無修訂或已完成 | UAT-M06-010 |
| REQ-M06-011 | 關鍵數字變動需可見差異 | 重大差異列出且待人工接受；不默默替換預覽內容 | UAT-M06-011 |
| REQ-M06-012 | 研究對話不夾帶KYC與AML | 只傳研究scope與服務語氣等最小資料；任何AML受限內容被阻擋 | UAT-M06-012 |

## 4. 主要使用案例

### UC-M06-A · 接案與初審

候選ready自動形成review case；依專長／資格／負載排序與人工指派；分析師看原件、來源時點、風險與可用證據，作接受初稿、要求修正或退回。

### UC-M06-B · Servant修正循環

建立有scope的conversation與revision request，明確指出需修改的結論／風險／計算／證據；以expected source version送外部；回傳artifact先驗證再顯示diff，分析師選接受或再修改。

### UC-M06-C · 版本一致後交付

專業審查須指向確切候選／修訂；若新來源在review期間到達，顯示過時並要求重評；完成後把內容與證據交M07建立正式報告版本，不能複製對話成已簽報告。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| review case | queued → assigned → reviewing → revision_pending → revised → ready_for_report/rejected；可stale | case.version與candidate_version同時檢查；resolved不代表簽章完成 |
| revision request | draft → submitted → acknowledged → running → completed/failed/cancelled | acknowledged不等於成果可用；timeout先查狀態，不重複建立昂貴任務 |
| conversation | created → active → closed | tenant/case/artifact scope固定；新上下文不得帶入客戶KYC/AML或其他tenant內容 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| review.case_file | status | 狀態：queued / assigned / reviewing / changes_requested / ready_to_sign / rejected / closed | CHECK (status IN ('queued','assigned','reviewing','changes_requested','ready_to_sign','rejected','closed')) |
| review.revision_request | status | 狀態：requested / accepted / running / completed / failed / cancelled / stale | CHECK (status IN ('requested','accepted','running','completed','failed','cancelled','stale')) |
| research.candidate | status | 狀態：received / validating / quarantined / ready / rejected / superseded | CHECK (status IN ('received','validating','quarantined','ready','rejected','superseded')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| review.case_file | 本模組權威寫入 | 分析師覆核工作項；非交易治理批准。 | candidate_id | internal | research |
| review.conversation | 本模組權威寫入 | Agora Servant 對話參照；不複製所有 Pantheon 知識。 | provider,external_conversation_id | internal | research |
| review.revision_request | 本模組權威寫入 | 分析師提出修訂並綁精確輸入版本。 | tenant_id,id | internal | research |
| review.revision_result | 本模組權威寫入 | 修訂產物不可變，新稿不覆寫已審稿。 | request_id,revision_no | internal | research |
| research.candidate | 唯讀／port，寫入由M05 | Pantheon Persona 自主研究或人員研究候選輸入；非正式對客報告。 | producer,external_candidate_id,external_version | internal | research |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| list_review_cases | GET /review-cases | — | ReviewCasePage | analyst,analyst_reviewer | 依OpenAPI |
| get_review_case | GET /review-cases/{review_case_id} | — | ReviewCase | analyst,analyst_reviewer | 依OpenAPI |
| assign_review_case | POST /review-cases/{review_case_id}/assignment | AssignmentRequest | ReviewCase | research_supervisor | If-Match 必須 |
| open_servant_conversation | POST /review-cases/{review_case_id}/conversations | ConversationRequest | Conversation | analyst | If-Match 必須 |
| request_servant_revision | POST /review-cases/{review_case_id}/revision-requests | RevisionRequest | Revision | analyst | If-Match 必須 |
| receive_servant_revision | POST /webhooks/servant-revisions | RevisionCallback | CallbackReceipt | servant_adapter | 依OpenAPI |
| get_revision_request | GET /revision-requests/{revision_id} | — | Revision | analyst,analyst_reviewer | 依OpenAPI |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| review.revision_requested | 發出／接收 | M06 | M06 | research_confidential | 1.0.0 |
| review.revision_received | 發出 | M06 | M07 | research_confidential | 1.0.0 |
| research.candidate_validated | ／接收 | M05 | M06 | research_confidential | 1.0.0 |
| research.candidate_superseded | ／接收 | M05 | M06,M07 | research_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 分析師主工作台 | 左候選／任務、中報告與證據、右Servant對話／版本差異 | 署名與發布狀態清晰；畫面同時顯示draft/waiting/revised/stale/error，禁用過時操作 |
| 修正請求編輯 | 範圍、需補證據、接受標準、對應report section、來源版本 | 可保存草稿與取消本地尚未送出請求；已送出的取消須外部確認，不能只把UI刪掉 |
| 版本比較 | 逐段變更、數值／標的／風險重大變更、證據增刪、來源時間 | 接受修訂需人工動作；修訂完成不自動沿用舊review／signature |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM 可草擬修訂、補證提問、摘要差異與待覆核項，所有結果標為draft並附來源。
- 必須受控：案例指派、scope、版本鎖、回呼驗證及可用操作由伺服器控制；批准／簽章不由AI執行。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 外部對話啟動timeout：查request status並以相同idempotency key重試，避免重開多個thread。
- revision callback 指向別的case/tenant：拒絕並security audit，不讓前端自行選case。
- 對話含prompt injection要求越權：資料當不可信研究內容，allowlisted tools無簽章或付款能力。
- 外部任務cancel未確認：UI顯示cancel_requested，不宣稱已終止。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M05 candidate immutable reference
- 外部Agora/Servant conversation/revision adapter
- M07 formal report version intake
- M14 evidence chain/audit

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M06-001 · 合法候選自動進分析師佇列

- 前提：M05候選ready
- 操作：處理candidate事件
- 預期：建立一case並保留來源版本；分析師可接案而不需重新發起研究
- API：`list_review_cases`, `assign_review_case`
- 證據：`review.case_file`、audit/correlation與適用外部receipt。

### UAT-M06-002 · 修正請求綁定確切版本

- 前提：case目前candidate v3
- 操作：送出Servant request
- 預期：記錄v3/hash、scope、request id與接受條件；外部回呼可對應原請求
- API：`open_servant_conversation`, `request_servant_revision`
- 證據：`review.revision_request`、audit/correlation與適用外部receipt。

### UAT-M06-003 · 回傳修訂只建立新版本

- 前提：Servant回v4 completed
- 操作：驗證callback
- 預期：保留v3、建立v4 revision result與差異；舊review不視為批准v4
- API：`receive_servant_revision`
- 證據：`review.revision_result`、audit/correlation與適用外部receipt。

### UAT-M06-004 · 舊版本操作遇衝突重載

- 前提：分析師讀case version5，外部v4剛到
- 操作：送If-Match5指派／修訂
- 預期：412；UI顯示新的candidate version，不覆蓋更新
- API：`assign_review_case`
- 證據：`review.case_file`、audit/correlation與適用外部receipt。

### UAT-M06-005 · Servant不能代簽或直接發布

- 前提：Servant principal持有revision scope
- 操作：請求report sign/publish
- 預期：403，無signature或publication事件
- API：`request_analyst_signature`
- 證據：`review.conversation`、audit/correlation與適用外部receipt。

### UAT-M06-006 · 對話scope不得跨客戶或tenant

- 前提：tenant A review conversation
- 操作：回呼包含tenant B artifact
- 預期：拒絕scope violation；A畫面不顯示B內容
- API：`receive_servant_revision`
- 證據：`review.revision_result`、audit/correlation與適用外部receipt。

### UAT-M06-007 · 外部timeout不重開多任務

- 前提：request已被Agora接受但本地timeout
- 操作：worker恢復／重試
- 預期：以原request id查狀態或同key重送；只有一外部revision job
- API：`request_servant_revision`, `get_revision_request`
- 證據：`review.revision_request`、audit/correlation與適用外部receipt。

### UAT-M06-008 · 新來源到達時標示review過時

- 前提：v3正在覆核
- 操作：candidate v4 supersedes事件到達
- 預期：case標stale並撤銷基於v3的ready標記；不自動把評論當v4覆核
- API：`get_review_case`
- 證據：`review.case_file`、audit/correlation與適用外部receipt。

### UAT-M06-009 · 修訂錯誤可重送並保留證據

- 前提：Servant回failed且有錯誤碼
- 操作：分析師修正請求重送
- 預期：新attempt連原request且原失敗保留；不修改舊artifact
- API：`request_servant_revision`
- 證據：`review.revision_request`、audit/correlation與適用外部receipt。

### UAT-M06-010 · 來源故障不產生虛假空研究

- 前提：Agora查詢503
- 操作：開啟case
- 預期：顯示degraded及最後成功狀態；不能顯示無修訂或已完成
- API：`get_review_case`
- 證據：`review.case_file`、audit/correlation與適用外部receipt。

### UAT-M06-011 · 關鍵數字變動需可見差異

- 前提：v3/v4目標、風險或持倉結論不同
- 操作：分析師查看revision result
- 預期：重大差異列出且待人工接受；不默默替換預覽內容
- API：`get_review_case`, `receive_servant_revision`
- 證據：`review.revision_result`、audit/correlation與適用外部receipt。

### UAT-M06-012 · 研究對話不夾帶KYC與AML

- 前提：revision要求客戶化措辭但無必要PII
- 操作：組裝外部prompt/context
- 預期：只傳研究scope與服務語氣等最小資料；任何AML受限內容被阻擋
- API：`open_servant_conversation`
- 證據：`review.conversation`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M06-agora_review.md)
- [GitHub issue #6](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/6)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
