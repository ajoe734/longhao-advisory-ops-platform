# M11 需求 → API → 測試追蹤

| REQ | UAT | API | 測試 |
| --- | --- | --- | --- |
| REQ-M11-001 | UAT-M11-001 | `list_company_pool`, `get_ownership_history` | `test_m11_uat.py::test_uat_m11_001_self_service_customer_without_owner_stays_in_company_pool` |
| REQ-M11-002 | UAT-M11-002 | `record_employee_departure`, `release_maintenance_owner` | `test_m11_uat.py::test_uat_m11_002_departure_returns_to_pool_immediately`；離職撤銷未來操作權：`test_authorization.py::test_departed_salesperson_cannot_claim_pool_customer`、`test_departed_salesperson_cannot_be_assigned_a_new_customer` |
| REQ-M11-003 | UAT-M11-003 | `assign_maintenance_owner` | `test_m11_uat.py::test_uat_m11_003_direct_assignment_grants_no_commission` |
| REQ-M11-004 | UAT-M11-004 | `claim_pool_customer` | `test_m11_uat.py::test_uat_m11_004_redevelopment_claim_grants_no_commission_either` |
| REQ-M11-005 | UAT-M11-005 | `evaluate_company_pool` | `test_m11_uat.py::test_uat_m11_005_seven_day_rule_is_not_seven_days_without_contact` |
| REQ-M11-006 | UAT-M11-006 | `evaluate_company_pool` | `test_m11_uat.py::test_uat_m11_006_only_full_168_hour_interruption_is_a_candidate` |
| REQ-M11-007 | UAT-M11-007 | `evaluate_company_pool` | `test_m11_uat.py::test_uat_m11_007_one_lapsed_monthly_service_does_not_offset_an_active_annual_service` |
| REQ-M11-008 | UAT-M11-008 | `evaluate_company_pool` | `test_m11_uat.py::test_uat_m11_008_unapproved_policy_blocks_live_recovery_but_allows_preview` |
| REQ-M11-009 | UAT-M11-009 | `evaluate_company_pool`, `list_company_pool`, `claim_pool_customer` | `test_m11_uat.py::test_uat_m11_009_compliance_restricted_customer_is_never_a_sales_worklist_candidate`、`test_uat_m11_009b_customer_restricted_after_entering_pool_is_live_suppressed`、`test_uat_m11_009c_restricted_customer_with_still_active_service_is_blocked_not_silently_ineligible` |
| REQ-M11-010 | UAT-M11-010 | `claim_pool_customer` | `test_m11_uat.py::test_uat_m11_010_only_one_concurrent_claim_succeeds` |
| REQ-M11-011 | UAT-M11-011 | `assign_maintenance_owner`, `get_ownership_history` | `test_m11_uat.py::test_uat_m11_011_handoff_never_rewrites_history` |
| REQ-M11-012 | UAT-M11-012 | `evaluate_company_pool` | `test_m11_uat.py::test_uat_m11_012_service_recovery_resets_the_interruption_clock` |

## 第11節 LLM 接手摘要／聯絡重點

| 能力 | 測試 |
| --- | --- |
| untrusted draft、model/prompt/ruleset 版本、source_refs | `test_automation.py::test_handoff_summary_is_untrusted_draft_with_versions_and_source_refs` |
| 人工 adopted/modified/rejected 審閱紀錄 | `test_automation.py::test_handoff_review_requires_adopted_summary_only_when_modified`、`test_handoff_review_for_unknown_run_is_rejected` |
| 最小化輸入（拒絕原始個資／自由文字） | `test_automation.py::test_handoff_automation_rejects_non_minimized_input` |
| 角色限制（僅 sales/sales_supervisor） | `test_automation.py::test_handoff_automation_requires_sales_role` |
| 無 ownership/activity/commission 寫入權 | `test_automation.py::test_handoff_automation_never_touches_ownership_state` |
| production fake guard | `test_automation.py::test_production_handoff_automation_refuses_local_fake` |

## 事件 producer/consumer

| 事件 | 方向 | 測試 |
| --- | --- | --- |
| `ownership.assignment_changed` | 發出 | 全部 `test_m11_uat.py` 寫入路徑間接驗證（經 outbox snapshot）；`test_worker.py` 驗證 publish/retry/deadletter/redrive |
| `ownership.pool_entered` | 發出 | 同上 |
| `ownership.employee_departed` | 發出／接收 | `test_m11_uat.py::test_uat_m11_002_*`（發出）、`test_consumer.py::test_self_consumed_employee_departed_is_a_defensive_noop`（接收） |
| `contract.effective` | 接收 | `test_consumer.py::test_contract_effective_establishes_company_pool_baseline`、`test_contract_effective_without_policy_records_window_but_skips_pool_baseline`、`test_contract_effective_propagates_inbound_correlation_and_causation_ids`、`test_contract_effective_for_a_restricted_customer_creates_a_suppressed_baseline` |
| `contract.terminated` | 接收 | `test_consumer.py::test_terminated_event_for_unknown_contract_is_not_found` |
| `contract.service_ended` | 接收 | `test_consumer.py::test_service_ended_tracks_each_contract_service_independently`（之前版本從未被呼叫；現在直接驗證多 `contract_service_id` 不互相覆寫，且 `effective_end()` 取最新已知值）；中斷判定情境在 `test_m11_uat.py` 間接覆蓋 |
| 信封驗證（classification／UUID） | 接收 | `test_consumer.py::test_envelope_with_mismatched_classification_is_rejected`、`test_envelope_with_non_uuid_correlation_id_is_rejected` |

## HTTP／Idempotency／Audit

| 行為 | 測試 |
| --- | --- |
| 首次 assignment（客戶無任何既有列）可經 HTTP 到達 | `test_http.py::test_first_ever_assignment_for_a_customer_with_no_prior_row_is_reachable_via_http` |
| Idempotency replay 保留原 ETag | `test_http.py::test_idempotency_replay_keeps_the_original_etag` |
| 授權失敗也寫拒絕 audit | `test_authorization.py::test_forbidden_claim_still_writes_a_rejection_audit` |
| `evaluate_company_pool` 每次恰好一筆 audit（不重複） | `test_authorization.py::test_evaluate_company_pool_writes_exactly_one_audit_per_call` |

## 跨租戶隔離

| 測試 |
| --- |
| `test_authorization.py::test_other_tenant_cannot_see_or_claim_a_pool_entry` |

## Worker：outbox／job lease 生命週期

| 行為 | 測試 |
| --- | --- |
| Outbox dead-letter 人工重驅 | `test_worker.py::test_outbox_publisher_redrive_moves_dead_letter_back_to_retry` |
| TaskRunner lease/complete/fail（可注入 clock） | `test_worker.py::test_task_runner_lease_complete_and_fail_use_the_injectable_clock` |
| TaskRunner dead-letter 後人工重驅 | `test_worker.py::test_task_runner_fail_deadletters_after_max_attempts_then_redrive` |

## Admin UI 靜態契約檢查

本環境無 `node_modules`／測試執行器，以下為純文字靜態核對（`test_admin_ui_contract.py`），
非行為測試：

| 檢查 | 測試 |
| --- | --- |
| `ViewState` 宣告全部必備 kind | `test_view_state_type_declares_every_required_kind` |
| 三個畫面都渲染 `stale` 與 `conflict` 橫幅 | `test_company_pool_workbench_handles_stale_and_conflict_banners`、`test_maintenance_timeline_handles_stale_and_conflict_banners`、`test_policy_preview_result_handles_stale_and_conflict` |
| `.m11-banner-stale` 樣式存在 | `test_stale_banner_style_exists` |

## 未執行 / 不適用

- PostgreSQL 層驗證（`database/proposals/M11/`）：NOT_RUN，需 disposable PostgreSQL 16
  才能實際套用 `verify_database.py`。本輪已用 `pglast.parse_sql()` 做純語法靜態檢查
  （PASS，11 top-level statements），但未驗證 trigger/CHECK 的實際執行結果。
- `apps/admin` TypeScript `tsc -b`：NOT_RUN，本環境未授權安裝 npm 依賴（無
  `node_modules`）；改用上方「Admin UI 靜態契約檢查」的純文字核對替代，不等同
  型別檢查或行為測試。
- 真實 Pantheon／Agora／LegendFlow／HR adapter 接線：NOT_CONNECTED。
