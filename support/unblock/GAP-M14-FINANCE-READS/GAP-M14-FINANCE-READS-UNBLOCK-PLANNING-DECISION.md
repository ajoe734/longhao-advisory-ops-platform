# GAP-M14-FINANCE-READS unblock — planning decision

Task: `GAP-M14-FINANCE-READS-UNBLOCK-PLANNING-DECISION`
Parent: `GAP-M14-FINANCE-READS`
Date: 2026-11-28

## Decision

The parent is not blocked on an unresolved finance product policy. Its implemented
M14 contract already requires a response-only `invoice_line_id`, backed by the
existing `finance.invoice_line.id`, and requires a refund allocation to reference
a line on the original payment's invoice. The missing work is ownership of the
generated shared contract artifacts.

`api/build_contracts.py` is the sole maintained input for `api/openapi.yaml`,
`api/operation-catalog.json`, `api/schema-index.json`, and `mocks/fixtures.json`
([API maintenance rule](../../../api/README.md)). The API schema steward must make
the M14 change there and regenerate the four outputs in the same change. The
parent's M14 runtime implementation remains the authoritative behaviour for the
cross-invoice and cross-tenant rejection checks; a static mock fixture is not a
substitute for that behaviour.

The following alternatives are explicitly rejected:

- Do not accept `contract_service_id` or an arbitrary UUID as `invoice_line_ref`.
- Do not add `invoice_line_id` to `InvoiceLineCreate`; it is server-assigned.
- Do not hand-edit only `mocks/fixtures.json` or only `api/openapi.yaml`.
- Do not treat this routing decision as approval of tax, revenue-recognition, GL,
  or refund-policy parameters.

## Scope cut and concrete parent next step

The follow-up scope is only `api/build_contracts.py` plus generated
`api/openapi.yaml`, `api/operation-catalog.json`, `api/schema-index.json`, and
`mocks/fixtures.json`. It must define a response `InvoiceLine` that requires
`invoice_line_id`, retain `InvoiceLineCreate` for requests, regenerate the
artifacts, and pass `python3 scripts/validate_contracts.py`.

After that change lands, resume `GAP-M14-FINANCE-READS` to merge the generated
contract artifacts into its branch, rerun `python3 scripts/validate_contracts.py`
and `python3 -m pytest tests/modules/finance_control -q`, and hand off a fresh
candidate. No database migration, external-provider work, or finance-policy
decision is part of this follow-up.

## Evidence and verification

| Finding / acceptance | Source and decision location | Reproduction → routed result | Command and result | Remaining limit |
| --- | --- | --- | --- | --- |
| Parent cannot hand off because `create_invoice` fixture omits required `invoice_line_id` | Parent head `9d24e627356be894258099296204b40c1c88ae36`; `api/README.md`; [ENG-CONTRACT-01](../../../docs/18-decision-register.md) | Detached parent worktree: M14 domain tests pass, while shared contract validation rejects the missing fixture field. The decision routes generator ownership rather than weakening the response contract. | `python3 -m pytest tests/modules/finance_control -q` → exit 0, `41 passed`; `python3 scripts/validate_contracts.py` → exit 1: `create_invoice response: 'invoice_line_id' is a required property`. | The API schema steward's generated-artifact change is still required. It has not been claimed by the parent task's write scope. |
| Canonical planning artifact records the decision, scope cut, and parent next action | `docs/18-decision-register.md` § ENG-CONTRACT-01; this artifact | N/A — documentation/routing change. | Validation pending at time of initial anchor; final results are appended below before candidate handoff. | Does not certify the parent candidate, external integrations, or formal finance-policy approval. |

## Final documentation checks

- `python3 scripts/validate_docs.py` — exit 0: 14 parallel modules, 142 API
  operations, 182 acceptance cases, and 182 trace rows checked.
- `git diff --check` — exit 0 after this final evidence update.

The initial anchor's `git show --check` exposed two unnecessary Markdown hard-break
spaces in the artifact header. They are removed in the final candidate; this is a
formatting repair only and does not change the routing decision.
