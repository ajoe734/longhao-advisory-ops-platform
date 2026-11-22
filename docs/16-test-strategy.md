# 16｜測試策略、追蹤與全量驗收

## 1. 四種證據不能混用

規格驗證（schema/links/SQL語法）證明文件可供工程使用；mock測試證明邊界預期；模組實作測試證明本平台程式；可取得的外部sandbox與公司正式設定驗證才證明實際接線/上線條件。報告各自status，不把mock回200當真供應商成功。

本次文件交付的執行證據在 `project/verification.md`、`database/verification-report.json` 及GitHub Actions。模組UAT見 `qa/uat.md`、機器清單 `qa/test-catalog.json`、追蹤 `qa/traceability.csv`。

## 2. 分層測試

| 層 | 實施內容 | 責任 |
|---|---|---|
| 契約 | refs可解、schema正反例、eventregistry、breakingchange | API steward |
| SQL | 空DB migrations/seed、FK/check、immutability、gates | Data steward |
| RBAC/RLS | 正常server/非superuser角色、跨tenant與AML隔離 | Platform＋每module |
| 模組unit | 邊界/日期/金額/狀態/計獎可重現 | 十四agents各自 |
| consumer/provider | adapter fake、重複/失序/timeout/未知 | 整合module |
| UI | loading/empty/denied/stale/conflict、鍵盤與可讀性 | 模組UI＋UI steward |
| E2E | 全鏈路簽約→審查→權限、簽章→發布、服務→收入→退款→佣金 | QA coordinator |
| 非功能 | 壓測、混沌、恢復、資料匯出、資安 | 平台/公司owner |

## 3. 必須共同跑的E2E

1. 官網先簽，尚未KYC/未承諾時付款也不開權限；補件後審查通過，公司承諾、契約生效及開通各有時間與證據。
2. 同客戶月費及年費並行；月費終止只關對應scope，仍有年費不進全客戶流失Pool。
3. Persona候選→Servant新revision→舊review失效→新覆核→exact bundle簽章→自動feed發布；沒有第二人工publish。
4. 授權收件快照後客戶被撤權；dispatch重查阻擋，其他合格客戶仍可收。
5. 訊息provider已接受但回應丟失；同key重試/query不重發，過期指示不補送。
6. 客戶正常自動續訂且未讀report，仍是留存；空聯絡/指派不產業務conversion獎金。
7. 業務離職回company，新業務有真實介入成功續訂，後續歸屬給新人，舊退款回原entry。
8. 年費預收、部分履約、未履約退款與已認列退款分開，收入/佣金不重複扣減，GL保持平衡。
9. 客服線上到電話同案、驗身不明不錯合併；投資問題轉分析師、退款轉財會，客服看不到AML詳情。
10. 假AMLclear callback、跨tenant UUID、prompt injection、同人製覆核都被拒絕且有適當紀錄。
11. crash在commit後publish前，outbox恢復不遺失；consumer已執行回應丟失，inbox去重不重做。
12. 復原備份後重建feed/ledger，逐項對provider已接受效果，不重退款/重通知。

細節與固定IDs以QA目錄為準；本文件的清單是全局關係，不另造第三份不一致的驗收來源。

## 4. 合成資料與時間控制

使用 `.invalid` email、假的provider references與固定UUID；不得用真身份證、真手機、真憑證或真卡資料。Clock與ID provider可注入，測DST/閏日/週年/月結/月底/UTC與AsiaTaipei交界。金額Decimal，涵蓋零、負調整、部分分攤、尾差與溢位；任何溢位/不平都不能靜默截斷。

## 5. 測試環境

十四worktree可各有獨立DB/端口；schema固定公共baseline，不共享可互相污染的測試資料。stub統一在mocks與fixtures，不各自造不同enum。CI正常Postgresserver驗RLS，standalone PostgreSQL僅作實際SQL與業務constraint驗證，明確不代替RLS結果。

## 6. 缺陷、完成及不在範圍的外部改動

每個module的acceptance failure直接對回REQ與Issue，不用一個通用「測試通過」結案。安全/財務/未簽發布缺陷阻擋相關正式啟用；全部十四本平台模組都需完整驗收。

Pantheon/LegendFlow程式不在修改範圍。adapter可對本平台fixtures完整驗收；對方未提供實際接口的部分單列integration readiness，不將修改外部repo當必要交付，也不因此停止其他module。取得可用sandbox後只測本平台adapter與提供的介面，不部署外部專案。
