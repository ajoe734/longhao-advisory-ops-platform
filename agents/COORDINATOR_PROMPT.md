# 貼給 HerdR 協調者的任務

你要完成 `ajoe734/longhao-advisory-ops-platform` 的全部十四模組。使用者已指定一次完成全部範圍並盡可能平行；不能改成 MVP 或先只實作一條流程。請立即進行工程工作，不只產生計畫。

1. 讀 `AGENTS.md`、`docs/00-document-index.md`、`agents/PARALLEL_DEVELOPMENT.md`、`project/module-manifest.json`。核對 Git main head 與工作樹，保留他人修改。
2. 依十四份 task packet 建立十四獨立 worktree/branch，各自執行。使用已安裝 HerdR 的 help/schema 找合法命令，記錄實際 workspace/pane ID；不要虛構 CLI。
3. 同時啟動或分派 M01–M14，讓每個工作流立刻用共享契約與 mocks 開始。指派額外共用責任：API/Event steward、Data steward、Platform/UI steward、QA/integration。共用責任與十四模組平行；不要要求其他人先停工等待。
4. 每個 agent 交付自己的 backend、frontend、worker、驗證、監控與文件；外部 provider 暫時不可用時，仍完成 adapters、fake 與consumer tests，列出真實sandbox整合待辦。不得假造真正付款、簽章或通知成功。
5. 使用明確資料owner；共享檔案由steward收斂。契約變更要帶受影響模組與相容方案，不直接改壞其他人的interfaces。
6. 業務規則依文件與最新使用者決策。先簽後審、公司承諾後啟用；Persona候選→分析師覆核修訂簽章→系統即發布；訊息連結已簽研究；業務實際轉換才計獎；獎金未核定參數不可默認live。
7. 逐一把所有 UAT 實作成有證據的測試，同時跑跨模組E2E。PR可持續合併，但所有十四模組都通過前不得宣告全案完成。
8. 禁止讀寫正式客戶、KYC、AML、金鑰或production付款/訊息；公開GitHub只存合成fixtures。憑證與正式配置由公司管理。
9. 每次回報已做成的實作、實際並行數、各模組PR/測試、阻塞與下一個具體修復。不要把「文件已完成」當成「軟體已完成」，不要再次詢問已授權的開發範圍。

完成標準：M01–M14 全部功能、正反驗收、授權/資料隔離、本平台adapter契約與可取得的sandbox結果、簽章立即發布、訊息證據鏈、退款/收入/獎金重算、客服案件、備份還原、正式部署與操作交接都有證據。外部正式參數與供應商存取尚未取得時，只把相關正式啟用標為blocked，其他能做的工作持續完成。

硬性邊界：禁止修改Pantheon與LegendFlow的程式、設定或部署，禁止往那兩個專案提交PR。本repo只定義對接spec、fake及adapter；外部未支援的API列gap，不能為了讓測試過關去改外部系統。Agora/Servant亦按外部系統處理。
