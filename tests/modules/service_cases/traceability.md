# M13 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M13-001 / UAT-M13-001 | create_service_case, add_case_interaction, SLA timestamps unchanged | `test_uat_m13_001_cross_channel_case_keeps_single_sla_timeline` |
| REQ-M13-002 / UAT-M13-002 | add_case_interaction idempotency replay | `test_uat_m13_002_duplicate_provider_message_creates_one_interaction` |
| REQ-M13-003 / UAT-M13-003 | get_service_case unlinked-case field masking | `test_uat_m13_003_unverified_case_hides_contract_and_payment_links` |
| REQ-M13-004 / UAT-M13-004 | draft/get draft/approve/send/get response/receive receipt | `test_uat_m13_004_draft_requires_approval_before_send_and_tracks_real_status` |
| REQ-M13-005 / UAT-M13-005 | draft_service_response requires_human + escalate_service_case(analyst) | `test_uat_m13_005_investment_question_routes_to_analyst_not_adhoc_advice` |
| REQ-M13-006 / UAT-M13-006 | escalate_service_case(finance) + no approve/execute method | `test_uat_m13_006_refund_request_cannot_be_approved_or_executed_by_service_agent` |
| REQ-M13-007 / UAT-M13-007 | escalate_service_case(complaint) + SuppressionJobRunner | `test_uat_m13_007_major_complaint_suppresses_marketing_without_blocking_service` |
| REQ-M13-008 / UAT-M13-008 | escalate/assign preserve SLA due dates; clock advance | `test_uat_m13_008_waiting_internal_does_not_reset_sla_clock` |
| REQ-M13-009 / UAT-M13-009 | escalate_service_case(compliance) hides content; draft blocked | `test_uat_m13_009_aml_handoff_does_not_copy_content_into_timeline_or_llm` |
| REQ-M13-010 / UAT-M13-010 | resolve_service_case 422 then success + CaseResolution evidence | `test_uat_m13_010_resolution_requires_evidence_and_keeps_delivery_status_separate` |
| REQ-M13-011 / UAT-M13-011 | reopen_service_case preserves resolution + SLA dates | `test_uat_m13_011_reopen_preserves_original_resolution_history` |
| REQ-M13-012 / UAT-M13-012 | assign/resolve vs ownership.assignment_changed projection | `test_uat_m13_012_case_owner_change_does_not_move_maintenance_ownership` |

額外控制由 `test_contract_worker_http.py` 驗 14 route、OpenAPI/event schema、consumer
hash/sequence-gap/tenant isolation、outbox 與 response-dispatch worker 的
lease/retry/unknown-result-lookup、production fake adapter gate；
`test_security_automation.py` 驗 tenant isolation、customer 互動方向限制、
錄音同意證據、automation 最小化輸入、AML 案件完全不呼叫 assistant、
`contains_new_investment_judgment` 的硬性拒絕核定/送出、本模組無退款
approve/execute 方法；`test_ui_contract.py` 驗三個畫面入口、八種 UI 狀態、
未驗證案件隱藏契約/付款連結、內部筆記分區與送達狀態不冒充已送達。
