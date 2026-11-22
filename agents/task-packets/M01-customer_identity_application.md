# M01 HerdR 任務包 · 客戶、身分與申辦核心

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M01 / `customer_identity_application` |
| Issue | [#1](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/1) |
| 分支 | `module/m01-customer_identity_application` |
| Worktree | `../longhao-worktrees/m01` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

建立同一客戶的可追溯身分與申辦紀錄，讓 LegendFlow 的匿名流量、已登入會員、線下建檔與後續契約能準確對接；不因信箱相同就合併客戶。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M01-customer_identity_application.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/customer_identity_application/`
- `apps/admin/src/features/customer_identity_application/`
- `workers/tasks/customer_identity_application/`
- `tests/modules/customer_identity_application/`
- `database/proposals/M01/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M01/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m01 -b module/m01-customer_identity_application origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M01-01：入口識別與申辦。LegendFlow 驗證登入後以穩定 external_subject 建立或解析客戶；保存來源／同意證據；建立服務申請及 client reference；回傳可續填 application_id。匿名 cookie 只作來源訊號，不能驗證身分。
- [ ] WORK-M01-02：跨通路協助。客服按已驗證聯絡資訊查詢遮罩名單，將電話／現場申辦連到既有客戶；有衝突時建立受限處理工作，不直接覆寫姓名或外部主體。
- [ ] WORK-M01-03：修正與合併。以 expected_version 修改可改欄位；身分合併保留 surviving_customer_id 與來源關聯歷史，歷史契約／財務不重新編號；誤連結使用可稽核解除流程。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_customer`：建立或解析單一客戶主檔；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_customers`：依授權範圍查詢客戶；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_customer`：取得客戶總覽及遮罩資料；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `link_external_identity`：綁定已驗證外部身分；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `propose_customer_merge`：提出重複主檔合併覆核；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `decide_customer_merge`：由不同人覆核身分合併；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_application`：建立申辦案件，保留行銷來源；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_application`：取得申辦進度與允許下一動作；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `withdraw_application`：客戶撤回申請並啟動款項處理；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `record_consent`：新增版次化同意證據；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_customer_merges`：列出等待異人覆核的身分合併案件；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_customer_merge`：查詢合併證據與原始主檔關係；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `update_customer`：本人／受權客服更新白名單資料，驗證聯絡資料以proof另核；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| customer.created | 發出 | M01 | M10,M13 | customer_confidential | 1.0.0 |
| customer.identity_linked | 發出 | M01 | M09 | customer_confidential | 1.0.0 |
| application.created | 發出 | M01 | M02,M10 | customer_confidential | 1.0.0 |
| customer.profile_updated | 發出 | M01 | M03,M04 | customer_confidential | 1.0.0 |
| customer.consent_changed | 發出 | M01 | M09,M10 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| application.company_decided | ／接收 | M02 | M01,M10,M14 | customer_confidential | 1.0.0 |
| ownership.employee_departed | ／接收 | M11 | M01,M10,M11 | internal | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| customer.customer | 本模組權威寫入 | 客戶管理主檔；潛在、申辦、已簽與已生效客戶共用身份。 | lookup_hmac | internal／personal | customer |
| customer.external_identity | 本模組權威寫入 | 經驗證外部登入身分；合併須證據與審核。 | provider,subject_hmac | internal | customer |
| customer.application | 本模組權威寫入 | 簽約申辦流程；客戶簽署先於 KYC，不代表公司已接受。 | tenant_id,id | internal | contract |
| customer.consent | 本模組權威寫入 | 個資告知、行銷同意、撤回事件；不能用簽約強迫行銷同意。 | tenant_id,id | internal | customer |
| customer.merge_case | 本模組權威寫入 | 重複客戶合併申請，核准後保留來源 ID alias 與原證據。 | tenant_id,id | internal | customer |
| core.object_asset | 受控共用基礎 | 加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。 | storage_key,object_version | internal | per_asset |
| core.audit_event | 受控共用基礎 | 不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。 | tenant_id,id | internal | audit |

- 登入 token issuer/audience/nonce 不符：拒絕並記 security audit，不建立客戶。
- 同一 Idempotency-Key 不同 payload：409；同 payload 回原識別碼。
- 身分供應商故障：顯示稍後重試，不退回未驗證自動登入。
- 個資更新與合併要求先檢查 legal hold／留存，再選擇遮罩或限制處理；不得直接刪除稽核鏈。

## 8. 必須交付的驗收證據

- [ ] `UAT-M01-001`：同一外部身分重送只建立一客戶。通過條件：回同一 customer_id；external_identity 唯一；新增事件不得重複產生行銷首購歸因
- [ ] `UAT-M01-002`：不同已驗證 subject 不能依同信箱自動合併。通過條件：建立待人工確認連結候選；原契約及客戶隔離，不把 email 當主鍵
- [ ] `UAT-M01-003`：申辦草稿跨會話續填與撤回。通過條件：續填保留同一 application_id、原來源和歷史版本；撤回記withdrawn及原因／時間，不能再當有效申辦；全程無服務權限
- [ ] `UAT-M01-004`：未授權跨客戶查詢。通過條件：拒絕且不洩漏存在性；事件不含 B 的明文個資
- [ ] `UAT-M01-005`：來源變更不覆寫原始取得來源。通過條件：原始來源不變；新的 touchpoint／transaction attribution 另記錄
- [ ] `UAT-M01-006`：同意撤回有範圍與時間。通過條件：停止後續行銷投遞；必要契約通知依法定／契約分類另判斷，不能誤停所有服務
- [ ] `UAT-M01-007`：外部 token 偽造遭拒。通過條件：401/403；不產生 linked identity、customer 或申辦
- [ ] `UAT-M01-008`：兩筆合併保留可追溯關聯。通過條件：保留來源 id 映射與 reviewer evidence；歷史付款與報告送達紀錄可由舊 id 查回
- [ ] `UAT-M01-009`：客戶資料樂觀鎖阻擋覆寫。通過條件：第二筆 412／版本衝突；第一筆資料仍在；UI 提供重載與差異
- [ ] `UAT-M01-010`：驗證供應商中斷不建立假身分。通過條件：回可重試外部依賴錯誤；狀態保持未驗證，不使用上次別人的會話
- [ ] `UAT-M01-011`：協助建檔不可冒充客戶同意。通過條件：API 拒絕不允許欄位；只建立代填者 audit，客戶同意須獨立證據
- [ ] `UAT-M01-012`：來源資料故障與空集合不同。通過條件：回 source_status/degraded 與 last_success_at；不能顯示沒有契約或沒有付款

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/customer_identity_application -q
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
