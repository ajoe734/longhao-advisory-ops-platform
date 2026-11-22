# M09 HerdR 任務包 · LegendFlow、會員服務與即時通路投遞

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M09 / `legendflow_delivery` |
| Issue | [#9](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/9) |
| 分支 | `module/m09-legendflow_delivery` |
| Worktree | `../longhao-worktrees/m09` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

以正式API整合既有LegendFlow客戶端；提供符合授權的會員feed、報告連結與LINE／簡訊等訊息投遞，精確保存accepted、sent、delivered、read及未知狀態。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M09-legendflow_delivery.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/legendflow_delivery/`
- `apps/admin/src/features/legendflow_delivery/`
- `workers/tasks/legendflow_delivery/`
- `tests/modules/legendflow_delivery/`
- `database/proposals/M09/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M09/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m09 -b module/m09-legendflow_delivery origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M09-01：會員feed與報告下載。LegendFlow以會員身份交換受限token，query時驗證customer/tenant及當前權限；只回已發布／可用資料；報告用短效簽名URL或受權代理讀取，不暴露bucket永久位置。
- [ ] WORK-M09-02：即時推播。consume publication.created，查publication狀態、expiry、客戶權限與通路偏好；依frozen channeltext產生request，保存attempt後對provider提交；處理receipt與對帳。
- [ ] WORK-M09-03：例外與更正。provider timeout保留unknown並查狀態；可重送的失敗按相同邏輯訊息id處理；撤回／更正事件更新feed並通知受影響客戶，原文不覆寫。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `get_member_feed`：LegendFlow 取得目前授權的已發布訊息；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `request_member_report_access`：即時檢查契約權限後提供短效報告存取票證；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `bind_member_channel`：驗證官方渠道身分後設定接收渠道；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `set_communication_preference`：區分服務通知與行銷同意；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_deliveries`：逐客戶、逐渠道投遞狀態查詢；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_message_receipt`：驗證供應商 accepted/sent/delivered/failed，未知讀取仍未知；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `reconcile_delivery`：查詢模糊投遞狀態後再判斷是否安全重送；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `redrive_delivery_event`：經檢查內容期限與最新權限後重新入列；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| delivery.status_changed | 發出 | M09 | M08,M13 | customer_confidential | 1.0.0 |
| customer.identity_linked | ／接收 | M01 | M09 | customer_confidential | 1.0.0 |
| contract.terminated | ／接收 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| entitlement.changed | ／接收 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| report.signature_revoked | ／接收 | M07 | M08,M09 | research_confidential | 1.0.0 |
| publication.created | ／接收 | M08 | M09 | research_confidential | 1.0.0 |
| publication.withdrawn | ／接收 | M08 | M09,M13 | research_confidential | 1.0.0 |
| publication.corrected | ／接收 | M08 | M09,M13 | research_confidential | 1.0.0 |
| crm.suppression_changed | ／接收 | M10 | M09,M10 | customer_confidential | 1.0.0 |
| customer.consent_changed | ／接收 | M01 | M09,M10 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| delivery.message | 本模組權威寫入 | 每 recipient/channel 一個邏輯消息；供應商重試另建 attempt。 | publication_id,recipient_id,channel；provider_idempotency_key | internal | report |
| delivery.channel_binding | 本模組權威寫入 | 經驗證會員/LINE/手機/郵件收件身分；路由不可使用模型產生的新地址。 | provider,channel,destination_hmac | internal／personal | customer |
| delivery.attempt | 本模組權威寫入 | 各次對 provider 實際傳送嘗試。 | message_id,attempt_no | internal | report |
| delivery.receipt | 本模組權威寫入 | 回呼事件不可變，亂序回呼不可把 delivered 降級為 sent。 | provider,provider_event_id | internal | report |
| publication.recipient | 唯讀／port，寫入由M08 | 逐收件人權益證據；不存姓名或手機。 | snapshot_id,customer_id,entitlement_id | internal | report |
| contract.entitlement | 唯讀／port，寫入由M02 | 權限來源為有效契約與審查結果；配送時重新檢查。 | contract_service_id,feature_code | internal | contract |

- 供應商不支援idempotency/status lookup：無法保證exactly-once；unknown須受控人工對帳或採明示重複風險策略。
- receipt亂序：保存所有原始狀態時間並以狀態偏序合併，不從read倒退sent。
- 短網址或report token撤權後仍需origin authorizer再驗，不可只靠長期有效CDN快取。
- provider rate limit：有上限重試、jitter、deadletter，仍每次檢查expires_at。

## 8. 必須交付的驗收證據

- [ ] `UAT-M09-001`：LegendFlow feed只顯示本人權限內容。通過條件：僅回X可見內容；Y不可由id猜到／直接下載
- [ ] `UAT-M09-002`：報告下載授權需當前有效。通過條件：依核定存取政策拒絕新授權；短效token不可越權讀其他客戶資料
- [ ] `UAT-M09-003`：未驗證通路不能綁成active。通過條件：要求challenge/provider proof；未驗證不成active收件端
- [ ] `UAT-M09-004`：發送前再查權限。通過條件：不送；記suppressed及當時entitlement decision id
- [ ] `UAT-M09-005`：accepted不誤顯示已送達。通過條件：accepted_at存在但delivered_at/read_at缺；不偽造送達率
- [ ] `UAT-M09-006`：未知已讀維持unknown。通過條件：read_state=unknown；不把無read事件認定未讀或流失
- [ ] `UAT-M09-007`：timeout先查結果避免重複。通過條件：先查provider request id或同key安全重試；只一logical message
- [ ] `UAT-M09-008`：亂序receipt不倒退。通過條件：保存兩時間與接收時間；最終狀態不從read倒退
- [ ] `UAT-M09-009`：過期DLQ不得重送舊建議。通過條件：回不可重發原因並稽核；不對provider送請求
- [ ] `UAT-M09-010`：撤回／更正保留原文。通過條件：feed標示更正，新message引用原；原body/hash/attempt仍可查
- [ ] `UAT-M09-011`：通路偏好與必要服務分類分開。通過條件：行銷被抑制；必要通知依已核定分類與偏好處理，禁止把行銷偽裝服務
- [ ] `UAT-M09-012`：provider webhook驗證與重放防護。通過條件：401/422且不更新message；合法重送去重並回原receipt

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/legendflow_delivery -q
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
