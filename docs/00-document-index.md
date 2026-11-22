# 文件總目錄與工程交付基線

版本：2.0；基準日期：2026-11-22（Asia/Taipei）。這份交付用於十四模組**同時開發、全範圍完成**，不縮減為MVP。

## 如何交給HerdR

讀[根AGENTS](../AGENTS.md)、[平行開發計畫](../agents/PARALLEL_DEVELOPMENT.md)、[HerdR手冊](../agents/HERDR_RUNBOOK.md)，再將[協調者提示詞](../agents/COORDINATOR_PROMPT.md)交給協調agent。十四工作流清單在[module-manifest](../project/module-manifest.json)，每個模組有獨立規格、任務包與Issue。

## 一、總體SA/SD與營運設計

| 文件 | 主題 | 開發時用途 |
|---|---|---|
| [01-product-scope.md](01-product-scope.md) | 產品範圍 | 十四模組全交付與外部界線 |
| [02-SA.md](02-SA.md) | 系統分析SA | 角色、端到端使用情境、規則與例外 |
| [03-SD.md](03-SD.md) | 系統設計SD | 架構、資料owner、交易、授權與可靠性 |
| [04-integrations.md](04-integrations.md) | 外部串接 | Pantheon/Agora/LegendFlow及供應商ports |
| [05-workflows.md](05-workflows.md) | 狀態機 | 先簽後審、研究發布、歸屬、退款與客服 |
| [06-crm-commission.md](06-crm-commission.md) | CRM與獎金 | 工作清單、來源/歸屬、試算與制度草案 |
| [07-finance-compliance-control.md](07-finance-compliance-control.md) | 財會與內控 | 收款/對帳/預收/收入/退款/GL/AML |
| [08-data-model.md](08-data-model.md) | 資料模型ERD | 模組schema、關聯、資料責任與migration |
| [09-api-events.md](09-api-events.md) | API與事件設計 | 版本、idempotency、安全、消息與測試 |
| [10-ui-ux.md](10-ui-ux.md) | UI/UX | 34個管理畫面與欄位/互動/錯誤狀態 |
| [11-security-rbac.md](11-security-rbac.md) | 權限與資安 | 角色、ABAC、職責分離與威脅案例 |
| [12-llm-automation.md](12-llm-automation.md) | LLM庶務 | 允許任務、草稿契約、成本、回退與評估 |
| [13-evidence-retention.md](13-evidence-retention.md) | 證據與保存 | 簽章/交付/計獎證據鏈、留存及匯出 |
| [14-nfr-observability.md](14-nfr-observability.md) | 非功能需求 | 可測目標、容量、metrics與故障降級 |
| [15-deployment-runbooks.md](15-deployment-runbooks.md) | 部署與維運 | 環境/CI/CD/回復/事故/DR/交接 |
| [16-test-strategy.md](16-test-strategy.md) | 測試策略 | 契約/模組/UI/E2E/真接線證據區別 |
| [17-sources-compliance.md](17-sources-compliance.md) | 來源與法遵對照 | 公司控制文件、公開法源與差異 |
| [18-decision-register.md](18-decision-register.md) | 決策紀錄 | 已確認方向、待核定參數、owner與影響 |
| [19-document-control.md](19-document-control.md) | 版本變更控制 | 相容變更、契約權威與文件維護 |
| [20-release-handoff.md](20-release-handoff.md) | 全量驗收與移交 | 工程完成、外部readiness與公司接管 |

## 二、模組細規與任務

- [十四模組SA/SD](modules/README.md)：用例、領域欄位、狀態、資料表、API/events、UI、錯誤與驗收。
- [十四HerdR任務包](../agents/task-packets/README.md)：工作區、owned paths、替身、可立即開工項目、整合與Definition of Done。
- [Issue更新內容](../project/issue-bodies/01.md)：01至14對應GitHub #1–#14；文件完成不自動關閉實作Issue。
- [完成證據模板](../project/completion-template.md)：避免只報進度百分比或mock成功。

## 三、可機器驗證的契約

| 交付 | 入口 | 內容 |
|---|---|---|
| HTTP契約 | [OpenAPI](../api/openapi.yaml)、[operation清單](../api/operation-catalog.json) | 完整request/response/security/error/CAS/idempotency |
| Adapter契約 | [ports](../api/adapter-ports.yaml)、[DB projection map](../api/database-projection-map.yaml) | 外部引用、內部read models、授權責任 |
| Event契約 | [registry](../events/registry.json) | schema、正反例、producer/consumer、順序/去重 |
| 資料庫 | [README](../database/README.md)、[dictionary](../database/data-dictionary.md)、[catalog](../database/catalog.json) | 真SQL、FK/check/RLS、資料分級與保存 |
| Mock | [使用手冊](../mocks/README.md) | 十四模組平行開發的本地provider替身 |
| 權限 | [RBAC矩陣](../config/rbac-matrix.csv) | 每個API操作與角色/資源scope |
| 獎金策略 | [draft policy](../config/commission-policy.example.json)、[試算](../config/commission-simulation.json) | 已確認係數與未核定rate/mode分離 |
| UI共用設計 | [元件契約](../design/component-contracts.md)、[tokens](../design/tokens.json) | 全後台一致的布局、互動與語義 |

## 四、驗收、部署及治理

- [UAT](../qa/uat.md)、[機器案例](../qa/test-catalog.json)、[需求追蹤CSV](../qa/traceability.csv)。案例存在與已執行的PASS分開。
- [SQL實跑工具](../database/tools/verify_database.py)、[migration作業](../database/migration-operations.md)、[本機驗證報告](../database/verification-report.json)。
- [CI](../.github/workflows/ci.yml)、[本地PostgreSQL](../infra/compose.dev.yaml)、[開發相依](../requirements-dev.txt)。
- [文件檢查](../scripts/validate_docs.py)、[契約檢查](../scripts/validate_contracts.py)、[SQL語法檢查](../scripts/validate_sql.py)、[公開repo檢查](../scripts/check_public_repository.py)。
- [十四worktree準備工具](../scripts/prepare_worktrees.py)：預設plan-only、明確apply才建，不reset既有工作。
- [九份ADR](adr/README.md)、[內部制度p5/p9修訂草案](policies/internal-management-amendment-draft.md)。草案未修改原PDF，也未冒稱已核准。
- [實際驗證與剩餘狀態](../project/verification.md)。

## 五、不可逾越的工作範圍

只改本營運平台；不得修改Pantheon或LegendFlow專案、提交PR或部署。Agora/Servant也作外部依賴。缺外部介面時完成本repo的spec/mock/adapter，列待對接清單，十四模組仍繼續開發。

## 六、完成語義

本文件包是可分工開發基線。正式應用、實際供應商接線、公司policy核定與production啟用分別有證據，不能用文件/假件的檢查通過聲稱全平台已上線。
