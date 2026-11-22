# Migration、並行變更與回復程序

## 1. 十四模組同時開發

各模組對 `catalog.json` 的既有欄位與型別開發。模組工程師可以同時提資料變更，但不直接重寫別人的 migration：在 PR 內列出新增表／欄位、API/schema 變更、資料回填、相容性、索引成本、回復方式。資料整合負責人分配下一個 migration 編號並合併到共用基線。**這是共用檔整合規則，不是模組開發先後順序。**

本次 `0001`–`0004` 是尚未部署的首版 schema；首次部署後，任何修改都使用新增 migration，禁止編輯歷史 checksum。`tools/build_schema.py` 只用於此初始基線和字典同步；投入生產後 generator 必須改為輸出提案，不可直接覆寫已部署 SQL。

## 2. 部署 gate

1. CI 在全新 PostgreSQL 16 執行 migrations、synthetic seed、兩份 SQL assertion suites。
2. 在空白與上一版真實結構的匿名化資料量測試 migration；不得把真客戶資料複製到 public repo 或開發者機器。
3. 檢查 table lock、最長 statement time、backfill batch、WAL 增長、磁碟、複寫延遲、RLS grants 變化。
4. migration principal 持有 DDL/CREATEROLE；runtime principals 沒有 DDL 或 BYPASSRLS。分離 DB/schema owner 與應用登入。
5. runner 取得 repository-wide advisory lock，查 migration history 的版本與 SHA-256；checksum 不符停止。
6. 每個 migration 在明確 transaction 內執行。`CREATE INDEX CONCURRENTLY` 類型須獨立非交易 migration，不能偷塞到目前 BEGIN/COMMIT。
7. 寫 migration history、release commit、開始結束時間與操作身分。檢查 read/write health 與資料一致性後才放流量。

## 3. 回復策略

首版空資料庫初始化失敗時 transaction rollback，再修正未部署 migration 重新建立 disposable DB。**正式環境不提供 `DROP SCHEMA CASCADE` 當一般 rollback。**

已含營運資料的版本依 expand → backfill → compare → switch → contract 升級。應用回滾必須仍能讀寫新舊相容欄位；破壞性欄位刪除延後到相容觀察窗後。資料損毀採 PITR／備份還原至隔離實例、差異比對與受控切換，不能用反向 SQL 反算已發送消息、已簽報告或已付款。

每個新增 migration PR 要提供：可重入性、schema diff、最大鎖時間、回填程序、驗證查詢、與上一版相容性、失敗停損點、restore 檢查。回滾保留 audit/outbox/provider receipts，對外副作用採補償事件。

## 4. 交易、併發與一致性

涉及跨域 gate 的命令（公司接受、權益啟用、簽章登錄、發布、Pool 回收、正式計獎、過帳）使用 `SERIALIZABLE`，對 `40001`／deadlock `40P01` 以原 idempotency key 有限重試。單純本人草稿修改可 `READ COMMITTED` + CAS。SQL gate 不單獨保證跨兩個並行交易沒有 race；應用整合測試必須執行雙連線競爭情境。

Pool assignment 以 tenant/customer advisory transaction lock 序列化歸屬調整，區間重疊被拒；每次計算查全部付費 service，不能只看一張月訂契約。客戶狀態不得因沒有聯絡就被錯當付費服務終止。

金流／簽章／訊息外部呼叫不包在長時間 DB transaction 內。先提交意圖與 outbox，再呼叫 provider；逾時先查交易結果，不把 timeout 當失敗後重新收費或重簽。

## 5. 保存與刪除

每筆物件有保存政策和版本，legal hold 以 append-only 指令保存。保存類型與起算事件需法遵／財會核定；未核定的 `duration_days=null`、起算未發生、active hold 任一情況都禁止自動刪除。

日後清理 worker 使用專用、限時、限範圍 role；檢查依賴、合約義務終了、report provided 事件、爭議、備份保存與物件鎖。產生 deletion manifest 和審核證據後清理，不能把 GDPR/個資刪除要求直接轉成所有歷史 `DELETE`。schema 本身目前不授一般模組 DELETE。

## 6. 驗證的真實範圍

`--standalone` 會執行 PostgreSQL 16 真正 DDL、PL/pgSQL、FK、CHECK、transaction、deferred constraint、seed 及業務 gate assertions；它不使用網路 socket。PostgreSQL single-user mode 始終特權執行，因此 **不得用這個模式宣稱 RLS/角色隔離通過**。`tests/002_rls_roles.sql` 只能由 `--dsn-env`／`--ephemeral` 的一般 server 執行；CI 應使用一般 server。跨連線 race、效能、備份還原及正式 provider integration 屬後續實作驗收，不能由本 schema 測試替代。
