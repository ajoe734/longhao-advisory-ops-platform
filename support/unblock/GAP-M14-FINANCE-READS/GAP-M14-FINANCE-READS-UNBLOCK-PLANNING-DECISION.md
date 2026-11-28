# GAP-M14-FINANCE-READS unblock — corrected planning decision

Task: `GAP-M14-FINANCE-READS-UNBLOCK-PLANNING-DECISION`
Parent: `GAP-M14-FINANCE-READS`
Reopened candidate: `f2294da94d01035a5224400ab4fa9d63e9fac4b1` (PR #42)
Date: 2026-11-28

This revision replaces the initial generator-only route. It retains each reviewer
finding and records the narrow repair boundary required after two review rounds.

## Decision

This is not an unresolved finance-policy decision. The M14 runtime contract is:

- An invoice response emits the server-assigned `InvoiceLine.invoice_line_id`;
  `InvoiceLineCreate` remains request-only and never accepts that ID.
- A refund allocation accepts only a line of the original payment's invoice in the
  same tenant, never `contract_service_id` or an arbitrary UUID.
- An accounting-export response emits `period_start` and `period_end` for create,
  acknowledgement, and read paths.

The shared OpenAPI documents those three response properties now, but they are
temporarily non-required. This is a backward-compatible compatibility boundary
for the stale generated synthetic fixtures only; the M14 runtime and its module
tests must keep emitting and asserting all three values. It does not approve tax,
GL, revenue-recognition, or refund-policy parameters.

## Reopened findings, reproduction, and repair boundary

| Finding / trigger | Actual source evidence and old result | Corrected boundary | Verification / remaining limit |
| --- | --- | --- | --- |
| F1 — running the generator for only M14 would destructively discard six merged GAP changes | `api/build_contracts.py:454-456` writes OpenAPI, catalog, and schema index; lines 557, 564, 566, 568 additionally write event schemas, examples, registry, and fixtures. It has not changed after `9020b52`, while `f61ba27`, `04bfafc`, `bc570e2`, `28b67e7`, `dbc5332`, and `91cb423` directly changed OpenAPI (M01 also changed registry). At `origin/dev`: OpenAPI has 152 operations / 204 schemas; catalog/index have 142 / 190. | Do **not** run `api/build_contracts.py`, and do not edit its generated surfaces for this parent repair. Make the three M14 properties non-required in `api/openapi.yaml` only, preserving the four M14 read operations/page schemas. | The complete generator reconciliation must be a separately created machine-truth task before any later generator invocation; it must merge all six GAP changes plus M14 and include `events/registry.json`. |
| F2 — `validate_contracts.py` stopped at the first M14 fixture error and masked three more | Parent `9d24e627356be894258099296204b40c1c88ae36`; `scripts/validate_contracts.py:82` raises after the first validation error. The formal validator first reports `create_invoice response: 'invoice_line_id' is a required property`. A read-only historical-schema probe against the same parent then reports: `create_invoice` missing `invoice_line_id`; `create_accounting_export`, `acknowledge_accounting_export`, and `get_accounting_export` each missing `period_start` and `period_end`. | The compatibility cut covers both schemas: `InvoiceLine.invoice_line_id` and `AccountingExport.period_start` / `period_end`. It is insufficient to address InvoiceLine alone. | Once those properties are not in OpenAPI `required`, the existing generated fixtures remain valid while runtime coverage still requires the values. Parent reruns the full validator after its exact change. |
| F3 — the original route and parent handoff were incomplete | The initial artifact incorrectly described the generator as safe and omitted the three AccountingExport responses and four M14 read operations. | Resume the parent with the concrete next step below. Update the parent machine-truth `next` at the same time as this artifact is published. | This artifact is planning evidence only; it neither certifies the parent runtime nor an external integration. |

## Concrete parent next step

On published branch `codex/gap-m14-finance-reads` at parent head
`9d24e627356be894258099296204b40c1c88ae36`, make this one non-destructive
compatibility edit in `api/openapi.yaml`:

1. Retain properties `InvoiceLine.invoice_line_id` and
   `AccountingExport.period_start` / `period_end`, but remove just those three
   names from their `required` arrays.
2. Preserve `list_refunds`, `list_accounting_exports`, `list_revenue_runs`, and
   `list_period_closes`, including their page schemas, finance-role restrictions,
   tenant scope, cursor, and half-open period filters.
3. Do not run or edit `api/build_contracts.py`, `api/operation-catalog.json`,
   `api/schema-index.json`, `mocks/fixtures.json`, or any `events/` output.
4. Run `python3 scripts/validate_contracts.py` and
   `python3 -m pytest tests/modules/finance_control -q`; record actual results,
   push normally, and hand off a new parent candidate.

No migration, external-provider work, mock-as-runtime substitution, or finance
policy change is authorized by this route.

## Required post-GAP follow-up

Before anyone next invokes `api/build_contracts.py`, the integration coordinator
must create and assign an API schema steward task (proposed ID:
`GAP-API-CONTRACT-GENERATOR-RECONCILIATION`) in machine truth. It must reconcile
the six merged GAP PRs and M14 into generator inputs, regenerate every file the
script writes, compare OpenAPI/catalog/index/fixtures/event registry outputs, and
then remove this temporary non-required compatibility exception. It is not a
blocker for the narrow parent action above.

## Verification record

| Finding / acceptance | Command / source | Result | Not verified here |
| --- | --- | --- | --- |
| F1 generator drift | `git diff --name-only 9020b52..origin/dev -- api/build_contracts.py api/openapi.yaml api/operation-catalog.json api/schema-index.json events/registry.json`; source lines cited above | Only `api/openapi.yaml` and `events/registry.json` changed after the generator baseline; no generator update exists. | Reconciliation task has not yet been created or run. |
| F2 all masked fixture failures | Read-only `jsonschema` validation of the four exact responses from `9d24e627356be894258099296204b40c1c88ae36` against that commit's OpenAPI | Four failures across `Invoice` and `AccountingExport`, as listed above. | Parent's repaired schema/runtime checks remain pending. |
| This planning revision | [ENG-CONTRACT-01](../../../docs/18-decision-register.md) and this artifact | Decision, scope cut, and parent next step now cover both schema gaps and prohibit destructive regeneration. | Does not certify parent candidate, CI, database, or external provider readiness. |

## Current planning-candidate checks

- `python3 scripts/validate_docs.py` — exit 0: 14 parallel modules, 142 API
  operations traced, 182 acceptance cases, and 182 trace rows.
- `python3 scripts/validate_contracts.py` — exit 0: 14 modules, 142 catalog
  operations, 204 OpenAPI schemas, 47 event contracts, 289 positive and 342
  negative schema cases.
- `git diff --check` — exit 0 before this evidence update. The final candidate
  reruns it after the evidence-only edit.

These are checks of this planning branch. They do not replace the parent branch's
post-edit full contract validation or its M14 module test run.
