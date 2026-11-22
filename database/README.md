# PostgreSQL 資料層交付

本目錄是十四模組共用的 **資料契約與可執行 migration 基線**，涵蓋完整產品範圍。資料庫尚不代表十四個應用模組已實作。各模組可同時依此開發、以 stub/fixtures 驗證邊界，不必等待其他模組上線。

## 檔案與執行順序

| 檔案 | 用途 |
|---|---|
| [migrations/0001_domains.sql](migrations/0001_domains.sql) | 14 個領域與 shared core 的真實表、欄位、FK、CHECK、COMMENT |
| [migrations/0002_isolation_indexes.sql](migrations/0002_isolation_indexes.sql) | 全表 tenant RLS、版本／歷史保護、FK 索引 |
| [migrations/0003_business_guards.sql](migrations/0003_business_guards.sql) | 接受／權益／簽章／發布／Pool／正式計獎／過帳的跨表完整性 |
| [migrations/0004_database_roles.sql](migrations/0004_database_roles.sql) | NOLOGIN 模組能力角色及明確 read dependencies |
| [catalog.json](catalog.json) | 機器可讀的表與逐欄規格 |
| [data-dictionary.md](data-dictionary.md) | 模組 ownership、用途、PII 分級、保存分類、逐欄資料字典 |
| [seeds/001_synthetic.sql](seeds/001_synthetic.sql) | 僅開發／測試用合成案例，不含真客戶或真憑證 |
| [tests/001_invariants.sql](tests/001_invariants.sql) | PostgreSQL 實際 constraint／歷史保護／業務 gates 測試 |
| [tests/002_rls_roles.sql](tests/002_rls_roles.sql) | 一般 PostgreSQL server 執行的 tenant/角色隔離測試；standalone 不可取代 |
| [tools/verify_database.py](tools/verify_database.py) | 在 disposable PostgreSQL 完整執行 migrations、seed、tests |
| [migration-operations.md](migration-operations.md) | 版本管理、回復、並行變更與線上升級程序 |

使用 PostgreSQL 16 或相容更新版。`pgserver` 為可選本機測試工具；正式 CI 應使用 PostgreSQL 16 service container 驗證目標版。

```bash
# 已有 disposable DB（連線 URI 由秘密管理注入，不要寫入 repo）
python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL

# 可選：本機 Python bundled PostgreSQL 自動啟動
python database/tools/verify_database.py --ephemeral
```

上述命令只對空白測試資料庫執行。migration 要由受控 migration principal 執行；runtime 身分不可持有建表、改 schema、CREATEROLE 或 BYPASSRLS 權限。正式 migration runner 需由平台實作並維護 checksum/history/互斥鎖；此驗證程式不冒充 production deployer。

## 共通規約

- PK/FK 都包含 `tenant_id`。UUID 由服務或資料庫生成；時間使用 UTC `timestamptz`，報表／薪酬月份明確轉 `Asia/Taipei`。
- `numeric(20,6)` + Decimal；API 傳十進位字串。四捨五入只在政策指定的結算層，保留原始高精度與尾差歸公司紀錄。
- 可更新列以 `UPDATE ... WHERE tenant_id=:tenant AND id=:id AND version=:expected` 更新；零列回 API 409/412。trigger 自增 `version`，不取代呼叫端 CAS 條件。
- 一個業務交易含狀態、audit 與 outbox，提交後 worker 才送外部。inbox 以 consumer/producer/event_id 唯一鍵去重並比較 payload hash；external exactly-once 不作不實承諾。
- `core.object_asset` 只有私有物件版本與 hash。正文、KYC、對話錄音由授權下載 proxy/KMS 控制；公開 repo 及事件匯流排不得放原文。
- `report.signature` 只存驗簽成功之 immutable evidence；pending/failed 位於 `report.signature_request`。API 統一 signature status 是 request＋evidence＋revocation 的讀取投影。
- `customer.customer` 個資加密欄位由 application-layer envelope encryption 使用；API 的顯示名稱／語系不代表可以直寫 KYC 或 verified 欄位。
- 權益啟用、報告發布、獎金付款仍需應用層角色、職務分離、政策版本、現時授權檢查；SQL 守住重要完整性，不能把存在 FK 當成已通過法遵。

## RLS 與角色

每個 request/worker transaction 由可信任 server 執行 `SET LOCAL app.tenant_id = ...`，不接受瀏覽器傳來 tenant 或 actor 任意指定。登入角色對應單一模組能力角色；請勿把十四個角色全部授與同一通用 runtime login。SQL 注入與 GUC 偽造是應用邊界問題，RLS 不是 token 認證。

`longhao_m01` … `longhao_m14` 僅擁有本域寫入與列明的讀取。`longhao_m04` 具 AML 操作能力，其餘模組只可按需取得 `aml.business_clearance` 最小安全結果；客服和 CRM 不可讀案件明細。`longhao_audit` 只讀一般稽核資料；AML 稽核需額外授權 `longhao_aml_audit`。DB grants 不能取代人員角色 ABAC。

`staff_credential` 是不可變資格證據，既有資格的撤銷寫入 `core.credential_revocation`；不得直接修改歷史資格檔。驗簽 gate 同時檢查有效期、撤銷事件與真實簽署時點。

一般模組對 `core.audit_event` 只有 INSERT，不能讀整個平台稽核紀錄。一般稽核角色受 classification RLS 排除 AML；AML 稽核角色使用獨立授權。`object_asset` 的 restricted_aml metadata 也限 AML 模組／AML 稽核讀取，不能從全域檔案列表側漏案件線索。

公司所有接受／拒絕／補件決策存 `contract.acceptance_decision`；只有 accept 才能另建 `company_acceptance` 並帶同一決策 ID。模板、Pool 與獎金政策的條款新建後不得原地修改；同 ID 的 approval endpoint 只可一次填 `approved_by/approved_at` 並轉 approved，或其後標示 retired。修改係數、價格、起算規則等要新 policy/package version，已核准證據不可覆寫。

## API／資料表 mapper

OpenAPI 是對外契約，資料表是內部持久化模型，以下差異必須由 repository／read-model adapter 明確轉換，不能讓前端假設表名或直接 CRUD 資料庫。

| API／事件欄位 | 資料層 |
|---|---|
| resource `state` | 多數表的 `status`，精確 enum 依各資源 schema |
| `allowed_actions` | 依 auth／ABAC／版本／狀態計算，不存為可自行修改的權限陣列 |
| ReportVersion `version_number` | `report_version.report_version_no` |
| ReportVersion `source_candidate_id` | `report_version.candidate_id` |
| ReportVersion `report_asset_id` | `report_version.content_asset_id` |
| `report_content_sha256` | `report_version.content_sha256`／`signature.content_sha256` |
| `bundle_manifest_sha256` | `report_version.bundle_sha256`／`signature.bundle_sha256` |
| ReportVersion `bundle_manifest_asset_id` | 同名 FK 指向 `core.object_asset`，並保留 `bundle_manifest` 結構化索引 |
| `signed_file_sha256` | `signature.signed_file_sha256`；待簽 read model 可為 null |
| Signature `state` | signature_request＋signature＋revocation 的組合投影 |
| Review `approve/request_changes/reject` | review_decision 的 `approved/changes_requested/rejected` |
| customer `display_name` | 解密授權後的 PII read model；不能直接暴露 ciphertext/key metadata |
| customer `preferred_locale` | `customer.customer.locale` |
| event `schema_version` | `core.outbox.schema_version` SemVer 字串，與 events schema 相同 |
| event `aggregate_id` | 按 registry 定義，例如 signature verified 事件使用 report_version ID |
| ServiceResponse `approval_id` | `support.response_approval.id`；不是可自行提供的 asset 或任意 UUID |
| ServiceResponse `state` | `support.response_dispatch.status`，provider facts 另存 attempt/receipt |

`seeds/signature_event.json` 與 DB outbox seed 表達同一 canonical envelope，可拿來跑跨 API/event/DB fixture 檢查。驗證 runner 還會 **讀取實際持久化的 outbox row**，組成 UTC wire envelope，對 event JSON Schema、payload hash 與 aggregate ID 檢查；不是只驗證旁邊的 JSON 範例。這些都是合成資料，物件和簽章 provider 參考均非真正服務端點或有效憑證。

禁止通用 `DELETE`。保存期、legal hold、物件版本、資料主體請求由受控清理工作流程處理；目前政策未核定時 fail closed，不以範例日數自動刪除。

## 已決定與待核定的分界

客戶先簽、再 KYC；公司接受後才依條件生效並開權益。驗簽成功與發布 outbox 必須同一交易，無第二個人工 Publish。七天未聯絡不得作為回 Pool 規則；資料模型支援**全部付費服務中斷連續七天**的待核定解讀及離職立即回 Pool。3% 獎金池率未經核准，seed 的 base_pool_rate 為 null，正式計獎被阻擋；100%／50%／25% 與介入續約 30/70 已記錄，交互算法待政策核定。
