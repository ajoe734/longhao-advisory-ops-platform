# M11 維護歸屬與公司Pool模組

此目錄實作 `api/openapi.yaml` M11 的 7 個 operationId：`list_company_pool`、
`assign_maintenance_owner`、`claim_pool_customer`、`release_maintenance_owner`、
`record_employee_departure`、`evaluate_company_pool`、`get_ownership_history`。
`domain.py` 保存業務不變量與 `ownership.policy_version`/`ownership.assignment`/
`ownership.pool_entry` 的記憶體模型；`service.py` 是唯一的命令/查詢入口；
`http.py` 提供 framework-neutral controller 及可選的 FastAPI router（未安裝 FastAPI
時仍可 import 與跑測試，跟 M01 的 `create_fastapi_router` 同一慣例）；`ports.py` 的
fake 只接受合成 fixture，production composition 會拒絕 fake adapter。

## 關鍵不變量

- `ownership.assignment` 每位客戶同時最多一筆 `valid_until IS NULL`；不是 `sales`
  就是 `company_pool`，兩者在同一張表、同一條時間軸上接續，沒有缺口。只有
  `owner_type == "sales"` 的列會變成 `MaintenanceAssignment` wire 資源——
  `salesperson_id` 在 OpenAPI 是必填 UUID，`company_pool` 列永不曝光（詳見
  `database/proposals/M11/README.md` 的契約協調說明）。
- claim／release／departure／evaluate 全部先驗 actor/role，再比 CAS 版本，最後驗
  業務前提（法遵限制、policy 核准狀態）；狀態、audit 與 outbox 同一交易提交。
- `evaluate_company_pool` 的 lapse 判定完全從目前已知的 `contract.effective` /
  `contract.terminated` / `contract.service_ended` 投影重新計算（見下），不靠
  任何已失效的「上次算過」快取；服務恢復自然會重置中斷時鐘。
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

## 已知待整合事項

- `database/proposals/M11/` 提出兩項與既有基線 migration 的契約協調（
  `pool_entry.reason_code` 列舉擴充、新增 `assignment.end_state` 欄位）；本模組的
  記憶體 domain 物件已經用新版語意建模，正式接線需 integration/data steward 套用
  該提案後，把 `repository.py` 換成真正的 PostgreSQL adapter。
- 本次 7 個 operationId 沒有 policy 生命週期端點，但 `ownership.policy_version`
  是每筆 assignment/pool_entry 的必要 FK。`service.py` 的 `propose_policy_version`
  / `approve_policy_version` 是內部／運維管理方法（未發布的 operationId，同
  M01 `revoke_external_identity` 的先例），供測試與 ops tooling 直接呼叫；正式
  policy 管理端點若要對外發布，需先送 API steward 做契約變更。
