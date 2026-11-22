# M07 · 正式研究報告、覆核與分析師簽章：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

把外部研究轉成龍好正式投顧報告版本，完整保存覆核與簽章證據。簽章精確綁定報告、建議、訊息版本及發布範圍；有效簽章確認後自動觸發M08發布。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 報告製作者／研究助理：編輯未凍結稿與來源，不能偽造核定
- 專業覆核者：覆核內容與依據；利益衝突／資格納入規則
- 法遵覆核者：依已批准的內容風險政策完成必要覆核
- 具有效資格之分析師簽署者：經MFA、明確意圖簽精確封包
- 簽章adapter：驗證provider憑據與精確檔案，不得改內容

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M07-001 | 缺必要覆核不能送簽 | 422 missing_review；無有效signature或publication事件 | UAT-M07-001 |
| REQ-M07-002 | 凍結包包含報告與訊息範圍 | 生成manifest綁report、recommendation、channeltexts、audience policy與expiry hash | UAT-M07-002 |
| REQ-M07-003 | 無有效資格者不能簽署 | 403/qualification_invalid；沒有provider有效request | UAT-M07-003 |
| REQ-M07-004 | 簽章回呼只在完整驗證後觸發事件 | signature verified與唯一report.signature_verified outbox在同transaction；之後M08自動發布 | UAT-M07-004 |
| REQ-M07-005 | 簽後修改建立新版本 | v1正文與frozen manifest不可覆寫；產生v2 draft並重新覆核／簽署，v1證據保留，狀態只依合法事件演進 | UAT-M07-005 |
| REQ-M07-006 | 內容與簽章檔hash分開 | content_hash=A來源內容，signed_file_hash=B；能驗證封裝關係，不要求A等於B | UAT-M07-006 |
| REQ-M07-007 | 資料過時阻擋簽章 | 按policy拒絕或中止request；不能沿用過期review自動發布 | UAT-M07-007 |
| REQ-M07-008 | provider重送不重複發布 | 一signature、一verified event；後次回相同receipt | UAT-M07-008 |
| REQ-M07-009 | 簽章payload替換遭拒 | invalid且告警；原frozen package保留；不發publication | UAT-M07-009 |
| REQ-M07-010 | 撤銷保留歷史並通知發布服務 | append revocation、發撤銷事件；不刪signature；後續投遞停止並走更正 | UAT-M07-010 |
| REQ-M07-011 | 簽署者知悉簽後自動發布 | 畫面顯示自動發布與範圍；不在簽後另加人工Publish gate | UAT-M07-011 |
| REQ-M07-012 | 多次對同版覆核保留完整歷史 | 舊review仍指v1；v2有新review；每位reviewer與時點可追溯 | UAT-M07-012 |

## 4. 主要使用案例

### UC-M07-A · 組裝正式版本

以M05/M06來源建立report，記錄判斷、依據、風險、利益衝突揭露及有效期限；對應M08建議與各通路內容；輸出固定canonical bytes及內容hash。

### UC-M07-B · 完成覆核與凍結

專業與依政策必要法遵覆核均完成；覆核對象為報告、建議、渠道文字及audience policy完整manifest；以CAS凍結，驗證沒有後來來源使review過時。

### UC-M07-C · 簽署與自動交付

授權分析師明確確認frozen package並送簽；provider回呼驗證簽者、資格、檔案與時間戳；同一資料庫交易記signature verified及outbox事件；M08自動發布。撤銷或後續更正只追加新紀錄。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| report version | draft → under_review → reviews_complete → frozen → signature_pending → signed（signature已驗證）；另rejected/stale/superseded | frozen後內容不可改；重大修改建立新report_version，既有簽章不搬用 |
| signature | requested → received → verified/invalid/revoked | received不等同verified；content_hash、package_hash、signed_file_hash分列 |
| review | pending → approved/rejected/changes_requested；可invalidated | 每一decision指明exact version/hash與policy version；新內容使相關review失效 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| report.report_version | status | 狀態：draft / in_review / changes_requested / ready_to_sign / signature_pending / signed / superseded / withdrawn | CHECK (status IN ('draft','in_review','changes_requested','ready_to_sign','signature_pending','signed','superseded','withdrawn')) |
| report.review_decision | decision | approved/changes_requested/rejected | CHECK (decision IN ('approved','changes_requested','rejected')) |
| report.signature_request | status | 狀態：pending / submitted / verified / failed / expired | CHECK (status IN ('pending','submitted','verified','failed','expired')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| report.report | 本模組權威寫入 | 正式投顧研究報告的穩定識別。 | report_number | internal | report |
| report.report_version | 本模組權威寫入 | 不可變報告正文版本及簽前核定 bundle；狀態投影可前進。 | report_id,report_version_no | internal | report |
| report.review_decision | 本模組權威寫入 | 逐版本覆核，綁相同內容與 bundle；修訂後不能沿用。 | tenant_id,id | internal | report |
| report.signature_request | 本模組權威寫入 | 簽章 provider 呼叫狀態；逾時需查詢，不可盲目重簽。 | provider,provider_transaction_id | internal | report |
| report.signature | 本模組權威寫入 | 已驗證分析師簽章證據；永不改內容。撤銷用 revocation。 | request_id；report_version_id | internal | report |
| report.revocation | 本模組權威寫入 | 撤銷或重驗失敗保留原簽章證據。 | tenant_id,id | internal | report |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_report | POST /reports | ReportCreate | AdvisoryReport | analyst | 依OpenAPI |
| create_report_version | POST /reports/{report_id}/versions | ReportVersionCreate | ReportVersion | analyst | If-Match 必須 |
| get_report_version | GET /report-versions/{report_version_id} | — | ReportVersion | analyst,analyst_reviewer,compliance_reviewer | 依OpenAPI |
| record_report_review | POST /report-versions/{report_version_id}/reviews | ReportReviewRequest | ReviewDecision | analyst_reviewer,compliance_reviewer | If-Match 必須 |
| freeze_report_version | POST /report-versions/{report_version_id}/freeze | FreezeReportRequest | ReportVersion | analyst | If-Match 必須 |
| request_analyst_signature | POST /report-versions/{report_version_id}/signature | AnalystSignatureRequest | SignatureSession | analyst_signer | If-Match 必須 |
| receive_analyst_signature | POST /webhooks/analyst-signatures | AnalystSignatureCallback | CallbackReceipt | signature_adapter | 依OpenAPI |
| revoke_signature | POST /signatures/{signature_id}/revocation | SignatureRevocationRequest | SignatureRevocation | compliance_supervisor | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| report.version_frozen | 發出／接收 | M07 | M07 | research_confidential | 1.0.0 |
| report.signature_verified | 發出 | M07 | M08 | research_confidential | 1.0.0 |
| report.signature_revoked | 發出 | M07 | M08,M09 | research_confidential | 1.0.0 |
| review.revision_received | ／接收 | M06 | M07 | research_confidential | 1.0.0 |
| research.candidate_superseded | ／接收 | M05 | M06,M07 | research_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 正式報告工作台 | 版本、來源鏈、覆核需求、待解決評論、報告全文、建議及通路預覽 | 不提供在已簽原件上直接編輯；可建立新版本／補充報告 |
| 簽署確認頁 | 顯示將簽的精確版本／內容hash、簽署者、有效資格、所有渠道文案與audience rule | 按鈕文案明示「簽署後自動發布」；缺任一必要覆核時禁用並說明 |
| 簽章證據頁 | content hash、signed-file hash、package hash、provider ref、verification與撤銷歷史 | 簽章失敗可查原因與重試；收到簽章但未驗證不得顯示已發布 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM可草擬報告與文案、檢查引用、標示數值不一致；簽後不得讓LLM生成新的投資判斷或更改已核定訊息。
- 必須受控：hash、版本鎖、覆核門檻、簽署者資格、憑證／時間戳驗證與事件交易由確定性服務執行。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- provider簽章成功但callback遺失：以request id查證，不要求分析師盲目重簽。
- content_hash不是signed_file_hash；簽章容器加入憑證後bytes改變應另存hash，不能誤判原文被改。
- 簽署者資格在request與verification時重驗；實際生效時間與撤銷狀態需保存。
- 影像章與密碼點按是否符合公司核定簽署控制須由實際provider評估；文件不把某CA等級當已驗證法律結論。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M05/M06 source lineage
- M08 recommendation/channel/audience manifest
- 外部簽章provider待驗證adapter
- M14不可變物件／audit evidence

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M07-001 · 缺必要覆核不能送簽

- 前提：professional approved但必需compliance pending
- 操作：送freeze/sign
- 預期：422 missing_review；無有效signature或publication事件
- API：`record_report_review`, `freeze_report_version`, `request_analyst_signature`
- 證據：`report.review_decision`、audit/correlation與適用外部receipt。

### UAT-M07-002 · 凍結包包含報告與訊息範圍

- 前提：所有reviews針對同版完成
- 操作：freeze version
- 預期：生成manifest綁report、recommendation、channeltexts、audience policy與expiry hash
- API：`create_report`, `create_report_version`, `freeze_report_version`
- 證據：`report.report_version`、audit/correlation與適用外部receipt。

### UAT-M07-003 · 無有效資格者不能簽署

- 前提：staff登入但credential過期或scope不符
- 操作：request signature
- 預期：403/qualification_invalid；沒有provider有效request
- API：`request_analyst_signature`
- 證據：`report.signature_request`、audit/correlation與適用外部receipt。

### UAT-M07-004 · 簽章回呼只在完整驗證後觸發事件

- 前提：provider收到signed bytes且hash/actor有效
- 操作：receive callback
- 預期：signature verified與唯一report.signature_verified outbox在同transaction；之後M08自動發布
- API：`receive_analyst_signature`
- 證據：`report.signature`、audit/correlation與適用外部receipt。

### UAT-M07-005 · 簽後修改建立新版本

- 前提：v1 signed（signature已驗證）
- 操作：修改結論或通路實質文案
- 預期：v1正文與frozen manifest不可覆寫；產生v2 draft並重新覆核／簽署，v1證據保留，狀態只依合法事件演進
- API：`create_report_version`, `get_report_version`
- 證據：`report.report_version`、audit/correlation與適用外部receipt。

### UAT-M07-006 · 內容與簽章檔hash分開

- 前提：原文PDF A經provider產出帶簽章PDF B
- 操作：驗證簽章
- 預期：content_hash=A來源內容，signed_file_hash=B；能驗證封裝關係，不要求A等於B
- API：`receive_analyst_signature`
- 證據：`report.signature`、audit/correlation與適用外部receipt。

### UAT-M07-007 · 資料過時阻擋簽章

- 前提：凍結前後收到superseding來源使review stale
- 操作：送簽／驗證
- 預期：按policy拒絕或中止request；不能沿用過期review自動發布
- API：`request_analyst_signature`
- 證據：`report.report_version`、audit/correlation與適用外部receipt。

### UAT-M07-008 · provider重送不重複發布

- 前提：同request/reference的valid callback兩次
- 操作：處理兩回呼
- 預期：一signature、一verified event；後次回相同receipt
- API：`receive_analyst_signature`
- 證據：`report.signature`、audit/correlation與適用外部receipt。

### UAT-M07-009 · 簽章payload替換遭拒

- 前提：回呼report id正確但file bytes/hash不同
- 操作：驗證callback
- 預期：invalid且告警；原frozen package保留；不發publication
- API：`receive_analyst_signature`
- 證據：`report.signature`、audit/correlation與適用外部receipt。

### UAT-M07-010 · 撤銷保留歷史並通知發布服務

- 前提：已發布報告簽章需撤銷
- 操作：授權人附理由revoke
- 預期：append revocation、發撤銷事件；不刪signature；後續投遞停止並走更正
- API：`revoke_signature`
- 證據：`report.revocation`、audit/correlation與適用外部receipt。

### UAT-M07-011 · 簽署者知悉簽後自動發布

- 前提：合法frozen報告及所有文案預覽
- 操作：在簽署確認頁確認
- 預期：畫面顯示自動發布與範圍；不在簽後另加人工Publish gate
- API：`request_analyst_signature`
- 證據：`report.signature_request`、audit/correlation與適用外部receipt。

### UAT-M07-012 · 多次對同版覆核保留完整歷史

- 前提：review changes_requested後修正新v2
- 操作：重新record review
- 預期：舊review仍指v1；v2有新review；每位reviewer與時點可追溯
- API：`record_report_review`
- 證據：`report.review_decision`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M07-report_signature.md)
- [GitHub issue #7](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/7)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
