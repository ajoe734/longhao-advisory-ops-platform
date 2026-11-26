# M10 database proposals

- `001_crm.sql`: additive-only changes to the already-merged baseline
  `crm.*` tables (`database/migrations/0001_domains.sql`):
  - `crm.opportunity`: widens the `kind` CHECK to add `retention`; adds
    `open_reason_code` (create-time rationale, distinct from
    `loss_reason_code`) and `converted_at`.
  - `crm.work_item`: adds `worklist`, `reason_codes[]`, `evidence_refs[]`,
    `suggestion_origin`, `note` so `CrmWorkItem`'s full API shape is
    representable (the baseline only had one `reason_code` and an
    unstructured `explanation` jsonb).
  - `crm.conversion`: adds `application_id`, `conversion_kind` (the API's
    own `conversion_type` vocabulary -- a different axis from the baseline
    `conversion_type` self_service/sales_assisted column),
    `claimed_salesperson_id`, `claim_history` (append-only competing-claim
    audit trail), `evidence_activity_ids[]`, `finance_transaction_ref`,
    `decision_at`; widens `decision_status` to add `under_review` and
    `reversed`.
  - `crm.suppression`: adds `reason_ref`.
  - `crm.activity`: adds `customer_response_evidence_id`, `needs_summary`,
    `late_entry`, `late_entry_approved_by`, `late_entry_approved_at`.
  - Two partial indexes for the worklist sweep's and the opportunity-owner
    query's hot paths.
  - Not yet applied to `database/migrations/`; the data steward owns
    merge, numbering and conflict resolution with parallel module
    proposals.
- Depends on the already-merged baseline `crm.*` tables and
  `core.touch_version`/`core.current_tenant_id` helper functions from
  `database/migrations/0001_domains.sql`.
- Not independently executed against PostgreSQL by this task; verify with
  `database/tools/verify_database.py` only after the steward merges it
  alongside the base migrations. This worker's sandbox has no PostgreSQL
  service available, so the SQL has been reviewed for correctness (valid
  `pg_constraint`/`pg_policies` introspection, idempotent `IF NOT EXISTS`/
  `DO $$...$$` blocks matching the exact style already used by
  `database/proposals/M09/001_legendflow_delivery.sql`) but not executed;
  this is an explicit NOT_RUN, not a passed check.
