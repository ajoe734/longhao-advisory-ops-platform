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

## Round-2 退修證據（Antigravity review of `7cfb35522b9f`, PR #22, 5 blocking findings）

完整審查報告：`/home/hsinping_hung_cctech_support_com/.gemini/antigravity-cli/brain/c219929b-e9eb-4869-a7e0-1b7bc1b616a9/review-m06-7cfb35522b9f.md`。
下表逐項對應該報告的 F1–F5；「舊版重現」欄位的測試在本輪新增於 `test_round2_review_regressions.py`，
針對舊版 `7cfb35522b9f` 的行為會失敗（已以該版程式碼手動確認），針對本輪修正版通過。

| Finding | 原始碼依據與修改位置 | 舊版重現 → 修正版結果（測試） | 命令與結果 |
| --- | --- | --- | --- |
| F1 webhook HMAC/headers 未接線 | `ports.py` 新增 `WebhookRequest`/`WebhookSecretPort`/`StaticWebhookSecretStore`；`service.py:_verify_webhook` 驗 timestamp/nonce/key-id 格式、查 secret、呼叫既有 `verify_servant_webhook`、以 `StoreState.webhook_nonces` 做 replay dedup；`http.py:_webhook_request` 由 headers 組裝、`create_fastapi_router` 改讀 `await request.body()` 原始位元組並轉傳 | 舊版 `M06Controller.handle("receive_servant_revision", ...)` 不帶任何簽章 header 回 202；修正版缺 header 回 422、簽章錯回 403、timestamp 超過 ±300s 回 403、未知 integration key 回 403、重放同一 nonce 回 403 | `test_f1_unsigned_webhook_is_rejected_by_the_controller`、`test_f1_tampered_signature_is_rejected`、`test_f1_stale_timestamp_is_rejected`、`test_f1_unknown_integration_key_is_rejected`、`test_f1_replayed_nonce_is_rejected_even_with_a_new_idempotency_key` |
| F2 principal 類型混用與存在性 oracle | `service.py:_authorize_staff`/新增 `_authorize_service` 嚴格要求 `principal_type` 完全匹配；`open_servant_conversation`/`request_servant_revision` 把角色檢查移到 body 驗證與 `_stored_case` 之前 | 舊版 `principal_type="service"` 帶 `research_supervisor` 執行 `assign_review_case` 回 200；`staff` principal 呼叫 webhook 被放行；未授權角色對存在 vs 不存在 case 回 403 vs 404 | `test_f2_service_principal_cannot_execute_a_staff_command`、`test_f2_staff_principal_cannot_call_the_servant_webhook`、`test_f2_unauthorized_caller_gets_consistent_403_for_existing_and_missing_cases` |
| F3 終態/凍結案件可被覆寫重開 | `service.py` 新增 `_require_open_case`（`rejected`/`closed`/`ready_to_sign` 回 409 `case_terminal`），`open_servant_conversation`/`request_servant_revision` 的 mutate 一律呼叫；`existing_conversation_ref` 重用分支新增 `conversation.status != "active"` 檢查 | 舊版對 `closed`(signed) 案件呼叫 `request_servant_revision` 回 202 並把狀態改回 `revision_requested`；`existing_conversation_ref` 可續接非 active 對話 | `test_f3_closed_case_rejects_new_revision_requests`、`test_f3_ready_to_sign_case_rejects_opening_a_new_conversation`、`test_f3_inactive_conversation_cannot_be_reused` |
| F4 DB tx 跨網路 + worker dispatch/lease 缺陷 | `service.py:open_servant_conversation` 拆成 validate-only atomic → 跨交易呼叫 `servant.open_conversation` → 重驗 CAS/終態後再 commit atomic（SA §5）；`tasks.py:_dispatch_revision` 改送完整 `instructions`/`base_artifact_ref`/`required_evidence_refs`/`requested_output`/`external_conversation_id`，`get_revision_status`/`submit_revision` 的 `DependencyUnavailableError`/`ForbiddenError` 均被捕捉（後者轉標 revision failed 而非讓 worker 崩潰）；`OutboxPublisher._lease` 加入 `status == "leased"` 以回收過期 lease | 舊版 `_dispatch_revision` payload 僅 3 欄位；`get_revision_status`/`ForbiddenError` 未捕捉會讓 worker 例外中斷；過期 `leased` row 永遠卡住 | `test_f4_open_conversation_commits_after_the_network_call_without_holding_the_lock`、`test_f4_dispatch_submits_the_full_revision_scope_to_servant`、`test_f4_servant_denial_marks_the_revision_failed_instead_of_crashing_the_worker`、`test_f4_get_revision_status_dependency_failure_is_retryable_not_a_crash`、`test_f4_outbox_worker_reclaims_an_expired_lease` |
| F5 版本號/ETag/審計重複/pending/retry/ExternalRef 比對缺陷 | `receive_servant_revision` 的 mutate 每次異動都 `revision.version += 1`；`IdempotencyReceipt` 新增 `aggregate_version`，`_run_command` 的 replay 分支與 receipt 寫入都帶上，replay 時 ETag 不再掉空；`_Outcome` 新增 `audit_result`/`audit_reason_code`，`receive_servant_revision` 移除手動 `self._audit(...)`，改由 `_run_command` 單一來源寫 audit（不再重複 rejected+succeeded）；`_pending_revision_ids` 排除已被某個 `retry_of_revision_id` 認領的失敗 revision；`request_servant_revision` 的 `retry_of` 改選「同一 conversation、尚未被認領、created_at 最新」的失敗 revision；`open_servant_conversation`/`_conversation_outcome` 回真實 `_conversation_actions(...)`；新增 `domain.external_ref_matches` 取代 `==`，用於 `_find_conversation_by_ref` 與 `receive_servant_revision` 的 scope 比對 | 舊版：`receive_servant_revision` 完成後 `revision.version` 仍為 1；idempotent replay 的 `ETag` 為 `None`；quarantined 回呼同時寫 `rejected` 與 `succeeded` 兩筆 audit；失敗 revision 永遠留在 `pending_revision_ids`（已於 `test_uat.py` 的 UAT-M06-009 斷言同步更新）；`retry_of_revision_id` 任意綁到舊失敗；`allowed_actions` 恆為 `[]`；帶選填欄位的 `ExternalRef` 比對失敗 | `test_f5_completed_callback_increments_the_revision_version`、`test_f5_idempotent_replay_of_the_webhook_still_returns_an_etag`、`test_f5_quarantined_callback_produces_exactly_one_rejected_audit`、`test_f5_retry_binds_to_the_most_recent_unclaimed_failure`、`test_f5_conversation_response_reports_real_allowed_actions`、`test_f5_external_ref_match_tolerates_optional_fields`；既有 `test_uat_m06_009_failed_revision_can_be_retried_and_keeps_the_failed_evidence` 斷言更新為排除已認領的失敗項 |

執行證據：`python3 -m pytest tests/modules/agora_review -q` → 52 passed（30 既有 + 22 新增）；
`python3 scripts/validate_contracts.py` → passed；`python3 scripts/validate_docs.py && python3 scripts/validate_sql.py
&& python3 scripts/check_public_repository.py && git show --format= --check HEAD` → 全部 pass；
`python3 -m pytest -q mocks/test_server.py qa/test_platform_runtime.py qa/test_cross_module_runtime.py
qa/test_ui_registry.py tests/modules/customer_identity_application` → 53 passed（無回歸）。
Admin TypeScript build/lint 與真實 Agora/Servant 串接仍為 NOT_CONNECTED（本輪未變更 admin 原始碼，無新增風險面）；
`AgoraReviewService` 建構簽章新增必填 `webhook_secrets` 參數，僅 `tests/modules/agora_review/` 內的呼叫點需更新，
已同步修改。

## 已知契約與整合缺口（未核定，fail closed）

- `ReviewCase.professional_reviewer_id` 與 `compliance_review_required`：OpenAPI 已定義欄位，但第 8 節列出的 7 個
  M06 API 均未提供指派這兩個欄位的命令；本實作預設 `null`／`false`，不自行發明指派規則或政策。
- `Revision` 的版本比較差異（`significant_diff`／`diff_summary`）目前只存在於內部 `get_review_case_workspace`
  投影，不在 OpenAPI `Revision` schema 內；對外 `request_servant_revision`/`receive_servant_revision`/
  `get_revision_request` 回應維持嚴格符合既有 schema。若要素正式對外，需先送 API 契約變更。
- `review.revision_received` 對 M07 的實際遞送、`current_report_version_id` 的回填，以及 M05 `research.candidate`
  的即時查詢，均為 NOT_CONNECTED：本模組只消費/產生既有 registry 事件，M05/M07 本身未在此次範圍實作。
