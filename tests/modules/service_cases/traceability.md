# M13 實作追蹤

| REQ / UAT | operation / domain | 實際測試 |
|---|---|---|
| REQ-M13-001 / UAT-M13-001 | create_service_case, add_case_interaction, SLA timestamps unchanged | `test_uat_m13_001_cross_channel_case_keeps_single_sla_timeline` |
| REQ-M13-002 / UAT-M13-002 | add_case_interaction idempotency replay | `test_uat_m13_002_duplicate_provider_message_creates_one_interaction` |
| REQ-M13-003 / UAT-M13-003 | get_service_case unlinked-case field masking | `test_uat_m13_003_unverified_case_hides_contract_and_payment_links` |
| REQ-M13-004 / UAT-M13-004 | draft/get draft/approve/send/get response/receive receipt | `test_uat_m13_004_draft_requires_approval_before_send_and_tracks_real_status` |
| REQ-M13-005 / UAT-M13-005 | draft_service_response requires_human + escalate_service_case(analyst) | `test_uat_m13_005_investment_question_routes_to_analyst_not_adhoc_advice` |
| REQ-M13-006 / UAT-M13-006 | escalate_service_case(finance) + no approve/execute method | `test_uat_m13_006_refund_request_cannot_be_approved_or_executed_by_service_agent` |
| REQ-M13-007 / UAT-M13-007 | escalate_service_case(complaint) + SuppressionJobRunner | `test_uat_m13_007_major_complaint_suppresses_marketing_without_blocking_service` |
| REQ-M13-008 / UAT-M13-008 | escalate/assign preserve SLA due dates; clock advance | `test_uat_m13_008_waiting_internal_does_not_reset_sla_clock` |
| REQ-M13-009 / UAT-M13-009 | escalate_service_case(compliance) hides content; draft blocked | `test_uat_m13_009_aml_handoff_does_not_copy_content_into_timeline_or_llm` |
| REQ-M13-010 / UAT-M13-010 | resolve_service_case 422 then success + CaseResolution evidence | `test_uat_m13_010_resolution_requires_evidence_and_keeps_delivery_status_separate` |
| REQ-M13-011 / UAT-M13-011 | reopen_service_case preserves resolution + SLA dates | `test_uat_m13_011_reopen_preserves_original_resolution_history` |
| REQ-M13-012 / UAT-M13-012 | assign/resolve vs ownership.assignment_changed projection | `test_uat_m13_012_case_owner_change_does_not_move_maintenance_ownership` |

額外控制由 `test_m13_contract_worker_http.py` 驗 14 route、OpenAPI/event schema、consumer
hash/sequence-gap/tenant isolation、outbox 與 response-dispatch worker 的
lease/retry/unknown-result-lookup（含永不落入非合約列舉值 `retry`）、
webhook Idempotency-Key/timestamp/nonce/signature 驗證、router prefix 不重複、
production fake adapter gate；`test_m13_security_automation.py` 驗 tenant isolation、
customer 互動方向限制、錄音同意證據、automation 最小化輸入、AML 案件完全不呼叫
assistant、`contains_new_investment_judgment` 的硬性拒絕核定/送出、
`approved_low_risk_template` 僅限受控 policy service 且排除
complaint/refund/research_question/AML、allowed_actions 不重複對已結案案件
提供 resolve、不對草稿人提供 approve、本模組無退款 approve/execute 方法；
`test_m13_ui_contract.py` 驗三個畫面入口、八種 UI 狀態、未驗證案件隱藏契約/付款連結、
內部筆記分區與送達狀態不冒充已送達。

測試檔名加 `m13_` 前綴（例如 `test_m13_uat.py`）以避免與 M01 等其他模組在
pytest rootdir-relative 匯入模式下的 basename 衝突；跨模組共用的
`case_body`/`create_case`/`draft_body`/`receive_receipt` 等輔助函式改用
`from tests.modules.service_cases.conftest import ...` 完整路徑匯入，取代原本
會撞名的 `from conftest import ...`。

## Reopen 修復證據（候選 `62d914bddbba43d1eab5b565c9e584828e13791c` → 本候選）

Reviewer `Antigravity` 的完整 review report：
`/home/hsinping_hung_cctech_support_com/.gemini/antigravity-cli/brain/729bc1e2-ca0a-4ce4-b561-8d86176caf37/m13-review-62d914b.md`。

| Finding | 原始碼依據與修改位置 | 舊版重現 → 修正版結果 | 命令、退出碼、執行版本與證據位置 | 未驗項與具體限制 |
| --- | --- | --- | --- | --- |
| F1 Critical：`dispatch.status="retry"` 違反 OpenAPI/CHECK | `workers/tasks/service_cases/tasks.py` `ResponseDispatchSender._fail`（新增 `retry_status` 參數）、`_lease`（篩選改 `{"queued","unknown"}`）；`domain.py` `DISPATCH_STATES` 本已正確，問題只在 tasks.py 寫入值 | 舊：`get_service_response()` 回 `"retry"`，Draft202012 驗證失敗。新：timeout 非終態轉 `queued`、unresolved unknown 轉 `unknown`，兩者皆在 `ServiceResponse.state` enum 內 | 新增 `test_response_dispatch_never_persists_an_invalid_retry_state`；`python3 -m pytest tests/modules/service_cases -q` exit 0（44 passed，含既有 `test_response_dispatch_worker_lease_bounded_retry_and_queries_unknown_before_resend` 不變） | 未新增獨立的 `unknown`→人工覆核 reconciliation 流程；`unknown` 仍在同一 lease 迴圈內，下次 lease 前才查 `provider.lookup`，與現有 worker 測試行為一致 |
| F2 Critical：`approved_low_risk_template` 繞過 maker-checker | `apps/api/src/modules/service_cases/service.py` `approve_service_response_draft`（新增 `_LOW_RISK_TEMPLATE_APPROVERS`/`_LOW_RISK_TEMPLATE_BLOCKED_CATEGORIES` 限制、`publication_reference_ids` 加 `require_uuid`） | 舊：drafter 以 `approval_mode="approved_low_risk_template"` 自核任意 case（含 complaint）成功 201。新：僅 `service_response_service` 角色可用，且 complaint/refund/research_question 與 `escalated_queue=="compliance"` 一律 Forbidden | 新增 `test_approved_low_risk_template_requires_policy_service_and_blocks_sensitive_categories`；同上 pytest 結果 | 無 |
| F3 Critical：缺 `longhao_platform` 整合、`/api/v1` 雙重 prefix | `apps/api/src/modules/service_cases/http.py` `create_fastapi_router`（移除 `APIRouter(prefix="/api/v1")`） | 舊：若掛載會變 `/api/v1/api/v1/...`。新：`router.prefix==""`，由 `longhao_platform/http.py:90` 的 `include_router(..., prefix="/api/v1")` 統一加一次 | 新增 `test_webhook_router_prefix_is_not_doubled`；`PYTHONPATH=apps/api/src python3 -m longhao_platform.main --check` exit 0，M13 仍為 `missing`/`MODULE_NOT_INTEGRATED`（與全部 14 模組一致） | **未新增 `plugin.py`**：`qa/test_platform_runtime.py::test_manifest_discovery_reports_missing_modules_without_breaking_startup` 斷言全部 14 模組狀態須為 `{"missing"}`；commit `3bac838`（M10 reopen）已明確以相同理由移除 M10 自己的 `plugin.py`，確認目前 repo 慣例是全模組一起延後整合，非 M13 單獨缺漏。已跑 `python3 -m pytest qa/test_platform_runtime.py -q` exit 0（11 passed）確認未破壞此斷言 |
| F4 High：`pytest tests/` collection 衝突 | `tests/modules/service_cases/` 四個檔案 `git mv` 成 `test_m13_*.py`；`from conftest import ...` 改 `from tests.modules.service_cases.conftest import ...`（含函式內 inline import） | 舊：`python3 -m pytest tests/ -q` exit 2（4 個 import file mismatch）。新：exit 0 | `python3 -m pytest tests/ -q` exit 0（73 passed，含 M01 等其他模組不受影響） | 未新增 `tests/__init__.py`／`tests/modules/__init__.py`（超出 M13 write_scope，且非必要：qualified import 已用 PEP 420 namespace package 解析） |
| F5 High：consumer 缺 `support.case_opened`、`new_id()` 重複呼叫、assignment state 誤寫 | `workers/tasks/service_cases/consumer.py` `_CONTRACTS` 加 `support.case_opened`；`_apply` 的 `publication.withdrawn` 分支改單次 `new_id()`；`ownership.assignment_changed` 分支依 state 清空/忽略/寫入 | 舊：consume 自家 `support.case_opened` 丟 `ValidationError`；`publication.withdrawn` 寫入的 dict key 與 `SlaEvent.id` 不同；`released`/`superseded` 都被當 `active` 覆寫。新：三者皆修正 | 新增 `test_consumer_accepts_its_own_self_produced_case_opened_event`、`test_consumer_assignment_state_clears_or_ignores_instead_of_blind_overwrite`、`test_consumer_publication_withdrawn_sla_event_id_matches_dict_key`；同一 pytest 結果 | 無 |
| F6 High：`_case_actions`/`_draft_actions` 與 UI 送出按鈕不可達 | `service.py` `_case_actions`（加 `send_service_response`；`resolve_service_case` 排除條件加 `resolved`）、`_draft_actions`（排除 drafter 自己，除非持有 `service_response_service` 角色）；`AppealRefundPanel.tsx` 送出按鈕移出 `{model.response && ...}` 區塊 | 舊：`_case_actions` 永不含 `send_service_response`；`resolved` 案件仍顯示 `resolve_service_case`；`_draft_actions` 對 drafter 顯示 `approve`；UI 首次送出按鈕不可見。新：四者皆修正 | 新增 `test_case_allowed_actions_offer_send_and_never_offer_resolve_twice`、`test_draft_allowed_actions_never_offer_approve_to_the_drafter`；`test_ui_contract.py` 既有字串斷言不變；同一 pytest 結果 | TSX 變更未跑 `tsc -b`／browser 互動測試：`apps/admin/node_modules` 在本 worktree 不存在、且 VM 限制禁止啟動 dev server／Playwright；需仰賴 hosted CI 的 `contracts-and-documentation`/`database-runtime-and-isolation` job 驗證 build |
| F7(a) Medium：`requires_human` 草稿違反 baseline CHECK | `service.py` `draft_service_response`：requires_human 分支改存 placeholder evidence asset 並填 `content_asset_id`/`content_sha256` | 舊：`ResponseDraft(status="requires_human", content_asset_id=None)` 違反 `0001_domains.sql` CHECK。新：`content_asset_id` 必填 | 於既有 `test_uat_m13_005_investment_question_routes_to_analyst_not_adhoc_advice` 加入 `stored_draft.content_asset_id is not None` 斷言；同一 pytest 結果 | 未跑正式 PG CHECK（非 superuser RLS 執行期）；已用 standalone PostgreSQL 16 套用 `database/proposals/M13/001_service_cases.sql` 確認 DDL/CHECK 語法相容（見下） |
| F7(b) Medium：webhook Idempotency-Key/Timestamp/Nonce/signature 未驗證 | `service.py` `receive_service_response_receipt` 加 `_KEY_RE`/`_WEBHOOK_TS_RE`/`_WEBHOOK_NONCE_RE`/±300s skew/`integration_keys.secret`/HMAC-SHA256 比對/nonce 重放拒絕；`ports.py` 新增 `IntegrationKeyPort`/`FakeIntegrationKeys`；`repository.py` `StoreState.webhook_nonces`；`http.py` 透傳 `raw_body` 並修正 `headers.get("integration-key-id")`→`"x-integration-key-id"`（原碼未加 `X-` 前綴，HTTP 層永遠讀不到此 header）；`database/proposals/M13/001_service_cases.sql` 新增 `support.webhook_nonce` 表 | 舊：僅檢查 `Integration-Key-Id` 非空字串，完全未驗證格式/時效/重放/簽章。新：六項缺失逐一修正並有獨立負向測試 | 新增 `test_receive_service_response_receipt_rejects_bad_webhook_headers`（bad key / bad timestamp / stale timestamp / bad nonce / bad signature / unknown integration key / nonce replay 七個子案例）；同一 pytest 結果；新表已用 standalone PostgreSQL 16 套用成功（`PASS database/proposals/M13/001_service_cases.sql`） | 未接 production HSM/KMS 金鑰管理；`FakeIntegrationKeys` 僅供測試，production 環境會被 `ServiceCaseService.__init__` 的 fake-adapter 守門擋下 |
| F7(c) Medium：`contract-changes.md` 誤稱 `request_refund` 不存在 | `database/proposals/M13/contract-changes.md` 改寫為 M14 cross-module port 邊界說明 | 舊：文件主張 `request_refund` 不存在於 `api/openapi.yaml`，建議 QA 刪除該 operationId。新：確認其存在於 M14（`POST /finance/refunds`，`x-allowed-roles` 含 `service_agent`），說明屬於 `NOT_CONNECTED` 的跨模組 port 邊界，不建議刪除 | `python3 -c "import yaml; ..."` 確認 `request_refund` 於 `api/openapi.yaml` 存在且 `x-module: M14`（見本次對話證據，非自動化測試） | 未修改 `qa/test-catalog.json`（超出 M13 write_scope，交 QA/integration steward 依文件建議調整） |

### 本輪整體回歸

- `python3 -m pytest tests/modules/service_cases -q`：exit 0，44 passed（35 既有 + 9 新增）。
- `python3 -m pytest tests/ -q`：exit 0，73 passed（整個 repo，含 M01）。
- `python3 scripts/validate_contracts.py`：`status: passed`，142 ops / 190 schemas / 47 events（與修復前相同，確認未變更契約）。
- `python3 scripts/validate_docs.py` / `python3 scripts/validate_sql.py` / `python3 scripts/check_public_repository.py`：全部 PASS。
- `git diff --check HEAD`：無空白字元錯誤。
- `python3 -m pytest qa/test_platform_runtime.py -q`：exit 0，11 passed（確認 F3 的「不加 plugin.py」決定未破壞平台共用測試）。
- standalone PostgreSQL 16（`pgserver` 單人引擎）依序套用 `0001_domains.sql`→`0004_database_roles.sql`→`001_synthetic.sql`→`database/proposals/M13/001_service_cases.sql`（含新 `support.webhook_nonce`）：全部無 ERROR/FATAL/PANIC。
- **NOT_RUN**：`apps/admin` 的 `tsc -b`/`vite build`（本 worktree 無 `node_modules`，VM 限制禁止安裝/啟動開發伺服器驗證 UI）；PostgreSQL 非 superuser RLS 執行期測試（需 `--dsn-env`/`--ephemeral`）；browser/Playwright 互動驗證。三者皆仰賴既有授權的 hosted CI workflow 對本候選 SHA 重新執行。
