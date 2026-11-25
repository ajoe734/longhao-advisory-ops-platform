# M05 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M05-001 / UAT-M05-001 | ingest_research_candidate（無需analyst ticket）+ validate 指定review_case_id | `test_uat_m05_001_persona_autonomous_creates_immutable_candidate_without_ticket` |
| REQ-M05-002 / UAT-M05-002 | ingest_research_candidate（origin=analyst_initiated 走同通道） | `test_uat_m05_002_analyst_initiated_uses_the_same_channel` |
| REQ-M05-003 / UAT-M05-003 | 同external_event_id去重，只建一次case | `test_uat_m05_003_resend_creates_a_single_case` |
| REQ-M05-004 / UAT-M05-004 | 同version不同hash進quarantine，A不被覆寫 | `test_uat_m05_004_same_version_different_hash_quarantines_without_overwrite` |
| REQ-M05-005 / UAT-M05-005 | 缺evidence checksum保持quarantined，raw ref可追查 | `test_uat_m05_005_missing_evidence_stays_quarantined` |
| REQ-M05-006 / UAT-M05-006 | 進review不等於對客發布；customer principal被拒 | `test_uat_m05_006_external_approval_never_reaches_customers` |
| REQ-M05-007 / UAT-M05-007 | source_occurred_at/source_committed_at/received_at分別保存不互相覆寫 | `test_uat_m05_007_research_time_is_not_backfilled_as_trade_time` |
| REQ-M05-008 / UAT-M05-008 | PantheonAdapter.translate對未知schema_version回422並存安全receipt | `test_uat_m05_008_unknown_schema_version_is_rejected` |
| REQ-M05-009 / UAT-M05-009 | SsrfSafeEvidenceFetcher拒絕非allowlist host／私有位址／惡意redirect | `test_uat_m05_009_ssrf_blocked_hosts_and_addresses_are_rejected` |
| REQ-M05-010 / UAT-M05-010 | license_scope非customer_distribution_licensed時customer_distribution_allowed=false | `test_uat_m05_010_unlicensed_source_cannot_reach_customers` |
| REQ-M05-011 / UAT-M05-011 | 新version supersede舊lineage head並發research.candidate_superseded | `test_uat_m05_011_new_version_marks_old_review_stale` |
| REQ-M05-012 / UAT-M05-012 | worker fetch失敗retry/dead_letter，received_at不變，redrive後一次成功不重建case | `test_uat_m05_012_pantheon_outage_does_not_discard_the_candidate` |

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI/event schema、self-consumer inbox hash/duplicate/sequence-gap/tenant隔離、outbox lease/retry、production fake gate；`test_security_automation.py` 驗 cross-tenant隔離、pantheon_adapter/research_intake_service/analyst_reviewer的SoD邊界、terminal candidate不可再變更但歷史保留、最小化triage automation無validate/reject權限，以及`resolve_pantheon_adapter_context()`對HMAC簽章、timestamp skew、nonce重放、未知key與缺headers的拒絕。
