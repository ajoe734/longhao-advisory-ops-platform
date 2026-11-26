-- M11 proposal: reconcile ownership.pool_entry.reason_code with the newer
-- OpenAPI/event wire contract, and add ownership.assignment.end_state so an
-- assignment's *closure* reason (active/released/departure/superseded) is a
-- real column instead of overloading reason_code (which already means "why
-- this interval was created"). See database/proposals/M11/README.md for the
-- full rationale. NOT an applied migration — integration/data steward owns
-- sequencing this into database/migrations/.

BEGIN;

-- 1. ownership.pool_entry.reason_code: widen the CHECK to the four-value
--    OpenAPI/event enum while keeping the two original values valid for any
--    existing seed/fixture data that already used them.
DO $$
DECLARE
  constraint_name text;
BEGIN
  SELECT con.conname INTO constraint_name
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'ownership'
    AND rel.relname = 'pool_entry'
    AND con.contype = 'c'
    AND pg_get_constraintdef(con.oid) ILIKE '%reason_code%';

  IF constraint_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE ownership.pool_entry DROP CONSTRAINT %I', constraint_name);
  END IF;
END $$;

ALTER TABLE ownership.pool_entry ADD CONSTRAINT pool_entry_reason_code_check
  CHECK (reason_code IN (
    'staff_departure', 'all_paid_services_lapsed',       -- original baseline values
    'unassigned_web', 'employee_departure', 'paid_service_lapse', 'manual_release'  -- api/openapi.yaml PoolEntry.entry_reason
  ));

COMMENT ON COLUMN ownership.pool_entry.reason_code IS
  'unassigned_web/employee_departure/paid_service_lapse/manual_release (api/openapi.yaml PoolEntry.entry_reason); staff_departure/all_paid_services_lapsed kept for pre-existing rows.';

-- 2. ownership.assignment.end_state: how this interval ended, distinct from
--    reason_code (why it was created). Only meaningful once valid_until is set.
ALTER TABLE ownership.assignment ADD COLUMN IF NOT EXISTS end_state text;

DO $$
DECLARE
  constraint_name text;
BEGIN
  SELECT con.conname INTO constraint_name
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'ownership'
    AND rel.relname = 'assignment'
    AND con.contype = 'c'
    AND con.conname = 'assignment_end_state_check';

  IF constraint_name IS NULL THEN
    ALTER TABLE ownership.assignment ADD CONSTRAINT assignment_end_state_check
      CHECK (
        (valid_until IS NULL AND end_state IS NULL)
        OR (valid_until IS NOT NULL AND end_state IN ('released', 'departure', 'superseded'))
      );
  END IF;
END $$;

COMMENT ON COLUMN ownership.assignment.end_state IS
  'How this interval ended (MaintenanceAssignment.state when valid_until is set): released/departure/superseded. Null while valid_until is null (still active).';

COMMIT;
