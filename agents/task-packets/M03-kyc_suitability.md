# M03 HerdR 任務包 · 線上 KYC、客戶資料與適合度

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M03 / `kyc_suitability` |
| Issue | [#3](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/3) |
| 分支 | `module/m03-kyc_suitability` |
| Worktree | `../longhao-worktrees/m03` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

在客戶已簽申請後完成線上身分與資料蒐集、完整性檢核、適合度評估及持續更新；以版本化資料和人工例外處理降低重複行政作業。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M03-kyc_suitability.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/kyc_suitability/`
- `apps/admin/src/features/kyc_suitability/`
- `workers/tasks/kyc_suitability/`
- `tests/modules/kyc_suitability/`
- `database/proposals/M03/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M03/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m03 -b module/m03-kyc_suitability origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M03-01：線上填答與補件。依 customer_signed application 提供版本化問卷；格式檢核、OCR 輔助填寫、文件有效期與前後不一致檢查；客戶確認 OCR 結果後提交；可離線續填但提交需已驗證身份。
- [ ] WORK-M03-02：適合度與例外。確定性規則計算風險/服務適合度初步結果；有不一致、條件式或不適合時轉授權人；規則輸出不是 LLM 判斷，override需理由、權限與影響服務。
- [ ] WORK-M03-03：持續 KYC 更新。以到期、風險改變、聯絡資料變更或法遵事件觸發 refresh；清楚區分補件期限與權限限制規則；更新結果推送 M02，舊資料保留作歷史決策證據。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_kyc_submission`：簽署後建立線上 KYC 資料草稿；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `request_kyc_upload`：取得隔離區上傳票證與檔案掃描狀態；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `submit_kyc`：提交問卷原始回答與證明文件；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_kyc_submission`：依受限權限取得 KYC 案件；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `decide_kyc`：完成核驗或要求補件；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `assess_suitability`：適合度評估使用固定問卷與規則版本；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `request_due_diligence_refresh`：依到期或風險事件建立重評案件；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_document_status`：查詢本人或受權文件的隔離掃描狀態；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| kyc.decision_recorded | 發出 | M03 | M02,M04 | customer_confidential | 1.0.0 |
| suitability.assessed | 發出 | M03 | M02 | customer_confidential | 1.0.0 |
| kyc.refresh_required | 發出 | M03 | M02,M10 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| customer.profile_updated | ／接收 | M01 | M03,M04 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| kyc.submission | 本模組權威寫入 | 版本化線上 KYC 回答與證據；先保留客戶原始填答。 | application_id,submission_version | internal／personal | kyc |
| kyc.submission_document | 本模組權威寫入 | 線上補件、驗證用途與結果；保留原件版本。 | tenant_id,id | internal | kyc |
| kyc.suitability_assessment | 本模組權威寫入 | 版本化適合度評估，條件適合必須附限制。 | tenant_id,id | internal | kyc |
| core.object_asset | 受控共用基礎 | 加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。 | storage_key,object_version | internal | per_asset |

- 文件上傳未掃毒／存取失敗：保持待檢查，不能顯示已審核完成。
- OCR 與本人填答衝突：保留兩者、標注衝突與證據；不靜默以 AI 改答案。
- 規則版本切換：在 submission snapshot 固定版本，明確重評才變更，不回溯改已決策結果。
- KYC 外部驗證 timeout：進 retry/exception queue，不當通過。

## 8. 必須交付的驗收證據

- [ ] `UAT-M03-001`：簽署後可開始線上 KYC。通過條件：保存答案、問卷版號、檔案 refs；status submitted；未直接開通服務
- [ ] `UAT-M03-002`：缺必填欄位不得完成審核。通過條件：回明確欄位 errors 或 incomplete；保留已填內容，無 approved
- [ ] `UAT-M03-003`：OCR 輔助不覆寫客戶原始答案。通過條件：顯示兩個來源與信心；等待確認；原始答案可查
- [ ] `UAT-M03-004`：文件惡意內容在隔離區遭阻擋。通過條件：不可讀取／進審核；保存安全原因碼，無外洩 URL
- [ ] `UAT-M03-005`：適合度由版本化規則計算。通過條件：得到相同結果與因素；不使用隨機 LLM 輸出取代評分
- [ ] `UAT-M03-006`：不適合服務不能因已付費通過。通過條件：gate 拒絕；可改服務或走拒絕退款流程；留存拒絕證據
- [ ] `UAT-M03-007`：新版本提交使舊審核過時。通過條件：回 stale_submission；v1舊答案仍在，須評估v2才可更新clearance
- [ ] `UAT-M03-008`：業務僅能看到補件狀態。通過條件：403/欄位遮罩；可看缺件項目與到期，不能取得 object download link
- [ ] `UAT-M03-009`：風險變更觸發重評。通過條件：建立一筆 refresh 工作與到期規則；不要自動沿用舊 suitable
- [ ] `UAT-M03-010`：人工例外需權限與獨立覆核。通過條件：拒絕；授權角色附證據及核定者才可形成有效例外
- [ ] `UAT-M03-011`：到期查核不被失敗重試當通過。通過條件：進exception queue並告警，狀態pending或manual_required；無pass事件
- [ ] `UAT-M03-012`：同一提交重放不產生多筆評估。通過條件：只有一有效 assessment及 outbox事件；結果可用原request id查詢

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/kyc_suitability -q
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
