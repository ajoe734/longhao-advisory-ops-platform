# M14 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M14-001 / UAT-M14-001 | receive_payment_webhook event_id dedup | `test_uat_m14_001_duplicate_payment_webhook_applies_once` |
| REQ-M14-001 / UAT-M14-001 (reopen 退修) | 不同 event_id 的重送不得二次入帳、停止倒退轉態、金額不符需隔離並開差異單 | `test_uat_m14_001b_payment_webhook_rejects_new_event_id_double_credit_downgrade_and_mismatch` |
| REQ-M14-002 / UAT-M14-002 | create_invoice, run_revenue_recognition eligibility gate | `test_uat_m14_002_prepayment_separated_from_recognized_revenue`（同一測試內含 contract 未接受時排除、接受後才可認列的兩段驗證） |
| REQ-M14-003 / UAT-M14-003 | import_settlement gross/fee/net | `test_uat_m14_003_settlement_fee_and_revenue_kept_in_separate_columns` |
| REQ-M14-004 / UAT-M14-004 | create_invoice tax_mapping_not_approved | `test_uat_m14_004_unapproved_tax_mapping_blocks_invoice_then_succeeds_once_approved` |
| REQ-M14-005 / UAT-M14-005 | request_refund, approve_refund, execute_refund SoD | `test_uat_m14_005_refund_maker_checker_executor_separation_of_duties` |
| REQ-M14-005 / UAT-M14-005 (reopen 退修) | 退款金額不得超過原付款、未核准/未執行的退款不得被 webhook 確認 | `test_uat_m14_005b_refund_amount_cannot_exceed_original_payment_and_unapproved_webhook_is_quarantined` |
| REQ-M14-006 / UAT-M14-006 | execute_refund query-before-submit | `test_uat_m14_006_refund_timeout_queries_before_resubmitting` |
| REQ-M14-006 / UAT-M14-006 (reopen 退修) | 真正 provider timeout 必須在對外呼叫前落盤 provider_idempotency_key，重試換新 key 須被拒絕、不得二次出金 | `test_uat_m14_006b_refund_timeout_retry_with_a_new_provider_key_is_rejected` |
| REQ-M14-007 / UAT-M14-007 | RefundAllocation unearned vs earned_reversal | `test_uat_m14_007_refund_splits_unearned_and_earned_reversal` |
| REQ-M14-008 / UAT-M14-008 | `_post_journal`/create_accounting_export balance check | `test_uat_m14_008_unbalanced_journal_rejected_from_accounting_export` |
| REQ-M14-009 / UAT-M14-009 | acknowledge_accounting_export event_id idempotency | `test_uat_m14_009_accounting_export_ack_resend_is_idempotent` |
| REQ-M14-010 / UAT-M14-010 | create_evidence_export manifest, list_audit_events | `test_uat_m14_010_evidence_export_manifest_traces_audit_chain` |
| REQ-M14-010 / UAT-M14-010 (reopen 退修) | aml_restricted 遮罩改依合規權限（compliance_reviewer/compliance_supervisor），不再是 internal_auditor | `test_restricted_audit_events_mask_for_auditor_but_not_compliance_reviewer`（`test_m14_security_automation.py`） |
| REQ-M14-011 / UAT-M14-011 | evaluate_purge_candidate, release_legal_hold SoD | `test_uat_m14_011_legal_hold_blocks_purge_until_independent_release` |
| REQ-M14-012 / UAT-M14-012 | record_control_exception, act_on_control_exception independent closure | `test_uat_m14_012_control_exception_restore_drill_requires_independent_closure` |

額外控制由 `test_m14_contract_worker_http.py` 驗 24 個 operationId 路由完整、OpenAPI/event schema、inbox hash/tenant/sequence-gap、outbox lease/retry/no-duplicate-publish，以及（reopen 退修）outbox lease 過期回收（`test_outbox_worker_reclaims_a_lease_abandoned_by_a_crashed_worker`）與 `audit.legal_hold_changed` consumer 冪等套用（`test_consumer_applies_audit_legal_hold_changed_idempotently`）；`test_m14_security_automation.py` 驗 cross-tenant 404、`execute_refund`/`receive_payment_webhook` 需服務身分、restricted evidence 門檻與最小化 automation（`ReconciliationNarrationService`）；`test_m14_ui_contract.py` 驗四個畫面入口、八種 UI 狀態與可及性標記。四個測試檔案改名為 `test_m14_*.py` 前綴，避免與 `tests/modules/customer_identity_application/` 同名檔在無 `__init__.py` 下的 pytest import-file-mismatch collection 衝突。
