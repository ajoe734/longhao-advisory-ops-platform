# M14 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M14-001 / UAT-M14-001 | receive_payment_webhook event_id dedup | `test_uat_m14_001_duplicate_payment_webhook_applies_once` |
| REQ-M14-002 / UAT-M14-002 | create_invoice, run_revenue_recognition eligibility gate | `test_uat_m14_002_prepayment_separated_from_recognized_revenue` |
| REQ-M14-003 / UAT-M14-003 | import_settlement gross/fee/net | `test_uat_m14_003_settlement_fee_and_revenue_kept_in_separate_columns` |
| REQ-M14-004 / UAT-M14-004 | create_invoice tax_mapping_not_approved | `test_uat_m14_004_unapproved_tax_mapping_blocks_invoice_then_succeeds_once_approved` |
| REQ-M14-005 / UAT-M14-005 | request_refund, approve_refund, execute_refund SoD | `test_uat_m14_005_refund_maker_checker_executor_separation_of_duties` |
| REQ-M14-006 / UAT-M14-006 | execute_refund query-before-submit | `test_uat_m14_006_refund_timeout_queries_before_resubmitting` |
| REQ-M14-007 / UAT-M14-007 | RefundAllocation unearned vs earned_reversal | `test_uat_m14_007_refund_splits_unearned_and_earned_reversal` |
| REQ-M14-008 / UAT-M14-008 | `_post_journal`/create_accounting_export balance check | `test_uat_m14_008_unbalanced_journal_rejected_from_accounting_export` |
| REQ-M14-009 / UAT-M14-009 | acknowledge_accounting_export event_id idempotency | `test_uat_m14_009_accounting_export_ack_resend_is_idempotent` |
| REQ-M14-010 / UAT-M14-010 | create_evidence_export manifest, list_audit_events | `test_uat_m14_010_evidence_export_manifest_traces_audit_chain` |
| REQ-M14-011 / UAT-M14-011 | evaluate_purge_candidate, release_legal_hold SoD | `test_uat_m14_011_legal_hold_blocks_purge_until_independent_release` |
| REQ-M14-012 / UAT-M14-012 | record_control_exception, act_on_control_exception independent closure | `test_uat_m14_012_control_exception_restore_drill_requires_independent_closure` |

額外控制由 `test_contract_worker_http.py` 驗 24 個 operationId 路由完整、OpenAPI/event schema、inbox hash/tenant/sequence-gap、outbox lease/retry/no-duplicate-publish；`test_security_automation.py` 驗 cross-tenant 404、`execute_refund`/`receive_payment_webhook` 需服務身分、restricted evidence 門檻與最小化 automation（`ReconciliationNarrationService`）；`test_ui_contract.py` 驗四個畫面入口、八種 UI 狀態與可及性標記。
