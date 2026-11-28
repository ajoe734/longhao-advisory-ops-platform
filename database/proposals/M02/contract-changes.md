# M02 contract API change: invoiceable service projection

## Change

`GET /api/v1/contracts/{contract_id}` now adds a `services` array to its existing `Contract` response. The M02 implementation always emits it; the OpenAPI field remains optional so deployed, independently versioned providers and older response fixtures stay wire-compatible during the additive rollout. Each `ContractServiceRead` contains:

- `contract_service_id`: the immutable `contract.contract_service.id` required by M14 `InvoiceLineCreate`.
- `service_code`, `service_period_start`, and `service_period_end` for the contractual service period.
- `untaxed_service_amount` as the existing `Money` shape (decimal string plus contract currency).
- `status`, derived from the corresponding `contract.entitlement` state. A missing entitlement is rendered `locked` so a damaged projection never appears invoice-ready.

The response is ordered by service period start, end, then service ID. It includes amendments/renewals that belong to the contract, so finance can choose the correct contractual period rather than infer one from `service_selections`.

## Compatibility and authorization

This is an additive response field: no existing operation, request field, or response field is renamed or removed. Existing `get_contract` authorization remains unchanged: only the contract's customer, a `contract_operator`, or a service principal with `m02:*` scope can read it. `platform_admin` is not newly permitted. Tenant filtering is applied before the projection, so a different tenant receives the established not-found response and no service identifier.

## M14 handoff

M14 can obtain `InvoiceLineCreate.contract_service_id` from the new read model and reuse the exposed service code and untaxed Money amount when constructing its finance-owned invoice line. `ContractServiceRead.service_period_start` and `service_period_end` are RFC3339 UTC date-times, while `InvoiceLineCreate` requires ISO `date` strings: M14 must parse each RFC3339 instant, normalize it to UTC, and submit its UTC calendar component as `YYYY-MM-DD` (for example, `2026-11-28T00:00:00.000000Z` becomes `2026-11-28`). It must not send the timestamp directly to `InvoiceLineCreate`; the finance parser intentionally rejects it. M14 remains responsible for finance tax amount/code and any invoice eligibility controls; M02's `status` is current entitlement safety state, not authorization to create an invoice.

## Verification evidence

`tests/modules/contracts_entitlements/test_contract_worker_http.py::test_http_get_contract_exposes_invoiceable_services_without_relaxing_access` verifies the real controller/service path returns the exact persisted service ID, period, Money amount, and active status after entitlement evaluation. It also verifies unauthenticated (`401`), `platform_admin` (`403`), and cross-tenant (`404`) requests cannot obtain the projection.

| Finding／驗收項 | 原始碼依據與修改位置 | 舊版重現 → 修正版結果 | 命令、結果與證據位置 | 未驗項與具體限制 |
| --- | --- | --- | --- | --- |
| GAP-M02-CONTRACT-SERVICES：M14 取得 invoice line 所需的 `contract_service_id` | Base `d5b707d` 的 `ContractsEntitlementsService.get_contract` 只呼叫 `Contract.to_api`；該 serializer 沒有服務投影。此候選在 `service.py` 加 tenant/contract-scoped projection，`domain.py` 加 `ContractService.to_api`，並在 `api/openapi.yaml` 加 `ContractServiceRead`。 | Base 的精確靜態路徑不會產出 `services`，因此財務 API 呼叫端無從取得服務 PK；修正後正式 controller 回傳儲存的 `contract_service_id`、服務期間、Money 與 entitlement 安全狀態。 | `python3 -m pytest tests/modules/contracts_entitlements/test_contract_worker_http.py::test_http_get_contract_exposes_invoiceable_services_without_relaxing_access -q` → `1 passed`；完整回歸 `python3 -m pytest tests/modules/contracts_entitlements -q` → `42 passed`。 | 未執行外部 M14/UI E2E：本工作範圍不啟動服務、管理介面在未合併 PR #33，且真實財務 provider 仍 NOT_CONNECTED。 |
| 授權不可放寬 | 同一 controller 測試透過正式 `M02Controller.handle` 及既有 `_authorize_customer_resource` 路徑。 | 無認證 → `401`；僅 `platform_admin` → `403`；另一 tenant 的 `contract_operator` → `404`，三者皆沒有服務清單；已授權 `contract_operator` 取得完整投影。 | 上列 scoped test，並由 `python3 -m pytest tests/modules/contracts_entitlements -q` 覆蓋。 | Service principal `m02:*` 的既有 read path 未另重複測試；本次未改變其授權條件。 |
| OpenAPI／文件相容性 | `Contract.services` 為 additive optional field；M02 實作仍每次發送，以避免舊 provider／fixture 在 rollout 過程因 required set 變更而不相容。 | 將欄位列為 required 會使既有 `get_contract` schema fixture 失敗；移除 required 後，實作行為不變而全量契約 fixture 再次有效。 | `python3 scripts/validate_contracts.py` → passed（14 modules、142 operations、191 schemas）；`python3 scripts/validate_docs.py` → PASS（182 acceptance cases / 182 trace rows）。 | Schema validation 驗證合約與 fixture，非 M14 finance integration acceptance。 |
| F1 Blocker（Antigravity 對 `d8cde3b2749e` 的退修）：`get_contract` 回應未被 `Contract` schema 接受 | `api/openapi.yaml` 原將 `services` 寫在 `ApplicationCreate.properties`，但正式 `GET /contracts/{contract_id}` 回傳 `$ref: Contract`，且 `Contract.additionalProperties: false`。本候選將相同 optional projection 移至 `Contract.properties`；`test_http_get_contract_exposes_invoiceable_services_without_relaxing_access` 用正式 `M02Controller.handle` 回應及 `Draft202012Validator` 驗證 `Contract`。 | 修補前新增的最小回歸案例實際失敗：`Additional properties are not allowed ('services' was unexpected)`；修補後同一 controller 回應可通過 `Contract` schema。 | 修補前：`python3 -m pytest tests/modules/contracts_entitlements/test_contract_worker_http.py::test_http_get_contract_exposes_invoiceable_services_without_relaxing_access -q` → `1 failed`（本檔之 `Contract` JSON-schema assertion）。修補後同指令 → `1 passed`；`python3 -m pytest tests/modules/contracts_entitlements -q` → `42 passed`。 | 未啟動 HTTP server；測試直接走正式 controller／service／repository，沒有 mock 被驗證的 projection 或授權邏輯。 |
| F2 Moderate（Antigravity 對 `d8cde3b2749e` 的退修）：M14 日期型別映射 | `ContractServiceRead.service_period_start/end` 使用 OpenAPI `*id003` RFC3339 date-time；`InvoiceLineCreate` 使用 `*id007` ISO date，且 `finance_control.domain.parse_date` 呼叫 `date.fromisoformat`。本文件明訂 RFC3339 instant → UTC `YYYY-MM-DD` 的 consumer mapping。 | 修補前文件稱 M14 可直接 reuse period，與 `parse_date` 不接受時間戳不相容；修補後文件明確禁止直接傳 timestamp，並給出 deterministic conversion。 | 文件內容核對：本檔「M14 handoff」段落與 `api/openapi.yaml` 的 `ContractServiceRead`／`InvoiceLineCreate` 及 `apps/api/src/modules/finance_control/domain.py:parse_date`；`python3 scripts/validate_contracts.py` → passed（14 modules、142 operations、191 schemas），`python3 scripts/validate_docs.py` → PASS（182 acceptance cases / 182 trace rows）。 | 本任務不改 M14 finance command；跨模組 invoice E2E 仍需由整合／M14 流程驗證。 |

API schema steward review is required because `api/openapi.yaml` expands the shared `Contract` response schema.
