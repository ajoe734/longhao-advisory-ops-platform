# M02 contract API change: invoiceable service projection

## Change

`GET /api/v1/contracts/{contract_id}` now returns a required `services` array in its existing `Contract` response. Each `ContractServiceRead` contains:

- `contract_service_id`: the immutable `contract.contract_service.id` required by M14 `InvoiceLineCreate`.
- `service_code`, `service_period_start`, and `service_period_end` for the contractual service period.
- `untaxed_service_amount` as the existing `Money` shape (decimal string plus contract currency).
- `status`, derived from the corresponding `contract.entitlement` state. A missing entitlement is rendered `locked` so a damaged projection never appears invoice-ready.

The response is ordered by service period start, end, then service ID. It includes amendments/renewals that belong to the contract, so finance can choose the correct contractual period rather than infer one from `service_selections`.

## Compatibility and authorization

This is an additive response field: no existing operation, request field, or response field is renamed or removed. Existing `get_contract` authorization remains unchanged: only the contract's customer, a `contract_operator`, or a service principal with `m02:*` scope can read it. `platform_admin` is not newly permitted. Tenant filtering is applied before the projection, so a different tenant receives the established not-found response and no service identifier.

## M14 handoff

M14 can obtain `InvoiceLineCreate.contract_service_id` from the new read model and reuse the exposed service code, period, and untaxed Money amount when constructing its finance-owned invoice line. M14 remains responsible for finance tax amount/code and any invoice eligibility controls; M02's `status` is current entitlement safety state, not authorization to create an invoice.

## Verification evidence

`tests/modules/contracts_entitlements/test_contract_worker_http.py::test_http_get_contract_exposes_invoiceable_services_without_relaxing_access` verifies the real controller/service path returns the exact persisted service ID, period, Money amount, and active status after entitlement evaluation. It also verifies unauthenticated (`401`), `platform_admin` (`403`), and cross-tenant (`404`) requests cannot obtain the projection.

API schema steward review is required because `api/openapi.yaml` expands the shared `Contract` response schema.
