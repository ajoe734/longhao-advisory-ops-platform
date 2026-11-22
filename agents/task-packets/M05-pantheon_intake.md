# M05 HerdR 任務包 · Pantheon 候選研究接收與證據驗證

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M05 / `pantheon_intake` |
| Issue | [#5](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/5) |
| 分支 | `module/m05-pantheon_intake` |
| Worktree | `../longhao-worktrees/m05` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

接收 Persona 自主研究／交易 pool 形成的候選研究，也支援分析師發起的研究。保存來源、時點、版本、證據與可用權利，轉交專業覆核；本模組不管理 Persona 的交易決策。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M05-pantheon_intake.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/pantheon_intake/`
- `apps/admin/src/features/pantheon_intake/`
- `workers/tasks/pantheon_intake/`
- `tests/modules/pantheon_intake/`
- `database/proposals/M05/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M05/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m05 -b module/m05-pantheon_intake origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M05-01：Persona 自主候選。Pantheon 發出 candidate envelope，包含 persona/pool/research/artifact/evidence references及 occurred_at/available_at；本地 inbox 驗證、去重，取得允許的 evidence snapshot，建立 review case。沒有分析師預先建立 ticket也能成立。
- [ ] WORK-M05-02：分析師發起候選。人工在外部研究系統發起的成果走同一接收通道；origin_type區分，不建立另一套審核／發布邏輯。
- [ ] WORK-M05-03：資料缺失與修正。缺證據、授權不明、時間矛盾或hash不符時隔離並請求外部補正；新候選保留parent與supersedes鏈，使M06知道目前覆核是否過時。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `ingest_research_candidate`：接收 Pantheon Persona 或分析師來源候選研究；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_research_candidates`：候選研究接收與錯誤佇列；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_research_candidate`：查詢來源版本、證據與 checksum；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `validate_research_candidate`：驗證雜湊、版次、授權範圍與來源引用；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `reject_research_candidate`：拒絕不適當候選並保留原始接收紀錄；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| research.candidate_received | 發出／接收 | M05 | M05 | research_confidential | 1.0.0 |
| research.candidate_validated | 發出 | M05 | M06 | research_confidential | 1.0.0 |
| research.candidate_superseded | 發出 | M05 | M06,M07 | research_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| research.candidate | 本模組權威寫入 | Pantheon Persona 自主研究或人員研究候選輸入；非正式對客報告。 | producer,external_candidate_id,external_version | internal | research |
| research.candidate_evidence | 本模組權威寫入 | 候選報告採用來源快照、時間與授權範圍。 | candidate_id,evidence_ref | internal | research |
| core.object_asset | 受控共用基礎 | 加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。 | storage_key,object_version | internal | per_asset |
| core.inbox | 受控共用基礎 | consumer 去重與 event fingerprint；同 ID 不同 payload 必須拒收。 | consumer,producer,event_id | internal | integration |
| core.outbox | 受控共用基礎 | 與業務異動同一 DB transaction 提交的待送事件。 | event_id；producer,aggregate_type,aggregate_id,sequence_no；producer,idempotency_key | internal | integration |

- 同一source_event_id重送：回receipt與原candidate，不再建立case。
- 相同artifact version但hash不同：409/conflict quarantine，保留雙方證據並告警。
- 資料源future timestamp、available_at晚於當時建議：阻擋回填為當時可得的研究。
- Pantheon暫時不可用：本地待驗證狀態仍可查，不回傳空資料假裝沒有候選。

## 8. 必須交付的驗收證據

- [ ] `UAT-M05-001`：Persona 可自主建立候選。通過條件：建立immutable candidate與review待辦；不要求先由分析師建研究
- [ ] `UAT-M05-002`：分析師發起成果走同一通道。通過條件：同樣驗證與建review；不跳過證據、覆核或簽章
- [ ] `UAT-M05-003`：候選重送只建一覆核案件。通過條件：一次inbox applied，第二次duplicate；同candidate/review id
- [ ] `UAT-M05-004`：相同版本不同hash進隔離。通過條件：衝突／quarantine；A不被覆寫；不能進簽章佇列
- [ ] `UAT-M05-005`：缺少必要證據不可ready。通過條件：指出缺失並保持quarantined；有raw來源receipt供追查
- [ ] `UAT-M05-006`：外部策略approved不當正式投顧核准。通過條件：只能進review；member feed不存在該候選
- [ ] `UAT-M05-007`：研究時間不得倒填交易時間。通過條件：保存trade/source/report/received各真實時間，不把report time設為trade time
- [ ] `UAT-M05-008`：未知schema版本明確拒絕。通過條件：422 unsupported_schema，保存安全receipt；不默認忽略新必要欄位
- [ ] `UAT-M05-009`：外部證據URL阻擋SSRF。通過條件：拒絕allowlist之外的地址與重新導向；不對任意URL發請求
- [ ] `UAT-M05-010`：授權不足的來源不可客戶發布。通過條件：標明customer_distribution不允許；需替換或取得權利，不自動公開全文
- [ ] `UAT-M05-011`：新候選版本通知舊review過時。通過條件：v1保留；M06收到具版本事件並標示stale/rebase，不自動簽v2
- [ ] `UAT-M05-012`：Pantheon故障不丟棄候選。通過條件：可在failure queue查與重送；一次成功後不重建case，原收到時間不變

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/pantheon_intake -q
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
