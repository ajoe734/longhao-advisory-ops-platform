-- M11 proposal: reconcile ownership.pool_entry.reason_code with the newer
-- OpenAPI/event wire contract, keep ownership.guard_pool_entry() covering the
-- new value, and add ownership.assignment.end_state so an assignment's
-- *closure* reason (active/released/departure/superseded) is a real column
-- instead of overloading reason_code (which already means "why this interval
-- was created"). See database/proposals/M11/README.md for the full rationale.
-- NOT an applied migration — integration/data steward owns sequencing this
-- into database/migrations/.

BEGIN;

-- 1. ownership.pool_entry.reason_code: widen the CHECK to the four-value
--    OpenAPI/event enum while keeping the two original values valid for any
--    existing seed/fixture data that already used them. There are TWO
--    pre-existing CHECK constraints that mention reason_code on this table
--    (database/data-dictionary.md §ownership.pool_entry):
--      - the value-list enum check
--      - the "paid_service_lapse requires all_paid_services_ended_at" check
--    Both must be located and replaced explicitly and by name; a single
--    ambiguous `ILIKE '%reason_code%'` lookup can match either one
--    nondeterministically and silently leave the other (and therefore the
--    old 2-value enum, or the un-widened ended_at requirement) in force.
DO $$
DECLARE
  value_list_constraint text;
  ended_at_constraint text;
BEGIN
  SELECT con.conname INTO value_list_constraint
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'ownership'
    AND rel.relname = 'pool_entry'
    AND con.contype = 'c'
    AND (
      pg_get_constraintdef(con.oid) ILIKE '%reason_code = ANY%'
      OR pg_get_constraintdef(con.oid) ILIKE '%reason_code IN (%'
    )
    AND pg_get_constraintdef(con.oid) NOT ILIKE '%all_paid_services_ended_at%';

  SELECT con.conname INTO ended_at_constraint
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'ownership'
    AND rel.relname = 'pool_entry'
    AND con.contype = 'c'
    AND pg_get_constraintdef(con.oid) ILIKE '%all_paid_services_ended_at IS NOT NULL%';

  IF value_list_constraint IS NULL OR ended_at_constraint IS NULL OR value_list_constraint = ended_at_constraint THEN
    RAISE EXCEPTION
      'expected two distinct ownership.pool_entry reason_code CHECK constraints (value-list=%, ended_at=%); refusing to drop ambiguously',
      value_list_constraint, ended_at_constraint;
  END IF;

  EXECUTE format('ALTER TABLE ownership.pool_entry DROP CONSTRAINT %I', value_list_constraint);
  EXECUTE format('ALTER TABLE ownership.pool_entry DROP CONSTRAINT %I', ended_at_constraint);
END $$;

ALTER TABLE ownership.pool_entry ADD CONSTRAINT pool_entry_reason_code_check
  CHECK (reason_code IN (
    'staff_departure', 'all_paid_services_lapsed',       -- original baseline values
    'unassigned_web', 'employee_departure', 'paid_service_lapse', 'manual_release'  -- api/openapi.yaml PoolEntry.entry_reason
  ));

-- Both the legacy and the new lapse value require all_paid_services_ended_at;
-- the original constraint only named the legacy value, so a live write using
-- the newer 'paid_service_lapse' value was never checked for this at all.
ALTER TABLE ownership.pool_entry ADD CONSTRAINT pool_entry_lapse_requires_ended_at_check
  CHECK (reason_code NOT IN ('all_paid_services_lapsed', 'paid_service_lapse') OR all_paid_services_ended_at IS NOT NULL);

COMMENT ON COLUMN ownership.pool_entry.reason_code IS
  'unassigned_web/employee_departure/paid_service_lapse/manual_release (api/openapi.yaml PoolEntry.entry_reason); staff_departure/all_paid_services_lapsed kept for pre-existing rows.';

-- 2. ownership.guard_pool_entry(): the live DB guard (approved policy, elapsed
--    lapse_days window, no restriction/suspension, no overlapping paid
--    service — database/migrations/0003_business_guards.sql) only matched
--    the legacy 'all_paid_services_lapsed' reason_code. Widening the CHECK
--    above without widening this trigger would let every live
--    'paid_service_lapse' write (the value the application actually uses,
--    see apps/api/src/modules/ownership_pool/service.py::evaluate_company_pool)
--    bypass the guard entirely. The body is otherwise unchanged from
--    0003_business_guards.sql.
CREATE OR REPLACE FUNCTION ownership.guard_pool_entry() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a ownership.assignment%ROWTYPE; p ownership.policy_version%ROWTYPE;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || ':' || NEW.customer_id::text, 0));
  SELECT * INTO STRICT a FROM ownership.assignment WHERE tenant_id=NEW.tenant_id AND id=NEW.assignment_id;
  SELECT * INTO STRICT p FROM ownership.policy_version WHERE tenant_id=NEW.tenant_id AND id=a.policy_version_id;
  IF a.customer_id<>NEW.customer_id OR a.owner_type<>'company_pool' THEN
    RAISE EXCEPTION 'pool entry must reference company assignment' USING ERRCODE='23514';
  END IF;
  IF NEW.reason_code IN ('all_paid_services_lapsed', 'paid_service_lapse') THEN
    IF p.approval_status<>'approved' OR NEW.entered_at<NEW.all_paid_services_ended_at + make_interval(days=>p.lapse_days) THEN
      RAISE EXCEPTION 'pool lapse requires approved policy and elapsed complete interval' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM customer.customer WHERE tenant_id=NEW.tenant_id AND id=NEW.customer_id AND status='restricted') OR
       EXISTS (SELECT 1 FROM contract.contract WHERE tenant_id=NEW.tenant_id AND customer_id=NEW.customer_id AND status='suspended') THEN
      RAISE EXCEPTION 'restricted or suspended relationship is not ordinary sales lapse' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM contract.contract_service s JOIN contract.contract c ON (c.tenant_id,c.id)=(s.tenant_id,s.contract_id)
      WHERE c.tenant_id=NEW.tenant_id AND c.customer_id=NEW.customer_id AND c.status='effective'
        AND tstzrange(s.starts_at,s.ends_at,'[)') && tstzrange(NEW.all_paid_services_ended_at,NEW.entered_at,'[]')) THEN
      RAISE EXCEPTION 'any paid service during lapse prevents whole-customer Pool release' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- 3. ownership.assignment.end_state: how this interval ended, distinct from
--    reason_code (why it was created). Only meaningful once valid_until is set.
ALTER TABLE ownership.assignment ADD COLUMN IF NOT EXISTS end_state text;

-- Backfill before adding the NOT-NULL-when-closed CHECK below: any row with
-- valid_until already set predates this column and would otherwise violate
-- the new constraint immediately. There is no reliable historical signal to
-- recover the exact original end_state (reason_code records why the interval
-- was *opened*, not how the prior one closed), so every pre-existing closed
-- row backfills to 'released' — the same conservative fallback the
-- application already uses when no dedicated wire state exists (see
-- service.py's evaluate_company_pool comment). The data steward should
-- re-derive a more precise value from audit/outbox history before merging
-- this into the baseline if that evidence is available.
UPDATE ownership.assignment
SET end_state = 'released'
WHERE valid_until IS NOT NULL AND end_state IS NULL;

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
