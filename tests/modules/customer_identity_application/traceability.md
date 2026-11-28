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

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI/event schema 預設值、`GET /applications` 的狀態／客戶篩選與 cursor、401／403／跨 tenant 讀取拒絕、inbox hash/tenant/order、四個 gate record ID 投影、`ownership.assignment_changed` 的 aggregate-version 亂序／重送、`application.company_decided` 未簽署拒絕、`ownership.employee_departed` aggregate_id 檢查、outbox lease/retry/dead-letter/unknown-result lookup、`TaskRunner` lease/redrive guardrails 及 FastAPI router prefix／輸入驗證；`test_security_automation.py` 驗 tenant isolation、identity correction 與最小化 automation；`test_ui_contract.py` 驗八種 UI 狀態及可及性標記。

## GAP-M01-READ-APIS repair evidence

Base: `d5b707deec5d63db79d88200603d4a0687691685`; anchor: `b80836b4debebdc6ae89b567ae832d166fc59fc2`.

| Finding／驗收 | 原始碼依據與修改位置 | 舊版重現 → 修正版結果 | 命令、退出碼與證據 | 未驗項與具體限制 |
| --- | --- | --- | --- | --- |
| UI-04 無法讀取 gate 紀錄 ID | `Application.to_api`、`M01EventConsumer`、`Application` OpenAPI/SQL proposal | base 的 `Application.to_api` 不含四個 ID，M01 contract map 只收 M02/M11；修正版由 M02/M03/M04 event 投影 ID，回應四欄均可驗證 | base static probe（無 `list_applications`／gate ID／assignment contract）exit 0；`python3 -m pytest tests/modules/customer_identity_application -q` exit 0，40 passed | M01 不讀取 AML 受限內容；真 M02/M03/M04 provider wiring 為 `NOT_CONNECTED` |
| UI-04 申請佇列不能讀取 | `CustomerIdentityApplicationService.list_applications`、`M01Controller`、`ApplicationPage` OpenAPI | base 沒有 service／route；修正版以 state/customer ID 篩選、ID cursor、固定 as_of，並測 401/403/tenant isolation | 同上；`python3 scripts/validate_contracts.py` exit 0；`python3 scripts/validate_docs.py` exit 0 | shared operation catalog/RBAC/generated client 由 API schema steward 在整合時同步 |
| M11 指派不會回寫 M01 customer owner | `M01EventConsumer._apply` assignment branch、`events/registry.json` | base 未註冊 consumer 且會拒絕 topic；修正版寫入 active owner、released/departure 清空、同 assignment 的舊 aggregate version 不覆寫、重送去重 | 同上；`python3 scripts/validate_sql.py database/proposals/M01/001_customer_identity_application.sql` exit 0（7 files / 2600 top-level statements） | SQL 僅 syntax/proposal validation；未取得受控 PostgreSQL/RLS runtime |
