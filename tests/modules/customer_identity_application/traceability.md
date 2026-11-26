# M01 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M01-001 / UAT-M01-001 | create_customer, link_external_identity, idempotency/outbox | `test_uat_m01_001_same_external_subject_is_single_customer` |
| REQ-M01-002 / UAT-M01-002 | create_customer, restricted merge candidate | `test_uat_m01_002_equal_email_different_subject_requires_human_review` |
| REQ-M01-003 / UAT-M01-003 | create/get/withdraw_application | `test_uat_m01_003_draft_resume_preserves_id_and_withdraws_without_entitlement` |
| REQ-M01-004 / UAT-M01-004 | get_customer/get_application auth + denied audit | `test_uat_m01_004_cross_customer_reads_hide_existence` |
| REQ-M01-005 / UAT-M01-005 | immutable first touch + touch history | `test_uat_m01_005_later_touch_does_not_replace_first_touch` |
| REQ-M01-006 / UAT-M01-006 | append-only scoped consent | `test_uat_m01_006_consent_withdrawal_is_scoped_and_append_only` |
| REQ-M01-007 / UAT-M01-007 | issuer/audience/nonce proof rejection | `test_uat_m01_007_forged_external_token_creates_nothing` |
| REQ-M01-008 / UAT-M01-008 | maker-checker merge + alias/evidence | `test_uat_m01_008_approved_merge_keeps_alias_and_checker_evidence` |
| REQ-M01-009 / UAT-M01-009 | If-Match CAS + UI conflict state | `test_uat_m01_009_stale_update_is_rejected_and_first_write_remains` |
| REQ-M01-010 / UAT-M01-010 | provider timeout fail-closed | `test_uat_m01_010_provider_timeout_does_not_create_identity` |
| REQ-M01-011 / UAT-M01-011 | no assisted consent/effectiveness impersonation | `test_uat_m01_011_assisted_draft_cannot_impersonate_consent_or_acceptance` |
| REQ-M01-012 / UAT-M01-012 | source degraded vs available-empty | `test_uat_m01_012_degraded_source_is_not_rendered_as_empty` |

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI/event schema 預設值、inbox hash/tenant/order、`application.company_decided` 未簽署拒絕、`ownership.employee_departed` aggregate_id 檢查、outbox lease/retry/dead-letter/unknown-result lookup、`TaskRunner` lease/redrive guardrails 及 FastAPI router prefix／輸入驗證；`test_security_automation.py` 驗 tenant isolation、identity correction 與最小化 automation；`test_ui_contract.py` 驗八種 UI 狀態及可及性標記。
