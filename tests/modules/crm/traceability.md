# M10 需求 → API →測試 追蹤

| REQ | UAT | 通過條件 | 測試 |
| --- | --- | --- | --- |
| REQ-M10-001 | UAT-M10-001 | 只一待辦含缺件原因、安全摘要、owner、next_action及due_at | `test_uat.py::test_uat_001_incomplete_application_single_actionable_work_item` |
| REQ-M10-002 | UAT-M10-002 | conversion type self_serve；不因有maintenance owner就給sales獎金 | `test_uat.py::test_uat_002_self_service_renewal_is_not_sales_conversion` |
| REQ-M10-003 | UAT-M10-003 | 保存活動／交易／因果與核定者；核定後才可用sales attribution | `test_uat.py::test_uat_003_sales_assisted_conversion_requires_prior_activity_and_transaction` |
| REQ-M10-004 | UAT-M10-004 | evidence_insufficient；不能approve為業務介入 | `test_uat.py::test_uat_004_blank_or_automated_contact_is_evidence_insufficient` |
| REQ-M10-005 | UAT-M10-005 | 不因report未讀標必然流失；以可觀測服務訊號附原因 | `test_uat.py::test_uat_005_unread_report_alone_never_implies_churn` |
| REQ-M10-006 | UAT-M10-006 | 不派行銷／升級催促；保留必要客服工作與抑制原因碼 | `test_uat.py::test_uat_006_suppression_blocks_sales_push_but_keeps_service_followup` |
| REQ-M10-007 | UAT-M10-007 | 規則阻擋轉為可聯絡升級名單；可提示需要重評 | `test_uat.py::test_uat_007_upgrade_requires_suitability` |
| REQ-M10-008 | UAT-M10-008 | 保留各自欄位與有效時間；計獎只用核定transaction owner | `test_uat.py::test_uat_008_four_owner_types_are_independent` |
| REQ-M10-009 | UAT-M10-009 | 只一有效歸因或明確批准分配；pending/disputed不進正式payout | `test_uat.py::test_uat_009_disputed_claims_never_double_pay` |
| REQ-M10-010 | UAT-M10-010 | 標late_entry需獨立核定，不改created_at；不自動獲獎金 | `test_uat.py::test_uat_010_late_entry_activity_cannot_backdate_attribution` |
| REQ-M10-011 | UAT-M10-011 | 按distinct application/customer去重；withdraw另列 | `test_uat.py::test_uat_011_funnel_replay_dedupes_and_tracks_withdrawal_separately` |
| REQ-M10-012 | UAT-M10-012 | 回原work_item或更新優先；保留owner與已完成結果 | `test_uat.py::test_uat_012_repeated_sweep_never_duplicates_open_work_item` |

## API operationId → 測試覆蓋

| operationId | 正向 | 反向／邊界 |
| --- | --- | --- |
| `create_opportunity` | `test_uat_001/002/.../*`（透過 `_create_opportunity_for`） | `test_security_automation.py::test_marketing_cannot_create_opportunity`、`test_contract_worker_http.py::test_idempotency_key_reused_with_different_payload_conflicts` |
| `list_opportunities` | — | 角色 scope 於 `test_security_automation.py::test_sales_cannot_access_another_sales_persons_opportunity`（同一授權路徑） |
| `get_opportunity` | `test_uat_003/008/009/010` | `test_contract_worker_http.py::test_unknown_opportunity_is_404`、`test_security_automation.py::test_sales_cannot_access_another_sales_persons_opportunity` |
| `record_sales_activity` | `test_uat_002/003/004/008/009/010` | `test_uat_004`（evidence_insufficient）、`test_uat_010`（late_entry） |
| `set_next_action` | `test_uat_001/006/007` | `test_uat_006`（suppression blocks）、`test_uat_007`（upgrade reassessment）、`test_contract_worker_http.py::test_missing_if_match_is_428`／`test_stale_if_match_is_412` |
| `claim_conversion` | `test_uat_002/003/008/009/010` | `test_uat_009`（under_review／已決定衝突）、`test_security_automation.py::test_sales_cannot_claim_credit_for_a_colleague` |
| `decide_conversion` | `test_uat_002/003/008/009` | `test_uat_004`（evidence_insufficient）、`test_security_automation.py::test_sales_cannot_decide_conversion`（maker-checker 經由角色先擋） |
| `create_crm_suppression` | `test_uat_006`、`test_ui_contract.py::test_work_item_suppressed_state_disables_set_next_action_for_sales_push` | — |
| `get_funnel_metrics` | `test_uat_011` | `test_ui_contract.py::test_funnel_metrics_page_shape_matches_ui_fields` |
| `list_crm_worklists` | `test_uat_001/005/012` | `test_ui_contract.py::test_marketing_role_only_sees_company_pool_worklist` |

## 事件 producer/consumer → 測試覆蓋

| 事件 | 方向 | 測試 |
| --- | --- | --- |
| `crm.opportunity_changed` | 發出 | `test_contract_worker_http.py` 的 outbox 測試（透過 `create_opportunity`/`decide_conversion` 產生） |
| `crm.conversion_decided` | 發出 | `test_uat_002/003/008/009`（approve/dispute/reversed 路徑） |
| `crm.suppression_changed` | 發出 | `test_uat_006` 間接覆蓋（suppression 建立必經此路徑） |
| `customer.created`／`customer.consent_changed` | 接收 | `test_uat_008`、`test_contract_worker_http.py::test_consumer_rejects_payload_hash_mismatch_on_replay` |
| `application.created`／`application.customer_signed`／`application.company_decided` | 接收 | `test_uat_011`、`test_contract_worker_http.py::test_consumer_holds_sequence_gap_for_retry` |
| `contract.effective`／`contract.terminated`／`contract.service_ended` | 接收 | `test_uat_011`（terminated）、`test_uat_012`（effective） |
| `entitlement.changed` | 接收 | `test_uat_005`、`test_uat_007` |
| `ownership.assignment_changed`／`ownership.pool_entered` | 接收 | `test_uat_008`、`test_ui_contract.py::test_marketing_role_only_sees_company_pool_worklist` |
| `ownership.employee_departed` | 接收 | 僅 envelope-level 契約驗證；本模組無對應業務效果（見 `workers/tasks/crm/consumer.py` 註解） |
| `support.case_opened`／`support.case_resolved` | 接收 | 僅投影更新，未獨立列測試（無 UAT 直接依賴；由 `_CONTRACTS`/schema 驗證覆蓋結構正確性） |
| `finance.payment_confirmed`／`finance.payment_failed` | 接收 | `test_uat_011`（confirmed via funnel）、`record_payment_failed` 由 service 單元邏輯覆蓋 |
| `finance.refund_confirmed` | 接收 | `test_contract_worker_http.py::test_refund_confirmed_reverses_an_approved_conversion`（含重播不重複 reverse）、`test_refund_confirmed_for_unknown_contract_is_a_no_op` |
| `kyc.refresh_required` | 接收 | `test_uat_001` |

## 未覆蓋 / NOT_RUN

- `support.case_opened`/`resolved` 對應的業務效果（若未來與 suppression 聯動）未定義也未測試，目前僅記錄投影。
- PostgreSQL 層的 CHECK／RLS／索引驗證（`database/proposals/M10/001_crm.sql`）未對真實資料庫執行；需
  `database/tools/verify_database.py`，等資料治理者併入正式 migration 後才能跑。
- HTTP／FastAPI 端到端（經 `longhao_platform.http.create_app()`）僅手動以 `TestClient` 驗證過路由掛載本身，未納入自動化回歸（見 `README.md` 已知限制）。
