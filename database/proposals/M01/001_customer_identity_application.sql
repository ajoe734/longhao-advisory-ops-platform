-- M01 proposal only. The data steward assigns the shared migration number.
-- Baseline: main@4b9adeb09485386c04d6ce38d789e5058ebe2082 (PostgreSQL 16).
SET TIME ZONE 'UTC';

-- Wire ExternalIdentity has an explicit revocation projection.
ALTER TABLE customer.external_identity
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'verified',
  ADD COLUMN IF NOT EXISTS revoked_at timestamptz,
  ADD CONSTRAINT m01_external_identity_status_ck
    CHECK (status IN ('verified', 'revoked')) NOT VALID,
  ADD CONSTRAINT m01_external_identity_revocation_ck
    CHECK ((status = 'revoked') = (revoked_at IS NOT NULL)) NOT VALID;

-- Withdrawal and acquisition facts cannot be reconstructed from updated_at.
ALTER TABLE customer.application
  ADD COLUMN IF NOT EXISTS original_acquisition_ref text,
  ADD COLUMN IF NOT EXISTS withdrawn_at timestamptz,
  ADD COLUMN IF NOT EXISTS withdrawal_reason_code text,
  ADD CONSTRAINT m01_application_withdrawal_ck
    CHECK (
      (status = 'withdrawn' AND withdrawn_at IS NOT NULL AND withdrawal_reason_code IS NOT NULL)
      OR (status <> 'withdrawn' AND withdrawn_at IS NULL AND withdrawal_reason_code IS NULL)
    ) NOT VALID;

-- OpenAPI Consent requires the exact displayed document digest. This starts
-- nullable for a steward-controlled backfill of existing synthetic rows; new
-- M01 writes require it in application code.
ALTER TABLE customer.consent
  ADD COLUMN IF NOT EXISTS document_sha256 char(64),
  ADD CONSTRAINT m01_consent_document_sha_ck
    CHECK (document_sha256 IS NULL OR document_sha256 ~ '^[a-f0-9]{64}$') NOT VALID;

-- Verified contact lookup is deliberately non-unique: equal email/phone must
-- create a review candidate, never an automatic customer merge.
CREATE TABLE IF NOT EXISTS customer.contact_lookup (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  contact_type text NOT NULL,
  contact_hmac char(64) NOT NULL,
  verified_at timestamptz NOT NULL,
  proof_asset_id uuid NOT NULL,
  revoked_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer(tenant_id, id),
  FOREIGN KEY (tenant_id, proof_asset_id) REFERENCES core.object_asset(tenant_id, id),
  CHECK (version > 0),
  CHECK (contact_type IN ('email', 'phone')),
  CHECK (contact_hmac ~ '^[a-f0-9]{64}$'),
  UNIQUE (tenant_id, customer_id, contact_type, contact_hmac)
);
CREATE INDEX IF NOT EXISTS ix_m01_contact_lookup_hmac
  ON customer.contact_lookup (tenant_id, contact_type, contact_hmac)
  WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS customer.application_touch (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  application_id uuid NOT NULL,
  touch_kind text NOT NULL,
  source_ref text NOT NULL,
  occurred_at timestamptz NOT NULL,
  recorded_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application(tenant_id, id),
  FOREIGN KEY (tenant_id, recorded_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (touch_kind IN ('first_touch', 'subsequent_touch', 'transaction_attribution')),
  CHECK (length(source_ref) BETWEEN 1 AND 160),
  UNIQUE (tenant_id, application_id, touch_kind, source_ref, occurred_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_m01_application_first_touch
  ON customer.application_touch (tenant_id, application_id)
  WHERE touch_kind = 'first_touch';

-- OpenAPI accepts several evidence assets while the baseline row stores its
-- primary evidence. This immutable child preserves the complete reviewer set.
CREATE TABLE IF NOT EXISTS customer.merge_case_evidence (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  merge_case_id uuid NOT NULL,
  evidence_asset_id uuid NOT NULL,
  evidence_role text NOT NULL,
  added_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, merge_case_id) REFERENCES customer.merge_case(tenant_id, id),
  FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset(tenant_id, id),
  FOREIGN KEY (tenant_id, added_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (evidence_role IN ('proposal', 'review', 'legal_hold_check')),
  UNIQUE (tenant_id, merge_case_id, evidence_asset_id, evidence_role)
);

-- The source customer row is retained, but an immutable alias makes explicit
-- which approved merge governs lookups from historical payment/report IDs.
CREATE TABLE IF NOT EXISTS customer.customer_alias (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  alias_customer_id uuid NOT NULL,
  surviving_customer_id uuid NOT NULL,
  merge_case_id uuid NOT NULL,
  reviewer_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, alias_customer_id) REFERENCES customer.customer(tenant_id, id),
  FOREIGN KEY (tenant_id, surviving_customer_id) REFERENCES customer.customer(tenant_id, id),
  FOREIGN KEY (tenant_id, merge_case_id) REFERENCES customer.merge_case(tenant_id, id),
  FOREIGN KEY (tenant_id, reviewer_id) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (alias_customer_id <> surviving_customer_id),
  UNIQUE (tenant_id, alias_customer_id),
  UNIQUE (tenant_id, merge_case_id)
);

CREATE TABLE IF NOT EXISTS customer.identity_automation_run (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  capability text NOT NULL,
  model_version text NOT NULL,
  prompt_version text NOT NULL,
  ruleset_version text NOT NULL,
  source_refs jsonb NOT NULL,
  input_sha256 char(64) NOT NULL,
  output_draft jsonb NOT NULL,
  status text NOT NULL DEFAULT 'untrusted_draft',
  requested_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, requested_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (input_sha256 ~ '^[a-f0-9]{64}$'),
  CHECK (jsonb_typeof(source_refs) = 'array'),
  CHECK (jsonb_typeof(output_draft) = 'array'),
  CHECK (status IN ('untrusted_draft', 'reviewed'))
);

CREATE TABLE IF NOT EXISTS customer.identity_automation_review (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  run_id uuid NOT NULL,
  reviewed_by uuid NOT NULL,
  action text NOT NULL,
  reason_code text NOT NULL,
  selected_customer_ids jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES customer.identity_automation_run(tenant_id, id),
  FOREIGN KEY (tenant_id, reviewed_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (action IN ('adopted', 'modified', 'rejected')),
  CHECK (jsonb_typeof(selected_customer_ids) = 'array')
);

CREATE OR REPLACE FUNCTION customer.m01_guard_identity_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.customer_id IS DISTINCT FROM NEW.customer_id
     OR OLD.provider IS DISTINCT FROM NEW.provider
     OR OLD.subject_hmac IS DISTINCT FROM NEW.subject_hmac
     OR OLD.verified_at IS DISTINCT FROM NEW.verified_at THEN
    RAISE EXCEPTION 'verified identity history is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS m01_guard_identity_history ON customer.external_identity;
CREATE TRIGGER m01_guard_identity_history
BEFORE UPDATE ON customer.external_identity
FOR EACH ROW EXECUTE FUNCTION customer.m01_guard_identity_history();

CREATE OR REPLACE FUNCTION customer.m01_guard_application_origin()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.original_acquisition_ref IS DISTINCT FROM NEW.original_acquisition_ref
     AND OLD.original_acquisition_ref IS NOT NULL THEN
    RAISE EXCEPTION 'original acquisition source is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS m01_guard_application_origin ON customer.application;
CREATE TRIGGER m01_guard_application_origin
BEFORE UPDATE ON customer.application
FOR EACH ROW EXECUTE FUNCTION customer.m01_guard_application_origin();

DO $m01_rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['contact_lookup', 'application_touch', 'merge_case_evidence', 'customer_alias', 'identity_automation_run', 'identity_automation_review']
  LOOP
    EXECUTE format('ALTER TABLE customer.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE customer.%I FORCE ROW LEVEL SECURITY', table_name);
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies WHERE schemaname = 'customer' AND tablename = table_name AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON customer.%I USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id())',
        table_name
      );
    END IF;
  END LOOP;
END
$m01_rls$;

DROP TRIGGER IF EXISTS guard_row ON customer.contact_lookup;
CREATE TRIGGER guard_row BEFORE UPDATE ON customer.contact_lookup
FOR EACH ROW EXECUTE FUNCTION core.touch_version();
DROP TRIGGER IF EXISTS guard_row ON customer.application_touch;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON customer.application_touch
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_row ON customer.merge_case_evidence;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON customer.merge_case_evidence
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_row ON customer.customer_alias;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON customer.customer_alias
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_row ON customer.identity_automation_run;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON customer.identity_automation_run
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_row ON customer.identity_automation_review;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON customer.identity_automation_review
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();

COMMENT ON TABLE customer.contact_lookup IS 'M01 verified contact keyed-HMAC lookup; duplicates are review candidates, not auto-merges.';
COMMENT ON TABLE customer.application_touch IS 'M01 immutable first/subsequent/transaction acquisition facts.';
COMMENT ON TABLE customer.merge_case_evidence IS 'M01 complete immutable evidence set for merge proposal and checker review.';
COMMENT ON TABLE customer.customer_alias IS 'M01 approved surviving-customer alias preserving historical IDs.';
COMMENT ON TABLE customer.identity_automation_run IS 'M01 minimized untrusted duplicate-candidate automation with versioned provenance.';
COMMENT ON TABLE customer.identity_automation_review IS 'M01 immutable human adoption, modification or rejection evidence for automation drafts.';
