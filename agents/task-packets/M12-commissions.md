# M12 HerdR 任務包 · 獎金政策、計算、調整與結算

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M12 / `commissions` |
| Issue | [#12](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/12) |
| 分支 | `module/m12-commissions` |
| Worktree | `../longhao-worktrees/m12` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

以實際未稅服務收入扣除可歸屬退款計算獎金，保存政策版本、收入來源與轉換證據。將已確認參數與尚待核定的基準比率／衰減互動明確分開，所有結果可重算與對帳。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M12-commissions.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/commissions/`
- `apps/admin/src/features/commissions/`
- `workers/tasks/commissions/`
- `tests/modules/commissions/`
- `database/proposals/M12/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M12/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m12 -b module/m12-commissions origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M12-01：政策版本。已確認：marketing第1年100%、第2年50%、第3年起符合持續自動續約25%；sales介入續約pool分30/70；基礎實際未稅服務收入扣退款。未確認：base pool rate、衰減與30/70套用順序、尾佣連續性、年份起算、接手後marketing share等，均可模擬但需核定才能live。
- [ ] WORK-M12-02：月結計算。取得M14已確認eligible revenue及退款allocation，讀M10核定conversion與M11歷史assignment；以policy effective period、cohort與lineage計算decimal金額，按受款人保留計算式及company retained；無sales intervention則sales=0。
- [ ] WORK-M12-03：退款與發放。退款只反轉原來已計入的收入與相應原受款人／原policy；已付獎金形成待處理差額，不自動扣薪。結算完成後maker/checker批准payout batch，依實際財會接口對帳，provider成功回執才paid。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_commission_policy`：建立100/50/25、30/70及未稅收入規則版次；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `approve_commission_policy`：核准池率及尚待定義之交互規則，異人覆核；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `preview_commissions`：純試算：回傳假設、未核准條件及逐筆拆分；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_commission_run`：僅核准政策與已結算財務基礎可入正式台帳；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_commission_run`：查看計算批次、差異與核准狀態；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `list_commission_entries`：依權限查看逐筆獎金歸因和退款調整；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `approve_commission_run`：異人核對批次與公式後核准；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_commission_adjustment`：原收入／原受領人連結的負向調整，不轉嫁接手人；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_commission_payout_batch`：產生核准批次的薪酬介接檔與控制總額；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_commission_payout_batch`：查詢薪酬匯出、核准及接收狀態；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `approve_commission_payout_batch`：薪酬輸出由不同人核准與原run控制總額對比；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `acknowledge_commission_payout_batch`：記錄薪酬接收結果，部分成功不重複發放；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| commission.run_calculated | 發出／接收 | M12 | M12 | internal | 1.0.0 |
| commission.run_approved | 發出 | M12 | M14 | internal | 1.0.0 |
| commission.adjustment_recorded | 發出 | M12 | M14 | internal | 1.0.0 |
| crm.conversion_decided | ／接收 | M10 | M12 | customer_confidential | 1.0.0 |
| ownership.assignment_changed | ／接收 | M11 | M10,M12,M13 | customer_confidential | 1.0.0 |
| finance.revenue_recognized | ／接收 | M14 | M12 | internal | 1.0.0 |
| finance.refund_confirmed | ／接收 | M14 | M02,M10,M12,M13 | customer_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| commission.policy_version | 本模組權威寫入 | 獎金演算法、來源年齡係數及未核准參數的版本化政策。 | policy_code,policy_version | internal | commission |
| commission.calculation_run | 本模組權威寫入 | 可重現的試算或正式計獎批次；未核准政策只可 simulation。 | run_key | internal | commission |
| commission.entry | 本模組權威寫入 | 逐筆收入切分行銷、業務、公司留存之計算明細，原始列不可改。 | run_id,recognition_id,beneficiary_type | internal | commission |
| commission.adjustment | 本模組權威寫入 | 退款/歸因修正以原受領人與原政策沖回；不直接扣薪。 | dedupe_key | internal | commission |
| commission.payout_batch | 本模組權威寫入 | 正式獎金支出批次；試算結果不可付款。 | tenant_id,id | internal | commission |
| commission.payout_line | 本模組權威寫入 | 獎金列與付款批次關係，避免一筆獎金重複支付。 | entry_id | internal | commission |

- 收入未確定或淨額為負：標例外與linked reversal；不得把負數直接裁為0而丟棄應調整事實。
- 第二／三年度依客戶cohort與service lineage，不依公司year2整批套50%。
- 同一refund allocation不可重複扣除；退尚未認列預收款不能再扣已認列收入。
- 計算策略未核定：simulation允許比較，live/payout拒絕；不以文件範例3%當批准。

## 8. 必須交付的驗收證據

- [ ] `UAT-M12-001`：未核定基準率不能正式計獎。通過條件：policy_not_approved；可simulation但結果不可付款
- [ ] `UAT-M12-002`：自助續訂無業務份額。通過條件：sales amount=0；marketing依來源／年係數；其餘保留公司，不給current owner
- [ ] `UAT-M12-003`：第一二年係數按客戶cohort。通過條件：係數分別1.00、0.50；不以公司年度統一降半
- [ ] `UAT-M12-004`：第三年尾佣需持續自動續約。通過條件：只有符合連續auto者適用0.25；不因打開auto開關就自動恢復既往尾佣資格
- [ ] `UAT-M12-005`：業務介入30/70需有核定證據。通過條件：pool先按該版本規則分配；marketing/sales與衰減順序可在明細重演，不能隱藏差額
- [ ] `UAT-M12-006`：衰減兩算法不能混用。通過條件：A與B結果分開且有policy hash；live只接受被核定的單一策略
- [ ] `UAT-M12-007`：退款反向原受款人原政策。通過條件：反向原entry recipient與policy；不扣新接手者；保留原金額與adjustment
- [ ] `UAT-M12-008`：預收未履約退款不能重扣收入。通過條件：只對原已計入basis部分調整；預收退還不重扣既有earned revenue
- [ ] `UAT-M12-009`：退款重送只調整一次。通過條件：一linked adjustment；net eligible total與財會一致
- [ ] `UAT-M12-010`：金額分配總數守恆。通過條件：marketing+sales+company=basepool；明確minor-unit rounding/remainder規則，無float誤差
- [ ] `UAT-M12-011`：製作人不可自核且發放須回執。通過條件：自核segregation_conflict；合法checker核定後才可交付款接口；有可驗證且去重的付款回執才標paid
- [ ] `UAT-M12-012`：已付退款不得自動扣薪。通過條件：列追索／抵銷待核定，無無授權自動薪資扣款；保留爭議與核准紀錄

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/commissions -q
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
