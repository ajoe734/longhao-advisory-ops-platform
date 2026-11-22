# M11 HerdR 任務包 · 維護歸屬、離職移交與公司 Pool

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M11 / `ownership_pool` |
| Issue | [#11](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/11) |
| 分支 | `module/m11-ownership_pool` |
| Worktree | `../longhao-worktrees/m11` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

保存客戶維護責任的有效期間與實質接手證據；離職即回公司，重新開發者在實際促成轉換後取得該筆業務獎金。維護assignment不是獎金憑證。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M11-ownership_pool.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/ownership_pool/`
- `apps/admin/src/features/ownership_pool/`
- `workers/tasks/ownership_pool/`
- `tests/modules/ownership_pool/`
- `database/proposals/M11/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M11/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m11 -b module/m11-ownership_pool origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M11-01：官網自助與維護指派。網路自行申辦未有維護人時company/marketing pool保有歸屬；日後指派業務維護只建立assignment；客戶自行續訂不因assignment自動產生sales award。
- [ ] WORK-M11-02：離職與接手。接收有效employee departure，立即終止未來assignment並移回company pool；歷史conversion與已核定獎金保留；新業務接手記錄實質活動，成功續約／升級交M10核定。
- [ ] WORK-M11-03：服務中斷七日提案。以「全部付費服務連續中斷168小時」作待確認的規則參數，排除仍有效年度服務；不是七天沒聯絡。規則未正式核定可模擬，不自動上線變更獎金歸屬。到期、停權、法遵限制與行政故障要分原因判斷。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `list_company_pool`：查看可重新開發的公司客戶池；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `assign_maintenance_owner`：指定維護責任與有效期間，不直接給獎金；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `claim_pool_customer`：競爭領取以版本鎖保證只有一位新維護人；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `release_maintenance_owner`：釋出維護關係但不撤回有效投顧服務；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `record_employee_departure`：業務離職即刻回公司池並停用授權；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `evaluate_company_pool`：規則化計算付費服務中斷七日；不是七日未聯絡；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_ownership_history`：查看有效期間與交接歷史，不以現owner覆寫舊獎金；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| ownership.assignment_changed | 發出 | M11 | M10,M12,M13 | customer_confidential | 1.0.0 |
| ownership.pool_entered | 發出 | M11 | M10 | customer_confidential | 1.0.0 |
| ownership.employee_departed | 發出／接收 | M11 | M01,M10,M11 | internal | 1.0.0 |
| contract.effective | ／接收 | M02 | M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.terminated | ／接收 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.service_ended | ／接收 | M02 | M11,M10,M14 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| ownership.policy_version | 本模組權威寫入 | Pool 判定政策；七天未聯絡不是既定規則。 | policy_code,policy_version | internal | policy |
| ownership.assignment | 本模組權威寫入 | 維護關係的有效時間；不直接決定所有交易佣金。 | tenant_id,id | internal | commission |
| ownership.pool_entry | 本模組權威寫入 | 客戶回公司 Pool 的證據與重新開發狀態。 | tenant_id,id | internal | commission |
| crm.activity | 唯讀／port，寫入由M10 | 真實業務行為及客戶回應證據；AI 建議、群發或分配名字不能單獨計獎。 | tenant_id,id | internal | crm |
| crm.conversion | 唯讀／port，寫入由M10 | 單筆交易銷售歸因；不是單純維護人欄位。 | contract_service_id | internal | commission |

- 同時兩業務claim：單一交易與unique active assignment；loser回409並取得最新狀態。
- 事件延遲：以真實effective_at記離職／服務中斷，收到時間另外保存；不回溯撤銷已實際完成且核定的轉換。
- 自動回Pool規則尚未核定：只preview，正式job回policy_not_approved。
- 法遵停權不是普通流失；不得自動當可以促銷的Pool客戶。

## 8. 必須交付的驗收證據

- [ ] `UAT-M11-001`：自助客戶無維護人也能續訂。通過條件：服務正常延續；無sales conversion；marketing/company依核定policy計算
- [ ] `UAT-M11-002`：離職立即回公司不等七天。通過條件：結束assignment並建company pool entry；撤銷離職者未來操作權
- [ ] `UAT-M11-003`：新指派不是新獎金。通過條件：不產生新業務sales獎金；保留company retention邏輯
- [ ] `UAT-M11-004`：新業務實際再開發才可領後續獎金。通過條件：該transaction指向新業務；舊已核定獎金與來源不改
- [ ] `UAT-M11-005`：七日規則不是七天沒聯絡。通過條件：不因未聯絡回Pool；最多依內部維護SLA提醒，不能移獎金
- [ ] `UAT-M11-006`：全部服務中斷七日才符合提案。通過條件：前者不符合、後者符合候選；正式執行仍須approved policy
- [ ] `UAT-M11-007`：單一月訂到期不抵銷年度有效服務。通過條件：不回Pool；all_paid_services_lapsed=false
- [ ] `UAT-M11-008`：未核定七日定義禁止自動回收。通過條件：policy_not_approved；preview可回受影響清單，assignment不變
- [ ] `UAT-M11-009`：法遵停權不被當可銷售流失名單。通過條件：列restricted，不派續約催促；完整AML理由不外洩
- [ ] `UAT-M11-010`：兩人同時認領只有一人成功。通過條件：一成功一409/412；無重疊active assignment；失敗者不能記成owner
- [ ] `UAT-M11-011`：歷史佣金不因移交改歸屬。通過條件：歷史entry仍原recipient；新的轉換依新證據另定
- [ ] `UAT-M11-012`：服務恢復可中止尚未成立中斷條件。通過條件：中斷clock重設／結束；不沿用舊T在隔日回Pool

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/ownership_pool -q
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
