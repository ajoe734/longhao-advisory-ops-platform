# M03 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M03-001 / UAT-M03-001 | create_kyc_submission, request_kyc_upload, submit_kyc | `test_uat_m03_001_signed_application_can_start_online_kyc` |
| REQ-M03-002 / UAT-M03-002 | submit_kyc missing_field_codes, decide_kyc gate | `test_uat_m03_002_missing_required_fields_blocks_approval` |
| REQ-M03-003 / UAT-M03-003 | OcrAutomationService untrusted draft vs. original answer | `test_uat_m03_003_ocr_assist_does_not_overwrite_customer_answer` |
| REQ-M03-004 / UAT-M03-004 | DocumentScanWorker + get_document_status + submit_kyc quarantine gate | `test_uat_m03_004_malicious_document_is_blocked_in_quarantine` |
| REQ-M03-004 / UAT-M03-004 | decide_kyc／OCR 再次核對 scan_status=='clean'（含送審後才被判定 rejected 的文件），evidence 必須屬於同一 application | `test_uat_m03_004b_decide_kyc_cannot_approve_unscanned_or_quarantined_documents`、`test_uat_m03_003`（OCR 擋未掃描文件段） |
| REQ-M03-005 / UAT-M03-005 | compute_suitability determinism + assess_suitability | `test_uat_m03_005_suitability_is_computed_by_versioned_rules` |
| REQ-M03-006 / UAT-M03-006 | override gate (suitability_authorizer required)，不可變歷史（新 id、不覆寫） | `test_uat_m03_006_not_suitable_cannot_be_overridden_without_authorization` |
| REQ-M03-007 / UAT-M03-007 | submission_version immutability + stale_submission on decide_kyc | `test_uat_m03_007_newer_submission_version_makes_the_old_one_stale` |
| REQ-M03-007 / UAT-M03-007 | 重送文件可沿用同一 application 更早版本上傳的文件（不限同一 submission_version） | `test_uat_m03_007b_resubmission_can_reuse_documents_from_earlier_submission_version` |
| REQ-M03-008 / UAT-M03-008 | get_kyc_submission masked_business_view | `test_uat_m03_008_business_role_sees_only_missing_items_not_evidence` |
| REQ-M03-009 / UAT-M03-009 | M03EventConsumer customer.profile_updated -> refresh_case + reassessment_due | `test_uat_m03_009_risk_change_triggers_refresh_not_silent_suitable` |
| REQ-M03-010 / UAT-M03-010 | suitability role/override authorization，conditional->suitable 削弱 override，maker!=checker（同 actor 持雙角色仍禁止自核） | `test_uat_m03_010_general_staff_without_role_cannot_assess_suitability`（含 conditional override 與 maker-checker 段）、`test_uat_m03_006` |
| REQ-M03-011 / UAT-M03-011 | DueDiligenceWorker bounded retry -> manual_required，redrive(reason) 重入隊 | `test_uat_m03_011_due_diligence_timeout_goes_to_manual_queue_never_passes` |
| REQ-M03-011 / UAT-M03-011 | 重評成功關閉 refresh_case 並把 submission 轉回 approved；refresh_required 狀態 allowed_actions 不是死路 | `test_uat_m03_011b_due_diligence_refresh_is_not_a_dead_end` |
| REQ-M03-011 / UAT-M03-011 | worker lease 被其他 worker 持有時回報 `worker_lease_lost`，不得覆寫 | `test_uat_m03_011c_due_diligence_worker_rejects_stolen_lease`、`test_document_scan_worker_rejects_stolen_lease` |
| REQ-M03-012 / UAT-M03-012 | submit_kyc idempotency replay | `test_uat_m03_012_replayed_submit_does_not_duplicate_the_submission` |

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI wire schema（`KycSubmission`/`UploadTicket`/`SuitabilityAssessment`/`DocumentStatus`/`Problem`，含 `request_due_diligence_refresh`）、event JSON Schema（`kyc.decision_recorded`/`suitability.assessed`/`kyc.refresh_required`，共三個 producer 事件）、inbox hash/tenant/order、outbox 與 document-scan worker 的 lease/retry/alert、idempotent replay 仍帶 ETag，以及 production fake-adapter gate。
