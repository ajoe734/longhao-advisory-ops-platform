# M14 · 帳務、對帳、退款與內控證據：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

連接契約、金流、結算、預收、收入、退款與會計；保留可重演的計獎基礎及稽核證據。每筆金流有對帳狀態，製作、核准與執行分權，帳務不得靠LLM猜測。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 財會製作者：帳單、對帳差異、認列／退款建議、會計匯出
- 財會核准者：覆核退款、調整、期間結帳與重大例外
- 出款服務：只執行已核定且未重複的退款／付款
- 內稽：唯讀證據與控制測試；不經辦帳務核定
- 平台維運：管理基礎設施，不因admin就能看PII或出款

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M14-001 | 付款回呼重送只入帳一次 | 一payment transition、一財務事件；不重複增加預收或收入 | UAT-M14-001 |
| REQ-M14-002 | 預收款與已履約收入分開 | payment captured且prepayment liability增加，revenue=0；無計獎eligible收入 | UAT-M14-002 |
| REQ-M14-003 | 結算費用與收入不同欄 | gross/fee/net分列且相符；不能把收入直接當970或refund30 | UAT-M14-003 |
| REQ-M14-004 | 稅額使用核定配置不猜除數 | 進配置例外，不能默認除1.05或1.02；有核定mapping後可重算 | UAT-M14-004 |
| REQ-M14-005 | 退款製作核准執行分權 | SoD拒絕；合法checker核准後受限service才可執行 | UAT-M14-005 |
| REQ-M14-006 | 退款timeout先查provider | 先查狀態或同key；只有一筆對外退款，保留attempt receipts | UAT-M14-006 |
| REQ-M14-007 | 退款拆分預收與已認列部分 | unearned refund與earned revenue reversal分列；只有已計獎基礎部分通知M12反向 | UAT-M14-007 |
| REQ-M14-008 | 會計分錄必須平衡 | 拒絕unbalanced，無會計成功標記；修正形成可追溯版本或反向分錄 | UAT-M14-008 |
| REQ-M14-009 | 會計匯出重送有批次收據 | 使用同external id查/重試；不重複記帳；ack與rejected distinct | UAT-M14-009 |
| REQ-M14-010 | 完整稽核鏈可由訊息回查 | manifest含各immutable refs/hash與實際時點；受限資料依role遮罩且access audit | UAT-M14-010 |
| REQ-M14-011 | legal hold阻止到期刪除且解除受控 | 有效hold時skip且記原因；未授權解除被拒；合法解除追加紀錄且保留原hold與audit，不直接刪證據 | UAT-M14-011 |
| REQ-M14-012 | 備份還原重放不重複金流 | 無第二扣款／退款；pending可續跑；恢復演練留證據與差異清單 | UAT-M14-012 |

## 4. 主要使用案例

### UC-M14-A · 收款與三方對帳

建立invoice/payment intent；金流回呼落inbox；每日取得provider settlement與bank參考，核對gross、fee、tax、refund、net與currency；未對齊列差異，pending prepayment不直接當收入。

### UC-M14-B · 服務收入與計獎基礎

依契約服務期間、履約內容與核定會計政策產生revenue schedule；記錄recognized amount與eligible commission basis；獎金只消費已確定的untaxed service revenue及refund allocation。稅別／稅率／未稅判斷由財會設定，不能預設除1.05或1.02。

### UC-M14-C · 退款與會計整合

按適用條款及已提供服務計算退款，預收退還與已認列收入沖回分別allocation；不同人批准後以idempotent命令執行；成功後append journal與commission adjustment event；匯出按batch與receipt對帳。

### UC-M14-D · 內控稽核與復原

查客戶→契約→審核→權限→訊息→報告→簽章→外部證據，以及payment→revenue→commission鏈；可輸出manifest/hash與審計；保全、權限變更、恢復演練及控制例外有責任人和結案證據。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| payment | created → authorized → captured/failed → partially_refunded/refunded/disputed | capture、settlement、bank match、revenue recognition是分開實體與時間 |
| reconciliation | unmatched → matched/exception → resolved | 自動比對僅在明確鍵／金額／幣別吻合，差額轉人工，不能auto writeoff |
| refund | requested → calculated → approval_pending → approved → submitted → confirmed/failed/unknown → accounted | provider timeout unknown先查再重送；退款不直接刪原付款 |
| journal/export | draft → balanced → approved → exported → acknowledged/rejected | 已記帳以反向／更正分錄調整；不可UPDATE原日記帳金額 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| finance.invoice | status | 狀態：draft / issued / paid / voided / credited | CHECK (status IN ('draft','issued','paid','voided','credited')) |
| finance.payment | status | 狀態：pending / authorized / paid / failed / refund_pending / refunded / disputed | CHECK (status IN ('pending','authorized','paid','failed','refund_pending','refunded','disputed')) |
| finance.reconciliation_item | status | 狀態：unmatched / matched / exception / resolved | CHECK (status IN ('unmatched','matched','exception','resolved')) |
| finance.revenue_run | status | 狀態：queued / calculated / review_pending / approved / posted / failed | CHECK (status IN ('queued','calculated','review_pending','approved','posted','failed')) |
| finance.refund | status | 狀態：requested / checking / approval_pending / approved / submitted / confirmed / failed / reconciliation_required / closed / rejected | CHECK (status IN ('requested','checking','approval_pending','approved','submitted','confirmed','failed','reconciliation_required','closed','rejected')) |
| finance.journal | status | 狀態：draft / approved / posted / reversed | CHECK (status IN ('draft','approved','posted','reversed')) |
| finance.accounting_export | status | 狀態：prepared / submitted / acknowledged / rejected / reconciled | CHECK (status IN ('prepared','submitted','acknowledged','rejected','reconciled')) |
| finance.control_exception | status | 狀態：open / assigned / remediating / awaiting_review / closed / waived | CHECK (status IN ('open','assigned','remediating','awaiting_review','closed','waived')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| finance.invoice | 本模組權威寫入 | 應收/發票文件與稅別；不得預設所有服務都是5%營業稅。 | invoice_number | internal | accounting |
| finance.invoice_line | 本模組權威寫入 | 逐服務帳款明細；升級增額不重複算原方案。 | invoice_id,line_no | internal | accounting |
| finance.payment | 本模組權威寫入 | 收款與授權；收現不直接算營收，未接受可為暫收款。 | provider,provider_transaction_id | internal | accounting |
| finance.settlement | 本模組權威寫入 | 支付商入帳／銀行結算，手續費分列。 | provider_settlement_id | internal | accounting |
| finance.reconciliation_item | 本模組權威寫入 | 金流、發票、銀行與總帳差異處理。 | source_type,source_ref | internal | accounting |
| finance.service_schedule | 本模組權威寫入 | 已接受契約的服務收入認列分期表，與收現分離。 | contract_service_id,schedule_version,period_start | internal | accounting |
| finance.revenue_run | 本模組權威寫入 | 收入認列批次之計算、覆核及正式過帳；dry-run不可進入付款/計獎。 | run_key | internal | accounting |
| finance.revenue_recognition | 本模組權威寫入 | 財會確認的收入／更正；獎金只消費 eligible 收入。 | recognition_key | internal | accounting |
| finance.refund | 本模組權威寫入 | 退款簽核與 provider 結果；失敗及未知不可當成功。 | idempotency_key | internal | accounting |
| finance.refund_allocation | 本模組權威寫入 | 退款拆分已認列收入沖減與未履約負債返還，避免獎金重扣。 | tenant_id,id | internal | accounting |
| finance.journal | 本模組權威寫入 | 會計分錄憑證；posted 必須平衡且 maker/checker。 | journal_number；source_type,source_id | internal | accounting |
| finance.journal_line | 本模組權威寫入 | 雙式簿記借貸明細；全憑證延遲檢查平衡。 | journal_id,line_no | internal | accounting |
| finance.accounting_export | 本模組權威寫入 | 匯出會計系統的批次檔與對帳回條。 | destination,export_sha256 | internal | accounting |
| finance.control_exception | 本模組權威寫入 | 內控例外、補正、覆核與稽核證據索引；避免散落試算表。 | tenant_id,id | internal | audit |
| core.audit_event | 受控共用基礎 | 不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。 | tenant_id,id | internal | audit |
| core.legal_hold | 受控共用基礎 | 司法、爭議或稽核保存指示；解除以新事件表達。 | tenant_id,id | internal | audit |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_invoice | POST /finance/invoices | InvoiceCreate | Invoice | finance_operator | 依OpenAPI |
| create_payment_intent | POST /finance/payment-intents | PaymentIntentCreate | PaymentIntent | finance_operator | 依OpenAPI |
| receive_payment_webhook | POST /webhooks/payments | PaymentCallback | CallbackReceipt | payment_adapter | 依OpenAPI |
| import_settlement | POST /finance/settlement-imports | SettlementImportRequest | SettlementImport | finance_operator | 依OpenAPI |
| list_reconciliation_items | GET /finance/reconciliation-items | — | ReconciliationItemPage | finance_operator | 依OpenAPI |
| resolve_reconciliation_item | POST /finance/reconciliation-items/{item_id}/resolution | ReconciliationResolution | ReconciliationItem | finance_checker | If-Match 必須 |
| run_revenue_recognition | POST /finance/revenue-runs | RevenueRunRequest | RevenueRun | finance_operator | 依OpenAPI |
| request_refund | POST /finance/refunds | RefundCreate | Refund | finance_operator,service_agent | 依OpenAPI |
| approve_refund | POST /finance/refunds/{refund_id}/approval | Decision | Refund | finance_checker | If-Match 必須 |
| execute_refund | POST /finance/refunds/{refund_id}/execution | RefundExecutionRequest | Refund | refund_service | If-Match 必須 |
| receive_refund_webhook | POST /webhooks/refunds | RefundCallback | CallbackReceipt | payment_adapter | 依OpenAPI |
| create_accounting_export | POST /finance/accounting-exports | AccountingExportRequest | AccountingExport | finance_operator | 依OpenAPI |
| acknowledge_accounting_export | POST /finance/accounting-exports/{export_id}/acknowledgement | ExportAckRequest | AccountingExport | accounting_adapter | 依OpenAPI |
| list_audit_events | GET /audit/events | — | AuditEventPage | internal_auditor,compliance_reviewer | 依OpenAPI |
| create_evidence_export | POST /audit/evidence-exports | EvidenceExportRequest | EvidenceExport | internal_auditor | 依OpenAPI |
| create_legal_hold | POST /audit/legal-holds | LegalHoldRequest | LegalHold | compliance_supervisor | 依OpenAPI |
| record_control_exception | POST /audit/control-exceptions | ControlExceptionRequest | ControlException | internal_auditor | 依OpenAPI |
| get_revenue_run | GET /finance/revenue-runs/{run_id} | — | RevenueRun | finance_operator,finance_checker | 依OpenAPI |
| approve_revenue_run | POST /finance/revenue-runs/{run_id}/approval | RevenueApprovalRequest | RevenueRun | finance_checker | If-Match 必須 |
| get_refund | GET /finance/refunds/{refund_id} | — | Refund | finance_operator,service_agent | 依OpenAPI |
| get_accounting_export | GET /finance/accounting-exports/{export_id} | — | AccountingExport | finance_operator,finance_checker | 依OpenAPI |
| get_evidence_export | GET /audit/evidence-exports/{export_id} | — | EvidenceExport | internal_auditor | 依OpenAPI |
| release_legal_hold | POST /audit/legal-holds/{hold_id}/release | LegalHoldReleaseRequest | LegalHold | compliance_supervisor | If-Match 必須 |
| act_on_control_exception | POST /audit/control-exceptions/{exception_id}/actions | ControlExceptionActionRequest | ControlException | control_remediation_owner,internal_auditor | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| finance.payment_confirmed | 發出 | M14 | M02,M10 | customer_confidential | 1.0.0 |
| finance.revenue_recognized | 發出 | M14 | M12 | internal | 1.0.0 |
| finance.refund_confirmed | 發出 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |
| finance.accounting_export_acknowledged | 發出／接收 | M14 | M14 | internal | 1.0.0 |
| audit.legal_hold_changed | 發出／接收 | M14 | M14 | internal | 1.0.0 |
| finance.payment_failed | 發出 | M14 | M10 | customer_confidential | 1.0.0 |
| application.company_decided | ／接收 | M02 | M01,M10,M14 | customer_confidential | 1.0.0 |
| contract.effective | ／接收 | M02 | M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.terminated | ／接收 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| commission.run_approved | ／接收 | M12 | M14 | internal | 1.0.0 |
| commission.adjustment_recorded | ／接收 | M12 | M14 | internal | 1.0.0 |
| contract.service_ended | ／接收 | M02 | M11,M10,M14 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 財會工作台 | 收款、settlement、bank、差異、預收／收入、退款、應計獎金與匯出狀態 | 把paid與earned分欄；不同幣別不可直接加總；金額以定點小數 |
| 退款核准頁 | 原付款、已履約／未履約分配、條款版本、計算式、預估稅／費、申請／核定者 | 顯示unknown退款要對帳；不存在timeout後盲目再付款 |
| 稽核證據工作台 | 依客戶／報告／訊息／付款／獎金追溯，選期間與用途 | 權限過濾敏感案情；下載包含manifest、雜湊、來源版本與匯出者審計 |
| 內控例外佇列 | 對帳差異、越權、到期、失敗重試、缺證據、restore drill結果 | owner/due/action/evidence/reviewer完整，無人工空勾就標結案 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM可整理對帳差異說明、辨識附件欄位與起草稽核答覆；數值／證據需引用權威紀錄，不可猜補。
- 必須受控：金額、稅別、認列、平衡分錄、退款、payout、保全與證據雜湊由確定性規則和maker/checker處理。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- payment callback驗簽、時間窗、去重與狀態偏序；同id不同金額／幣別為高優先差異。
- settlement fee不能直接當收入退款；gross、fee、tax、net各欄位分開，會計mapping版本化。
- provider已退款但回呼遺失：status query/reconciliation回復，不二次出金。
- 備援還原後outbox/inbox與金流idempotency必須同時保留；replay不得重送扣款／退款。
- 稽核保留期限按記錄類型與觸發時點設定；legal hold優先於排程刪除。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- 外部金流／銀行結算／會計adapter
- M02 contract/service conditions
- M12 revenue basis與refund allocation
- M13 refund request
- 所有模組audit/outbox/object manifests

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M14-001 · 付款回呼重送只入帳一次

- 前提：同provider event/payment id與金額
- 操作：接收兩次
- 預期：一payment transition、一財務事件；不重複增加預收或收入
- API：`create_payment_intent`, `receive_payment_webhook`
- 證據：`finance.payment`、audit/correlation與適用外部receipt。

### UAT-M14-002 · 預收款與已履約收入分開

- 前提：客戶付款但公司未接受或服務未開始
- 操作：跑收款與認列
- 預期：payment captured且prepayment liability增加，revenue=0；無計獎eligible收入
- API：`run_revenue_recognition`, `get_revenue_run`, `approve_revenue_run`
- 證據：`finance.revenue_recognition`、audit/correlation與適用外部receipt。

### UAT-M14-003 · 結算費用與收入不同欄

- 前提：gross1000 fee30 settlement970的synthetic交易
- 操作：匯入結算對帳
- 預期：gross/fee/net分列且相符；不能把收入直接當970或refund30
- API：`import_settlement`, `list_reconciliation_items`, `resolve_reconciliation_item`
- 證據：`finance.settlement`、audit/correlation與適用外部receipt。

### UAT-M14-004 · 稅額使用核定配置不猜除數

- 前提：invoice指定tax treatment但無核定mapping
- 操作：建立收入／計獎基礎
- 預期：進配置例外，不能默認除1.05或1.02；有核定mapping後可重算
- API：`create_invoice`
- 證據：`finance.invoice_line`、audit/correlation與適用外部receipt。

### UAT-M14-005 · 退款製作核准執行分權

- 前提：maker建立退款申請
- 操作：同maker approve/execute
- 預期：SoD拒絕；合法checker核准後受限service才可執行
- API：`request_refund`, `approve_refund`, `execute_refund`
- 證據：`finance.refund`、audit/correlation與適用外部receipt。

### UAT-M14-006 · 退款timeout先查provider

- 前提：provider已退款但本地unknown
- 操作：重新執行／復原
- 預期：先查狀態或同key；只有一筆對外退款，保留attempt receipts
- API：`execute_refund`, `get_refund`, `receive_refund_webhook`
- 證據：`finance.refund`、audit/correlation與適用外部receipt。

### UAT-M14-007 · 退款拆分預收與已認列部分

- 前提：部分履約服務退款
- 操作：計算refund allocation
- 預期：unearned refund與earned revenue reversal分列；只有已計獎基礎部分通知M12反向
- API：`request_refund`, `receive_refund_webhook`
- 證據：`finance.refund_allocation`、audit/correlation與適用外部receipt。

### UAT-M14-008 · 會計分錄必須平衡

- 前提：日記帳借貸差0.01
- 操作：要求export/approve
- 預期：拒絕unbalanced，無會計成功標記；修正形成可追溯版本或反向分錄
- API：`create_accounting_export`
- 證據：`finance.journal_line`、audit/correlation與適用外部receipt。

### UAT-M14-009 · 會計匯出重送有批次收據

- 前提：同batch曾export但回覆遺失
- 操作：重送或收到重複ack
- 預期：使用同external id查/重試；不重複記帳；ack與rejected distinct
- API：`acknowledge_accounting_export`, `get_accounting_export`
- 證據：`finance.accounting_export`、audit/correlation與適用外部receipt。

### UAT-M14-010 · 完整稽核鏈可由訊息回查

- 前提：有合法訊息、報告、簽章、來源、權限與收件紀錄
- 操作：建立evidence export
- 預期：manifest含各immutable refs/hash與實際時點；受限資料依role遮罩且access audit
- API：`list_audit_events`, `create_evidence_export`, `get_evidence_export`
- 證據：`core.audit_event`、audit/correlation與適用外部receipt。

### UAT-M14-011 · legal hold阻止到期刪除且解除受控

- 前提：物件超常規retention但有有效hold
- 操作：purge job執行，之後嘗試未授權解除，再由授權者依理由解除
- 預期：有效hold時skip且記原因；未授權解除被拒；合法解除追加紀錄且保留原hold與audit，不直接刪證據
- API：`create_legal_hold`, `release_legal_hold`
- 證據：`core.legal_hold`、audit/correlation與適用外部receipt。

### UAT-M14-012 · 備份還原重放不重複金流

- 前提：restore包含既有payment/refund/outbox/inbox
- 操作：復原worker並replay events
- 預期：無第二扣款／退款；pending可續跑；恢復演練留證據與差異清單
- API：`record_control_exception`, `act_on_control_exception`
- 證據：`finance.control_exception`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M14-finance_control.md)
- [GitHub issue #14](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/14)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
