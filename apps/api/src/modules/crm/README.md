# M10 · 客戶漏斗、業務維護與轉換歸因

依 [docs/modules/M10-crm.md](../../../../../docs/modules/M10-crm.md) 實作。
本模組的 domain + in-memory adapter 可獨立測試；`http.py` 提供
framework-neutral controller 及可由 shared app 掛載的 FastAPI router
（`create_fastapi_router`，不帶 `/api/v1` 前綴，交由
`longhao_platform.http.create_app()` 統一掛載）。`plugin.py`（`MODULE_ID = "M10"`、`register(runtime)`）以本模組的
`InMemoryRepository` 與 synthetic evidence adapter 組裝 `CrmService` 並掛上
router；production 因尚無真實 `core.object_asset` adapter，`CrmService` 的
生產環境防呆會讓註冊失敗並回報 `broken`。shared repository 與跨模組 port 仍待整合。

## 模組結構

- `domain.py`：`Opportunity`/`SalesActivity`/`Conversion`/`CrmWorkItem`/
  `Suppression`/`MarketingAttribution` 等實體、API↔DB 列舉映射表
  （`OPPORTUNITY_KIND_TO_DB`、`OPPORTUNITY_STATE_TO_DB`、
  `WORK_ITEM_STATE_FROM_DB` 等）及其語意說明。
- `ports.py`：`core.object_asset` 存在性檢查的 protocol 與 deterministic fake
  adapter（M10 從不直接儲存原始通話錄音、逐字稿或客訴內容）。
- `repository.py`：copy-on-write in-memory store（同一交易內 state＋audit＋
  outbox＋idempotency 原子提交）。
- `service.py`：10 個 operationId 的 command/query，worker 用的
  `sweep_worklists`（REQ-M10-001/005/012 的定時名單計算入口）、各
  `record_*`／`reverse_conversion_for_refund` 上游事件投影方法，以及
  `_upsert_work_item`（UAT-M10-012 的待辦去重/回收邏輯）。
- `automation.py`：`daily_contact_draft`（唯一核准 LLM 能力）——只整理待辦為
  非決策性聯絡草稿，不可自行指派owner、改名單資格或核准任何歸因/計獎。
- `http.py`：framework-neutral controller；`create_fastapi_router` 延遲匯入
  FastAPI。

## 關鍵業務規則對應

| 規則 | 實作位置 |
| --- | --- |
| REQ-M10-002 自助續約不計業務轉換 | `decide_conversion`：`approved_salesperson_id=null` 核准＝self_service，不檢查證據 |
| REQ-M10-003/UAT-004 業務轉換需真實雙向證據 | `record_sales_activity` 計算 `qualifies_for_claim`；`decide_conversion` 在 `approved_salesperson_id` 非空時要求至少一筆 qualifying activity，否則 `evidence_insufficient` |
| REQ-M10-006 禁止聯絡/未結抱怨抑制銷售 | `_sales_push_blocked`／`_SALES_PUSH_BLOCKING_SCOPES`（含 `marketing_contact`）：`set_next_action` 除 `resolve_issue` 外全數擋；`record_consent_changed`（marketing opt-out 建立抑制、re-grant 自動解除）與 `record_support_case_opened`/`record_support_case_resolved`（`complaint`+`high`/`urgent` 自動建立/解除抑制）皆發 `crm.suppression_changed`；`CrmWorkItem.to_api` 對 open/assigned/snoozed 項目投影 `suppressed` |
| REQ-M10-007 升級需服務適合度 | `_upgrade_suitability`：有 `restricted` entitlement 時 `create_opportunity` 直接建立為 `cancelled`，`set_next_action` 需先 `upgrade_assessment` |
| REQ-M10-008 四種owner不互相覆寫 | `Conversion` 分開保存 `claimed_salesperson_id`（最初認領）與 `sales_actor_id`（核准歸因）；`Opportunity.owner_actor_id`、`CrmWorkItem.owner_actor_id` 各自獨立欄位 |
| REQ-M10-009 爭議認領不重複計獎 | `claim_conversion` 以 `contract_service_id` unique upsert；第二筆認領進 `claim_history`，`Conversion.api_state()` 回報 `under_review`；`decide_conversion` 的 maker-checker 擋認領人自核 |
| REQ-M10-010 補寫活動不回填搶獎 | `_has_claimed_conversion`（任一已存在的 claim，不限 `approved`）判斷 `late_entry`；`late_entry` 時 `qualifies_for_claim=False`；`decide_conversion` 另獨立比對 `activity.occurred_at < conversion.created_at` 驗證時間因果，不只信任寫入時的旗標 |
| REQ-M10-011 漏斗重放與退出正確 | `get_funnel_metrics` 以 `application_id`/`customer_id` 的 `set` 去重；`churn` 另列 stage，不與 `active` 混算；`renewal`/`upgrade` 只計 `OPPORTUNITY_OPEN_STATES`（排除 lost/cancelled/converted）；`active` 排除已 `contract.terminated_at` 的服務線 |
| REQ-M10-012 待辦不因日批次重複 | `_upsert_work_item` 以 `dedupe_key = customer_id:worklist` upsert，terminal 狀態保留 owner/結果 |

## 已知限制與 NOT_CONNECTED

- `core.object_asset` 存在性檢查、`finance.refund_confirmed` 的
  `contract_id` 與本模組 `contract_service_id` 粒度假設、`claim_history`/
  `conversion_kind` 等欄位均需
  [database/proposals/M10](../../../../../database/proposals/M10) 併入正式
  migration 才在 PostgreSQL 生效；見該目錄 `contract-changes.md`。
- Pantheon／Agora／LegendFlow／金流／簽章均非本模組依賴；本模組僅消費
  M01/M02/M03/M09/M11/M13/M14 已發布事件的本平台 projection，不直接呼叫對方
  API；真實事件來源接線前標記 `NOT_CONNECTED`。
- `longhao_platform`目前的 `SyntheticAuthenticator` 僅提供
  `platform_admin`/`internal_auditor`/`platform_worker` 角色，尚未提供
  `sales`/`sales_supervisor`/`marketing`/`service_agent` 等本模組角色的測試
  principal；本模組測試改用 `ActorContext` 直接呼叫 `CrmService`，覆蓋同一
  授權邏輯，端到端走 HTTP 需等共用平台補上對應 principal 與本模組的
  `register(runtime)` 入口。
