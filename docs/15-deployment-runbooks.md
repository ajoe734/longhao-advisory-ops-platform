# 15｜環境、部署、CI/CD、事故與復原手冊

## 1. 環境與部署責任

| 環境 | 資料 | 介接 | 用途 |
|---|---|---|---|
| dev | 合成fixtures | 本地mock | 十四worktree同時開發 |
| test/CI | 每次建立disposable DB | deterministic fakes | 契約/SQL/權限/模組驗證 |
| staging | 合成或公司批准非正式資料 | 可取得的供應商sandbox | 本平台整合/負載/演練 |
| production | 公司正式資料 | 正式已核定供應商 | 人員授權、公司控制、全量gate後啟用 |

不修改Pantheon或LegendFlow的程式/設定/部署；本repo只交付自己的API與adapter。對方未提供sandbox接口時記待接線，不在外部專案建立「補介面」PR。

目標部署單元為admin靜態資產、API、worker/scheduler、PostgreSQL、private object storage、telemetry與secret manager。正式provider/IaC帳戶未指定，因此本文定義輸入、拓撲與runbook，不假造已部署網址。`infra/compose.dev.yaml`僅供本平台資料庫測試。

## 2. 配置與秘密

非秘密配置：environment、module flags、provider modes、tenant/rolepolicy版本、retention與commission policy refs、timeouts/retry budget、allowed origins、locale/timezone、logging分類。秘密：DBpassword、IdP credentials、PSP/API credentials、HMAC keys、簽章HSM引用、KMS權限，由secret manager/runtime注入。

production啟動guard：禁止mock adapter；所有必要policy approved；指定IdP和aud；無預設dev credentials；TLS/儲存加密；migration與schema相容；provider health明確。sourcehash、image digest、schema及policy版本寫release manifest。不從repo讀production .env。

## 3. CI分層

本規格包CI：文件連結/14module覆蓋、OpenAPI/JSONSchema/正反fixtures、mock boundary tests、SQL parser、正常Postgres16 migration/seed/invariants/RLS與public repository檢查。

應用實作後同一CI擴充：lint/type/unit、module contract、browser component/E2E、dependency/secret/SAST、image scan/SBOM、migration compatibility。尚無應用測試不能以空job綠勾假稱產品CI完整。PR檢查與main檢查同一組gate；外部secret不提供fork PR。

所有GitHub actions以查證的commit SHA pin，permissions最小contents:read。安全掃描的結果需保留工具版本與範圍；自製PII/secret pattern是輔助檢查，不等於完整滲透測試。

## 4. 本平台正式部署順序

1. 取得全量release候選：十四modules完整、已通過必要tests、已核定production設定。
2. 驗backup可用、old/new schema相容、rollback界線；保存release manifest。
3. apply expand型migration（新欄位/表），驗checksum與執行鎖，禁止用runtime role改schema。
4. 部署相容API/worker，暫停高風險外部side effect至health驗證完成；read-only smoke與權限檢查。
5. 在核定窗口啟用本平台worker、scheduler及production routes，觀測簽章/權限/對帳/事件。
6. 確認指標與對帳；舊欄位刪除只在另次contract migration完成回填與consumer切換後進行。

這是部署的技術步驟，不代表把業務模組分期省略。正式啟用不會替外部專案部署。

## 5. 回復策略

可回復的API/UI image用先前digest；migration不任意down/drop已產生的signed evidence/financial rows。schema優先forward fix或相容回復。簽章、付款、退款、外部訊息一旦外部接受，不能靠DB rollback撤回；需查provider與追加補償/更正記錄。

發現重大權限洩漏或未核准內容，先依範圍停該consumer/route、保留證據，不全庫清除。恢復前核對inbox/outbox/provider receipts，將unknown逐筆reconcile。

## 6. Runbooks

| 情境 | 第一處置 | 修復與復核 |
|---|---|---|
| 已簽未發布 | 查signature event與outbox/inbox、確認未withdrawn/expired | 修worker/lease後同event重放，不重新假簽 |
| 訊息未知或延遲 | 查provider id與有效期限，暫停盲重送 | 已過期記suppressed；更正需要新的有效依據 |
| 付款/退款unknown | 停相同financial intent重試 | 查provider/bank，同key恢復，差異maker/checker處理 |
| AML供應商失敗 | 案件保持pending、不得clear | 恢復篩檢、檢查list freshness與受影響期間 |
| 誤認客戶/身份綁定 | 限制該binding、保全接觸及簽署證據 | CMS主管經驗身移除錯binding，任何merge可追溯 |
| 錯誤權限或離職仍可登入 | 即撤session/role、停敏感動作 | 核對active assignments、access logs與補救 |
| 獎金金額不平 | 停該batch正式export | 重算diff、核對policy/revenue/refund，不改歷史paid |
| 已發布報告需修正 | 先按權限暫停新增交付/標示撤回 | 新版覆核簽章，向適當受眾送更正，原文保留 |
| 死信堆積 | 依事件類別/原因分流 | 修原因、演練idempotent redrive，記actor/reason |
| 資安/資料洩漏 | incident owner隔離、保全logs和時間 | 公司啟動通報/調查程序，法律期限按適用事件核定 |

## 7. 備份/DR演練

備份包括DB WAL/basebackup、object versions、政策與設定、KMS key references及restore權限。金鑰不公開備份到Git。復原環境先禁外部送信/退款，恢復DB與objects、校hash和RLS、重建projection、核對provider已發生效果，再受控啟用。本平台復原不等於Pantheon/LegendFlow也已復原，adapter需容忍外部不可用。

演練至少驗一份先簽後審契約、一個已簽報告與發送、一筆部分退款和獎金調整、restricted AML access。記實際RPO/RTO與缺失；備份完成log不能代替可還原證據。

## 8. 公司交接

交付repo/版本、環境manifest、role清單、supplier聯絡與內部owner、policy核定、操作與事故runbook、restore證據、未決對接項、供應商限額、監測dashboard與例外queue。production密碼走核定secret manager；不把管理權限交給每個開發agent。
