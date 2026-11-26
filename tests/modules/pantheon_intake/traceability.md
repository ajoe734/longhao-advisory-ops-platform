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

## 候選 `70d1dee` review 退修（F1–F6）對應測試

Review artifact：`/home/hsinping_hung_cctech_support_com/.gemini/antigravity-cli/brain/a62d17e9-4301-4f47-a107-e4b34b85f30f/review-m05-70d1dee.md`。每項均以暫時停用對應修正、觀察新測試由 pass 轉 fail、再還原修正並轉回 pass 的方式完成最小重現驗證（未使用 git stash/reset，不影響 working tree 其他內容）。

| Finding | 修正位置 | 新增/擴充測試 | 重現結果 |
|---|---|---|---|
| F1（Blocking）同 external_event_id 不同 payload 誤回202 | `service.py` `ingest_research_candidate`：新增 `candidate_event_fingerprints`（repository.py `StoreState`），比對 canonical_hash 後不符回409 `event_payload_conflict` | `test_uat_m05_003b_resend_with_different_payload_is_a_conflict_not_a_duplicate` | 停用比對→`DID NOT RAISE ConflictError`；還原後 pass |
| F2（Blocking）source_available_at 晚於 source_occurred_at 未阻擋 | `service.py` seeded_codes 新增 `source_available_at_look_ahead`（available_at > occurred_at 或 > committed_at） | `test_available_at_after_occurred_at_is_quarantined_not_backfilled` | 停用判斷→codes為空、accepted；還原後 quarantined |
| F3（Blocking）reject 丟棄 ReasonRequest 且審計未存 reason_code | `service.py` `reject_research_candidate` 接受 `evidence_asset_ids`、note maxLength 4000，持久化 `rejection_reason_code/note/evidence_asset_ids`（domain.py `ResearchCandidate`），`_Outcome.reason_code` 傳入 `_audit` | `test_reject_accepts_full_reason_request_and_persists_it_on_audit` | 還原 allowed fields 限制→422 unsupported fields: evidence_asset_ids；還原後 pass 且 audit.reason_code 落盤 |
| F4a（Blocking）terminal rejected 被同版本不同hash改回quarantined | `service.py` ingest 的 hash-conflict 分支新增 `previous_status != "rejected"` guard | `test_uat_m05_004b_hash_conflict_against_rejected_head_stays_rejected` | 停用guard→status變quarantined；還原後維持rejected |
| F4b（Blocking）ready→quarantined 未發 candidate_validated(quarantined) | 同分支，`previous_status == "ready"` 時額外 emit `research.candidate_validated` | `test_uat_m05_004c_hash_conflict_against_ready_head_notifies_quarantine` | 停用emit→0筆事件；還原後1筆 |
| F5a（Blocking）worker 遇 ConflictError/StaleVersionError/NotFoundError 崩潰、job卡leased | `workers/tasks/pantheon_intake/tasks.py` `CandidateVerificationWorker._run_job` 新增對應except分支，complete該job | `test_worker_does_not_crash_on_a_terminal_candidate_and_completes_the_job` | 停用分支→`ConflictError` 往外拋；還原後 processed=1、job completed |
| F5b（Blocking）TaskRunner.lease 不分kind/tenant；worker誤用單一service_context | `tasks.py` `TaskRunner.lease` 新增 `kind` 篩選；`CandidateVerificationWorker` 改用 `service_context_factory(tenant_id)` | `test_worker_leases_only_its_own_kind_and_resolves_the_correct_tenant` | （以conftest service_context_factory驗證多租戶正確解析；未另做停用探針，邏輯直接對應建構子變更） |
| F5c（Blocking）OutboxPublisher 未處理 unknown 外部結果 | `tasks.py` 新增 `UnknownExternalResult`，`OutboxPublisher.run_once` 捕捉後以 `lookup()` 判斷是否已送達，比照M01寫法 | `test_outbox_worker_resolves_unknown_result_via_lookup_before_retrying` | 新行為；兩個分支（lookup=accepted / not_found）皆覆蓋 |
| F6a（Hygiene）`from conftest import` 與 M01 conftest 撞名 | 新增 `tests/modules/pantheon_intake/__init__.py`，3個test檔改用 `from .conftest import ...` | 既有 `pytest tests/modules/customer_identity_application/ tests/modules/pantheon_intake/` | 修正前 collection錯誤（ImportError跨模組conftest）；修正後 `tests/modules/` 全跑 63 passed |
| F6b（Hygiene）idempotency replay 缺 ETag | `domain.py` `IdempotencyReceipt.response_aggregate_version`；`service.py` `_run_command` replay分支帶回該版本號 | `test_controller_returns_etag_problem_and_replay_headers`（擴充） | 停用→replay缺ETag header；還原後 `"1"` |
| F6c（Hygiene）403拒絕未記審計 | `service.py` `_authorize_integration/_authorize_service/_authorize_staff` 改經 `_deny()` 先audit再raise | `test_only_the_right_principal_type_can_call_each_command`（擴充） | 停用audit→denied清單為空；還原後3筆 `reason_code="forbidden"` |

驗證指令：`python3 -m pytest tests/modules/pantheon_intake -q` → 34 passed；`python3 -m pytest tests/modules/customer_identity_application/ tests/modules/pantheon_intake/ -q` → 63 passed；`python3 scripts/validate_contracts.py` / `validate_docs.py` / `validate_sql.py` → PASS；`python3 -m pytest -q mocks/test_server.py qa/test_platform_runtime.py qa/test_cross_module_runtime.py qa/test_ui_registry.py` → 24 passed。`apps/admin` tsc 與 PostgreSQL 16 runtime 驗證維持前一輪候選的 NOT_RUN（本次未變更 admin UI 或 migration 檔，環境限制同前：無 node_modules／無 Docker/PG runtime 授權）。
