# M11 需求 → API → 測試追蹤

| REQ | UAT | API | 測試 |
| --- | --- | --- | --- |
| REQ-M11-001 | UAT-M11-001 | `list_company_pool`, `get_ownership_history` | `test_uat.py::test_uat_m11_001_self_service_customer_without_owner_stays_in_company_pool` |
| REQ-M11-002 | UAT-M11-002 | `record_employee_departure`, `release_maintenance_owner` | `test_uat.py::test_uat_m11_002_departure_returns_to_pool_immediately` |
| REQ-M11-003 | UAT-M11-003 | `assign_maintenance_owner` | `test_uat.py::test_uat_m11_003_direct_assignment_grants_no_commission` |
| REQ-M11-004 | UAT-M11-004 | `claim_pool_customer` | `test_uat.py::test_uat_m11_004_redevelopment_claim_grants_no_commission_either` |
| REQ-M11-005 | UAT-M11-005 | `evaluate_company_pool` | `test_uat.py::test_uat_m11_005_seven_day_rule_is_not_seven_days_without_contact` |
| REQ-M11-006 | UAT-M11-006 | `evaluate_company_pool` | `test_uat.py::test_uat_m11_006_only_full_168_hour_interruption_is_a_candidate` |
| REQ-M11-007 | UAT-M11-007 | `evaluate_company_pool` | `test_uat.py::test_uat_m11_007_one_lapsed_monthly_service_does_not_offset_an_active_annual_service` |
| REQ-M11-008 | UAT-M11-008 | `evaluate_company_pool` | `test_uat.py::test_uat_m11_008_unapproved_policy_blocks_live_recovery_but_allows_preview` |
| REQ-M11-009 | UAT-M11-009 | `evaluate_company_pool` | `test_uat.py::test_uat_m11_009_compliance_restricted_customer_is_never_a_sales_worklist_candidate` |
| REQ-M11-010 | UAT-M11-010 | `claim_pool_customer` | `test_uat.py::test_uat_m11_010_only_one_concurrent_claim_succeeds` |
| REQ-M11-011 | UAT-M11-011 | `assign_maintenance_owner`, `get_ownership_history` | `test_uat.py::test_uat_m11_011_handoff_never_rewrites_history` |
| REQ-M11-012 | UAT-M11-012 | `evaluate_company_pool` | `test_uat.py::test_uat_m11_012_service_recovery_resets_the_interruption_clock` |

## 事件 producer/consumer

| 事件 | 方向 | 測試 |
| --- | --- | --- |
| `ownership.assignment_changed` | 發出 | 全部 `test_uat.py` 寫入路徑間接驗證（經 outbox snapshot）；`test_worker.py` 驗證 publish/retry/deadletter |
| `ownership.pool_entered` | 發出 | 同上 |
| `ownership.employee_departed` | 發出／接收 | `test_uat.py::test_uat_m11_002_*`（發出）、`test_consumer.py::test_self_consumed_employee_departed_is_a_defensive_noop`（接收） |
| `contract.effective` | 接收 | `test_consumer.py::test_contract_effective_establishes_company_pool_baseline`、`test_contract_effective_without_policy_records_window_but_skips_pool_baseline` |
| `contract.terminated` | 接收 | `test_consumer.py::test_terminated_event_for_unknown_contract_is_not_found` |
| `contract.service_ended` | 接收 | 經 `ContractWindow.effective_end()` 在 `test_uat.py` 的中斷判定情境間接覆蓋 |

## 未執行 / 不適用

- PostgreSQL 層驗證（`database/proposals/M11/`）：NOT_RUN，需 disposable PostgreSQL 16。
- `apps/admin` TypeScript `tsc -b`：NOT_RUN，本環境未授權安裝 npm 依賴。
- 真實 Pantheon／Agora／LegendFlow／HR adapter 接線：NOT_CONNECTED。
