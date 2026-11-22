# 十四模組平行開發與整合責任

## 1. 一次完成的定義

全部 M01–M14 交給十四個獨立工作流同時開發，各自交付後端命令/查詢、管理介面、worker、資料提案、權限、測試、監控與操作說明。對外系統則交付 adapter 和 provider 契約測試。任何模組沒有整合通過，全平台不得標記完成。

共用契約已由此規格包提供，不要求十四個工作流等待核心功能寫完。物理執行緒數依 HerdR 所在主機、模型額度與外部 API 配額配置；排程意圖是十四個同時 active，不是逐個完成才啟動下一個。若環境資源不足，協調者應回報實際並行數與擴充方式，不可暗中改為縮減範圍。

## 2. 開工依賴與整合依賴

| 工作流 | 可立即使用的替身 | 最終整合的提供者 | 交付焦點 |
|---|---|---|---|
| M01 | IdP/LegendFlow identity fixture | M02/M03/M10/M13 | 單一客戶與來源歸戶 |
| M02 | KYC/AML/payment gate fixture | M01/M03/M04/M14 | 先簽後審、公司承諾與權限 |
| M03 | application、screening stub | M01/M02/M04 | 線上資料、適合度、有效期限 |
| M04 | screening provider fake | M01/M02/M03/M14 | restricted case、EDD與限制旗標 |
| M05 | Pantheon candidate fixture | 外部 Pantheon、M06/M07 | 不可變候選與證據引用 |
| M06 | Servant conversation/revision fake | 外部 Agora、M05/M07 | 修訂請求、結果與版本衝突 |
| M07 | review、signing provider fake | M05/M06/M08 | 覆核、freeze bundle、核驗簽章 |
| M08 | signed bundle、entitlement snapshot | M07/M02/M09 | 簽章後自動發布、訂正撤回 |
| M09 | publication 與channel fake | M08/M02、LegendFlow/通路 | feed、授權重查、回執與重送 |
| M10 | customer/service/activity fixtures | M01/M02/M09/M11/M12/M13 | 可交辦的續訂、升級與留存 |
| M11 | employment/service timeline fixtures | M02/M10/M12 | 歸屬、離職、公司Pool、成功重啟 |
| M12 | verified revenue/refund fixtures | M14/M10/M11 | 版本化可重算、調整與結算 |
| M13 | channel/contact/payment fixtures | M01/M07/M09/M14 | 同案跨渠道、客訴、服務恢復 |
| M14 | PSP/bank/GL/refund fake | M02/M04/M12/M13 | 財會、對帳、核決、稽核與共用可靠性 |

這些提供者是最終互通要求，**不是開工先後順序**。開發假件不得在 production 配置中載入；模組驗收證據必須標記 fake 或 real。

## 3. 工作區及檔案責任

`project/module-manifest.json` 是十四工作流機器可讀清單，每個 module 有獨立 branch/worktree、後端模組、前端 feature、worker 與 tests 目錄。任務包將功能對到既有 Issue #1–#14。單一模組可持續提交多個小 PR；每個 PR 要可審查且保持已整合分支可運作。

| 共用範圍 | Steward（職責，可由專責 agent 執行） | 模組作者的操作 |
|---|---|---|
| API/Event/SDK 契約 | Integration coordinator | 在 PR 提出相容 schema diff＋fixtures；不各自覆寫全檔 |
| core schema／跨模組 FK／migration 順序 | Data steward | 寫 `database/proposals/Mxx/`，由 steward 分配 migration 編號 |
| auth、tenant context、command bus、outbox/inbox | Platform steward | 透過已定義 interfaces 開發；需要擴充時送具體契約 |
| UI shell、design tokens、路由與共用元件 | UI steward | 模組 feature export 登錄，避免十四人改同一入口 |
| requirements／lockfile／CI／deploy | Build steward | 提 dependencies/配置 diff，統一 lock 更新 |
| E2E、跨模組測試與merge queue | QA/integration coordinator | 提實作證據，整合者執行全域驗證 |

Steward 是協調責任，不新增串行產品階段。各模組在共用底層實作期間依既定 interface 和 fake 開發；shared platform 與十四條業務線同步進行。

## 4. 契約變更與避免互相等待

1. 用最新主分支契約產生自己的 typed client/server interface，固定 baseline SHA。
2. 新增 optional 欄位、相容事件版本可向 coordinator 提出；breaking change 必須附受影響的 module 清單、雙版本支援與 fixtures。
3. 在整合者處理期間，消費者繼續以自己的 adapter boundary 加兼容 mapping，不直接改其他模組資料庫。
4. 同一 aggregate 命令由單一 owner 寫；跨域用 domain port 或 durable event，禁止互相循環 HTTP 呼叫造成分散式 transaction。
5. 任何 unresolved business decision 用策略介面與兩組測試隔離，僅對該正式決策動作關閉 production enablement；其他開發不暫停。
6. 每日以 contract compatibility report 對照所有十四 streams，漂移當日修正，不能最後才發現資料欄位不同。

## 5. Merge 與整合

每個模組的 PR 都包含本模組正反案例、授權隔離、版本競爭、重試、稽核證據與畫面狀態。可以依完成速度合併，不能等待十四個巨大 PR 同時衝突。合併不是宣告產品範圍已完成。

整合分組同步執行：A 客戶/契約/審查 M01–04；B 研究/簽章/發布 M05–09；C CRM/歸屬/獎金 M10–12；D 客服/財會/內控 M13–14。另有跨組 E2E：簽約→開權限→接收建議→客訴→退款→收入與獎金沖回。

## 6. 全範圍 release gates

| Gate | 驗證內容 | 通過條件 |
|---|---|---|
| G0 契約基線 | schema、資料owner、mock、任務包、風險決策 | 十四工作流可同時實作 |
| G1 模組實作 | 每個module的UAT與安全/可靠性 | 14/14 實作完成，無僅stub成功 |
| G2 整合 | 本平台跨模組E2E與adapter契約；可取得時測真sandbox | 本平台完整通過；外部未接線另列NOT_CONNECTED，不修改外部專案 |
| G3 全量交付 | 對帳/退款/簽章/稽核/DR/業務制度 | 全部必要測試通過、參數簽核、正式部署證據 |

G0–G3 是所有工作流共用的驗證門檻，不分期刪掉任何功能。程式可在 G0 同時開工；供應商 sandbox 未開通不影響其餘程式、負向測試和管理畫面。

## 7. 協調者每日狀態

每個 Mxx 回報：base/head SHA、目前實作項、已通過測試、契約變更、integration blockers、下一個可交付 PR。禁止只報百分比或以文件存在當作程式完成。以尚未完成的驗收項決定下一步；不要用「再規劃 MVP」替換已確認範圍。

## 8. 外部專案不可變更

任何工作流均不得修改Pantheon或LegendFlow專案、建立PR或部署；只做本repo內串接規格、mock及adapter。外部契約驗證於可取得的sandbox進行，未取得時如實標記待對接，不要求agent去外部repo補實作。十四個本平台模組仍依既定範圍開發。
