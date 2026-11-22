# M14 HerdR 任務包 · 帳務、對帳、退款與內控證據

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M14 / `finance_control` |
| Issue | [#14](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/14) |
| 分支 | `module/m14-finance_control` |
| Worktree | `../longhao-worktrees/m14` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

連接契約、金流、結算、預收、收入、退款與會計；保留可重演的計獎基礎及稽核證據。每筆金流有對帳狀態，製作、核准與執行分權，帳務不得靠LLM猜測。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M14-finance_control.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/finance_control/`
- `apps/admin/src/features/finance_control/`
- `workers/tasks/finance_control/`
- `tests/modules/finance_control/`
- `database/proposals/M14/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M14/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m14 -b module/m14-finance_control origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M14-01：收款與三方對帳。建立invoice/payment intent；金流回呼落inbox；每日取得provider settlement與bank參考，核對gross、fee、tax、refund、net與currency；未對齊列差異，pending prepayment不直接當收入。
- [ ] WORK-M14-02：服務收入與計獎基礎。依契約服務期間、履約內容與核定會計政策產生revenue schedule；記錄recognized amount與eligible commission basis；獎金只消費已確定的untaxed service revenue及refund allocation。稅別／稅率／未稅判斷由財會設定，不能預設除1.05或1.02。
- [ ] WORK-M14-03：退款與會計整合。按適用條款及已提供服務計算退款，預收退還與已認列收入沖回分別allocation；不同人批准後以idempotent命令執行；成功後append journal與commission adjustment event；匯出按batch與receipt對帳。
- [ ] WORK-M14-04：內控稽核與復原。查客戶→契約→審核→權限→訊息→報告→簽章→外部證據，以及payment→revenue→commission鏈；可輸出manifest/hash與審計；保全、權限變更、恢復演練及控制例外有責任人和結案證據。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_invoice`：依契約、稅務代碼與服務期建立應收；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_payment_intent`：建立支付意圖；簽約前後款項保持獨立狀態；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_payment_webhook`：回呼驗簽與交易去重；不直接認列收入；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `import_settlement`：匯入金流／銀行對帳檔與控制總數；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_reconciliation_items`：列示短溢付、重複、手續費與未知交易；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `resolve_reconciliation_item`：覆核有差異對帳項目；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `run_revenue_recognition`：依核准服務期攤認收入並產生獎金基礎事件；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `request_refund`：區分未履約預收退回與已認列收入沖回；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `approve_refund`：退款金額／原付款／收入分攤由異人核准；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `execute_refund`：受控執行核准退款，timeout 先查帳再重送；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_refund_webhook`：退款供應商確認及帳務沖回；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_accounting_export`：輸出平衡借貸、來源索引與會計映射版本；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `acknowledge_accounting_export`：會計系統回覆成功／部分失敗，禁止重複過帳；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_audit_events`：依權限查核系統與作業稽核紀錄；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_evidence_export`：重建客戶或發布完整證據鏈並留匯出存取紀錄；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_legal_hold`：受控保全阻止自動刪除；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `record_control_exception`：可追蹤內控缺失、改善人、期限與覆核；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_revenue_run`：檢視收入攤認批次與控制總額；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `approve_revenue_run`：異人核准攤認批次，核准後才能過帳及產生獎金基礎；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_refund`：查詢退款、對帳與收入沖回進度；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_accounting_export`：查詢會計匯出hash與部分接收回覆；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_evidence_export`：查詢稽核匯出與到期票證流程；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `release_legal_hold`：依權威解除證據保全，不能由排程自行解除；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `act_on_control_exception`：改善承辦與獨立稽核覆核保留歷程；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

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

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

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

- payment callback驗簽、時間窗、去重與狀態偏序；同id不同金額／幣別為高優先差異。
- settlement fee不能直接當收入退款；gross、fee、tax、net各欄位分開，會計mapping版本化。
- provider已退款但回呼遺失：status query/reconciliation回復，不二次出金。
- 備援還原後outbox/inbox與金流idempotency必須同時保留；replay不得重送扣款／退款。
- 稽核保留期限按記錄類型與觸發時點設定；legal hold優先於排程刪除。

## 8. 必須交付的驗收證據

- [ ] `UAT-M14-001`：付款回呼重送只入帳一次。通過條件：一payment transition、一財務事件；不重複增加預收或收入
- [ ] `UAT-M14-002`：預收款與已履約收入分開。通過條件：payment captured且prepayment liability增加，revenue=0；無計獎eligible收入
- [ ] `UAT-M14-003`：結算費用與收入不同欄。通過條件：gross/fee/net分列且相符；不能把收入直接當970或refund30
- [ ] `UAT-M14-004`：稅額使用核定配置不猜除數。通過條件：進配置例外，不能默認除1.05或1.02；有核定mapping後可重算
- [ ] `UAT-M14-005`：退款製作核准執行分權。通過條件：SoD拒絕；合法checker核准後受限service才可執行
- [ ] `UAT-M14-006`：退款timeout先查provider。通過條件：先查狀態或同key；只有一筆對外退款，保留attempt receipts
- [ ] `UAT-M14-007`：退款拆分預收與已認列部分。通過條件：unearned refund與earned revenue reversal分列；只有已計獎基礎部分通知M12反向
- [ ] `UAT-M14-008`：會計分錄必須平衡。通過條件：拒絕unbalanced，無會計成功標記；修正形成可追溯版本或反向分錄
- [ ] `UAT-M14-009`：會計匯出重送有批次收據。通過條件：使用同external id查/重試；不重複記帳；ack與rejected distinct
- [ ] `UAT-M14-010`：完整稽核鏈可由訊息回查。通過條件：manifest含各immutable refs/hash與實際時點；受限資料依role遮罩且access audit
- [ ] `UAT-M14-011`：legal hold阻止到期刪除且解除受控。通過條件：有效hold時skip且記原因；未授權解除被拒；合法解除追加紀錄且保留原hold與audit，不直接刪證據
- [ ] `UAT-M14-012`：備份還原重放不重複金流。通過條件：無第二扣款／退款；pending可續跑；恢復演練留證據與差異清單

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/finance_control -q
```

第二個命令是實作agent必須交付的驗證入口，不代表此文件包已有應用程式或該測試已通過。SQL migration驗證按[資料庫README](../../database/README.md)，前端lint/typecheck/UI測試按實際專案工具設定；不能捏造尚不存在的pnpm script。必要測試覆蓋上述業務不變量、權限與失敗恢復，不為了數量複製實作邏輯當測試。

## 10. 合併與完成回報

本模組可獨立開發、可持續合併符合契約的部分，但issue維持open直到全部範圍完成。所有14模組與完整跨域UAT達標才宣告整體完成，不把某條旅程先可用當成全案完成。

提交回報必須含：

- changed files與本模組所有REQ完成狀態；未完成者不得省略。
- commit／PR、API及事件契約版本、migration proposal與版本衝突處理。
- 真正執行的驗證命令、結果、UAT evidence refs、失敗及NOT_RUN項目。
- 外部adapter哪些mock、哪些sandbox驗證；未核定政策是否確實被生產gate阻擋。
- 整合風險、需協調者合併的共用契約變更與操作runbook位置。
