# M12 · 獎金政策、計算、調整與結算：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

以實際未稅服務收入扣除可歸屬退款計算獎金，保存政策版本、收入來源與轉換證據。將已確認參數與尚待核定的基準比率／衰減互動明確分開，所有結果可重算與對帳。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- 薪獎政策管理者：製作版本化政策，不能自行核准生效
- 政策核定者：批准比率、衰減、分配、尾佣與生效區間
- 財會：確認收入基礎、退款調整與結算；與出款核准分離
- 業務／行銷：只讀本人可見明細及提出爭議，不改規則或收入
- 內稽：可重演計算與核對證據

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M12-001 | 未核定基準率不能正式計獎 | policy_not_approved；可simulation但結果不可付款 | UAT-M12-001 |
| REQ-M12-002 | 自助續訂無業務份額 | sales amount=0；marketing依來源／年係數；其餘保留公司，不給current owner | UAT-M12-002 |
| REQ-M12-003 | 第一二年係數按客戶cohort | 係數分別1.00、0.50；不以公司年度統一降半 | UAT-M12-003 |
| REQ-M12-004 | 第三年尾佣需持續自動續約 | 只有符合連續auto者適用0.25；不因打開auto開關就自動恢復既往尾佣資格 | UAT-M12-004 |
| REQ-M12-005 | 業務介入30/70需有核定證據 | pool先按該版本規則分配；marketing/sales與衰減順序可在明細重演，不能隱藏差額 | UAT-M12-005 |
| REQ-M12-006 | 衰減兩算法不能混用 | A與B結果分開且有policy hash；live只接受被核定的單一策略 | UAT-M12-006 |
| REQ-M12-007 | 退款反向原受款人原政策 | 反向原entry recipient與policy；不扣新接手者；保留原金額與adjustment | UAT-M12-007 |
| REQ-M12-008 | 預收未履約退款不能重扣收入 | 只對原已計入basis部分調整；預收退還不重扣既有earned revenue | UAT-M12-008 |
| REQ-M12-009 | 退款重送只調整一次 | 一linked adjustment；net eligible total與財會一致 | UAT-M12-009 |
| REQ-M12-010 | 金額分配總數守恆 | marketing+sales+company=basepool；明確minor-unit rounding/remainder規則，無float誤差 | UAT-M12-010 |
| REQ-M12-011 | 製作人不可自核且發放須回執 | 自核segregation_conflict；合法checker核定後才可交付款接口；有可驗證且去重的付款回執才標paid | UAT-M12-011 |
| REQ-M12-012 | 已付退款不得自動扣薪 | 列追索／抵銷待核定，無無授權自動薪資扣款；保留爭議與核准紀錄 | UAT-M12-012 |

## 4. 主要使用案例

### UC-M12-A · 政策版本

已確認：marketing第1年100%、第2年50%、第3年起符合持續自動續約25%；sales介入續約pool分30/70；基礎實際未稅服務收入扣退款。未確認：base pool rate、衰減與30/70套用順序、尾佣連續性、年份起算、接手後marketing share等，均可模擬但需核定才能live。

### UC-M12-B · 月結計算

取得M14已確認eligible revenue及退款allocation，讀M10核定conversion與M11歷史assignment；以policy effective period、cohort與lineage計算decimal金額，按受款人保留計算式及company retained；無sales intervention則sales=0。

### UC-M12-C · 退款與發放

退款只反轉原來已計入的收入與相應原受款人／原policy；已付獎金形成待處理差額，不自動扣薪。結算完成後maker/checker批准payout batch，依實際財會接口對帳，provider成功回執才paid。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| policy | draft → simulation_ready → approval_pending → approved → effective → retired | 3%僅歷史試算提案；production rate未核准時必須null／disabled，不可帶預設 |
| run | prepared → calculating → calculated → reconciled → approved/voided | inputs snapshot與policy hash固定；重算產生新run或idempotent同run，不覆寫已核定結果 |
| entry | provisional → eligible → approved → payable → paid；另adjusted/disputed | payout非計算工作者直接出金；原entry保留，退款透過linked adjustment |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| commission.policy_version | approval_status | draft/approved/retired | CHECK (approval_status IN ('draft','approved','retired')) |
| commission.calculation_run | status | 狀態：queued / calculating / calculated / review_pending / approved / rejected / exported / failed | CHECK (status IN ('queued','calculating','calculated','review_pending','approved','rejected','exported','failed')) |
| commission.payout_batch | status | 狀態：draft / approval_pending / approved / exported / paid / reversed | CHECK (status IN ('draft','approval_pending','approved','exported','paid','reversed')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| commission.policy_version | 本模組權威寫入 | 獎金演算法、來源年齡係數及未核准參數的版本化政策。 | policy_code,policy_version | internal | commission |
| commission.calculation_run | 本模組權威寫入 | 可重現的試算或正式計獎批次；未核准政策只可 simulation。 | run_key | internal | commission |
| commission.entry | 本模組權威寫入 | 逐筆收入切分行銷、業務、公司留存之計算明細，原始列不可改。 | run_id,recognition_id,beneficiary_type | internal | commission |
| commission.adjustment | 本模組權威寫入 | 退款/歸因修正以原受領人與原政策沖回；不直接扣薪。 | dedupe_key | internal | commission |
| commission.payout_batch | 本模組權威寫入 | 正式獎金支出批次；試算結果不可付款。 | tenant_id,id | internal | commission |
| commission.payout_line | 本模組權威寫入 | 獎金列與付款批次關係，避免一筆獎金重複支付。 | entry_id | internal | commission |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| create_commission_policy | POST /commissions/policies | CommissionPolicyCreate | CommissionPolicy | commission_operator | 依OpenAPI |
| approve_commission_policy | POST /commissions/policies/{policy_id}/approval | PolicyApprovalRequest | CommissionPolicy | commission_approver | If-Match 必須 |
| preview_commissions | POST /commissions/previews | CommissionRunRequest | CommissionPreview | commission_operator | 依OpenAPI |
| create_commission_run | POST /commissions/runs | CommissionRunRequest | CommissionRun | commission_operator | 依OpenAPI |
| get_commission_run | GET /commissions/runs/{run_id} | — | CommissionRun | commission_operator,commission_approver | 依OpenAPI |
| list_commission_entries | GET /commissions/entries | — | CommissionEntryPage | commission_operator,commission_approver,sales | 依OpenAPI |
| approve_commission_run | POST /commissions/runs/{run_id}/approval | Decision | CommissionRun | commission_approver | If-Match 必須 |
| create_commission_adjustment | POST /commissions/adjustments | CommissionAdjustmentRequest | CommissionAdjustment | commission_operator | 依OpenAPI |
| create_commission_payout_batch | POST /commissions/payout-batches | PayoutBatchRequest | PayoutBatch | payroll_operator | 依OpenAPI |
| get_commission_payout_batch | GET /commissions/payout-batches/{batch_id} | — | PayoutBatch | payroll_operator,commission_approver | 依OpenAPI |
| approve_commission_payout_batch | POST /commissions/payout-batches/{batch_id}/approval | Decision | PayoutBatch | commission_approver | If-Match 必須 |
| acknowledge_commission_payout_batch | POST /commissions/payout-batches/{batch_id}/acknowledgement | PayoutAckRequest | PayoutBatch | payroll_adapter | 依OpenAPI |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| commission.run_calculated | 發出／接收 | M12 | M12 | internal | 1.0.0 |
| commission.run_approved | 發出 | M12 | M14 | internal | 1.0.0 |
| commission.adjustment_recorded | 發出 | M12 | M14 | internal | 1.0.0 |
| crm.conversion_decided | ／接收 | M10 | M12 | customer_confidential | 1.0.0 |
| ownership.assignment_changed | ／接收 | M11 | M10,M12,M13 | customer_confidential | 1.0.0 |
| finance.revenue_recognized | ／接收 | M14 | M12 | internal | 1.0.0 |
| finance.refund_confirmed | ／接收 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 政策與試算頁 | 已批准與提案標籤、版本、生效期間、rate、100/50/25、30/70、尾佣規則、rounding | 缺任何關鍵核定參數只准preview；顯示衰減算法A/B結果差異，不選一個假稱user已核准 |
| 個人獎金明細 | 收入／退款、契約服務期、原來源、conversion evidence、policy、基準池、marketing/sales/company分配 | 每筆可追算；維護owner不是自動領款人 |
| 月結與差異 | 收入對帳、已計／未計原因、重算差異、退款調整與付款批次 | 待爭議、缺basis或缺核定不能直接付款；已付更正有獨立流程 |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM可解釋已完成的計算、整理差異與爭議證據；不得自由決定金額、獎金率或受款人。
- 必須受控：計算由decimal規則引擎執行；每個數值可由固定input snapshot重演；approval、發放與調整遵循SoD。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 收入未確定或淨額為負：標例外與linked reversal；不得把負數直接裁為0而丟棄應調整事實。
- 第二／三年度依客戶cohort與service lineage，不依公司year2整批套50%。
- 同一refund allocation不可重複扣除；退尚未認列預收款不能再扣已認列收入。
- 計算策略未核定：simulation允許比較，live/payout拒絕；不以文件範例3%當批准。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- M14 finance-confirmed eligible revenue/refund allocations
- M10 approved sales conversion
- M11 historical owner and departure
- M01/M10 original marketing attribution
- 財會薪獎／付款adapter

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M12-001 · 未核定基準率不能正式計獎

- 前提：policy rate=null或只有3%提案
- 操作：建立live run/payout
- 預期：policy_not_approved；可simulation但結果不可付款
- API：`create_commission_policy`, `approve_commission_policy`, `create_commission_run`
- 證據：`commission.policy_version`、audit/correlation與適用外部receipt。

### UAT-M12-002 · 自助續訂無業務份額

- 前提：已核定test policy、無sales conversion
- 操作：計算有效收入10000
- 預期：sales amount=0；marketing依來源／年係數；其餘保留公司，不給current owner
- API：`preview_commissions`
- 證據：`commission.entry`、audit/correlation與適用外部receipt。

### UAT-M12-003 · 第一二年係數按客戶cohort

- 前提：同一天有year1與year2客戶且同test pool
- 操作：preview兩筆
- 預期：係數分別1.00、0.50；不以公司年度統一降半
- API：`preview_commissions`
- 證據：`commission.entry`、audit/correlation與適用外部receipt。

### UAT-M12-004 · 第三年尾佣需持續自動續約

- 前提：year3兩客戶一持續auto一人工重啟
- 操作：preview核定test policy
- 預期：只有符合連續auto者適用0.25；不因打開auto開關就自動恢復既往尾佣資格
- API：`preview_commissions`
- 證據：`commission.entry`、audit/correlation與適用外部receipt。

### UAT-M12-005 · 業務介入30/70需有核定證據

- 前提：成功renewal且M10 conversion approved
- 操作：按明確test policy執行
- 預期：pool先按該版本規則分配；marketing/sales與衰減順序可在明細重演，不能隱藏差額
- API：`create_commission_run`, `get_commission_run`, `list_commission_entries`
- 證據：`commission.entry`、audit/correlation與適用外部receipt。

### UAT-M12-006 · 衰減兩算法不能混用

- 前提：test政策A僅marketing衰減、B全pool衰減
- 操作：同year2 sales-assisted收入做preview
- 預期：A與B結果分開且有policy hash；live只接受被核定的單一策略
- API：`preview_commissions`
- 證據：`commission.policy_version`、audit/correlation與適用外部receipt。

### UAT-M12-007 · 退款反向原受款人原政策

- 前提：已計獎交易後退款且維護人已改
- 操作：建立adjustment
- 預期：反向原entry recipient與policy；不扣新接手者；保留原金額與adjustment
- API：`create_commission_adjustment`
- 證據：`commission.adjustment`、audit/correlation與適用外部receipt。

### UAT-M12-008 · 預收未履約退款不能重扣收入

- 前提：退款含未認列預收與已認列部分
- 操作：M14分配後計算
- 預期：只對原已計入basis部分調整；預收退還不重扣既有earned revenue
- API：`create_commission_adjustment`
- 證據：`commission.adjustment`、audit/correlation與適用外部receipt。

### UAT-M12-009 · 退款重送只調整一次

- 前提：同refund allocation webhook兩次
- 操作：consume並重跑
- 預期：一linked adjustment；net eligible total與財會一致
- API：`create_commission_adjustment`
- 證據：`commission.adjustment`、audit/correlation與適用外部receipt。

### UAT-M12-010 · 金額分配總數守恆

- 前提：各種小額decimal收入及百分比
- 操作：計算rounding
- 預期：marketing+sales+company=basepool；明確minor-unit rounding/remainder規則，無float誤差
- API：`preview_commissions`
- 證據：`commission.entry`、audit/correlation與適用外部receipt。

### UAT-M12-011 · 製作人不可自核且發放須回執

- 前提：maker建立run及payout batch
- 操作：同人批准遭拒後由合法checker核定；查batch並提交對帳回執
- 預期：自核segregation_conflict；合法checker核定後才可交付款接口；有可驗證且去重的付款回執才標paid
- API：`approve_commission_run`, `create_commission_payout_batch`, `approve_commission_payout_batch`, `get_commission_payout_batch`, `acknowledge_commission_payout_batch`
- 證據：`commission.payout_batch`、audit/correlation與適用外部receipt。

### UAT-M12-012 · 已付退款不得自動扣薪

- 前提：entry paid後產生負向adjustment
- 操作：產生下期結算
- 預期：列追索／抵銷待核定，無無授權自動薪資扣款；保留爭議與核准紀錄
- API：`create_commission_adjustment`
- 證據：`commission.adjustment`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M12-commissions.md)
- [GitHub issue #12](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/12)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
