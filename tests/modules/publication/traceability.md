# M08 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M08-001 / UAT-M08-001 | report.signature_verified consumer → PublicationWorker → list/get_publication | `test_uat_m08_001_signature_verified_publishes_without_a_second_step` |
| REQ-M08-002 / UAT-M08-002 | create_recommendation_version（未簽）+ list_publications | `test_uat_m08_002_unsigned_draft_never_appears_in_publications` |
| REQ-M08-003 / UAT-M08-003 | entry/exit 各自 supporting report_version | `test_uat_m08_003_entry_and_exit_each_cite_their_own_report` |
| REQ-M08-004 / UAT-M08-004 | 一份 report 觸發兩個獨立 version 的發布 | `test_uat_m08_004_one_signed_report_supports_two_messages` |
| REQ-M08-005 / UAT-M08-005 | ReportBundlePort content hash mismatch → dead_letter | `test_uat_m08_005_post_signature_content_swap_is_blocked` |
| REQ-M08-006 / UAT-M08-006 | EntitlementAudiencePort 排除 denied/pending | `test_uat_m08_006_audience_snapshot_excludes_ineligible_candidates` |
| REQ-M08-007 / UAT-M08-007 | entitlement.changed 消費後 snapshot/recipient 不回寫 | `test_uat_m08_007_revocation_after_snapshot_does_not_rewrite_history` |
| REQ-M08-008 / UAT-M08-008 | 同 event_id 重送 3 次只發布一次 | `test_uat_m08_008_duplicate_signature_event_publishes_once` |
| REQ-M08-009 / UAT-M08-009 | withdraw_publication 保留 recipient 證據 | `test_uat_m08_009_withdrawal_preserves_published_evidence` |
| REQ-M08-010 / UAT-M08-010 | link_publication_correction 雙向引用 | `test_uat_m08_010_correction_links_original_and_replacement` |
| REQ-M08-011 / UAT-M08-011 | bounded retry → dead_letter → redrive → expired_blocked | `test_uat_m08_011_expired_redrive_is_blocked_not_resent` |
| REQ-M08-012 / UAT-M08-012 | source_candidate_id 建立時間 vs published_at | `test_uat_m08_012_publication_timing_is_separate_from_source_candidate_timing` |

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI/event JSON Schema、controller ETag/replay/problem
headers、consumer 的 schema/hash/tenant/sequence 檢查、outbox lease/bounded-retry、publish job lease 互
斥、`create_fastapi_router` 透過 `fastapi.testclient.TestClient` 實際掛載並送出請求（本機環境已安裝
fastapi 0.165.0），以及 production composition 拒絕 fake adapter。`test_uat.py` 另含 idempotency 409、
CAS 412、非 analyst 403、entry/non-entry `supersedes_version_id` 驗證。

`create_fastapi_router` 回傳的 router 本身帶 `/api/v1` prefix；測試直接 `app.include_router(router)`
掛載即可在 `/api/v1/...` 收到請求。若未來由 `longhao_platform.http.create_app` 以
`app.include_router(router, prefix="/api/v1")` 二次掛載，會變成 `/api/v1/api/v1/...`——這是 M01 既有
`create_fastapi_router` 就已存在的掛載慣例（本模組原樣沿用，未新增此落差），留給 integration steward 在
真正接線時統一決定要在 router 或 app 層各留一次 prefix。

未執行項：真實 PostgreSQL（`database/proposals/M08/001_publication.sql`，含新增的
`pg_constraint` 動態查找 DO block，本次僅人工核對未上機）、admin UI 的 `tsc -b` 型別檢查（本機沙箱禁止
`npm install`，見 `apps/admin/src/features/publication` 的 PR 說明）。
