# M13 HerdR 任務包 · 線上線下客服、申訴與跨部門案件

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M13 / `service_cases` |
| Issue | [#13](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/13) |
| 分支 | `module/m13-service_cases` |
| Worktree | `../longhao-worktrees/m13` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

將網站、官方訊息、電子郵件、電話與現場案件匯成同一可追蹤服務歷程；快速分流與回覆，重要投資判斷、申訴、退款與AML事項交授權流程。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M13-service_cases.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/service_cases/`
- `apps/admin/src/features/service_cases/`
- `workers/tasks/service_cases/`
- `tests/modules/service_cases/`
- `database/proposals/M13/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M13/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m13 -b module/m13-service_cases origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M13-01：跨通路受理。以provider message id、customer identity及case ref去重／對案；電話與現場由staff記錄真實時間和摘要，附件／錄音依同意與留存政策；不可只憑同電話就把敏感案件合併。
- [ ] WORK-M13-02：AI協作與部門分工。分類、摘要、檢索核定知識、提出回覆草稿和路由；低風險庶務可依核定模板自動回覆並記錄；投資判斷／抱怨／退款／AML需對應授權人或系統流程。
- [ ] WORK-M13-03：解決與留存。問題解決需附處理結果、通知證據與SLA；退費案只提出M14 request，不直接出金；報告錯誤回M07/M08更正；維護／轉換owner不因客服接案而轉移。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_service_case`：線上、電話、現場、郵件與官方訊息整併案件；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_service_cases`：客服佇列及 SLA 優先順序；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_service_case`：客服時間軸與關聯契約／研究／退款；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `add_case_interaction`：記錄跨渠道互動與原始內容證據；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `assign_service_case`：客服承辦人與業務維護人分離；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `escalate_service_case`：升級分析師、財會或法遵並限制敏感資料；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `draft_service_response`：LLM 僅使用獲准知識與既有案件資訊起草；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `resolve_service_case`：記錄解決方式及客戶告知證據；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `reopen_service_case`：客戶回覆或未解決問題重新開案；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_service_response_draft`：取回LLM草稿及引用供客服覆核；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `send_service_response`：核定精確草稿或低風險固定模板後由本平台送出客服回覆；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_service_response`：查詢客服回覆之排程、接受、送達或未知結果；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_service_response_receipt`：接收客服回覆渠道receipt，不能用outbound紀錄冒充送達；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `approve_service_response_draft`：對固定草稿做人工核定或可信固定模板規則核定，產生server approval record；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

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

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

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

- 同provider message重送：只一interaction，回原receipt。
- 找不到正確客戶：unlinked case先受理，但敏感資料不顯示給未驗證者。
- AI無支持知識：說明需人工處理並轉案，不能編造公司政策。
- SLA責任轉派不重新歸零計時；明確保留原case open時間。

## 8. 必須交付的驗收證據

- [ ] `UAT-M13-001`：同案跨通路持續追蹤。通過條件：同case timeline有兩channel及各時間；不重設SLA
- [ ] `UAT-M13-002`：外部訊息重送只建一互動。通過條件：一interaction；案件數與待辦不重複
- [ ] `UAT-M13-003`：未驗證身份不得看到契約細節。通過條件：可記unlinked case但不揭露合同／付款；先完成適當驗證
- [ ] `UAT-M13-004`：AI草稿須核定發送且狀態真實。通過條件：草稿無sent_at；送出綁精確hash與server產的approval_id，queued/accepted不當sent/delivered；重複receipt去重，unknown先查狀態
- [ ] `UAT-M13-005`：投資問題需分析師與報告依據。通過條件：轉分析師；不得即席生成對客戶的新建議；正式建議回M07/M08
- [ ] `UAT-M13-006`：客服退款申請不能直接出金。通過條件：建立M14 request與link；客服無approve/execute權限
- [ ] `UAT-M13-007`：重大申訴抑制不當促銷。通過條件：發安全suppression給M10；仍可必要服務回覆，不發催升級名單
- [ ] `UAT-M13-008`：內部等待不任意停SLA。通過條件：按policy持續計時或合規pause；不可轉派即清零；逾期告警
- [ ] `UAT-M13-009`：AML移交不複製案情。通過條件：一般case只留安全link/status；AML內容不進普通timeline或LLM context
- [ ] `UAT-M13-010`：結案必須有結果與通知。通過條件：422缺必要資訊；填妥核定結果才resolved，保留通知delivery狀態
- [ ] `UAT-M13-011`：重開案件保留原歷史。通過條件：同case新增reopen事件與時間，不刪原resolution；SLA重啟方式按policy
- [ ] `UAT-M13-012`：客服owner不自動成業務獎金人。通過條件：maintenance與conversion owner不因case owner改變；若有真實sales另走M10核定

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/service_cases -q
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
