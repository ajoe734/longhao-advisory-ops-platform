# 全十四模組驗收計畫與完整案例索引

## 驗收範圍與當前狀態

範圍包含十四模組全部功能，共 **168個模組案例 + 14個跨模組E2E = 182個驗收案例**。此文件描述預期行為；目前應用程式尚未因此被宣稱完成，所有業務案例都是 **NOT_RUN**。資料契約／SQL自動驗證是另一類技術檢核，不得冒充業務UAT通過。

| 模組 | 範圍 | 案例數 | 詳細Given/When/Then | 實測狀態 |
| --- | --- | --- | --- | --- |
| M01 | 客戶、身分與申辦核心 | 12 | [M01逐案規格](../docs/modules/M01-customer_identity_application.md) | NOT_RUN |
| M02 | 線上契約、公司接受與服務權限 | 12 | [M02逐案規格](../docs/modules/M02-contracts_entitlements.md) | NOT_RUN |
| M03 | 線上 KYC、客戶資料與適合度 | 12 | [M03逐案規格](../docs/modules/M03-kyc_suitability.md) | NOT_RUN |
| M04 | AML/CFT 查核、風險與受限案件 | 12 | [M04逐案規格](../docs/modules/M04-aml_cft.md) | NOT_RUN |
| M05 | Pantheon 候選研究接收與證據驗證 | 12 | [M05逐案規格](../docs/modules/M05-pantheon_intake.md) | NOT_RUN |
| M06 | 研究覆核工作台與 Agora／Servant 協作 | 12 | [M06逐案規格](../docs/modules/M06-agora_review.md) | NOT_RUN |
| M07 | 正式研究報告、覆核與分析師簽章 | 12 | [M07逐案規格](../docs/modules/M07-report_signature.md) | NOT_RUN |
| M08 | 建議序列、簽章驅動發布與更正 | 12 | [M08逐案規格](../docs/modules/M08-publication.md) | NOT_RUN |
| M09 | LegendFlow、會員服務與即時通路投遞 | 12 | [M09逐案規格](../docs/modules/M09-legendflow_delivery.md) | NOT_RUN |
| M10 | 客戶漏斗、業務維護與轉換歸因 | 12 | [M10逐案規格](../docs/modules/M10-crm.md) | NOT_RUN |
| M11 | 維護歸屬、離職移交與公司 Pool | 12 | [M11逐案規格](../docs/modules/M11-ownership_pool.md) | NOT_RUN |
| M12 | 獎金政策、計算、調整與結算 | 12 | [M12逐案規格](../docs/modules/M12-commissions.md) | NOT_RUN |
| M13 | 線上線下客服、申訴與跨部門案件 | 12 | [M13逐案規格](../docs/modules/M13-service_cases.md) | NOT_RUN |
| M14 | 帳務、對帳、退款與內控證據 | 12 | [M14逐案規格](../docs/modules/M14-finance_control.md) | NOT_RUN |

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

下列E2E先以本平台十四模組＋external contract mocks執行；這是本次授權範圍的完整平台驗收。各紀錄保留execution_mode與external_connection_status，真實連線未驗證時標NOT_CONNECTED。即使本平台所有案例通過，也不能宣稱已正式連到外部系統。

## 執行環境與證據

- 開發與整合環境只使用synthetic客戶、合成簽章測試憑據、contract mocks；若另有可使用的provider sandbox，驗證結果另列，不改外部專案。不得複製真KYC/AML/客戶資料進public repo。
- 每案例記錄commit、環境、actor/role/tenant、policy/schema/fixture版號、預期、實際、執行時間與evidence refs。任何阻擋條件未滿足則記BLOCKED而非PASS。
- 證據至少含API request/response、安全遮罩後的資料列前後版本、audit/correlation與適用outbox/inbox/provider receipts。
- 錯誤與授權測試需包含兩個tenant、一般staff、authorized reviewer、LLM service和admin；admin不自動有業務權限。
- 時間相關案例使用可控clock，至少涵蓋Asia/Taipei日期邊界、年資周年、合約有效時間、七日服務中斷、訊息到期與delayed events。
- 金額使用Decimal；測試全額、部分、零、退款大於當期收入、不同currency、重複refund allocation、重算與rounding餘額。

## 必須通過的整體旅程

### UAT-E2E-001 · 客戶先簽、線上查核、公司接受、開通全鏈

- 參與模組：M01, M02, M03, M04, M09, M14
- 前提：Synthetic applicant未有契約；合法身份及mock金流／查核／簽章可用
- 操作：建立申辦→先簽→確認locked→線上KYC適合度及AML→預收付款→公司接受→有效契約及權限→LegendFlow讀feed
- 預期：每一狀態及時間獨立；KYC前客戶可簽；company acceptance之前不提供付費投顧服務；一次付款與一次開通；完整audit chain
- APIs：`create_application`, `create_customer_signature`, `submit_kyc`, `assess_suitability`, `start_aml_screening`, `decide_company_acceptance`, `evaluate_entitlements`, `get_member_feed`
- 證據資料：`customer.application`, `contract.customer_signature`, `kyc.submission`, `aml.business_clearance`, `contract.entitlement`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-002 · 付款後拒絕與退款全鏈

- 參與模組：M02, M03, M04, M12, M13, M14
- 前提：客戶已簽及預收，但公司尚未接受
- 操作：審核拒絕→安全通知／客服案→退款計算→maker/checker→provider確認→財會分配→核對獎金
- 預期：未曾開通；退款僅一次；未履約預收無收入與無獎金；客服可追狀態但不可出金；拒絕原因受限分級
- APIs：`decide_company_acceptance`, `create_service_case`, `request_refund`, `approve_refund`, `execute_refund`, `receive_refund_webhook`
- 證據資料：`contract.company_acceptance`, `finance.payment`, `finance.refund`, `finance.refund_allocation`, `commission.entry`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-003 · Persona自主研究到簽章自動即時發布

- 參與模組：M05, M06, M07, M08, M09
- 前提：沒有分析師預先建立ticket；有具來源證據的Persona候選；合法簽署者及客戶權限
- 操作：候選入件→分析師覆核→Servant修正→新版本diff→必要review→freeze→簽署驗證→publication→LegendFlow/通路
- 預期：全流程不要求分析師先發起；簽後無第二Publish操作；每訊息指向已簽report及當時來源；Persona trade不當客戶成交
- APIs：`ingest_research_candidate`, `request_servant_revision`, `receive_servant_revision`, `create_report_version`, `record_report_review`, `freeze_report_version`, `receive_analyst_signature`, `get_member_feed`
- 證據資料：`research.candidate`, `review.revision_result`, `report.report_version`, `report.signature`, `publication.publication`, `delivery.message`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-004 · 簽署競態與重大修訂阻擋

- 參與模組：M05, M06, M07, M08
- 前提：v1覆核完成；另一條線同時送入v2重大更新
- 操作：v1正在送簽時收到superseding event；callback到達；比較stale狀態和凍結包
- 預期：不讓舊review自動批准v2；過時v1按核定規則停止；任何發布都引用真實批准的唯一版本，無hash/版本混用
- APIs：`ingest_research_candidate`, `get_review_case`, `request_analyst_signature`, `receive_analyst_signature`, `get_publication`
- 證據資料：`review.case_file`, `report.review_decision`, `report.signature_request`, `publication.publication`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-005 · 進場更新出場與更正證據

- 參與模組：M05, M07, M08, M09, M14
- 前提：一recommendation series已發entry且部分收件已送達
- 操作：新增update/exit判斷及支持報告→簽章→發布→發現需更正→新簽補充報告→correction link→稽核匯出
- 預期：各訊息版本有當時可支持的signed report；不覆寫已送原文；更正明確告知；可從任何message追到來源及收件資格
- APIs：`create_recommendation_version`, `create_report_version`, `receive_analyst_signature`, `link_publication_correction`, `create_evidence_export`
- 證據資料：`publication.recommendation_version`, `report.signature`, `publication.publication`, `delivery.message`, `core.audit_event`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-006 · 投遞中撤權及回呼亂序

- 參與模組：M02, M08, M09
- 前提：audience sealed；多個通路有queued/accepted/sent狀態
- 操作：撤銷客戶權限→部分訊息dispatch→亂序receipt→timeout reconciliation→expired DLQ redrive
- 預期：dispatch前即時檢查；未送部分suppressed；既有sent事實不改；read未知保持未知；過期消息不盲發
- APIs：`suspend_entitlement`, `receive_message_receipt`, `reconcile_delivery`, `redrive_delivery_event`
- 證據資料：`contract.entitlement`, `publication.recipient`, `delivery.attempt`, `delivery.receipt`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-007 · 網路取得與連續自助續約尾佣

- 參與模組：M01, M02, M10, M11, M12, M14
- 前提：合成客戶不同首購cohort，年1/年2/年3連續auto，核定test-only政策
- 操作：逐期付款認列→CRM判定self_serve→計獎preview/run→明細追算
- 預期：marketing係數100/50/25依客戶cohort與連續性；無sales intervention則sales=0；未核定生產rate不得live；公司年度不代替客戶年資
- APIs：`create_application`, `run_revenue_recognition`, `claim_conversion`, `preview_commissions`, `list_commission_entries`
- 證據資料：`crm.marketing_attribution`, `crm.conversion`, `finance.revenue_recognition`, `commission.entry`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-008 · 業務續約30/70與歸因爭議

- 參與模組：M10, M11, M12, M14
- 前提：維護人與促成續約者不同；兩人提出claim；有事前活動及成功交易
- 操作：記錄活動→成功renewal→兩人claim→主管核定→收入確認→policy計獎→稽核
- 預期：僅核定的transaction evidence決定sales受款；原source與maintenance不覆寫；衰減策略明示；同收入不重複發獎金
- APIs：`record_sales_activity`, `claim_conversion`, `decide_conversion`, `run_revenue_recognition`, `create_commission_run`
- 證據資料：`crm.activity`, `crm.conversion`, `ownership.assignment`, `commission.entry`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-009 · 離職Pool重新開發與歷史獎金

- 參與模組：M10, M11, M12, M14
- 前提：舊業務有已核定獎金與有效維護assignment
- 操作：離職即刻移交company→客戶自助續訂→新業務接手聯絡→促成下次續約→計獎
- 預期：離職不等七天；自助期無新業務獎金；新業務只得實際促成後續transaction份額；歷史獎金不被改歸屬
- APIs：`record_employee_departure`, `claim_pool_customer`, `record_sales_activity`, `decide_conversion`, `create_commission_run`
- 證據資料：`ownership.assignment`, `ownership.pool_entry`, `crm.conversion`, `commission.entry`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-010 · 多契約與七日服務中斷提案

- 參與模組：M02, M10, M11
- 前提：月訂與年度並行；Pool七日解讀以test approved policy模擬
- 操作：月訂到期→年度仍active→年度也到期→167h/168h評估→其中一項在第6日恢復
- 預期：只在全部有效付費服務中斷且連續滿核定窗口才可候選；沒有每週聯絡要求；復訂中止clock；policy draft時不實際移交
- APIs：`terminate_contract`, `evaluate_entitlements`, `evaluate_company_pool`, `list_company_pool`
- 證據資料：`contract.contract_service`, `ownership.policy_version`, `ownership.pool_entry`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-011 · 退款、收入沖回及原人獎金反轉

- 參與模組：M02, M12, M13, M14
- 前提：部分履約且已認列／已計獎金，後來maintenance owner已變更
- 操作：客服申請退款→未履約與已認列分配→核定執行→財會分錄→原policy獎金adjustment→對帳
- 預期：無重複退款；預收返還不再次扣收入；已計獎部分反轉原受款人；不扣接手者；已付部分不得自動扣薪
- APIs：`request_refund`, `approve_refund`, `execute_refund`, `receive_refund_webhook`, `create_commission_adjustment`
- 證據資料：`finance.refund_allocation`, `finance.journal_line`, `commission.adjustment`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-012 · 跨通路申訴與升級抑制

- 參與模組：M01, M10, M13, M14
- 前提：客戶有升級worklist並在網站抱怨，後續電話與官方訊息補充
- 操作：驗證身份→合案→AI摘要→重大申訴升級→暫停促銷→跨部門處理→核定結案
- 預期：同案SLA不中斷；業務名單被抑制但必要服務持續；AI無擅自承諾；case owner不自動獲sales bonus
- APIs：`create_service_case`, `add_case_interaction`, `draft_service_response`, `approve_service_response_draft`, `send_service_response`, `receive_service_response_receipt`, `escalate_service_case`, `create_crm_suppression`, `resolve_service_case`
- 證據資料：`support.case_file`, `support.interaction`, `support.sla_event`, `crm.suppression`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-013 · 受限資料隔離與一般營運阻擋

- 參與模組：M01, M02, M03, M04, M06, M10, M13, M14
- 前提：兩tenant，AML case含受限證據；各一般角色與LLM principal就緒
- 操作：跨tenant嘗試讀資料→sales/客服/研究查AML→AI要求解除命中→合法AML核定→安全clearance投影→audit export
- 預期：跨tenant與越權均拒絕；CRM/研究prompt無AML案情；公司接受只依安全clearance；AML稽核另授權，通用admin不自動可讀
- APIs：`get_customer`, `get_aml_case`, `decide_aml_case`, `get_business_clearance`, `open_servant_conversation`, `create_evidence_export`
- 證據資料：`aml.case_file`, `aml.business_clearance`, `core.audit_event`
- 執行狀態：**NOT_RUN**。

### UAT-E2E-014 · 故障復原、重播與全範圍驗收

- 參與模組：M01, M02, M03, M04, M05, M06, M07, M08, M09, M10, M11, M12, M13, M14
- 前提：所有14 modules與本平台adapter與外部contract mocks在整合環境，含queued outbox/unknown provider results
- 操作：在commit後publish前殺worker→還原備份→重播與亂序事件→對帳金流與訊息→跑全部關鍵旅程→證據匯出
- 預期：無第二扣退款、重複signature publication或獎金；版本與audit一致；RPO/RTO有測量結果；十四模組及跨域關鍵案例全過才可宣告整體完成
- APIs：`receive_payment_webhook`, `receive_analyst_signature`, `reconcile_delivery`, `create_commission_run`, `create_evidence_export`, `record_control_exception`
- 證據資料：`core.outbox`, `core.inbox`, `finance.payment`, `report.signature`, `publication.publication`, `commission.calculation_run`
- 執行狀態：**NOT_RUN**。

## 所有模組案例索引

逐案前提、操作與通過條件見上方各模組文件第14節，機器可讀完整內容在[test-catalog.json](test-catalog.json)；[traceability.csv](traceability.csv)包含每一REQ到API、資料表、任務包、issue與UAT映射。

| UAT | 模組 | 需求ID | 情境 | 狀態 |
| --- | --- | --- | --- | --- |
| UAT-M01-001 | M01 | REQ-M01-001 | 同一外部身分重送只建立一客戶 | NOT_RUN |
| UAT-M01-002 | M01 | REQ-M01-002 | 不同已驗證 subject 不能依同信箱自動合併 | NOT_RUN |
| UAT-M01-003 | M01 | REQ-M01-003 | 申辦草稿跨會話續填與撤回 | NOT_RUN |
| UAT-M01-004 | M01 | REQ-M01-004 | 未授權跨客戶查詢 | NOT_RUN |
| UAT-M01-005 | M01 | REQ-M01-005 | 來源變更不覆寫原始取得來源 | NOT_RUN |
| UAT-M01-006 | M01 | REQ-M01-006 | 同意撤回有範圍與時間 | NOT_RUN |
| UAT-M01-007 | M01 | REQ-M01-007 | 外部 token 偽造遭拒 | NOT_RUN |
| UAT-M01-008 | M01 | REQ-M01-008 | 兩筆合併保留可追溯關聯 | NOT_RUN |
| UAT-M01-009 | M01 | REQ-M01-009 | 客戶資料樂觀鎖阻擋覆寫 | NOT_RUN |
| UAT-M01-010 | M01 | REQ-M01-010 | 驗證供應商中斷不建立假身分 | NOT_RUN |
| UAT-M01-011 | M01 | REQ-M01-011 | 協助建檔不可冒充客戶同意 | NOT_RUN |
| UAT-M01-012 | M01 | REQ-M01-012 | 來源資料故障與空集合不同 | NOT_RUN |
| UAT-M02-001 | M02 | REQ-M02-001 | 客戶可以在 KYC 前完成簽署 | NOT_RUN |
| UAT-M02-002 | M02 | REQ-M02-002 | 未完成審核不可公司接受 | NOT_RUN |
| UAT-M02-003 | M02 | REQ-M02-003 | 全部門檻通過後只開通一次 | NOT_RUN |
| UAT-M02-004 | M02 | REQ-M02-004 | 公司拒絕已收款申辦 | NOT_RUN |
| UAT-M02-005 | M02 | REQ-M02-005 | 兩份並行契約權限分開 | NOT_RUN |
| UAT-M02-006 | M02 | REQ-M02-006 | 文件改版不得套用舊簽署 | NOT_RUN |
| UAT-M02-007 | M02 | REQ-M02-007 | 審核快照過期應重新審查 | NOT_RUN |
| UAT-M02-008 | M02 | REQ-M02-008 | 簽章 callback 不等於完成驗證 | NOT_RUN |
| UAT-M02-009 | M02 | REQ-M02-009 | 續約預建不提前給權限 | NOT_RUN |
| UAT-M02-010 | M02 | REQ-M02-010 | 公司接受權限需職務且防自審 | NOT_RUN |
| UAT-M02-011 | M02 | REQ-M02-011 | 權限事件延遲不以網站快取越權 | NOT_RUN |
| UAT-M02-012 | M02 | REQ-M02-012 | 終止與留存各自生效 | NOT_RUN |
| UAT-M03-001 | M03 | REQ-M03-001 | 簽署後可開始線上 KYC | NOT_RUN |
| UAT-M03-002 | M03 | REQ-M03-002 | 缺必填欄位不得完成審核 | NOT_RUN |
| UAT-M03-003 | M03 | REQ-M03-003 | OCR 輔助不覆寫客戶原始答案 | NOT_RUN |
| UAT-M03-004 | M03 | REQ-M03-004 | 文件惡意內容在隔離區遭阻擋 | NOT_RUN |
| UAT-M03-005 | M03 | REQ-M03-005 | 適合度由版本化規則計算 | NOT_RUN |
| UAT-M03-006 | M03 | REQ-M03-006 | 不適合服務不能因已付費通過 | NOT_RUN |
| UAT-M03-007 | M03 | REQ-M03-007 | 新版本提交使舊審核過時 | NOT_RUN |
| UAT-M03-008 | M03 | REQ-M03-008 | 業務僅能看到補件狀態 | NOT_RUN |
| UAT-M03-009 | M03 | REQ-M03-009 | 風險變更觸發重評 | NOT_RUN |
| UAT-M03-010 | M03 | REQ-M03-010 | 人工例外需權限與獨立覆核 | NOT_RUN |
| UAT-M03-011 | M03 | REQ-M03-011 | 到期查核不被失敗重試當通過 | NOT_RUN |
| UAT-M03-012 | M03 | REQ-M03-012 | 同一提交重放不產生多筆評估 | NOT_RUN |
| UAT-M04-001 | M04 | REQ-M04-001 | 可能命中建立受限案件 | NOT_RUN |
| UAT-M04-002 | M04 | REQ-M04-002 | LLM 無權解除命中 | NOT_RUN |
| UAT-M04-003 | M04 | REQ-M04-003 | 一般業務不可讀 AML 案情 | NOT_RUN |
| UAT-M04-004 | M04 | REQ-M04-004 | 名單 provider timeout 不視為無命中 | NOT_RUN |
| UAT-M04-005 | M04 | REQ-M04-005 | 誤命中解除需核定與證據 | NOT_RUN |
| UAT-M04-006 | M04 | REQ-M04-006 | 名單版本更新可再查 | NOT_RUN |
| UAT-M04-007 | M04 | REQ-M04-007 | 宣告簽名不能替代查核 | NOT_RUN |
| UAT-M04-008 | M04 | REQ-M04-008 | 受限事件只輸出安全摘要 | NOT_RUN |
| UAT-M04-009 | M04 | REQ-M04-009 | EDD 補件可追蹤且不洩密 | NOT_RUN |
| UAT-M04-010 | M04 | REQ-M04-010 | 內稽存取有目的與時限 | NOT_RUN |
| UAT-M04-011 | M04 | REQ-M04-011 | 未結案件受 legal hold | NOT_RUN |
| UAT-M04-012 | M04 | REQ-M04-012 | 過期clearance不能被公司接受重用 | NOT_RUN |
| UAT-M05-001 | M05 | REQ-M05-001 | Persona 可自主建立候選 | NOT_RUN |
| UAT-M05-002 | M05 | REQ-M05-002 | 分析師發起成果走同一通道 | NOT_RUN |
| UAT-M05-003 | M05 | REQ-M05-003 | 候選重送只建一覆核案件 | NOT_RUN |
| UAT-M05-004 | M05 | REQ-M05-004 | 相同版本不同hash進隔離 | NOT_RUN |
| UAT-M05-005 | M05 | REQ-M05-005 | 缺少必要證據不可ready | NOT_RUN |
| UAT-M05-006 | M05 | REQ-M05-006 | 外部策略approved不當正式投顧核准 | NOT_RUN |
| UAT-M05-007 | M05 | REQ-M05-007 | 研究時間不得倒填交易時間 | NOT_RUN |
| UAT-M05-008 | M05 | REQ-M05-008 | 未知schema版本明確拒絕 | NOT_RUN |
| UAT-M05-009 | M05 | REQ-M05-009 | 外部證據URL阻擋SSRF | NOT_RUN |
| UAT-M05-010 | M05 | REQ-M05-010 | 授權不足的來源不可客戶發布 | NOT_RUN |
| UAT-M05-011 | M05 | REQ-M05-011 | 新候選版本通知舊review過時 | NOT_RUN |
| UAT-M05-012 | M05 | REQ-M05-012 | Pantheon故障不丟棄候選 | NOT_RUN |
| UAT-M06-001 | M06 | REQ-M06-001 | 合法候選自動進分析師佇列 | NOT_RUN |
| UAT-M06-002 | M06 | REQ-M06-002 | 修正請求綁定確切版本 | NOT_RUN |
| UAT-M06-003 | M06 | REQ-M06-003 | 回傳修訂只建立新版本 | NOT_RUN |
| UAT-M06-004 | M06 | REQ-M06-004 | 舊版本操作遇衝突重載 | NOT_RUN |
| UAT-M06-005 | M06 | REQ-M06-005 | Servant不能代簽或直接發布 | NOT_RUN |
| UAT-M06-006 | M06 | REQ-M06-006 | 對話scope不得跨客戶或tenant | NOT_RUN |
| UAT-M06-007 | M06 | REQ-M06-007 | 外部timeout不重開多任務 | NOT_RUN |
| UAT-M06-008 | M06 | REQ-M06-008 | 新來源到達時標示review過時 | NOT_RUN |
| UAT-M06-009 | M06 | REQ-M06-009 | 修訂錯誤可重送並保留證據 | NOT_RUN |
| UAT-M06-010 | M06 | REQ-M06-010 | 來源故障不產生虛假空研究 | NOT_RUN |
| UAT-M06-011 | M06 | REQ-M06-011 | 關鍵數字變動需可見差異 | NOT_RUN |
| UAT-M06-012 | M06 | REQ-M06-012 | 研究對話不夾帶KYC與AML | NOT_RUN |
| UAT-M07-001 | M07 | REQ-M07-001 | 缺必要覆核不能送簽 | NOT_RUN |
| UAT-M07-002 | M07 | REQ-M07-002 | 凍結包包含報告與訊息範圍 | NOT_RUN |
| UAT-M07-003 | M07 | REQ-M07-003 | 無有效資格者不能簽署 | NOT_RUN |
| UAT-M07-004 | M07 | REQ-M07-004 | 簽章回呼只在完整驗證後觸發事件 | NOT_RUN |
| UAT-M07-005 | M07 | REQ-M07-005 | 簽後修改建立新版本 | NOT_RUN |
| UAT-M07-006 | M07 | REQ-M07-006 | 內容與簽章檔hash分開 | NOT_RUN |
| UAT-M07-007 | M07 | REQ-M07-007 | 資料過時阻擋簽章 | NOT_RUN |
| UAT-M07-008 | M07 | REQ-M07-008 | provider重送不重複發布 | NOT_RUN |
| UAT-M07-009 | M07 | REQ-M07-009 | 簽章payload替換遭拒 | NOT_RUN |
| UAT-M07-010 | M07 | REQ-M07-010 | 撤銷保留歷史並通知發布服務 | NOT_RUN |
| UAT-M07-011 | M07 | REQ-M07-011 | 簽署者知悉簽後自動發布 | NOT_RUN |
| UAT-M07-012 | M07 | REQ-M07-012 | 多次對同版覆核保留完整歷史 | NOT_RUN |
| UAT-M08-001 | M08 | REQ-M08-001 | 驗證簽章後無第二發布操作 | NOT_RUN |
| UAT-M08-002 | M08 | REQ-M08-002 | 候選或未簽稿不可出現在feed | NOT_RUN |
| UAT-M08-003 | M08 | REQ-M08-003 | 每一進出場訊息有對應報告 | NOT_RUN |
| UAT-M08-004 | M08 | REQ-M08-004 | 一份有效報告可支持多則核定訊息 | NOT_RUN |
| UAT-M08-005 | M08 | REQ-M08-005 | 簽後實質文案變動不可偷渡 | NOT_RUN |
| UAT-M08-006 | M08 | REQ-M08-006 | 受眾快照排除無權限者 | NOT_RUN |
| UAT-M08-007 | M08 | REQ-M08-007 | 快照後停權仍不得送出 | NOT_RUN |
| UAT-M08-008 | M08 | REQ-M08-008 | 重複簽章事件僅發布一次 | NOT_RUN |
| UAT-M08-009 | M08 | REQ-M08-009 | 撤回不抹除已發布證據 | NOT_RUN |
| UAT-M08-010 | M08 | REQ-M08-010 | 更正指向原發布與新簽版本 | NOT_RUN |
| UAT-M08-011 | M08 | REQ-M08-011 | 延遲重送過期建議遭攔截 | NOT_RUN |
| UAT-M08-012 | M08 | REQ-M08-012 | 來源trade和客戶建議時間分列 | NOT_RUN |
| UAT-M09-001 | M09 | REQ-M09-001 | LegendFlow feed只顯示本人權限內容 | NOT_RUN |
| UAT-M09-002 | M09 | REQ-M09-002 | 報告下載授權需當前有效 | NOT_RUN |
| UAT-M09-003 | M09 | REQ-M09-003 | 未驗證通路不能綁成active | NOT_RUN |
| UAT-M09-004 | M09 | REQ-M09-004 | 發送前再查權限 | NOT_RUN |
| UAT-M09-005 | M09 | REQ-M09-005 | accepted不誤顯示已送達 | NOT_RUN |
| UAT-M09-006 | M09 | REQ-M09-006 | 未知已讀維持unknown | NOT_RUN |
| UAT-M09-007 | M09 | REQ-M09-007 | timeout先查結果避免重複 | NOT_RUN |
| UAT-M09-008 | M09 | REQ-M09-008 | 亂序receipt不倒退 | NOT_RUN |
| UAT-M09-009 | M09 | REQ-M09-009 | 過期DLQ不得重送舊建議 | NOT_RUN |
| UAT-M09-010 | M09 | REQ-M09-010 | 撤回／更正保留原文 | NOT_RUN |
| UAT-M09-011 | M09 | REQ-M09-011 | 通路偏好與必要服務分類分開 | NOT_RUN |
| UAT-M09-012 | M09 | REQ-M09-012 | provider webhook驗證與重放防護 | NOT_RUN |
| UAT-M10-001 | M10 | REQ-M10-001 | 未完成申辦產生可執行待辦 | NOT_RUN |
| UAT-M10-002 | M10 | REQ-M10-002 | 自助續約不計業務轉換 | NOT_RUN |
| UAT-M10-003 | M10 | REQ-M10-003 | 業務續約需事前活動和交易 | NOT_RUN |
| UAT-M10-004 | M10 | REQ-M10-004 | 空白聯絡或自動訊息不能搶獎金 | NOT_RUN |
| UAT-M10-005 | M10 | REQ-M10-005 | 沒有讀研究報告不單獨判流失 | NOT_RUN |
| UAT-M10-006 | M10 | REQ-M10-006 | 禁止聯絡或未結抱怨抑制銷售 | NOT_RUN |
| UAT-M10-007 | M10 | REQ-M10-007 | 升級機會須服務適合度允許 | NOT_RUN |
| UAT-M10-008 | M10 | REQ-M10-008 | 四種owner不互相覆寫 | NOT_RUN |
| UAT-M10-009 | M10 | REQ-M10-009 | 爭議認領不能重複計獎 | NOT_RUN |
| UAT-M10-010 | M10 | REQ-M10-010 | 轉換後補寫活動不能回填搶獎 | NOT_RUN |
| UAT-M10-011 | M10 | REQ-M10-011 | 漏斗統計重放與退出正確 | NOT_RUN |
| UAT-M10-012 | M10 | REQ-M10-012 | 同一待辦不因日批次無限重複 | NOT_RUN |
| UAT-M11-001 | M11 | REQ-M11-001 | 自助客戶無維護人也能續訂 | NOT_RUN |
| UAT-M11-002 | M11 | REQ-M11-002 | 離職立即回公司不等七天 | NOT_RUN |
| UAT-M11-003 | M11 | REQ-M11-003 | 新指派不是新獎金 | NOT_RUN |
| UAT-M11-004 | M11 | REQ-M11-004 | 新業務實際再開發才可領後續獎金 | NOT_RUN |
| UAT-M11-005 | M11 | REQ-M11-005 | 七日規則不是七天沒聯絡 | NOT_RUN |
| UAT-M11-006 | M11 | REQ-M11-006 | 全部服務中斷七日才符合提案 | NOT_RUN |
| UAT-M11-007 | M11 | REQ-M11-007 | 單一月訂到期不抵銷年度有效服務 | NOT_RUN |
| UAT-M11-008 | M11 | REQ-M11-008 | 未核定七日定義禁止自動回收 | NOT_RUN |
| UAT-M11-009 | M11 | REQ-M11-009 | 法遵停權不被當可銷售流失名單 | NOT_RUN |
| UAT-M11-010 | M11 | REQ-M11-010 | 兩人同時認領只有一人成功 | NOT_RUN |
| UAT-M11-011 | M11 | REQ-M11-011 | 歷史佣金不因移交改歸屬 | NOT_RUN |
| UAT-M11-012 | M11 | REQ-M11-012 | 服務恢復可中止尚未成立中斷條件 | NOT_RUN |
| UAT-M12-001 | M12 | REQ-M12-001 | 未核定基準率不能正式計獎 | NOT_RUN |
| UAT-M12-002 | M12 | REQ-M12-002 | 自助續訂無業務份額 | NOT_RUN |
| UAT-M12-003 | M12 | REQ-M12-003 | 第一二年係數按客戶cohort | NOT_RUN |
| UAT-M12-004 | M12 | REQ-M12-004 | 第三年尾佣需持續自動續約 | NOT_RUN |
| UAT-M12-005 | M12 | REQ-M12-005 | 業務介入30/70需有核定證據 | NOT_RUN |
| UAT-M12-006 | M12 | REQ-M12-006 | 衰減兩算法不能混用 | NOT_RUN |
| UAT-M12-007 | M12 | REQ-M12-007 | 退款反向原受款人原政策 | NOT_RUN |
| UAT-M12-008 | M12 | REQ-M12-008 | 預收未履約退款不能重扣收入 | NOT_RUN |
| UAT-M12-009 | M12 | REQ-M12-009 | 退款重送只調整一次 | NOT_RUN |
| UAT-M12-010 | M12 | REQ-M12-010 | 金額分配總數守恆 | NOT_RUN |
| UAT-M12-011 | M12 | REQ-M12-011 | 製作人不可自核且發放須回執 | NOT_RUN |
| UAT-M12-012 | M12 | REQ-M12-012 | 已付退款不得自動扣薪 | NOT_RUN |
| UAT-M13-001 | M13 | REQ-M13-001 | 同案跨通路持續追蹤 | NOT_RUN |
| UAT-M13-002 | M13 | REQ-M13-002 | 外部訊息重送只建一互動 | NOT_RUN |
| UAT-M13-003 | M13 | REQ-M13-003 | 未驗證身份不得看到契約細節 | NOT_RUN |
| UAT-M13-004 | M13 | REQ-M13-004 | AI草稿須核定發送且狀態真實 | NOT_RUN |
| UAT-M13-005 | M13 | REQ-M13-005 | 投資問題需分析師與報告依據 | NOT_RUN |
| UAT-M13-006 | M13 | REQ-M13-006 | 客服退款申請不能直接出金 | NOT_RUN |
| UAT-M13-007 | M13 | REQ-M13-007 | 重大申訴抑制不當促銷 | NOT_RUN |
| UAT-M13-008 | M13 | REQ-M13-008 | 內部等待不任意停SLA | NOT_RUN |
| UAT-M13-009 | M13 | REQ-M13-009 | AML移交不複製案情 | NOT_RUN |
| UAT-M13-010 | M13 | REQ-M13-010 | 結案必須有結果與通知 | NOT_RUN |
| UAT-M13-011 | M13 | REQ-M13-011 | 重開案件保留原歷史 | NOT_RUN |
| UAT-M13-012 | M13 | REQ-M13-012 | 客服owner不自動成業務獎金人 | NOT_RUN |
| UAT-M14-001 | M14 | REQ-M14-001 | 付款回呼重送只入帳一次 | NOT_RUN |
| UAT-M14-002 | M14 | REQ-M14-002 | 預收款與已履約收入分開 | NOT_RUN |
| UAT-M14-003 | M14 | REQ-M14-003 | 結算費用與收入不同欄 | NOT_RUN |
| UAT-M14-004 | M14 | REQ-M14-004 | 稅額使用核定配置不猜除數 | NOT_RUN |
| UAT-M14-005 | M14 | REQ-M14-005 | 退款製作核准執行分權 | NOT_RUN |
| UAT-M14-006 | M14 | REQ-M14-006 | 退款timeout先查provider | NOT_RUN |
| UAT-M14-007 | M14 | REQ-M14-007 | 退款拆分預收與已認列部分 | NOT_RUN |
| UAT-M14-008 | M14 | REQ-M14-008 | 會計分錄必須平衡 | NOT_RUN |
| UAT-M14-009 | M14 | REQ-M14-009 | 會計匯出重送有批次收據 | NOT_RUN |
| UAT-M14-010 | M14 | REQ-M14-010 | 完整稽核鏈可由訊息回查 | NOT_RUN |
| UAT-M14-011 | M14 | REQ-M14-011 | legal hold阻止到期刪除且解除受控 | NOT_RUN |
| UAT-M14-012 | M14 | REQ-M14-012 | 備份還原重放不重複金流 | NOT_RUN |

## 執行順序與平行方式

十四個模組案例可在各自worktree／測試schema同時實作和執行。跨域E2E可先用provider fixture驗證消費端，之後用真實模組與外部sandbox再跑；兩種結果分開記錄。先驗契約與測試資料、再跑本域驗收、再做跨域切換及故障復原，是驗證依賴，不是把模組開發改成逐一等待。

## 完成與停止準則

只有十四模組所有必要案例及十四個E2E都通過，關鍵權限／金流／簽章／資料隔離缺陷關閉，本平台adapter／mock行為及生產政策gate有證據，才可聲稱本次範圍的整體開發驗收完成。外部NOT_CONNECTED保留為接線狀態，不能要求修改外部專案才讓本地完工。不能因畫面能點、mock單純回200、文件已存在或部分流程成功就關閉所有issues。發現不符合已定義契約的缺陷時修復受影響範圍並重驗相關案例；不為追求測試數量做無關重跑。

## 驗收結果範本

```json
{
  "case_id": "UAT-M07-004",
  "commit": "<實際commit>",
  "environment": "integration-synthetic",
  "actor_role": "analyst",
  "policy_version": "<實際核定或test-only版本>",
  "status": "NOT_RUN",
  "actual_result": null,
  "executed_at": null,
  "evidence_refs": []
}
```

結果填入時不得將真實個資、AML原文、token、private key或正式付款資料放進repository；受控證據保留在公司授權儲存，repo只留安全reference與雜湊。
