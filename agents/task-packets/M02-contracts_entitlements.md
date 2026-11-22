# M02 HerdR 任務包 · 線上契約、公司接受與服務權限

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M02 / `contracts_entitlements` |
| Issue | [#2](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/2) |
| 分支 | `module/m02-contracts_entitlements` |
| Worktree | `../longhao-worktrees/m02` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

實現客戶先在線上簽申請／契約，再完成 KYC／適合度／AML，由公司評估接受後才建立有效服務。客戶簽署與公司接受、契約生效、付款和權限分開記錄。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M02-contracts_entitlements.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/contracts_entitlements/`
- `apps/admin/src/features/contracts_entitlements/`
- `workers/tasks/contracts_entitlements/`
- `tests/modules/contracts_entitlements/`
- `database/proposals/M02/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M02/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m02 -b module/m02-contracts_entitlements origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M02-01：先簽再評估。以已批准 package 建立簽署 envelope，顯示公司仍須接受及申辦未完成的狀態；客戶完成簽署後開啟線上 KYC 流程，保持 entitlement locked。
- [ ] WORK-M02-02：公司接受與開通。審核者讀版本鎖定的簽署、KYC、適合度、AML clearance 與付款條件；授權人作 accept/reject；工作流在全部門檻有效時以原子命令記錄 effective 與 entitlement_decision，並通知 LegendFlow。
- [ ] WORK-M02-03：服務變更與退費。續約／升級建立明確 service period、差額與關聯；退款、撤回、到期及法遵限制觸發重新評估權限，保留原契約與已提供服務證據；拒絕時預收款交 M14 退款，不直接修改付款紀錄。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_contract_package`：建立契約文件版本，待法遵核准；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `approve_contract_package`：核准可供簽署的固定文件包；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_contract_package`：取得指定契約文件與版本；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_customer_signature`：客戶先簽署申辦／契約文件包；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_customer_signature`：驗證客戶簽署完成回呼；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `decide_company_acceptance`：KYC、適合度與 AML clearance 完成後記錄公司承諾；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_contract`：查看契約成立、生效與服務期間；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_contract_amendment`：升級或變更形成新申辦與明確差額；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `terminate_contract`：記錄契約終止及退款評估；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_customer_entitlements`：列示服務權限及有效區間；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `evaluate_entitlements`：授權系統重算條件而非任意開通；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `suspend_entitlement`：有依據的服務權限暫停；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| application.customer_signed | 發出 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| application.company_decided | 發出 | M02 | M01,M10,M14 | customer_confidential | 1.0.0 |
| contract.effective | 發出 | M02 | M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.terminated | 發出 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| entitlement.changed | 發出 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| contract.service_ended | 發出 | M02 | M11,M10,M14 | customer_confidential | 1.0.0 |
| application.created | ／接收 | M01 | M02,M10 | customer_confidential | 1.0.0 |
| kyc.decision_recorded | ／接收 | M03 | M02,M04 | customer_confidential | 1.0.0 |
| suitability.assessed | ／接收 | M03 | M02 | customer_confidential | 1.0.0 |
| business_clearance.changed | ／接收 | M04 | M02 | customer_confidential | 1.0.0 |
| finance.payment_confirmed | ／接收 | M14 | M02,M10 | customer_confidential | 1.0.0 |
| finance.refund_confirmed | ／接收 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |
| kyc.refresh_required | ／接收 | M03 | M02,M10 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| contract.package_version | 本模組權威寫入 | 申請暨投顧契約及揭露文件完整版本。 | package_code,package_version | internal | contract |
| contract.customer_signature | 本模組權威寫入 | 客戶先簽申請／契約封包的不可變證據。 | provider,provider_transaction_id | internal | contract |
| contract.acceptance_decision | 本模組權威寫入 | 公司接受、拒絕或要求補件的完整決策歷史；accept才另產生company_acceptance。 | tenant_id,id | internal | contract |
| contract.company_acceptance | 本模組權威寫入 | 公司完成必要審查後接受；與客戶簽署分離。 | application_id；decision_id | internal | contract |
| contract.contract | 本模組權威寫入 | 法律關係及服務契約狀態；不把付款或前端權限當成成立證據。 | contract_number；application_id | internal | contract |
| contract.contract_service | 本模組權威寫入 | 一客多契約多服務及續約服務鏈。 | tenant_id,id | internal | contract |
| contract.entitlement | 本模組權威寫入 | 權限來源為有效契約與審查結果；配送時重新檢查。 | contract_service_id,feature_code | internal | contract |
| contract.entitlement_decision | 本模組權威寫入 | 每次啟停權限的決策紀錄。 | tenant_id,id | internal | audit |

- 簽章 provider callback 重送：驗證 provider reference 與 payload hash；同一 envelope 僅一有效簽署。
- 審核期間資料改版：舊審核 snapshot 失效，重新評估而非套用已失效 clearance。
- 開通事件傳送失敗：權威決策已提交且 outbox 重試；LegendFlow 不可自行開權限。
- 退款成功不意味可刪契約；終止、生效、退款與服務截止各保留實際時間。

## 8. 必須交付的驗收證據

- [ ] `UAT-M02-001`：客戶可以在 KYC 前完成簽署。通過條件：customer_signature=verified；application 推進；entitlement 仍 locked，UI 導向 KYC
- [ ] `UAT-M02-002`：未完成審核不可公司接受。通過條件：422 gate_not_satisfied；無 effective/active entitlement；保留拒絕理由
- [ ] `UAT-M02-003`：全部門檻通過後只開通一次。通過條件：只有一有效生效決策與權限事件；UTC effective_at 一致
- [ ] `UAT-M02-004`：公司拒絕已收款申辦。通過條件：契約不生效、權限維持 locked；建立可追蹤退款工作並交 M14，無直接出金
- [ ] `UAT-M02-005`：兩份並行契約權限分開。通過條件：月訂權限到期；年度服務仍 active；客戶未因此被當全部服務中斷
- [ ] `UAT-M02-006`：文件改版不得套用舊簽署。通過條件：拒絕 hash/version 不符；建立 v2 新簽署要求並保存 v1
- [ ] `UAT-M02-007`：審核快照過期應重新審查。通過條件：412 或 stale_assessment；不得基於 v1直接開通
- [ ] `UAT-M02-008`：簽章 callback 不等於完成驗證。通過條件：signature=invalid；不發 verified 事件；顯示可重新簽署原因碼
- [ ] `UAT-M02-009`：續約預建不提前給權限。通過條件：只有現行期服務生效；續約 entitlement 在生效時點才轉 active
- [ ] `UAT-M02-010`：公司接受權限需職務且防自審。通過條件：403 或 segregation_conflict；決策沒有落地
- [ ] `UAT-M02-011`：權限事件延遲不以網站快取越權。通過條件：由權威 entitlement 再查而拒絕；記錄 denied decision id
- [ ] `UAT-M02-012`：終止與留存各自生效。通過條件：停止對應服務；仍可稽核契約及簽署原件；不刪除受保全物件

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/contracts_entitlements -q
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
