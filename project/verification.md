# 本輪規格交付與實際驗證

基準日期：2026-11-22（Asia/Taipei）。本輪完成的是十四模組完整開發規格、資料與介面契約、模擬服務、驗收/協作/維運文件；不宣稱十四模組應用已完成或已在HerdR執行。

## 交付規模

| 項目 | 數量/範圍 |
|---|---|
| 全域設計 | 20份主題文件＋總目錄、9份ADR、制度修訂草案 |
| 單元規格 / HerdR任務包 / Issue替換內容 | 各14份 |
| HTTP API | 142 operations、190 schemas |
| 事件 | 47種、各有schema與合成example |
| 資料庫 | 4 migrations；90張表（89 catalog表＋core.tenant）；1251個catalog欄位 |
| 管理UI設計 | 34個畫面、共用元件契約與tokens |
| 業務驗收 | 168 module UAT＋14跨域E2E＝182；仍為NOT_RUN |

## 已實際執行

| 命令/驗證 | 本輪結果 |
|---|---|
| `python scripts/validate_docs.py` | 14平行工作流、142 API完整對映、182驗收/追蹤與相對連結通過 |
| `python scripts/validate_contracts.py` | 289正向、342反向schema案例通過；refs/security/event/adapter一致性通過 |
| `python scripts/validate_sql.py` | 7份SQL、2600個top-level statements語法通過（不等於RLS） |
| `python -m pytest -q mocks/test_server.py` | 8項HTTP mock測試通過 |
| `python scripts/prepare_worktrees.py --base main` | 正確產生14個獨立branch/worktree計畫，未建立或啟動使用者agent |
| `python scripts/check_public_repository.py` | 指定pattern與檔案類型檢查通過，不當作全面資安/PII保證 |
| `python database/tools/verify_database.py --standalone --report database/verification-report.json` | PostgreSQL16.2實際apply 4 migrations/seed，52個業務constraint assertions通過 |
| 持久outbox→canonical event | 從實際DB讀出report.signature_verified，schema、payload hash與identity/有效期通過 |

詳見[SQL執行證據](../database/verification-report.json)與[DB-event證據](../api/db-event-verification.json)。Mock測試本地環境曾出現外部pytest plugin的deprecation warning，不影響8項通過；不將warning偽報為零警告。

## 正常PostgreSQL/RLS與CI

本工作環境無法啟動一般socket server，standalone執行具特權，**不能證明RLS或runtime roles已通過**。正常PostgreSQL16 CI已在[run 45026440882的資料庫job](https://github.com/ajoe734/longhao-advisory-ops-platform/actions/runs/45026440882/job/137220397308)通過4份migration、52項業務約束及12項RLS/角色隔離，共64項assertions。此run的文件job因pip cache未指定requirements-dev.txt路徑而在安裝前失敗；已補cache-dependency-path，完整後續結果見[GitHub Actions](https://github.com/ajoe734/longhao-advisory-ops-platform/actions)。不得將單一job通過誤稱整個run通過。

## 不混淆的未完成項

- 182個業務UAT是後續軟體實作驗收，目前全NOT_RUN；契約測試不代表這182項已做完。
- 本平台的應用command handlers、UI、workers、正式provider adapters由十四HerdR任務實作；此文件包包含其完整契約與工作範圍。
- Pantheon、LegendFlow、Agora只預留外部spec/mock/本平台adapter邊界；未修改其repo、PR或部署，真接線為NOT_CONNECTED。
- 獎金率、衰減交互、Pool明確判定及公司正式條款等未定案參數以draft策略隔離，避免擅自啟用。這些不會把全部十四模組變成串行開發。

## 2026-11-23 共用 runtime／UI／QA 實作

基準 `4b9adeb09485386c04d6ce38d789e5058ebe2082` 的 integration worktree 已新增 FastAPI composition root、React/TypeScript 34-screen shell、module plugin discovery、authenticated principal、CAS/idempotency command bus、canonical event schema validation、audit/outbox/inbox reference semantics及 M07→M08→M09 跨模組測試。十四條 module implementation 尚未合併到本 checkout，`longhao-api --check` 因此正確顯示 `degraded`、`0/14 loaded`、`release_gate_passed=false`。

本次實際結果：

| 命令/驗證 | 結果 |
|---|---|
| `PYTHONPATH=apps/api/src python -m pytest -q qa/test_platform_runtime.py qa/test_cross_module_runtime.py qa/test_ui_registry.py` | 15 passed、1 skipped；skip 是 sandbox 無 DNS，無法安裝 FastAPI/httpx 以跑 TestClient，核心／event／UI registry 測試均通過 |
| `PYTHONPATH=apps/api/src python -m longhao_platform.main --check` | PASS；47 event contracts、14 個 module 均為 `MODULE_NOT_INTEGRATED`、無 broken module |
| `python scripts/validate_docs.py` | PASS；14 modules、142 operations、182 acceptance/trace rows |
| `python scripts/validate_contracts.py` | PASS；190 schemas、47 events、289 positive、342 negative cases |
| `python scripts/validate_sql.py` | PASS；7 files、2600 statements |
| `python scripts/check_public_repository.py` | PASS；261 files 的 targeted checks |
| `python -m pytest -q mocks/test_server.py ...` | 環境阻擋；sandbox 禁止建立 loopback socket，既有 8 個 mock HTTP tests 在 setup 出現 `PermissionError`；新增測試仍通過 |
| `python database/tools/verify_database.py --standalone --report /tmp/longhao-database-verification.json` | 環境阻擋；缺 `pgserver`，且 sandbox DNS 不可用，不能安裝 `requirements-db-local.txt` |
| `npm install --prefix apps/admin --ignore-scripts --no-audit --no-fund` | 環境阻擋；npm registry 無 DNS，已終止等待，未產生 lockfile、未執行 TypeScript/Vite build |

上述環境阻擋不改寫為 PASS。CI 已加入 editable FastAPI package、共用／跨模組 tests、runtime check 與 React build；目前直接相依 exact pin，但應在可連 registry 的 integration run 產生並審查 `package-lock.json`，再把 `npm install` 收斂成 `npm ci`。外部 Pantheon、LegendFlow、Agora/Servant 仍為 NOT_CONNECTED，未讀寫或修改其 repository／production。
