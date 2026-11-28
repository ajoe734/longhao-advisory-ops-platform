# 18｜業務與工程決策紀錄

## 1. 使用者已確認

| ID | 已確認內容 | 落點 |
|---|---|---|
| USER-01 | 十四模組全部做完、最大程度平行開發 | scope、AGENTS、parallel plan、14packets |
| USER-02 | LegendFlow客戶UI，Pantheon研究，管理平台為本案核心 | 三方邊界與adapters |
| USER-03 | 先客戶簽署，後KYC/適合度/AML，公司同意後開通 | M02–M04、獨立時間/狀態 |
| USER-04 | Persona可全程產候選，分析師最後修訂覆核簽章 | M05–M07 |
| USER-05 | 分析師簽章核驗後即自動發布 | M07–M09、report.signature_verified |
| USER-06 | 進出/更新訊息重點在對到研究依據 | recommendation series、report links |
| USER-07 | CRM要有續訂、升級、留存的具體業務行動 | M10、source/owner/conversion分離 |
| USER-08 | 自助客戶無業務即行銷/公司；業務離職回公司，新開發者獲後續業務獎金 | M11/M12 |
| USER-09 | 行銷100/50/第三年連續auto25；業務介入30/70；實際未稅服務收入淨退款 | M12與財會來源 |
| USER-10 | 不得修改Pantheon、LegendFlow兩外部專案 | 根AGENTS、adapters、所有packets |

## 2. 可繼續實作、正式啟用前需定案

| ID | 問題／目前明確建議 | 決定owner | 不中斷開發的方法 | 正式啟用門檻 |
|---|---|---|---|---|
| BIZ-CON-01 | 公司承諾/成立/生效條款及先收款文案；p5/p9修訂草案 | 公司法遵/治理 | 狀態與時間分開、versioned模板 | 正式契約/制度版本核定 |
| BIZ-POOL-01 | 斷掉一週解讀為全部付費coverage中斷168h；不是七天沒聯絡 | 營運/業務主管 | 可配置策略、simulation及離職立即移轉 | auto_pool policy approved |
| BIZ-COM-01 | 獎金池總rate未指定，3%僅曾提案 | 經營/財會 | null＋A/B試算fixture | rate與生效日期核定 |
| BIZ-COM-02 | 先30/70後只衰減行銷或整池衰減 | 經營/財會/業務 | 同時支援兩strategy、成本比較 | mode明確選定 |
| BIZ-COM-03 | 業務介入後的未來auto尾隨資格、重啟是否終止 | 經營/行銷/業務 | service lineage＋兩種scenario | 有版本的eligibility規則 |
| BIZ-COM-04 | cohort週年、歸因期限、多人轉換爭議、品質保留/扣回 | 經營/人事/法遵 | 策略參數＋可解釋case review | 制度附件核定 |
| BIZ-FIN-01 | 含稅/未稅定義、憑證、收入期間、科目與四捨五入 | 財會 | 多稅型/服務期fixtures；不得硬除1.05或1.02 | GL/tax/recognition policy approved |
| BIZ-KYC-01 | 問卷版本、服務適合度、refresh及法人受理範圍 | 法遵/審查主管 | versioned問卷與criteria interface | 正式問卷/審查策略核定 |
| BIZ-AML-01 | 名單provider、風險門檻、EDD、監測/申報流程 | AML主管 | restricted adapter fake與例外流程 | provider＋policy＋職責核定 |
| BIZ-RES-01 | 各報告必要覆核、有效期限、Persona pool衝突政策 | 投研/法遵 | policy gates、候選來源分類 | report review policy approved |
| BIZ-SIG-01 | 客戶/分析師簽署證據方式與供應商 | 法遵/資訊 | provider port＋challenge/hash/receipt tests | 方法/供應商驗證與正式設定 |
| OPS-01 | 容量、SLO/RPO/RTO與主機/預算 | 資訊/營運 | NFR文件的工程假設壓測 | 測試證據與採購設定 |
| OPS-02 | 各類保存與銷毀schedule | 法遵/財會/資安 | metadata/hold/cleanup dry-run | schedule核定後才啟刪除 |
| EXT-01 | Pantheon/Agora提供的實際介面與payload | 對接窗口 | 僅本repoadapter/mock/contract | 可取得時本平台sandbox驗證 |
| EXT-02 | LegendFlow身份/session/feed整合 | 對接窗口 | 同上，不改其repo | 可取得時接線驗證 |
| EXT-03 | 金流/訊息/銀行/會計/IdP實際供應商 | 對接窗口 | ports/fakes與mapping | 正式keys和契約確認 |

這些未定案不要求使用者現在逐項回答才繼續開發；本文件把邊界和影響具體化，使十四模組都能實作。正式支付/公司承諾/監管決策等動作在對應設定未核定時fail closed。

## 3. 工程選定基線

Python3.12、FastAPI、React/TypeScript、PostgreSQL16、私有加密object storage、modular monolith、durable worker、transactional outbox/inbox、module ownership、API/event契約先定、十四worktree。這是本平台技術設計；外部專案不必採相同技術。

### ENG-CONTRACT-01｜M14 發票明細識別碼、會計期間與共用契約漂移（2026-11-28，修訂）

`InvoiceLineCreate` 保持建立帳單的 request schema，不要求 client 提供資料庫識別碼；建立或讀取帳單的 `Invoice.lines[]` 必須由 M14 runtime 回傳既有 `finance.invoice_line.id` 為 `invoice_line_id`。退款的 `allocations[].invoice_line_ref` 只能引用同一 tenant、且屬於 `original_payment` 所對應發票的明細；不得以 `contract_service_id` 或任意 UUID 替代。`AccountingExport` 的 M14 runtime 亦必須在建立、確認回覆及讀取回覆中回傳實際 `period_start`、`period_end`。這些是帳務稽核所需的工程契約，並不核定含稅／未稅、GL、認列期間或退款核准等財會業務政策。

目前不可把 `api/build_contracts.py` 視為可安全重生產的唯一來源。它自 `9020b52` 後未更新；其第 454–456、557、564、566、568 行會覆寫 OpenAPI、catalog、schema index、event schemas/examples、registry 及 fixtures。相對地，`f61ba27`、`04bfafc`、`bc570e2`、`28b67e7`、`dbc5332`、`91cb423` 已直接擴充 `api/openapi.yaml`，其中 M01 另擴充 event registry。此刻 OpenAPI 有 152 operations／204 schemas，舊 catalog/index 僅有 142／190；故不得為 M14 執行該 generator，亦不得以只含 M14 的 generator 輸入覆寫已合併的 GAP 契約。

在後述 generator reconciliation 完成前，採用明確、向下相容的暫時規則：`InvoiceLine.invoice_line_id` 與 `AccountingExport.period_start`／`period_end` 必須保留為 OpenAPI response properties，但**不得**列在 shared OpenAPI 的 `required` 清單。這只允許歷史 synthetic fixture 暫時省略三欄；不放寬 M14 runtime，runtime 與其 module tests 仍必須發出並驗證全部三欄。這個 additive compatibility cut 與先前已直接加入 OpenAPI 的 GAP-M02／GAP-M01 契約方式一致，且不會覆寫 catalog、schema index、fixture 或 event registry。

`GAP-M14-FINANCE-READS` 的具體 unblocked next step 是：在既有 M14 branch 的 `api/openapi.yaml`，僅將上述三個 response 欄位由 `required` 改為 properties-only，保留四個新增讀取 operations／page schemas（`list_refunds`、`list_accounting_exports`、`list_revenue_runs`、`list_period_closes`）及既有角色、tenant、cursor/period 限制；不要執行或修改 `api/build_contracts.py`、catalog、schema index、fixtures、events。接著執行 `python3 scripts/validate_contracts.py` 與 `python3 -m pytest tests/modules/finance_control -q`，並以新 SHA 交審。此 scope 不新增 migration、不改外部系統、也不變更付款／退款規則。

integration coordinator 必須在任何下一次 generator invocation 前，於 machine-truth board 建立並指派一個 API schema steward follow-up，暫稱 `GAP-API-CONTRACT-GENERATOR-RECONCILIATION`。其範圍是把上述六個 GAP PR 與 M14 的 operation/schema/event-registry 變更收斂回 generator inputs，重生產並逐項比較所有受寫入檔案，再移除本段暫時相容例外；該後續工作不是 M14 parent 的阻塞前提。

## 4. 變更紀錄方式

新決策包含request、理由、owner、影響module/API/schema/UAT、舊新行為與生效日期。已簽/已送/已關帳歷史依原版本保存。業務政策變更不能混在一般bugfix默默改既有計算；外部project修改永遠不在本任務範圍。
