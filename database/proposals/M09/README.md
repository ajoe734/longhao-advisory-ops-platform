# M09 database proposals

- `001_legendflow_delivery.sql`: adds `delivery.communication_preference`
  (service vs marketing opt-in per channel, required by `set_communication_preference`),
  `delivery.dispatch_job` (per-message outbound lease/retry/dead-letter queue;
  `redrive_delivery_event`'s `dead_letter_id` CAS target), and
  `delivery.message.replaces_delivery_id` (correction messages point at the
  original without rewriting it). Not yet applied to `database/migrations/`;
  the data steward owns merge, numbering and conflict resolution with
  parallel module proposals.
- Depends on the already-merged baseline `delivery.message` /
  `delivery.channel_binding` / `delivery.attempt` / `delivery.receipt` tables
  and `core.touch_version` / `core.current_tenant_id` helper functions from
  `database/migrations/0001_domains.sql`.
- Not independently executed against PostgreSQL by this task; verify with
  `database/tools/verify_database.py` only after the steward merges it
  alongside the base migrations.
