# M07 正式研究報告、覆核與分析師簽章模組

此目錄實作 OpenAPI 1.0.0 的 8 個 M07 operationId。`domain.py` 與 `service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app 掛載的 FastAPI router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter。

關鍵控制：actor/tenant/SoD 先驗（reviewer 不能覆核自己撰寫的版本）、CAS、payload-bound idempotency、report_content_sha256 與 bundle_manifest_sha256 分離驗證、frozen 包內容不可覆寫（修改一律建立新 `report_version`）、簽章 provider 回呼以 `signature_request_id` 去重且 tamper payload 一律 quarantine、撤銷只追加 `report.revocation` 證據並重發 `report.signature_revoked`、audit/outbox 原子提交。

`report.version_frozen` 由本模組同時 producer／consumer（自身投影一致性檢查）；`research.candidate_superseded`（M05）與 `review.revision_received`（M06，`stale=true`）消費後標記對應 `source_candidate_id` 為 superseded，`request_analyst_signature` 與 `freeze_report_version` 會拒絕沿用過期來源簽署（REQ-M07-007）。

`automation.py` 只接受已核定 channel 文案與雜湊、保存 model/prompt/ruleset 版本並產生 `untrusted_draft` 一致性檢查；它沒有簽章、覆核或發布命令權。沒有核准 LLM provider 時使用 deterministic local fallback，production 會拒絕 fake。

外部狀態：真實簽章 provider（憑證、MFA、精確檔案驗簽）、M05/M06 來源血緣、M08 建議／通路／受眾 manifest、M14 不可變物件儲存均為 `NOT_CONNECTED`。本模組只有 adapter port 與 local fake；沒有修改或呼叫外部 repository／production endpoint。

整合者需提供：shared PostgreSQL repository/UoW、FastAPI auth context resolver（含 webhook HMAC 驗證後把 `signature_adapter` service principal 交給 controller）、真實簽章 provider adapter、M14 物件儲存 adapter、metrics exporter，以及套用 `database/proposals/M07` 後的 migration 編號。

## Antigravity review reopen（候選 `98ae789aa7e5` → 本候選）修復對照

逐項對應 `/home/hsinping_hung_cctech_support_com/.gemini/antigravity-cli/brain/7d00b444-de70-4a81-9b39-65f1b40ee477/m07-review-98ae789aa7e5.md` 的 6 項 blocking finding；完整回歸證據與命令見 `tests/modules/report_signature/README.md`／`traceability.md`。

| Finding | 修改位置 | 舊版重現 → 修正版結果 | 回歸測試 |
| --- | --- | --- | --- |
| F1 TS2308 build break | `apps/admin/.../index.ts` | `types.ts` 的 `interface ReportWorkbench` 與元件 `ReportWorkbench` 同名 `export *` 衝突；改 named re-export（同 e763e3f 作法）。`npm install`/`tsc -b` 本機沙箱政策擋下（見 guardrail 拒絕訊息），**NOT_RUN**，靜態核對已排除名稱衝突，待 hosted CI 跑 `npm run build --prefix apps/admin` | `test_m07_ui_contract.py::test_ui_exports_required_screens_and_all_state_labels` |
| F2 unresolved rejection bypass | `service.py` `freeze_report_version` | 舊：comp approve→comp reject→pro approve→freeze([pro,old_comp]) 成功 ready_to_sign。新：鏡像 `report.guard_signature` 全歷史未解決否決檢查，同情境拋 `unresolved_review_rejection`；並拒絕重複/跨版本 `required_review_ids` | `test_m07_uat.py::test_uat_m07_001b_*`、`test_uat_m07_001c_*` |
| F3 receive_analyst_signature 驗證缺口 | `service.py` `receive_analyst_signature` | 舊：session ref／signer subject／superseded candidate／未來 `signed_at`／UUID 格式皆未驗證即可能通過。新：全部於驗證交易內重查，任一不符一律 `quarantined`，frozen package 不變 | `test_m07_security_automation.py::test_signed_at_in_the_future_is_quarantined`、`test_callback_with_wrong_provider_session_ref_is_quarantined`、`test_callback_impersonating_a_different_signer_subject_is_quarantined`、`test_callback_for_superseded_source_candidate_is_quarantined_at_verification`、`test_callback_rejects_malformed_asset_and_event_uuids` |
| F4 idempotency/CAS 順序 | `service.py` `request_analyst_signature`、`_run_command` | 舊：重送回 409；stale/缺 If-Match 先建 provider session 才查 CAS；replay 不帶 ETag。新：idempotency 查詢移到最前，CAS 併入 `check_signable` 於 provider 呼叫前執行，`IdempotencyReceipt` 保存 `aggregate_version` | `test_m07_security_automation.py::test_request_analyst_signature_idempotent_replay_succeeds_after_state_moved_on`、`test_request_analyst_signature_stale_cas_does_not_leak_a_provider_session`、`test_request_analyst_signature_missing_if_match_does_not_leak_a_provider_session` |
| F5 bundle_manifest 缺報告/期限綁定 | `service.py` `create_report_version` | 舊：改報告內容或 `valid_until` 不改變 `bundle_manifest_sha256`。新：manifest 納入 `report_asset_id`／`report_content_sha256`／`valid_from`／`valid_until` | `test_m07_uat.py::test_uat_m07_002b_*` |
| F6 worker/UI/tests/SQL | `tasks.py`、UI、`001_report_signature.sql` | lease 回收+dead-letter audited redrive、manual-review 去重、provider lookup timeout skip；UI 重送鍵改依 `ready_to_sign`+`allowed_actions`、補 stale 顯示與簽署者/資格欄、`types.ts` 補 `source_artifact_ref`；告警路徑改走 202 成功分支；補 `review.revision_received` 與 `report.signature_revoked` schema 測試；測試檔改 `test_m07_*` 前綴解決與 M01 的 pytest 同名衝突（已驗證 `pytest tests/modules` 83 全綠）；SQL 補 `guard_frozen_version` 兩新欄位與 `redirect_url` https CHECK | `test_m07_contract_worker_http.py::test_outbox_worker_reclaims_*`、`test_outbox_worker_dead_letters_*`、`test_outbox_redrive_rejects_*`、`test_signature_expiry_reconciler_skips_requests_when_provider_lookup_times_out`、`test_consumer_applies_review_revision_received_*`、`test_m07_signature_revoked_envelope_validates_*` |
