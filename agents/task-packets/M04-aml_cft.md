# M04 HerdR 任務包 · AML/CFT 查核、風險與受限案件

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M04 / `aml_cft` |
| Issue | [#4](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/4) |
| 分支 | `module/m04-aml_cft` |
| Worktree | `../longhao-worktrees/m04` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

提供身分與風險導向查核、可能命中處理、加強審查、持續監控及受限調查流程。一般營運只取得可否建立／維持關係的安全摘要，不取得敏感案情。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M04-aml_cft.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/aml_cft/`
- `apps/admin/src/features/aml_cft/`
- `workers/tasks/aml_cft/`
- `tests/modules/aml_cft/`
- `database/proposals/M04/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M04/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m04 -b module/m04-aml_cft origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M04-01：首次查核。接收已簽申請的必要 KYC 欄位，在受限 adapter 使用經核定名單與篩檢 policy；記錄 provider、名單版本、查詢與結果摘要；具體命中轉 case。
- [ ] WORK-M04-02：可能命中與 EDD。人員核對身份、別名、關係及必要文件，提出判斷與 evidence；另一授權人按 policy核定解除／限制；非判定為洗錢、非自動通報。
- [ ] WORK-M04-03：持續監控與處置。名單更新、風險資料變更及異常金流事件觸發再查；受限調查／通報遵循核定流程；只向一般模組發布不洩漏原因的 clearance change，必要時保全資料。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `submit_aml_declaration`：客戶線上聲明與證明資料，並非 AML 放行；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `start_aml_screening`：發起身分／名單篩檢；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_aml_screening`：接收受限區名單比對結果；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_aml_cases`：受限 AML 案件工作佇列；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_aml_case`：檢視 AML 證據與審查紀錄；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `add_aml_case_action`：要求 EDD、補件或向主管升級；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `decide_aml_case`：受權人裁決與公司建立關係之 clearance；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_business_clearance`：僅輸出通過／待處理／不通過及有效期限；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| aml.case_changed | 發出／接收 | M04 | M04 | aml_restricted | 1.0.0 |
| business_clearance.changed | 發出 | M04 | M02 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| kyc.decision_recorded | ／接收 | M03 | M02,M04 | customer_confidential | 1.0.0 |
| customer.profile_updated | ／接收 | M01 | M03,M04 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| aml.screening | 本模組權威寫入 | 名單比對供應商查核；模糊命中不是自動認定或自動清除。 | provider,provider_request_id | internal | aml |
| aml.case_file | 本模組權威寫入 | AML/CFT 受限覆核案件，禁止 CRM 讀內情。 | tenant_id,id | internal／restricted_aml | aml |
| aml.case_action | 本模組權威寫入 | AML 調查、EDD、申報與決定之受限軌跡。 | tenant_id,id | internal／restricted_aml | aml |
| aml.business_clearance | 本模組權威寫入 | 供公司接受服務查核的最小結果；不暴露比對名單、申報或案件細節。 | tenant_id,id | internal | aml |
| core.audit_event | 受控共用基礎 | 不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。 | tenant_id,id | internal | audit |
| core.legal_hold | 受控共用基礎 | 司法、爭議或稽核保存指示；解除以新事件表達。 | tenant_id,id | internal | audit |

- 名單 provider 失聯、版本不明或結果過期：保持 pending/manual review，不以無結果等同 clear。
- 同名誤判的解除保留證據和雙人核定；後續名單版號更新可再查，不刪舊紀錄。
- 一般 event bus、CRM、客服、Pantheon prompt 禁止出現 raw PII、命中詳情或通報內容。
- 對外通知模板不能透露調查敏感資訊；notify event 只放一般原因碼與受控 template_id。

## 8. 必須交付的驗收證據

- [ ] `UAT-M04-001`：可能命中建立受限案件。通過條件：建立 case、clearance pending/restricted；公司接受 gate不能通過
- [ ] `UAT-M04-002`：LLM 無權解除命中。通過條件：403；案件不變；安全審計記錄拒絕，不把 AI summary當核定
- [ ] `UAT-M04-003`：一般業務不可讀 AML 案情。通過條件：403/不存在等值回應；一般摘要只顯示pending/restricted狀態
- [ ] `UAT-M04-004`：名單 provider timeout 不視為無命中。通過條件：error/retry/manual_required，clearance不轉permitted；告警含provider不含PII
- [ ] `UAT-M04-005`：誤命中解除需核定與證據。通過條件：自核拒絕；合法checker核定後產生case action與新clearance版本
- [ ] `UAT-M04-006`：名單版本更新可再查。通過條件：新增screening保留v1；新的possiblematch阻擋後續操作，不能覆寫為原通過
- [ ] `UAT-M04-007`：宣告簽名不能替代查核。通過條件：拒絕；簽署文件僅證據，必須完成指定查核與決策
- [ ] `UAT-M04-008`：受限事件只輸出安全摘要。通過條件：payload只含customer id/status/version/policy ref；禁止raw案情與PII
- [ ] `UAT-M04-009`：EDD 補件可追蹤且不洩密。通過條件：一般平台取得中性待補清單；完整研判僅AML工作台可見
- [ ] `UAT-M04-010`：內稽存取有目的與時限。通過條件：只有授權範圍可讀；每次讀取audit；不能修改決策
- [ ] `UAT-M04-011`：未結案件受 legal hold。通過條件：物件仍保留且記錄hold；刪除作業報skipped_hold，不解除限制
- [ ] `UAT-M04-012`：過期clearance不能被公司接受重用。通過條件：回不可建立關係及需更新的安全原因；不公開內部詳細案情

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/aml_cft -q
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
