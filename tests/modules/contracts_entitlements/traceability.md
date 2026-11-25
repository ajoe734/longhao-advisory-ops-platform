# M02 契約與權限：測試追蹤矩陣

| UAT | REQ | 測試 | 涵蓋API | 狀態 |
| --- | --- | --- | --- | --- |
| UAT-M02-001 | REQ-M02-001 | `test_uat_m02_001_customer_can_sign_before_kyc` | create_customer_signature, receive_customer_signature | PASS（本機 pytest） |
| UAT-M02-002 | REQ-M02-002 | `test_uat_m02_002_cannot_accept_without_completed_review` | decide_company_acceptance | PASS |
| UAT-M02-003 | REQ-M02-003 | `test_uat_m02_003_single_activation_under_concurrent_evaluation` | evaluate_entitlements, list_customer_entitlements | PASS |
| UAT-M02-004 | REQ-M02-004 | `test_uat_m02_004_rejecting_an_already_paid_application_queues_refund_not_payout` | decide_company_acceptance | PASS |
| UAT-M02-005 | REQ-M02-005 | `test_uat_m02_005_parallel_contracts_keep_independent_entitlements` | get_contract, list_customer_entitlements | PASS |
| UAT-M02-006 | REQ-M02-006 | `test_uat_m02_006_new_package_version_rejects_old_signature_hash` | create_contract_package, approve_contract_package, get_contract_package, create_customer_signature | PASS |
| UAT-M02-007 | REQ-M02-007 | `test_uat_m02_007_stale_kyc_reference_is_rejected` | decide_company_acceptance | PASS |
| UAT-M02-008 | REQ-M02-008 | `test_uat_m02_008_callback_result_failed_is_not_verification` | receive_customer_signature | PASS |
| UAT-M02-009 | REQ-M02-009 | `test_uat_m02_009_amendment_does_not_grant_early_access` | create_contract_amendment, evaluate_entitlements | PASS |
| UAT-M02-010 | REQ-M02-010 | `test_uat_m02_010_segregation_of_duties_blocks_self_acceptance` | decide_company_acceptance | PASS |
| UAT-M02-011 | REQ-M02-011 | `test_uat_m02_011_suspended_entitlement_stays_denied_on_reevaluation` | suspend_entitlement, evaluate_entitlements | PASS |
| UAT-M02-012 | REQ-M02-012 | `test_uat_m02_012_termination_preserves_contract_and_signature_evidence` | terminate_contract, get_contract | PASS |

## 其他正反測試（`test_contract_worker_http.py`）

- HTTP：未認證拒絕（401）、webhook 缺簽章 header（422）、webhook 簽章錯誤（401 `webhook_signature_invalid`）、webhook 驗證逾時（503 `signature_verifier_timeout`）、完整簽署往返、`If-Match` 格式錯誤（422）。
- Worker：outbox 暫時性失敗→重試→恢復發佈；超過 `max_attempts`→dead_letter；`redrive` 要求非空理由。
- Consumer：inbox 去重（相同 event_id 重送回 duplicate、不重建 job）、payload hash 不符拒絕、sequence gap 進 retry、`EntitlementRecheckWorker` 依 job 重新觸發 `evaluate_entitlements`。

## 執行方式與證據

```bash
python3 -m pytest tests/modules/contracts_entitlements -q
```

本輪於 worktree `claude-m02-contracts-entitlements` 本機執行，24 項全部 PASS（12 UAT + 12 其他正反測試），無 SKIP／NOT_RUN。本結果只是 in-memory 參考實作（`InMemoryRepository`、`Fake*Port`）的行為驗證；PostgreSQL schema 相容性由 `python database/tools/verify_database.py`（需 disposable PostgreSQL，本次未在此 worktree 執行）另行驗證，`scripts/validate_contracts.py` 已確認本次改動未破壞既有契約（`status: passed`）。跨模組 E2E（M01/M03/M04/M09/M14 實際串接）未執行，仍為 NOT_RUN；本模組使用對應 Fake port 模擬其輸出。
