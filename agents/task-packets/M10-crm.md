# M10 HerdR 任務包 · 客戶漏斗、業務維護與轉換歸因

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M10 / `crm` |
| Issue | [#10](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/10) |
| 分支 | `module/m10-crm` |
| Worktree | `../longhao-worktrees/m10` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

從未簽約、申辦中及已訂閱客戶找出可完成申辦、續約、留存、升級與重新開發的名單，具體分派下一步業務工作；用真實業務活動與成功交易建立獎金歸因證據。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M10-crm.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/crm/`
- `apps/admin/src/features/crm/`
- `workers/tasks/crm/`
- `tests/modules/crm/`
- `database/proposals/M10/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M10/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m10 -b module/m10-crm origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M10-01：漏斗與名單。收申辦、契約、付款、使用與客服事件，按原始來源及服務關係生成名單：未完成簽署、待補件、付款失敗、到期續約、適合升級、服務問題、流失與Pool；每筆給具體原因與建議下一動作。
- [ ] WORK-M10-02：業務維護。業務記錄雙向對話／會談／實際需求處理的證據、同意聯絡範圍與下一步；AI可摘要、提醒與草擬，業務確認後執行；CRM不能用報告未讀率單獨認定流失。
- [ ] WORK-M10-03：轉換認領與歸因。成功續約／升級後，檢查在轉換前的可驗證活動及因果關聯；主管核定transaction conversion owner；原始來源、當前維護人、機會owner與計獎人分開保存；無實質介入為self-serve。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `list_crm_worklists`：續約、升級、補件、流失與留存工作清單；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_opportunity`：建立具體續約、升級或重新開發機會；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `record_sales_activity`：留下真實業務介入、客戶需求與下一步證據；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `set_next_action`：指定負責人、期限與執行動作；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `claim_conversion`：提出與成功續約／升級交易相連的業務歸因；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `decide_conversion`：以證據及付款／生效事實裁定轉換歸因；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_crm_suppression`：拒絕行銷、客訴或受限狀態暫停銷售觸達；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_funnel_metrics`：依來源、服務代碼與 cohort 檢視轉換／留存；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_opportunities`：依owner與客戶scope檢視機會清單；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_opportunity`：查詢具體業務機會與狀態；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| crm.opportunity_changed | 發出／接收 | M10 | M10 | customer_confidential | 1.0.0 |
| crm.conversion_decided | 發出 | M10 | M12 | customer_confidential | 1.0.0 |
| crm.suppression_changed | 發出／接收 | M10 | M09,M10 | customer_confidential | 1.0.0 |
| customer.created | ／接收 | M01 | M10,M13 | customer_confidential | 1.0.0 |
| application.created | ／接收 | M01 | M02,M10 | customer_confidential | 1.0.0 |
| application.customer_signed | ／接收 | M02 | M01,M03,M04,M10 | customer_confidential | 1.0.0 |
| application.company_decided | ／接收 | M02 | M01,M10,M14 | customer_confidential | 1.0.0 |
| contract.effective | ／接收 | M02 | M10,M11,M14 | customer_confidential | 1.0.0 |
| contract.terminated | ／接收 | M02 | M09,M10,M11,M14 | customer_confidential | 1.0.0 |
| entitlement.changed | ／接收 | M02 | M08,M09,M10 | customer_confidential | 1.0.0 |
| ownership.assignment_changed | ／接收 | M11 | M10,M12,M13 | customer_confidential | 1.0.0 |
| ownership.pool_entered | ／接收 | M11 | M10 | customer_confidential | 1.0.0 |
| ownership.employee_departed | ／接收 | M11 | M01,M10,M11 | internal | 1.0.0 |
| support.case_opened | ／接收 | M13 | M10,M13 | customer_confidential | 1.0.0 |
| support.case_resolved | ／接收 | M13 | M10 | customer_confidential | 1.0.0 |
| finance.payment_confirmed | ／接收 | M14 | M02,M10 | customer_confidential | 1.0.0 |
| finance.refund_confirmed | ／接收 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |
| customer.consent_changed | ／接收 | M01 | M09,M10 | customer_confidential | 1.0.0 |
| kyc.refresh_required | ／接收 | M03 | M02,M10 | customer_confidential | 1.0.0 |
| finance.payment_failed | ／接收 | M14 | M10 | customer_confidential | 1.0.0 |
| contract.service_ended | ／接收 | M02 | M11,M10,M14 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| crm.marketing_attribution | 本模組權威寫入 | 客戶最初來源及後續可稽核 attribution；不因換業務清除。 | tenant_id,id | internal | crm |
| crm.opportunity | 本模組權威寫入 | 首次申辦、續訂、升級或重新開發機會。 | tenant_id,id | internal | crm |
| crm.activity | 本模組權威寫入 | 真實業務行為及客戶回應證據；AI 建議、群發或分配名字不能單獨計獎。 | tenant_id,id | internal | crm |
| crm.conversion | 本模組權威寫入 | 單筆交易銷售歸因；不是單純維護人欄位。 | contract_service_id | internal | commission |
| crm.work_item | 本模組權威寫入 | 業務要執行的具體名單工作；不只顯示分數。 | dedupe_key | internal | crm |
| crm.suppression | 本模組權威寫入 | 限制招攬但不停止必要服務通知。 | tenant_id,id | internal | crm |

- 相同事件／窗口生成同一待辦：unique work item key去重，更新原因而非每日堆疊。
- 客戶拒絕行銷、未結重大抱怨、法遵限制：依核定政策suppress sales，保留必要客服追蹤。
- UI錄音轉文字未確認：記draft activity，不能直接作正式conversion evidence。
- 原始來源缺失：標unknown而非任意給當前業務；可補證但audit記修正。

## 8. 必須交付的驗收證據

- [ ] `UAT-M10-001`：未完成申辦產生可執行待辦。通過條件：只一待辦含缺件原因、安全摘要、owner、next_action及due_at
- [ ] `UAT-M10-002`：自助續約不計業務轉換。通過條件：conversion type self_serve；不因有maintenance owner就給sales獎金
- [ ] `UAT-M10-003`：業務續約需事前活動和交易。通過條件：保存活動／交易／因果與核定者；核定後M12才可用sales attribution
- [ ] `UAT-M10-004`：空白聯絡或自動訊息不能搶獎金。通過條件：evidence_insufficient；不能approve為業務介入
- [ ] `UAT-M10-005`：沒有讀研究報告不單獨判流失。通過條件：不因report未讀標必然流失；以可觀測服務與續約訊號附原因
- [ ] `UAT-M10-006`：禁止聯絡或未結抱怨抑制銷售。通過條件：不派行銷／升級催促；保留必要客服工作與抑制原因碼
- [ ] `UAT-M10-007`：升級機會須服務適合度允許。通過條件：規則阻擋轉為可聯絡升級名單；可提示需要重評，不能直接推銷
- [ ] `UAT-M10-008`：四種owner不互相覆寫。通過條件：保留各自欄位與有效時間；計獎只用核定transaction owner
- [ ] `UAT-M10-009`：爭議認領不能重複計獎。通過條件：只一有效歸因或明確批准分配；pending/disputed不進正式payout
- [ ] `UAT-M10-010`：轉換後補寫活動不能回填搶獎。通過條件：標late_entry需獨立核定，不改created_at；不自動獲獎金
- [ ] `UAT-M10-011`：漏斗統計重放與退出正確。通過條件：按distinct application/customer定義去重；withdraw另列，不把累計簽署當有效訂閱
- [ ] `UAT-M10-012`：同一待辦不因日批次無限重複。通過條件：回原work_item或更新優先；保留owner與已完成結果

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/crm -q
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
