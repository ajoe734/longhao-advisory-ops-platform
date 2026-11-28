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

### ENG-CONTRACT-01｜M14 發票明細識別碼與共用契約產物（2026-11-28）

`InvoiceLineCreate` 保持建立帳單的 request schema，不要求 client 提供資料庫識別碼；建立或讀取帳單的 `Invoice.lines[]` 則必須回傳既有 `finance.invoice_line.id` 為 `invoice_line_id`。退款的 `allocations[].invoice_line_ref` 只能引用同一 tenant、且屬於 `original_payment` 所對應發票的明細；不得以 `contract_service_id` 或任意 UUID 替代。這是 M14 帳單與退款可稽核對應的工程契約決定，不涉及含稅／未稅、GL、認列期間或退款核准等尚待財會核定的業務政策。

`api/build_contracts.py` 是 `api/openapi.yaml`、`api/operation-catalog.json`、`api/schema-index.json` 與 `mocks/fixtures.json` 的共同產生來源；任何上述 schema 的語意變更，API schema steward 必須先更新產生器，再以同一變更集重生產全部四個產物並執行 `python3 scripts/validate_contracts.py`。M14 模組不得只手改其中一個生成檔，也不得把缺少 `invoice_line_id` 的 shared fixture 視為可接受的暫時相容方案。

此路由的最小後續範圍是 `api/build_contracts.py` 和其四個生成產物；不新增 migration、不改外部系統、也不變更 M14 的付款／退款業務規則。完成該 scope 後，`GAP-M14-FINANCE-READS` 需合併生成契約，重跑 `python3 scripts/validate_contracts.py` 與 `python3 -m pytest tests/modules/finance_control -q`，再以新 SHA 交審。

## 4. 變更紀錄方式

新決策包含request、理由、owner、影響module/API/schema/UAT、舊新行為與生效日期。已簽/已送/已關帳歷史依原版本保存。業務政策變更不能混在一般bugfix默默改既有計算；外部project修改永遠不在本任務範圍。
