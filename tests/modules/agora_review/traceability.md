# M06 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M06-001 / UAT-M06-001 | consumer `research.candidate_validated` → `review.case_file`；`list_review_cases`, `assign_review_case` | `test_uat_m06_001_valid_candidate_enters_analyst_queue_and_case_can_be_claimed` |
| REQ-M06-002 / UAT-M06-002 | `open_servant_conversation`, `request_servant_revision` 綁定 base_artifact_ref/base_content_sha256 | `test_uat_m06_002_revision_request_binds_the_exact_candidate_version` |
| REQ-M06-003 / UAT-M06-003 | `receive_servant_revision` 建立 revision_result，不覆寫 case 既有 artifact | `test_uat_m06_003_returned_revision_only_creates_a_new_version` |
| REQ-M06-004 / UAT-M06-004 | `assign_review_case` If-Match CAS + UI 衝突狀態 | `test_uat_m06_004_stale_version_reload_shows_new_version_without_overwrite` |
| REQ-M06-005 / UAT-M06-005 | servant_adapter 角色拒絕分析師命令；automation 無簽章/發布能力 | `test_uat_m06_005_servant_cannot_assign_or_act_as_analyst`, `test_automation_cannot_sign_publish_or_move_money` |
| REQ-M06-006 / UAT-M06-006 | `receive_servant_revision` conversation_ref 不符即 quarantined + security audit | `test_uat_m06_006_conversation_scope_cannot_cross_case_or_tenant` |
| REQ-M06-007 / UAT-M06-007 | `ServantDispatchPublisher` 查狀態優先於重送，`FakeServant.submitted_job_count` 仍為 1 | `test_uat_m06_007_external_timeout_does_not_open_a_second_job` |
| REQ-M06-008 / UAT-M06-008 | consumer `research.candidate_superseded` 標 case stale，撤銷 ready 標記 | `test_uat_m06_008_new_source_version_marks_the_case_stale` |
| REQ-M06-009 / UAT-M06-009 | 失敗 revision 保留，`request_servant_revision` 建新 attempt 並以 `retry_of_revision_id` 關聯 | `test_uat_m06_009_failed_revision_can_be_retried_and_keeps_the_failed_evidence` |
| REQ-M06-010 / UAT-M06-010 | `get_review_case_workspace` 的 `servant_health` 與既有 revisions 資料分離呈現 | `test_uat_m06_010_degraded_dependency_shows_last_known_state_not_an_empty_or_completed_view` |
| REQ-M06-011 / UAT-M06-011 | `ArtifactDiffPort` 驅動 `RevisionResult.significant_diff`/`diff_summary`，case 不自動採用新內容 | `test_uat_m06_011_significant_numeric_changes_surface_as_a_visible_pending_diff` |
| REQ-M06-012 / UAT-M06-012 | `require_external_ref` 封鎖 AML/KYC 證據種類；送往 Servant 的 payload 僅含最小欄位 | `test_uat_m06_012_research_conversation_never_carries_kyc_or_aml_content` |

額外控制由 `test_contract_worker_http.py` 驗 7 個 OpenAPI route、兩個 producer 事件（`review.revision_requested`、
`review.revision_received`）與兩個 consumer 事件（`research.candidate_validated`、`research.candidate_superseded`）
的 schema/hash/duplicate/sequence-gap、tenant 隔離、outbox lease/retry 與 production fake adapter 拒絕；
`test_security_automation.py` 驗 cross-tenant 隔離、LLM/automation 特權能力拒絕與 webhook HMAC 簽章驗證；
`test_ui_contract.py` 驗四個畫面入口、八種 UI 狀態與 degraded/stale 文案。

## 已知契約與整合缺口（未核定，fail closed）

- `ReviewCase.professional_reviewer_id` 與 `compliance_review_required`：OpenAPI 已定義欄位，但第 8 節列出的 7 個
  M06 API 均未提供指派這兩個欄位的命令；本實作預設 `null`／`false`，不自行發明指派規則或政策。
- `Revision` 的版本比較差異（`significant_diff`／`diff_summary`）目前只存在於內部 `get_review_case_workspace`
  投影，不在 OpenAPI `Revision` schema 內；對外 `request_servant_revision`/`receive_servant_revision`/
  `get_revision_request` 回應維持嚴格符合既有 schema。若要素正式對外，需先送 API 契約變更。
- `review.revision_received` 對 M07 的實際遞送、`current_report_version_id` 的回填，以及 M05 `research.candidate`
  的即時查詢，均為 NOT_CONNECTED：本模組只消費/產生既有 registry 事件，M05/M07 本身未在此次範圍實作。
