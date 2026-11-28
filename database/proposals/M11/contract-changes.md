# GAP-M11-POOL-POLICY-API contract change record

Task: `GAP-M11-POOL-POLICY-API`
Base: `d5b707d` (`origin/dev` on 2026-11-28)
Owner: Codex; reviewer: Antigravity

## Backward-compatible API addition

The M11 policy lifecycle existed only as
`OwnershipPoolService.propose_policy_version` and
`OwnershipPoolService.approve_policy_version`.  This change publishes three
tenant-scoped staff operations without renaming or removing any existing M11
field or operation:

| Operation | HTTP path | Role | Command/query guarantees |
| --- | --- | --- | --- |
| `create_company_pool_policy` | `POST /ownership/company-pool-policies` | `sales_supervisor` | Versioned draft creation, idempotency receipt and audit in one repository transaction. |
| `approve_company_pool_policy` | `POST /ownership/company-pool-policies/{policy_id}/approval` | `sales_supervisor` | Strong `If-Match`, idempotency receipt, audit and the existing maker/checker rule (`approved_by != proposed_by`). |
| `get_current_company_pool_policy` | `GET /ownership/company-pool-policies/current` | `sales_supervisor` | Audited tenant-scoped read of the highest **approved** `company_pool_lapse` version; later drafts never hide the active version. |

`platform_admin` is deliberately absent from all three role lists.  Existing
`list_company_pool` remains restricted to `sales` and `sales_supervisor`.
Unauthenticated API controller calls return the existing 401 domain problem;
cross-tenant policy identifiers return 404 rather than exposing policy data.

`CompanyPoolPolicyVersion` exposes immutable version/audit-relevant metadata
(`proposed_by`, `approved_by`, `approved_at`) and server-computed
`allowed_actions`; it does not accept tenant, actor, policy-code, approval, or
version fields from a request body.  A draft may still be used for preview by
the existing service rules, but cannot become this endpoint's "current" policy
or enable a live sweep before a different supervisor approves it.

## Database and generated-contract compatibility

No database migration is proposed: `ownership.policy_version` already contains
the policy version, approval state, maker/checker and CAS columns required by
these operations (see `database/migrations/0001_domains.sql`).  This change
only makes that existing lifecycle reachable through the M11 API adapter.

`api/openapi.yaml` is the requested shared-schema surface. `api/build_contracts.py`
remains the generator for `api/operation-catalog.json` and `mocks/fixtures.json`,
but it does not yet declare these three operations. Those generated files and
their source are outside this task's assigned write scope. The API schema
steward must add the same three operation declarations and schemas, regenerate
the artifacts, and preserve these additive paths during conflict resolution.

`python3 scripts/validate_contracts.py` currently passes because it validates
the generated catalog and fixtures as a matched pair but does not assert that
they enumerate every operation in `api/openapi.yaml`. It is therefore not
evidence that the generator gap is resolved. The generated-client/mock parity
work remains a schema-steward handoff, not an application behavior failure.

## Finding and verification evidence

| Finding / acceptance | Source and changed boundary | Prior state -> repaired result | Command/result evidence | Remaining limitation |
| --- | --- | --- | --- | --- |
| No public company Pool policy lifecycle API | `OwnershipPoolService.create_company_pool_policy`, `approve_company_pool_policy`, `get_current_company_pool_policy`; `M11Controller` and `api/openapi.yaml` | At base `d5b707d`, creation/approval were direct service-only calls and no policy HTTP routes existed -> the three routes delegate to the existing rules and preserve command audit/idempotency behavior | `python3 -m pytest tests/modules/ownership_pool -q` -> `71 passed in 2.12s` | Generated operation catalog/fixtures need schema-steward synchronization. |
| Maker must differ from reviewer; current must exclude drafts | `OwnershipPoolService.approve_policy_version`, `get_current_company_pool_policy` | Same actor cannot approve a draft (403); an approved v1 remains current after draft v2 -> preserved by controller regression | `tests/modules/ownership_pool/test_http.py::test_company_pool_policy_lifecycle_is_published_audited_and_current` | No external provider boundary applies. |
| 401/403, tenant isolation, server-controlled fields and CAS | `M11Controller.handle`; policy route dispatch and `OwnershipPoolService._body`/`_cas` | No policy HTTP surface -> missing identity gives 401, `platform_admin` gives 403, foreign tenant gives 404, forged tenant input gives 422, missing/stale `If-Match` gives 428/412 | `tests/modules/ownership_pool/test_http.py::test_company_pool_policy_controller_rejects_missing_identity_and_platform_admin`, `::test_company_pool_policy_controller_is_tenant_scoped`, `::test_company_pool_policy_commands_reject_server_controlled_fields_and_stale_versions` | Hosted API/runtime integration remains owned by the shared platform workflow. |
| OpenAPI validity and published route/schema alignment | `api/openapi.yaml`; `ROUTES`; OpenAPI-response regression | Base has no operations/schemas -> all three paths expose the documented role/audit metadata and a live response validates as `CompanyPoolPolicyVersion` | `python3 scripts/validate_contracts.py` -> passed; `python3 scripts/validate_docs.py` -> passed; `::test_company_pool_policy_operations_and_live_response_match_openapi` | Contract validator has the catalog coverage blind spot described above. |
