# M06 HerdR 任務包 · 研究覆核工作台與 Agora／Servant 協作

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M06 / `agora_review` |
| Issue | [#6](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/6) |
| 分支 | `module/m06-agora_review` |
| Worktree | `../longhao-worktrees/m06` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

讓分析師在候選佇列閱讀證據、要求 Servant 修改或補證，接受可追溯的新版本後再進正式報告與簽章。Servant 是外部研究協作入口，不能代替分析師簽章。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M06-agora_review.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/agora_review/`
- `apps/admin/src/features/agora_review/`
- `workers/tasks/agora_review/`
- `tests/modules/agora_review/`
- `database/proposals/M06/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M06/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m06 -b module/m06-agora_review origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M06-01：接案與初審。候選ready自動形成review case；依專長／資格／負載排序與人工指派；分析師看原件、來源時點、風險與可用證據，作接受初稿、要求修正或退回。
- [ ] WORK-M06-02：Servant修正循環。建立有scope的conversation與revision request，明確指出需修改的結論／風險／計算／證據；以expected source version送外部；回傳artifact先驗證再顯示diff，分析師選接受或再修改。
- [ ] WORK-M06-03：版本一致後交付。專業審查須指向確切候選／修訂；若新來源在review期間到達，顯示過時並要求重評；完成後把內容與證據交M07建立正式報告版本，不能複製對話成已簽報告。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `list_review_cases`：研究覆核工作佇列；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_review_case`：最新候選、修改請求及審查狀態；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `assign_review_case`：分派具資格覆核人員；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `open_servant_conversation`：啟動或續接 scoped Agora Servant 對話；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `request_servant_revision`：送出有版本前提的報告修改要求；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_servant_revision`：接收新 artifact；過時回覆不覆寫審查版；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_revision_request`：查詢Servant非同步修訂進度及版本；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| review.revision_requested | 發出／接收 | M06 | M06 | research_confidential | 1.0.0 |
| review.revision_received | 發出 | M06 | M07 | research_confidential | 1.0.0 |
| research.candidate_validated | ／接收 | M05 | M06 | research_confidential | 1.0.0 |
| research.candidate_superseded | ／接收 | M05 | M06,M07 | research_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| review.case_file | 本模組權威寫入 | 分析師覆核工作項；非交易治理批准。 | candidate_id | internal | research |
| review.conversation | 本模組權威寫入 | Agora Servant 對話參照；不複製所有 Pantheon 知識。 | provider,external_conversation_id | internal | research |
| review.revision_request | 本模組權威寫入 | 分析師提出修訂並綁精確輸入版本。 | tenant_id,id | internal | research |
| review.revision_result | 本模組權威寫入 | 修訂產物不可變，新稿不覆寫已審稿。 | request_id,revision_no | internal | research |
| research.candidate | 唯讀／port，寫入由M05 | Pantheon Persona 自主研究或人員研究候選輸入；非正式對客報告。 | producer,external_candidate_id,external_version | internal | research |

- 外部對話啟動timeout：查request status並以相同idempotency key重試，避免重開多個thread。
- revision callback 指向別的case/tenant：拒絕並security audit，不讓前端自行選case。
- 對話含prompt injection要求越權：資料當不可信研究內容，allowlisted tools無簽章或付款能力。
- 外部任務cancel未確認：UI顯示cancel_requested，不宣稱已終止。

## 8. 必須交付的驗收證據

- [ ] `UAT-M06-001`：合法候選自動進分析師佇列。通過條件：建立一case並保留來源版本；分析師可接案而不需重新發起研究
- [ ] `UAT-M06-002`：修正請求綁定確切版本。通過條件：記錄v3/hash、scope、request id與接受條件；外部回呼可對應原請求
- [ ] `UAT-M06-003`：回傳修訂只建立新版本。通過條件：保留v3、建立v4 revision result與差異；舊review不視為批准v4
- [ ] `UAT-M06-004`：舊版本操作遇衝突重載。通過條件：412；UI顯示新的candidate version，不覆蓋更新
- [ ] `UAT-M06-005`：Servant不能代簽或直接發布。通過條件：403，無signature或publication事件
- [ ] `UAT-M06-006`：對話scope不得跨客戶或tenant。通過條件：拒絕scope violation；A畫面不顯示B內容
- [ ] `UAT-M06-007`：外部timeout不重開多任務。通過條件：以原request id查狀態或同key重送；只有一外部revision job
- [ ] `UAT-M06-008`：新來源到達時標示review過時。通過條件：case標stale並撤銷基於v3的ready標記；不自動把評論當v4覆核
- [ ] `UAT-M06-009`：修訂錯誤可重送並保留證據。通過條件：新attempt連原request且原失敗保留；不修改舊artifact
- [ ] `UAT-M06-010`：來源故障不產生虛假空研究。通過條件：顯示degraded及最後成功狀態；不能顯示無修訂或已完成
- [ ] `UAT-M06-011`：關鍵數字變動需可見差異。通過條件：重大差異列出且待人工接受；不默默替換預覽內容
- [ ] `UAT-M06-012`：研究對話不夾帶KYC與AML。通過條件：只傳研究scope與服務語氣等最小資料；任何AML受限內容被阻擋

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/agora_review -q
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
