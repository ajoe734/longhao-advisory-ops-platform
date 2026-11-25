# M04 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M04-001 / UAT-M04-001 | start_aml_screening, receive_aml_screening（possible_match 開案） | `test_uat_m04_001_possible_match_creates_restricted_case_and_blocks_clearance` |
| REQ-M04-002 / UAT-M04-002 | decide_aml_case 拒絕 service/LLM principal；automation 無核定能力 | `test_uat_m04_002_ai_summary_principal_cannot_decide` |
| REQ-M04-003 / UAT-M04-003 | get_aml_case/list_aml_cases 403；get_business_clearance 僅安全欄位 | `test_uat_m04_003_general_ops_cannot_read_case_or_filing_detail` |
| REQ-M04-004 / UAT-M04-004 | ScreeningDispatchWorker provider timeout → error/manual_required | `test_uat_m04_004_provider_timeout_is_not_treated_as_clear` |
| REQ-M04-005 / UAT-M04-005 | add_aml_case_action + decide_aml_case maker-checker | `test_uat_m04_005_false_positive_release_requires_independent_checker` |
| REQ-M04-006 / UAT-M04-006 | 新 screening（list v2）不覆寫舊 screening（v1） | `test_uat_m04_006_list_version_refresh_blocks_and_keeps_v1_screening` |
| REQ-M04-007 / UAT-M04-007 | submit_aml_declaration 不影響 business_clearance | `test_uat_m04_007_declaration_signature_cannot_substitute_screening` |
| REQ-M04-008 / UAT-M04-008 | aml.case_changed / business_clearance.changed payload 欄位斷言 | `test_uat_m04_008_restricted_event_only_carries_safe_summary` |
| REQ-M04-009 / UAT-M04-009 | add_aml_case_action(request_edd) 中性投影 | `test_uat_m04_009_edd_request_is_neutral_outside_the_workbench` |
| REQ-M04-010 / UAT-M04-010 | grant_case_read 限定時效；過期即撤回讀取 | `test_uat_m04_010_auditor_needs_scoped_time_boxed_grant` |
| REQ-M04-011 / UAT-M04-011 | apply_legal_hold + RetentionSweeper skipped_hold | `test_uat_m04_011_open_case_under_legal_hold_is_never_cleaned_up` |
| REQ-M04-012 / UAT-M04-012 | 過期 approved clearance 投影為 expired | `test_uat_m04_012_expired_clearance_cannot_be_reused` |

額外控制由 `test_contract_worker_http.py` 驗 OpenAPI/event schema、inbox hash/sequence/tenant、outbox lease/retry；`test_security_automation.py` 驗 tenant isolation、customer 自助聲明防冒用、webhook adapter 角色、最小化 automation；`test_ui_contract.py` 驗八種 UI 狀態及可及性標記、一般平台元件不洩漏受限欄位。

## Antigravity 退修 (candidate `0138d97`) 逐項修復與驗證

| Finding | 原始碼依據與修改位置 | 舊版重現 → 修正版結果 | 驗證測試 |
| --- | --- | --- | --- |
| F1 existence oracle (get_aml_case) | `service.py` `get_aml_case`/`_authorize_case_read`：改為先用 `aml_case_id` 授權（角色或 grant），再查 `_stored_case` | 舊版：sales 讀既有案件 403、讀不存在案件 404（可分辨存在性）。新版：兩者皆 403，同一 code | `test_get_aml_case_gives_sales_the_same_forbidden_for_existing_and_missing_case` |
| F1 existence oracle (submit_aml_declaration) | `service.py` `submit_aml_declaration`：owner/authorize 移到 `evidence.exists` 之前 | 舊版：冒用者可用 evidence 是否存在探測，先得 `evidence_not_found` 再得 `resource_not_found`。新版：一律先 `resource_not_found` | `test_submit_declaration_rejects_ownership_mismatch_before_leaking_evidence_existence` |
| F2 clear 覆寫未結案件 | `service.py` `receive_aml_screening`：新增 `_blocking_case_for`，`clear` 遇未結/rejected/closed 案件改為 `pending` | 舊版：第二次 clear 直接覆寫為 approved，繞過仍開啟的 possible_match 案件。新版：維持 pending，`expires_at` 不設 | `test_clear_screening_never_auto_approves_while_a_case_is_unresolved` |
| F2 空白 list_version/provider_ref 與 expires_at 用 now | `service.py` `receive_aml_screening`：改為要求 1..160 字元，且 `expires_at` 改用 `screened_at + _CLEAR_VALIDITY` | 舊版：空字串通過、2020 screened_at 仍核給 180 天新效期。新版：空字串 422，舊 screened_at 的 clearance 立即顯示 `expired` | `test_clear_screening_rejects_empty_list_version_and_expiry_tracks_screened_at` |
| F2 worker timeout fail-open | `workers/tasks/aml_cft/tasks.py` `_mark_manual_required`：補上 pending `BusinessClearance`、`business_clearance.changed` outbox event、`screening.version += 1` 與 audit | 舊版：timeout 後舊 approved clearance 原樣留存。新版：強制新增 pending clearance 並發佈事件 | `test_uat_m04_004_provider_timeout_is_not_treated_as_clear`（既有，行為延伸由上列新測試覆蓋 workers 內部契約） |
| F3 decide_aml_case 缺 checker AmlCaseAction | `service.py` `decide_aml_case`：新增 `AmlCaseAction`（`decision_approve`/`decision_deny`/`decision_request_information`），`domain.py` `AmlCaseAction` 加 `independent_reviewer_id` | 舊版：核定後 case_actions 仍只有 maker 一筆。新版：maker+checker 共 2 筆 | `test_uat_m04_005_false_positive_release_requires_independent_checker`（已更新斷言） |
| F4 consumer new_id() 兩次 / 缺事件 | `workers/tasks/aml_cft/consumer.py` `_apply`：action_id 只生成一次；kyc 拒絕 annotate 補 `aml.case_changed`；profile 刷新補 `business_clearance.changed` | 舊版：dict key 與 action.id 不同、兩個分支都不發事件。新版：key==id、兩分支皆發對應事件 | `test_consumer_kyc_rejection_emits_aml_case_changed_and_action_key_matches_id`、`test_consumer_profile_refresh_emits_business_clearance_changed` |
| F4 outbox/job lease 過期不回收 | `tasks.py` `OutboxPublisher._lease`、`ScreeningDispatchWorker._lease`：補 `status == "leased" and lease_until <= now` 的回收分支 | 舊版：lease 過期的列永遠停在 `leased`。新版：下一個 worker 可安全重新 lease 並完成 | `test_outbox_worker_reclaims_a_lease_abandoned_by_a_dead_worker`、`test_dispatch_worker_reclaims_a_job_lease_abandoned_by_a_dead_worker` |
| F4 事件 aggregate_version 用錯聚合 | `service.py` `_Outcome.events` 改帶 `(event_type, aggregate_id, aggregate_version, payload)`，`_run_command` 改用各事件自身版本 | 舊版：`aml.case_changed` 的 envelope `aggregate_version` 套用 outcome 主聚合（screening）版本，與 payload `restricted_record_version`（case 版本）不一致。新版：envelope 版本即 case 自身版本 | `test_aml_case_changed_envelope_version_matches_restricted_record_version` |
| F5 webhook headers 未驗證 | `service.py` 新增 `_authorize_webhook`（`webhook_timestamp`/`webhook_nonce`/`integration_key_id`），`http.py` `_dispatch` 轉送 header | 舊版：完全忽略這三個必填 header。新版：缺失、格式錯誤或超過 ±300 秒回 401 | `test_receive_aml_screening_requires_valid_webhook_headers`、`test_controller_threads_webhook_headers_to_the_service` |
| F5 list_aml_cases 未對齊 internal_auditor | `service.py` `list_aml_cases`：非 aml_officer/aml_supervisor 但有 internal_auditor 角色時，依有效 `read_grant` 範圍過濾 | 舊版：internal_auditor 整體 403，看不到任何已授權案件。新版：只回已授權的 case，未授權的案件不出現 | `test_uat_m04_010_auditor_needs_scoped_time_boxed_grant`（擴充兩案情境） |
| F6 PG16 migration 順序/命名錯誤 | `database/proposals/M04/001_aml_cft.sql`：DROP 真實約束名 `case_file_risk_level_check` 並移到 UPDATE 之前；`aml.screening` 補 `list_version` 可為空與以 partial unique index 取代原 UNIQUE | 舊版：對已有 `medium`/`undetermined` 資料的 DB 執行會違反尚未刪除的舊 CHECK；`list_version NOT NULL` 與空 `provider_request_id` 的 UNIQUE 阻擋新建 pending screening。新版：兩者皆在真實 PostgreSQL 16（`pgserver` 一次性實例）驗證通過 | 本機以 `pgserver`/`psycopg` 對 disposable PostgreSQL 16 套用 migrations 0001-0004 + seed 後重現舊版失敗、驗證新版成功（見 handoff 證據，非 pytest，不在 `tests/modules/aml_cft` 樹內） |
| F7 AmlWorkbench.tsx UI 缺陷 | `apps/admin/src/features/aml_cft/AmlWorkbench.tsx` `AmlCaseDetail`：補 `stale` 狀態渲染、受控輸入＋按鈕＋trim、核定理由改用實際輸入值、停用原因改為可見 `role="note"` 段落（保留 title） | 舊版：缺 stale 渲染、Enter-only 無按鈕未 trim、approve/deny 硬編 reason_code、停用原因只有 title。新版：均已補齊 | `test_ui_contract.py`（既有字串斷言全部仍通過；前端 tsc 因 `apps/admin` 無 `node_modules`/lockfile 仍為 NOT_RUN，非本輪引入） |
