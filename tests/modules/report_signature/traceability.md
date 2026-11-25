# M07 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M07-001 / UAT-M07-001 | record_report_review, freeze_report_version missing-review gate | `test_uat_m07_001_missing_review_blocks_freeze_and_sign` |
| REQ-M07-002 / UAT-M07-002 | freeze_report_version manifest binding | `test_uat_m07_002_freeze_binds_report_recommendation_channel_and_audience_hash` |
| REQ-M07-003 / UAT-M07-003 | request_analyst_signature qualification gate | `test_uat_m07_003_unqualified_signer_is_denied_without_provider_request` |
| REQ-M07-004 / UAT-M07-004 | receive_analyst_signature atomic verify + outbox | `test_uat_m07_004_callback_verifies_signature_and_emits_exactly_one_event_before_m08` |
| REQ-M07-005 / UAT-M07-005 | create_report_version supersede + evidence retention | `test_uat_m07_005_post_signature_change_creates_new_version_and_keeps_v1_evidence` |
| REQ-M07-006 / UAT-M07-006 | content hash vs signed-file hash separation | `test_uat_m07_006_content_hash_and_signed_file_hash_are_distinct` |
| REQ-M07-007 / UAT-M07-007 | M05 candidate_superseded consumer blocks signing | `test_uat_m07_007_superseded_source_blocks_signing` |
| REQ-M07-008 / UAT-M07-008 | receive_analyst_signature duplicate receipt | `test_uat_m07_008_duplicate_callback_does_not_republish` |
| REQ-M07-009 / UAT-M07-009 | receive_analyst_signature tamper quarantine | `test_uat_m07_009_tampered_payload_is_quarantined_and_frozen_package_survives` |
| REQ-M07-010 / UAT-M07-010 | revoke_signature append-only + downstream event | `test_uat_m07_010_revocation_keeps_history_and_notifies_downstream` |
| REQ-M07-011 / UAT-M07-011 | SignatureConfirmation auto-publish disclosure | `test_ui_contract.py::test_uat_m07_011_confirmation_screen_discloses_auto_publish_with_no_manual_gate` |
| REQ-M07-012 / UAT-M07-012 | record_report_review history across versions | `test_uat_m07_012_repeated_reviews_across_versions_retain_full_history` |

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI/event schema、M05/M06/自身 `report.version_frozen` 三個 consumer 的 hash/duplicate/order/tenant、outbox lease/retry、簽章逾時 reconciler 的查證而非盲重簽；`test_security_automation.py` 驗 maker-checker SoD、tenant isolation、idempotency conflict、CAS 412/428、撤銷重複、webhook 必須 service principal、`signed_at` 超出 frozen/expiry/validity 窗口、draft-assist 無權限與 production fake gate；`test_ui_contract.py` 驗八種 UI 狀態及可及性標記。
