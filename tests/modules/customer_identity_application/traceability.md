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

Base: `04bfafc5aae036b1a69e056cdf999f9735e98caa` (`origin/dev` at branch creation); initial anchor: `849617d381d42adcc3ddc72720ace41cbb1cc2fa`; first candidate: `2930f0c0af1d4052e0c65a1207e874cca0517368` (reopened by Antigravity).

| Finding／驗收 | 原始碼依據與修改位置 | 舊版重現 → 修正版結果 | 命令、退出碼與證據 | 未驗項與具體限制 |
| --- | --- | --- | --- | --- |
| UI-04 無法讀取 gate 紀錄 ID | `Application.to_api`、`M01EventConsumer`、`Application` OpenAPI/SQL proposal | base 的 `Application.to_api` 不含四個 ID，M01 contract map 只收 M02/M11；修正版由 M02/M03/M04 event 投影 ID，回應四欄均可驗證 | base static probe（無 `list_applications`／gate ID／assignment contract）exit 0；`python3 -m pytest tests/modules/customer_identity_application -q` exit 0，40 passed | M01 不讀取 AML 受限內容；真 M02/M03/M04 provider wiring 為 `NOT_CONNECTED` |
| UI-04 申請佇列不能讀取 | `CustomerIdentityApplicationService.list_applications`、`M01Controller`、`ApplicationPage` OpenAPI | base 沒有 service／route；修正版以 state/customer ID 篩選、ID cursor、固定 as_of，並測 401/403/tenant isolation | 同上；`python3 scripts/validate_contracts.py` exit 0；`python3 scripts/validate_docs.py` exit 0 | shared operation catalog/RBAC/generated client 由 API schema steward 在整合時同步 |
| M11 指派不會回寫 M01 customer owner | `M01EventConsumer._apply` assignment branch、`events/registry.json` | base 未註冊 consumer 且會拒絕 topic；修正版寫入 active owner、released/departure 清空、同 assignment 的舊 aggregate version 不覆寫、重送去重 | 同上；`python3 scripts/validate_sql.py database/proposals/M01/001_customer_identity_application.sql` exit 0（7 files / 2600 top-level statements） | SQL 僅 syntax/proposal validation；未取得受控 PostgreSQL/RLS runtime |

### Antigravity reopen repair evidence

Review source: Antigravity reopen of `2930f0c0af1d4052e0c65a1207e874cca0517368`; repair anchor: `1016b539759e6d3d50c3a2425537e28bf0453f2d`. The minimal reproducer was added to the existing M01 contract/worker test and run against the still-unmodified candidate code before the repair: four targeted cases failed as specified below. The same formal controller/consumer paths pass after the repair.

| Finding／驗收 | 原始碼依據與修改位置 | 舊版重現 → 修正版結果 | 命令、退出碼、執行版本與證據 | 未驗項與具體限制 |
| --- | --- | --- | --- | --- |
| F1 `GET /applications?state=` 空列舉被當作空集合 | `CustomerIdentityApplicationService.list_applications`（`service.py`）、`M01Controller._dispatch`；OpenAPI state enum | `2930f0c` 回 200 / `items=[]`；修正版以 `state_filter is not None` 驗列舉，空值與 invented state 都回 422，無效 customer UUID 回 422；亦驗 draft/customer_signed 篩選、sales/customer/platform_admin 403、未登入 401 與跨 tenant 隔離 | pre-fix：`python3 -m pytest tests/modules/customer_identity_application/test_contract_worker_http.py -q -k 'list_applications_filters_pages_and_preserves_read_authorization or rejects_invalid_gate_and_assignment_enums_before_projection or enforces_event_classification_from_published_contract or preserves_newer_assignment_when_an_older_assignment_releases'` → exit 1, 4 failed；post-fix same command → exit 0, 4 passed；Python 3.12.3 / pytest 9.2.0 | 無需 runtime server；controller test 走正式 HTTP adapter，FastAPI mount 另有既有測試 |
| F2 四個 consumer 可寫入不受契約約束的列舉，KYC 壞值拋 raw `KeyError` | `M01EventConsumer._validate_envelope`、`_apply`；event schema 的 KYC decision、suitability state、clearance decision、assignment state | `2930f0c` KYC `decision=bogus` 拋 `KeyError`，其餘壞字串可投影；修正版先以 enum map 驗證，四個壞 payload 都為 `ValidationError(field=payload.<field>)`，inbox/application/customer 均未變動 | 同一 pre-/post-fix command；全模組 `python3 -m pytest tests/modules/customer_identity_application -q` → exit 0, 43 passed（1 upstream Starlette/httpx deprecation warning） | JSON schema payload type/format 的完整 producer-side contract test 仍由事件 registry/CI 驗證；本消費者未讀取 AML 受限內容 |
| F3 先前 assignment 的 late release 清掉後來 active owner | `M01EventConsumer._apply` assignment projection；M11 assignment ID 表示獨立 interval | `2930f0c` A1 released（S1）在 A2 active（S2）後仍清為 null；修正版僅在 payload salesperson 為 null 或等於 current owner 時清除。回歸依序驗 A1 active → superseded、A2 active、late A1 released 保留 S2、A2 departure 清除 S2 | 同一 pre-/post-fix command；修正版 result codes 分別有 superseded ignored、late clear ignored、current departure projected | 未以 PostgreSQL projection runtime 重播；本測試以正式 in-memory repository atomic path 驗證業務邊界 |
| F4 assignment classification 與 published schema 漂移；trace anchor/base 錯誤 | `M01EventConsumer._validate_envelope`；`incoming` test helper；`ownership.assignment_changed.v1.schema.json`（read-only authority）；本 trace artifact | `2930f0c` helper 對所有 ownership events 填 `internal`，M01 也未驗 classification；修正版 helper 對 assignment 填 `customer_confidential`、僅 employee_departed 為 `internal`；consumer 拒絕 assignment=`internal`（`event_schema_invalid`）且不寫 owner | 同一 pre-/post-fix command；`python3 scripts/validate_contracts.py` → exit 0（14 modules / 142 operations / 193 schemas / 47 event contracts）；`python3 scripts/validate_docs.py` → exit 0；`python3 scripts/validate_sql.py database/proposals/M01/001_customer_identity_application.sql` → exit 0（7 files / 2600 statements） | `database/proposals/M01/traceability.md` 不存在；唯一 evidence artifact 是本檔，handoff 必須引用此路徑 |
