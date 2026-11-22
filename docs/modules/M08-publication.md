# M08 · 建議序列、簽章驅動發布與更正：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

有效簽章驗證後立即由系統把已核定報告／投顧建議發布給符合權限的客戶。維持進場、更新、出場的一組建議序列，並能由每則訊息找到當時相對應的已簽報告。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 分析師／覆核者：在簽前建立與審查recommendation和渠道內容
- 發布worker：只處理有效report.signature_verified及凍結manifest
- 法遵／授權處置者：在異常時撤回或建立更正關聯
- 內稽：追溯建議與受眾，不可改發布歷史

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M08-001 | 驗證簽章後無第二發布操作 | 自動建立publication/audience/delivery事件；人員不用另按發布 | UAT-M08-001 |
| REQ-M08-002 | 候選或未簽稿不可出現在feed | 無publication；訪客／客戶不可讀未簽正式內容 | UAT-M08-002 |
| REQ-M08-003 | 每一進出場訊息有對應報告 | 每版有supporting report_version/hash；exit判斷改變時須有相應新／補充已簽報告 | UAT-M08-003 |
| REQ-M08-004 | 一份有效報告可支持多則核定訊息 | 可共用report reference；各message有獨立version/hash/時間；不強制每則重新全文研究 | UAT-M08-004 |
| REQ-M08-005 | 簽後實質文案變動不可偷渡 | hash mismatch阻擋；B須新版本覆核／簽章 | UAT-M08-005 |
| REQ-M08-006 | 受眾快照排除無權限者 | 僅合格客戶入snapshot；保存policy與entitlement decision；其餘有reason summary | UAT-M08-006 |
| REQ-M08-007 | 快照後停權仍不得送出 | 即時重查拒絕；snapshot保留原事實，delivery記suppressed_revoked | UAT-M08-007 |
| REQ-M08-008 | 重複簽章事件僅發布一次 | 一publication且一組邏輯delivery；inbox顯示duplicate | UAT-M08-008 |
| REQ-M08-009 | 撤回不抹除已發布證據 | 停止新發送並追加withdrawal；已發送原文、受眾及時間保留 | UAT-M08-009 |
| REQ-M08-010 | 更正指向原發布與新簽版本 | 新publication引用原id與新report；受影響客戶可見更正標示，原版仍可audit | UAT-M08-010 |
| REQ-M08-011 | 延遲重送過期建議遭攔截 | expired/blocked，不發失效進出場建議；有重驅者及理由 | UAT-M08-011 |
| REQ-M08-012 | 來源trade和客戶建議時間分列 | 只在真實簽章發布時點開始建議；不宣稱客戶早盤已收到或成交 | UAT-M08-012 |

## 4. 主要使用案例

### UC-M08-A · 簽前建議關聯

每個實質建議建立entry/update/exit版本。尚未產生正式報告時，draft可先記source_candidate_id並保留空supporting_report_version_ids；M07 create_report_version再建立雙向關聯，freeze前必須補齊可支持判斷的report_version、有效期限、通路文案與客群條件，不形成建立順序循環。同一仍有效報告可支援多則已覆核訊息，重大改判須先新簽報告／補充報告。

### UC-M08-B · 簽後即時發布

consume report.signature_verified，重查簽章未撤銷、report/recommendation hash一致、未過期、來源與policy可用；seal audience snapshot、寫publication與publication.created outbox；無第二個人工發布步驟。

### UC-M08-C · 更正／撤回

必要時撤回未送部分，追加correction/withdrawal事件；已送內容不覆寫；更正含新正式版本與original publication reference，通知已受影響客群並保留當時資格證據。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| recommendation series | open → updated → closed/withdrawn | entry/update/exit是有順序的recommendation versions，與Pantheon實際trade不同 |
| publication | pending → validating → published/blocked/failed → withdrawn/superseded | published代表平台有效發布，不等同各通路delivered/read |
| audience snapshot | resolving → sealed | 記錄policy version、時間、合格customer集合與entitlement evidence；dispatch仍需即時重查 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| publication.publication | status | 狀態：queued / publishing / published / partially_failed / expired / corrected / withdrawn | CHECK (status IN ('queued','publishing','published','partially_failed','expired','corrected','withdrawn')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| publication.recommendation_series | 本模組權威寫入 | 帶進、追蹤、帶出的建議序列，與 Persona 交易及客戶交易分離。 | series_code | internal | report |
| publication.recommendation_version | 本模組權威寫入 | 正式訊息與同時有效報告連結；重大判斷改變需新簽證。 | series_id,recommendation_version_no | internal | report |
| publication.publication | 本模組權威寫入 | 驗簽完成即建立發布，不加第二個 Publish 人工作業。 | recommendation_version_id | internal | report |
| publication.audience_snapshot | 本模組權威寫入 | 發布當下受眾規則及快照；不取代送出時再次查權限。 | publication_id | internal | report |
| publication.recipient | 本模組權威寫入 | 逐收件人權益證據；不存姓名或手機。 | snapshot_id,customer_id,entitlement_id | internal | report |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_recommendation_series | POST /recommendation-series | RecommendationSeriesCreate | RecommendationSeries | analyst | 依OpenAPI |
| create_recommendation_version | POST /recommendation-series/{series_id}/versions | RecommendationVersionCreate | RecommendationVersion | analyst | If-Match 必須 |
| list_publications | GET /publications | — | PublicationPage | analyst,service_agent,compliance_reviewer | 依OpenAPI |
| get_publication | GET /publications/{publication_id} | — | Publication | analyst,service_agent,compliance_reviewer | 依OpenAPI |
| withdraw_publication | POST /publications/{publication_id}/withdrawal | PublicationWithdrawal | Publication | analyst_reviewer,compliance_supervisor | If-Match 必須 |
| link_publication_correction | POST /publications/{publication_id}/correction-links | PublicationCorrection | Publication | analyst_reviewer | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| publication.created | 發出 | M08 | M09 | research_confidential | 1.0.0 |
| publication.withdrawn | 發出 | M08 | M09,M13 | research_confidential | 1.0.0 |
| publication.corrected | 發出 | M08 | M09,M13 | research_confidential | 1.0.0 |
| entitlement.changed | ／接收 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| report.signature_verified | ／接收 | M07 | M08 | research_confidential | 1.0.0 |
| report.signature_revoked | ／接收 | M07 | M08,M09 | research_confidential | 1.0.0 |
| delivery.status_changed | ／接收 | M09 | M08,M13 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 建議序列檢視 | 標的、entry/update/exit時間、研究依據、狀態、簽章、publication連結 | 不把Persona買賣當客戶成交；不顯示投資績效承諾 |
| 發布監控 | 由簽章→發布→各channel結果，標出延遲與失敗、受眾數與政策版 | 僅可安全重試／撤回／查原因；不存在簽後必按Publish的工作 |
| 更正工作頁 | 原內容與更正內容並列，說明影響範圍、報告依據和通知狀態 | 已送版本永久可稽核；更正仍受研究覆核／簽章規則 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM可在簽前草擬訊息摘要或提出來源關聯候選；簽後僅允許受控格式呈現，不引入新投資判斷。
- 必須受控：發布、受眾解析、hash檢核、有效期、去重、更正與權限判斷由確定性worker處理。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- report.signature_verified重複：以signature/report/recommendation版本unique key防重建publication。
- 受眾解析timeout：保持可追蹤pending/blocked，不能改成全體發送。
- 過期訊息在重試時丟到expired/blocked，不發昨日進場建議當即時新訊息。
- 投遞中撤銷：新dispatch拒絕；已發送保留並另送已核定更正，不能回寫為從未寄出。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M07 signature/report immutable manifest
- M02 authoritative entitlements
- M09 delivery / LegendFlow member feed
- M14 publication evidence export

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M08-001 · 驗證簽章後無第二發布操作

- 前提：有效signature event及全部核定manifest
- 操作：處理事件
- 預期：自動建立publication/audience/delivery事件；人員不用另按發布
- API：`list_publications`, `get_publication`
- 證據：`publication.publication`、audit/correlation與適用外部receipt。

### UAT-M08-002 · 候選或未簽稿不可出現在feed

- 前提：只有Pantheon candidate或draft report
- 操作：要求發布／列member feed
- 預期：無publication；訪客／客戶不可讀未簽正式內容
- API：`get_member_feed`
- 證據：`publication.publication`、audit/correlation與適用外部receipt。

### UAT-M08-003 · 每一進出場訊息有對應報告

- 前提：同series有entry與exit
- 操作：建立recommendation versions
- 預期：每版有supporting report_version/hash；exit判斷改變時須有相應新／補充已簽報告
- API：`create_recommendation_series`, `create_recommendation_version`
- 證據：`publication.recommendation_version`、audit/correlation與適用外部receipt。

### UAT-M08-004 · 一份有效報告可支持多則核定訊息

- 前提：報告仍有效且內容未新增判斷
- 操作：建立兩則一致且簽前核定的channel messages
- 預期：可共用report reference；各message有獨立version/hash/時間；不強制每則重新全文研究
- API：`create_recommendation_version`
- 證據：`publication.recommendation_version`、audit/correlation與適用外部receipt。

### UAT-M08-005 · 簽後實質文案變動不可偷渡

- 前提：signed manifest含文字A
- 操作：worker收到替換文字B
- 預期：hash mismatch阻擋；B須新版本覆核／簽章
- API：`get_publication`
- 證據：`publication.publication`、audit/correlation與適用外部receipt。

### UAT-M08-006 · 受眾快照排除無權限者

- 前提：active、expired、pending三組客戶
- 操作：resolve audience
- 預期：僅合格客戶入snapshot；保存policy與entitlement decision；其餘有reason summary
- API：`get_publication`
- 證據：`publication.audience_snapshot`、audit/correlation與適用外部receipt。

### UAT-M08-007 · 快照後停權仍不得送出

- 前提：客戶被納入snapshot後entitlement revoked
- 操作：M09準備dispatch
- 預期：即時重查拒絕；snapshot保留原事實，delivery記suppressed_revoked
- API：`get_publication`, `list_deliveries`
- 證據：`publication.recipient`、audit/correlation與適用外部receipt。

### UAT-M08-008 · 重複簽章事件僅發布一次

- 前提：同report.signature_verified重送三次
- 操作：consume event
- 預期：一publication且一組邏輯delivery；inbox顯示duplicate
- API：`get_publication`
- 證據：`publication.publication`、audit/correlation與適用外部receipt。

### UAT-M08-009 · 撤回不抹除已發布證據

- 前提：publication已部分發送
- 操作：授權人withdraw
- 預期：停止新發送並追加withdrawal；已發送原文、受眾及時間保留
- API：`withdraw_publication`
- 證據：`publication.publication`、audit/correlation與適用外部receipt。

### UAT-M08-010 · 更正指向原發布與新簽版本

- 前提：原訊息需重大更正且新報告已簽
- 操作：link correction
- 預期：新publication引用原id與新report；受影響客戶可見更正標示，原版仍可audit
- API：`link_publication_correction`
- 證據：`publication.publication`、audit/correlation與適用外部receipt。

### UAT-M08-011 · 延遲重送過期建議遭攔截

- 前提：expires_at已過但DLQ被重驅
- 操作：worker處理dispatch
- 預期：expired/blocked，不發失效進出場建議；有重驅者及理由
- API：`redrive_delivery_event`
- 證據：`publication.recommendation_version`、audit/correlation與適用外部receipt。

### UAT-M08-012 · 來源trade和客戶建議時間分列

- 前提：Persona早盤trade，報告午後簽章
- 操作：查看recommendation timeline
- 預期：只在真實簽章發布時點開始建議；不宣稱客戶早盤已收到或成交
- API：`create_recommendation_series`, `create_recommendation_version`
- 證據：`publication.recommendation_series`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M08-publication.md)
- [GitHub issue #8](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/8)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
