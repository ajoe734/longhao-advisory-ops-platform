# M04 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M04-001 / UAT-M04-001 | start_aml_screening, receive_aml_screening（possible_match 開案） | `test_uat_m04_001_possible_match_creates_restricted_case_and_blocks_clearance` |
| REQ-M04-002 / UAT-M04-002 | decide_aml_case 拒絕 service/LLM principal；automation 無核定能力 | `test_uat_m04_002_ai_summary_principal_cannot_decide` |
| REQ-M04-003 / UAT-M04-003 | get_aml_case/list_aml_cases 403；get_business_clearance 僅安全欄位 | `test_uat_m04_003_general_ops_cannot_read_case_or_filing_detail` |
| REQ-M04-004 / UAT-M04-004 | ScreeningDispatchWorker provider timeout → error/manual_required | `test_uat_m04_004_provider_timeout_is_not_treated_as_clear` |
| REQ-M04-005 / UAT-M04-005 | add_aml_case_action + decide_aml_case maker-checker | `test_uat_m04_005_false_positive_release_requires_independent_checker` |
| REQ-M04-006 / UAT-M04-006 | 新 screening（list v2）不覆寫舊 screening（v1） | `test_uat_m04_006_list_version_refresh_blocks_and_keeps_v1_screening` |
| REQ-M04-007 / UAT-M04-007 | submit_aml_declaration 不影響 business_clearance | `test_uat_m04_007_declaration_signature_cannot_substitute_screening` |
| REQ-M04-008 / UAT-M04-008 | aml.case_changed / business_clearance.changed payload 欄位斷言 | `test_uat_m04_008_restricted_event_only_carries_safe_summary` |
| REQ-M04-009 / UAT-M04-009 | add_aml_case_action(request_edd) 中性投影 | `test_uat_m04_009_edd_request_is_neutral_outside_the_workbench` |
| REQ-M04-010 / UAT-M04-010 | grant_case_read 限定時效；過期即撤回讀取 | `test_uat_m04_010_auditor_needs_scoped_time_boxed_grant` |
| REQ-M04-011 / UAT-M04-011 | apply_legal_hold + RetentionSweeper skipped_hold | `test_uat_m04_011_open_case_under_legal_hold_is_never_cleaned_up` |
| REQ-M04-012 / UAT-M04-012 | 過期 approved clearance 投影為 expired | `test_uat_m04_012_expired_clearance_cannot_be_reused` |

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI/event schema、inbox hash/sequence/tenant、outbox lease/retry；`test_security_automation.py` 驗 tenant isolation、customer 自助聲明防冒用、webhook adapter 角色、最小化 automation；`test_ui_contract.py` 驗八種 UI 狀態及可及性標記、一般平台元件不洩漏受限欄位。
