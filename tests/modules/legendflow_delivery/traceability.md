# M09 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M09-001 / UAT-M09-001 | get_member_feed（entitled recipients only） | `test_uat_m09_001_feed_shows_only_entitled_publication` |
| REQ-M09-002 / UAT-M09-002 | request_member_report_access（live entitlement recheck） | `test_uat_m09_002_report_access_requires_current_entitlement` |
| REQ-M09-003 / UAT-M09-003 | bind_member_channel（forged/timeout proof 拒絕） | `test_uat_m09_003_unverified_channel_cannot_bind` |
| REQ-M09-004 / UAT-M09-004 | dispatch_publication→process_job（送出前再查權限） | `test_uat_m09_004_dispatch_rechecks_entitlement_and_suppresses` |
| REQ-M09-005 / UAT-M09-005 | process_job accepted（accepted_at 存在但不偽造 delivered/read） | `test_uat_m09_005_accepted_does_not_fabricate_delivery` |
| REQ-M09-006 / UAT-M09-006 | SMS channel read_capability（不偽稱已讀/流失） | `test_uat_m09_006_unknown_read_state_is_not_fabricated_as_unread` |
| REQ-M09-007 / UAT-M09-007 | reconcile_delivery（timeout 先查再判斷；單一邏輯訊息） | `test_uat_m09_007_timeout_is_resolved_before_retry_as_one_logical_message` |
| REQ-M09-008 / UAT-M09-008 | receive_message_receipt（亂序 read/delivered 不倒退） | `test_uat_m09_008_out_of_order_receipts_never_regress` |
| REQ-M09-009 / UAT-M09-009 | redrive_delivery_event（過期內容拒絕重驅、不call provider） | `test_uat_m09_009_expired_dead_letter_cannot_be_redriven` |
| REQ-M09-010 / UAT-M09-010 | publication.corrected consumer + get_member_feed（保留原文） | `test_uat_m09_010_correction_preserves_original_content`、`test_uat_m09_010_correction_links_replacement_delivery_to_original_either_order`、`test_uat_m09_010_withdrawal_cancels_queued_jobs_and_process_job_independently_refuses_to_send` |
| REQ-M09-011 / UAT-M09-011 | `resolve_delivery_permission`（行銷退訂不影響必要服務通知）＋`set_communication_preference` 實際 API | `test_uat_m09_011_marketing_opt_out_never_suppresses_service_notice`、`test_uat_m09_011_set_communication_preference_end_to_end` |
| REQ-M09-012 / UAT-M09-012 | receive_message_receipt（webhook 簽章/重放防護與去重） | `test_uat_m09_012_webhook_requires_valid_signature_and_dedupes_legitimate_replay` |

額外控制由 `test_contract_worker_http.py` 驗 8 個 operationId 的 idempotency/CAS/pagination、webhook timestamp/key/nonce/key-id 長度與 provider 比對、nonce 不因後續驗證失敗被浪費、9 個 consumer 事件的 schema（改為對 `events/schemas/*.json` 做完整 `jsonschema` 驗證，涵蓋 classification/uuid/enum）/hash/duplicate/sequence-gap 合約、apply+inbox 單一交易原子性、`contract.terminated` 依 contract_id 而非 customer_id 隔離、`process_job` 的 lookup-first 重試與 withdrawn/revoked/binding+preference 復核、`DependencyUnavailableError` 不中斷整批、`DispatchWorker`／`OutboxPublisher` 的 lease/retry/dead-letter；`test_security_automation.py` 驗 member/staff SoD、跨 member 資源隔離、`_require_member` 的 member 角色檢查、`bind_member_channel` 單次性 proof 在 idempotent replay 下仍成功、`list_deliveries` 僅授權 OpenAPI 已發布的角色，以及 `delivery_exception_summary` LLM 能力只能草擬摘要、不可自行核准重驅，並驗證 production 門檻拒絕 fake adapter；`test_ui_contract.py` 驗兩個畫面入口、八種 UI 狀態與可及性標記。

## 已知限制

- 整組 `tests/` 樹（跨模組一起跑 `pytest tests/`）在未加 `__init__.py` 的既有慣例下，
  同名測試檔（`test_uat.py` 等）會與其他模組（如 M01）互相覆蓋 Python module
  identity，導致 collection 失敗；此為既有跨模組測試檔命名慣例共有的限制，非本模組
  新增。官方驗證入口維持本模組獨立執行：
  `python -m pytest tests/modules/legendflow_delivery -q`（已驗證綠燈，59 passed）。
- `apps/api/src/modules/legendflow_delivery/plugin.py` 新增後，
  `PlatformRuntime.bootstrap()` 會把 M09 回報為 `loaded`（其餘 13 模組仍是
  `missing`，待各自補 `plugin.py`）；連帶更新了共用
  `qa/test_platform_runtime.py::test_manifest_discovery_reports_missing_modules_without_breaking_startup`
  的斷言（僅改「全部 missing」為「M09 loaded、其餘 missing」，未改動該檔案其他測試）。
  這個檔案不在本任務 `write_scopes` 內，是 F8 修復的必要連動；已以
  `python -m pytest qa/test_platform_runtime.py -q` 驗證（11 passed）。
- `delivery_supervisor` 目前沒有任何已核准契約可查詢 `dead_letter` 投遞清單（見
  `database/proposals/M09/contract-changes.md`「待議」段）；`list_deliveries`
  已改回嚴格比對 `api/openapi.yaml` 的 `x-allowed-roles`
  （`service_agent`、`delivery_operator`），不再私自多放
  `delivery_supervisor`。
- `OutboxPublisher` 沿用 M01 的設計，直接讀真實 wall-clock（非 injectable test
  clock）；對應的 drill 測試以真實 `time.sleep` 跨過 backoff 窗口，而非推進
  `FixedClock`。
- `delivery.communication_preference`／`delivery.dispatch_job` 僅為 proposal
  （`database/proposals/M09`），尚未併入 `database/migrations`；本套件測試全部
  跑在 in-memory `InMemoryRepository`，PostgreSQL RLS／CHECK 需待 data steward
  併入正式 migration 後，在非 superuser PostgreSQL 16 另行執行
  `database/tools/verify_database.py` 驗證。
- LegendFlow member BFF、LINE/SMS/email provider、M08 publication/recipient、
  M02 entitlement、M07 report signing 均為 `FakePublicationPort` 等本機測試替身；
  通過本套件不代表任何外部系統已真實接通（`NOT_CONNECTED`）。
