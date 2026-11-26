# M11 維護歸屬與公司Pool模組

此目錄實作 `api/openapi.yaml` M11 的 7 個 operationId：`list_company_pool`、
`assign_maintenance_owner`、`claim_pool_customer`、`release_maintenance_owner`、
`record_employee_departure`、`evaluate_company_pool`、`get_ownership_history`。
`domain.py` 保存業務不變量與 `ownership.policy_version`/`ownership.assignment`/
`ownership.pool_entry` 的記憶體模型；`service.py` 是唯一的命令/查詢入口；
`http.py` 提供 framework-neutral controller 及可選的 FastAPI router（未安裝 FastAPI
時仍可 import 與跑測試，跟 M01 的 `create_fastapi_router` 同一慣例）；`ports.py` 的
fake 只接受合成 fixture，production composition 會拒絕 fake adapter。`automation.py`
是獨立的 LLM 接手摘要/聯絡重點 assistant（第11節），完全不 import
`OwnershipPoolService`，也沒有任何 assignment/pool_entry/departure/policy 的寫入權。

## 關鍵不變量

- `ownership.assignment` 每位客戶同時最多一筆 `valid_until IS NULL`；不是 `sales`
  就是 `company_pool`，兩者在同一張表、同一條時間軸上接續，沒有缺口。只有
  `owner_type == "sales"` 的列會變成 `MaintenanceAssignment` wire 資源——
  `salesperson_id` 在 OpenAPI 是必填 UUID，`company_pool` 列永不曝光（詳見
  `database/proposals/M11/README.md` 的契約協調說明）。所有關閉interval的路徑
  （`assign_maintenance_owner`/`claim_pool_customer`/`release_maintenance_owner`/
  `record_employee_departure`/`evaluate_company_pool`）都經過 `service.py`
  的 `_close_interval()`，拒絕 `valid_until < valid_from` 並一律填 `end_state`。
- claim／assign／release／departure／evaluate 全部先驗 actor/role 與「actor／目標
  業務是否已離職」（`_employee_departed`，REQ-M11-002：離職立即撤銷未來操作權，
  不只限於 M01 的 session 撤銷），再比 CAS 版本，最後驗業務前提（法遵限制、policy
  核准狀態）；狀態、audit 與 outbox 同一交易提交。授權／驗證失敗也會寫一筆拒絕
  audit（透過 pre-flight try/except 轉 `_audit_failure`），不只限於通過 `_run_command`
  的 mutate-time 例外。
- `list_company_pool`／`claim_pool_customer`／`_evaluate_customer` 都在查詢／操作
  當下即時呼叫 `RestrictionPort.is_restricted`，不只信任 pool entry 建立當時記下的
  `state`／`suppressed_reason`——客戶可能在入池「之後」才被法遵停權。
- `evaluate_company_pool` 的 lapse 判定完全從目前已知的 `contract.effective` /
  `contract.terminated` / `contract.service_ended` 投影重新計算（見下），不靠
  任何已失效的「上次算過」快取；服務恢復自然會重置中斷時鐘。live 執行關閉／開啟
  interval 一律用 `as_of`（評估基準時間），不是 worker 實際執行當下的真實時鐘。
- 未核准的 `ownership.policy_version` 永遠可以 `dry_run` 預演，但 `dry_run=false`
  一律 422 `policy_not_approved`——不會自動改用另一個已核准政策頂替。
- 法遵停權（`RestrictionPort.is_restricted`）只回一個布林值；sales 看到的 pool
  worklist 永遠不會多出任何 AML 案件細節。

## 外部依賴（開發時以 fake 取代）

- `ports.py::RestrictionPort`：M04/M13 的安全停權摘要（"M13/04 safe restriction
  flags"）。
- `ports.py::EvidencePort`：`core.object_asset` 存在性檢查（HR 離職文件、移交證據）。
- `contract.effective`/`contract.terminated`/`contract.service_ended`
  （M02）：M11 是 `events/registry.json` 登記的 consumer，由
  `workers/tasks/ownership_pool/consumer.py` 消費並維護本模組自己的
  `StoreState.contracts` 讀模型——不是 port，因為真正的契約是「事件驅動投影」，
  不是同步查詢。
- `crm.activity`/`crm.conversion`（M10）：依資料字典列為唯讀依賴，但本次 7 個
  operationId 都不需要同步呼叫 M10；`claim_pool_customer` 建立的 assignment 永遠
  `commission_entitlement_granted=false`，真正的銷售歸因與獎金核定完全是 M10/M12
  的職責。

以上均為 `NOT_CONNECTED`：本模組只有 adapter port 與 local fake，沒有修改或呼叫外部
repository／production endpoint。

## LLM 接手摘要／聯絡重點（第11節）

`automation.py::HandoffAutomationService` 僅接受已最小化的結構化輸入（predecessor
的 `reason_code`/`valid_from`/`valid_until`、signals 的 `signal_type`/`occurred_at`/
`evidence_asset_id`，不接受自由文字或原始個資），輸出一律是
`status="untrusted_draft"` 並記錄 `model_version`/`prompt_version`/`ruleset_version`
與 `source_refs`；人工 `record_review`（adopted/modified/rejected）才是最終決策。
`LocalHandoffAssistant` 是本機 deterministic fallback（`is_fake=True`），`environment=
"production"` 時建構子會直接拒絕 fake adapter（同 M01 `automation.py` 先例）。這個
service 完全不 import `OwnershipPoolService`，也沒有拿到任何 assignment/pool_entry/
departure/policy 的寫入方法——無法指派 owner、無法製造活動證據、碰不到任何歷史獎金。

## 已知限制

- `ContractWindow.service_ended_ats`（`workers/tasks/ownership_pool/consumer.py`）
  記錄每個已知 `contract_service_id` 的 `ended_at`，`effective_end()` 取其中最新者，
  修正了舊版單一 scalar 被任一事件覆寫、資料遺失的 bug；但 M02 沒有發布「這個
  contract 共有幾個 service」或「全部 service 已結束」的訊號，所以單一 contract 內
  如果有多個 service，其中一個結束不能單獨證明全部都結束——這點與
  REQ-M11-007（跨 contract 的多服務情境）已由多筆 `ContractWindow` 聚合正確處理不同，
  仍是本次事件集合下的結構性限制，需要 M02 補發訊號才能完全解決。
- `service.py::_close_interval()` 只拒絕 `valid_until < valid_from`（嚴格早於），
  允許兩者相等——這是因為既有測試套件以固定測試時鐘建立 pool baseline 與其後續
  assign/claim 常落在同一瞬間；真實環境的 DB CHECK（`valid_until > valid_from`，
  `database/migrations/0001_domains.sql`）比這嚴格。正式接線到真 PostgreSQL
  repository 時，若真實時鐘精度不足以區分兩個緊接的寫入，仍可能被 DB 拒絕，
  需另外處理（例如人工指定略晚於 baseline 建立時間的 `effective_from`）。

## 已知待整合事項

- `database/proposals/M11/` 提出三項與既有基線 migration 的契約協調（
  `pool_entry.reason_code` 列舉擴充、新增 `assignment.end_state` 欄位、同步更新
  `ownership.guard_pool_entry()` trigger 認得新列舉值）；本模組的記憶體 domain
  物件已經用新版語意建模，正式接線需 integration/data steward 套用該提案後，把
  `repository.py` 換成真正的 PostgreSQL adapter。提案 SQL 已用 `pglast.parse_sql`
  做靜態語法檢查（PASS），但仍需 disposable PostgreSQL 16 實際套用驗證
  （`verify_database.py`，本環境 NOT_RUN）。
- 本次 7 個 operationId 沒有 policy 生命週期端點，但 `ownership.policy_version`
  是每筆 assignment/pool_entry 的必要 FK。`service.py` 的 `propose_policy_version`
  / `approve_policy_version` 是內部／運維管理方法（未發布的 operationId，同
  M01 `revoke_external_identity` 的先例），供測試與 ops tooling 直接呼叫；正式
  policy 管理端點若要對外發布，需先送 API steward 做契約變更。
