# M08 HerdR 任務包 · 建議序列、簽章驅動發布與更正

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M08 / `publication` |
| Issue | [#8](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/8) |
| 分支 | `module/m08-publication` |
| Worktree | `../longhao-worktrees/m08` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

有效簽章驗證後立即由系統把已核定報告／投顧建議發布給符合權限的客戶。維持進場、更新、出場的一組建議序列，並能由每則訊息找到當時相對應的已簽報告。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M08-publication.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/publication/`
- `apps/admin/src/features/publication/`
- `workers/tasks/publication/`
- `tests/modules/publication/`
- `database/proposals/M08/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M08/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m08 -b module/m08-publication origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M08-01：簽前建議關聯。每個實質建議建立entry/update/exit版本。尚未產生正式報告時，draft可先記source_candidate_id並保留空supporting_report_version_ids；M07 create_report_version再建立雙向關聯，freeze前必須補齊可支持判斷的report_version、有效期限、通路文案與客群條件，不形成建立順序循環。同一仍有效報告可支援多則已覆核訊息，重大改判須先新簽報告／補充報告。
- [ ] WORK-M08-02：簽後即時發布。consume report.signature_verified，重查簽章未撤銷、report/recommendation hash一致、未過期、來源與policy可用；seal audience snapshot、寫publication與publication.created outbox；無第二個人工發布步驟。
- [ ] WORK-M08-03：更正／撤回。必要時撤回未送部分，追加correction/withdrawal事件；已送內容不覆寫；更正含新正式版本與original publication reference，通知已受影響客群並保留當時資格證據。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_recommendation_series`：建立帶進、更新、帶出共同系列；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_recommendation_version`：建議版次必須對應研究依據及後續簽章包；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_publications`：檢視簽章後自動發布狀態；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_publication`：檢視版本、受眾快照、渠道狀態；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `withdraw_publication`：撤回後停用尚未送出內容並保留歷史；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `link_publication_correction`：關聯另已簽署的新更正版本，禁止覆寫原訊息；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| publication.created | 發出 | M08 | M09 | research_confidential | 1.0.0 |
| publication.withdrawn | 發出 | M08 | M09,M13 | research_confidential | 1.0.0 |
| publication.corrected | 發出 | M08 | M09,M13 | research_confidential | 1.0.0 |
| entitlement.changed | ／接收 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| report.signature_verified | ／接收 | M07 | M08 | research_confidential | 1.0.0 |
| report.signature_revoked | ／接收 | M07 | M08,M09 | research_confidential | 1.0.0 |
| delivery.status_changed | ／接收 | M09 | M08,M13 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| publication.recommendation_series | 本模組權威寫入 | 帶進、追蹤、帶出的建議序列，與 Persona 交易及客戶交易分離。 | series_code | internal | report |
| publication.recommendation_version | 本模組權威寫入 | 正式訊息與同時有效報告連結；重大判斷改變需新簽證。 | series_id,recommendation_version_no | internal | report |
| publication.publication | 本模組權威寫入 | 驗簽完成即建立發布，不加第二個 Publish 人工作業。 | recommendation_version_id | internal | report |
| publication.audience_snapshot | 本模組權威寫入 | 發布當下受眾規則及快照；不取代送出時再次查權限。 | publication_id | internal | report |
| publication.recipient | 本模組權威寫入 | 逐收件人權益證據；不存姓名或手機。 | snapshot_id,customer_id,entitlement_id | internal | report |

- report.signature_verified重複：以signature/report/recommendation版本unique key防重建publication。
- 受眾解析timeout：保持可追蹤pending/blocked，不能改成全體發送。
- 過期訊息在重試時丟到expired/blocked，不發昨日進場建議當即時新訊息。
- 投遞中撤銷：新dispatch拒絕；已發送保留並另送已核定更正，不能回寫為從未寄出。

## 8. 必須交付的驗收證據

- [ ] `UAT-M08-001`：驗證簽章後無第二發布操作。通過條件：自動建立publication/audience/delivery事件；人員不用另按發布
- [ ] `UAT-M08-002`：候選或未簽稿不可出現在feed。通過條件：無publication；訪客／客戶不可讀未簽正式內容
- [ ] `UAT-M08-003`：每一進出場訊息有對應報告。通過條件：每版有supporting report_version/hash；exit判斷改變時須有相應新／補充已簽報告
- [ ] `UAT-M08-004`：一份有效報告可支持多則核定訊息。通過條件：可共用report reference；各message有獨立version/hash/時間；不強制每則重新全文研究
- [ ] `UAT-M08-005`：簽後實質文案變動不可偷渡。通過條件：hash mismatch阻擋；B須新版本覆核／簽章
- [ ] `UAT-M08-006`：受眾快照排除無權限者。通過條件：僅合格客戶入snapshot；保存policy與entitlement decision；其餘有reason summary
- [ ] `UAT-M08-007`：快照後停權仍不得送出。通過條件：即時重查拒絕；snapshot保留原事實，delivery記suppressed_revoked
- [ ] `UAT-M08-008`：重複簽章事件僅發布一次。通過條件：一publication且一組邏輯delivery；inbox顯示duplicate
- [ ] `UAT-M08-009`：撤回不抹除已發布證據。通過條件：停止新發送並追加withdrawal；已發送原文、受眾及時間保留
- [ ] `UAT-M08-010`：更正指向原發布與新簽版本。通過條件：新publication引用原id與新report；受影響客戶可見更正標示，原版仍可audit
- [ ] `UAT-M08-011`：延遲重送過期建議遭攔截。通過條件：expired/blocked，不發失效進出場建議；有重驅者及理由
- [ ] `UAT-M08-012`：來源trade和客戶建議時間分列。通過條件：只在真實簽章發布時點開始建議；不宣稱客戶早盤已收到或成交

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/publication -q
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
