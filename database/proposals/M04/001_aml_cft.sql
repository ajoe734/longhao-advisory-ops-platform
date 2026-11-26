-- M04 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev branch shared platform migrations 0001-0004 (PostgreSQL 16).
SET TIME ZONE 'UTC';

-- OpenAPI AmlCase.risk_level / AmlDecisionRequest.risk_level publish
-- low/standard/high; the baseline CHECK still has low/medium/high/undetermined.
-- OpenAPI is the wire contract authority (see ../../../docs/modules/M04-aml_cft.md
-- section 6); this corrects the baseline instead of inventing a module-local
-- enum. The old baseline CHECK must be dropped before the backfill UPDATE:
-- its own unnamed-constraint auto-name is `case_file_risk_level_check` (the
-- bare table name, not schema-qualified as `aml_case_file_risk_level_check`),
-- and it does not allow 'standard' yet, so backfilling into it first would
-- itself violate the constraint it is meant to replace.
ALTER TABLE aml.case_file
  DROP CONSTRAINT IF EXISTS case_file_risk_level_check;
UPDATE aml.case_file SET risk_level = 'standard' WHERE risk_level IN ('medium', 'undetermined');
ALTER TABLE aml.case_file
  ADD CONSTRAINT m04_case_file_risk_level_ck
    CHECK (risk_level IN ('low', 'standard', 'high')) NOT VALID;

-- Provider-adapter dispatch state the OpenAPI ScreeningRequest/AmlScreening
-- contract needs but the baseline row does not yet carry.
ALTER TABLE aml.screening
  ADD COLUMN IF NOT EXISTS scope jsonb,
  ADD COLUMN IF NOT EXISTS reason_code text,
  ADD COLUMN IF NOT EXISTS manual_required boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT m04_screening_scope_ck
    CHECK (scope IS NULL OR jsonb_typeof(scope) = 'array') NOT VALID;

-- start_aml_screening creates the screening row before the provider has
-- accepted dispatch (provider/provider_request_id/list_version are not yet
-- known), but the baseline schema requires list_version NOT NULL and a
-- UNIQUE(tenant_id, provider, provider_request_id) that would collide across
-- concurrently pending rows (same tenant, provider='', provider_request_id='').
-- Relax list_version for the pending window and replace the baseline unique
-- constraint with a partial index that only applies once a real provider
-- request id has been assigned.
ALTER TABLE aml.screening ALTER COLUMN list_version DROP NOT NULL;
ALTER TABLE aml.screening DROP CONSTRAINT IF EXISTS screening_tenant_id_provider_provider_request_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS m04_screening_provider_request_uidx
  ON aml.screening (tenant_id, provider, provider_request_id)
  WHERE provider_request_id <> '';

-- OpenAPI AmlCase.screening_ids is an array (one case can accumulate more than
-- one possible/confirmed match); the baseline case_file keeps a single
-- screening_id FK as the triggering screening. This link table is the
-- additive, backward-compatible way to represent the full set without
-- dropping the baseline column.
CREATE TABLE IF NOT EXISTS aml.case_screening_link (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  screening_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES aml.case_file(tenant_id, id),
  FOREIGN KEY (tenant_id, screening_id) REFERENCES aml.screening(tenant_id, id),
  CHECK (version > 0),
  UNIQUE (tenant_id, case_id, screening_id)
);

-- OpenAPI AmlCase.restricted_evidence_ids is an array; the baseline schema has
-- no case-level evidence set (only a per-case_action evidence_asset_id).
CREATE TABLE IF NOT EXISTS aml.case_evidence_link (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  evidence_asset_id uuid NOT NULL,
  added_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES aml.case_file(tenant_id, id),
  FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset(tenant_id, id),
  FOREIGN KEY (tenant_id, added_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  UNIQUE (tenant_id, case_id, evidence_asset_id)
);

-- submit_aml_declaration has no backing table in the baseline catalog; the
-- declaration itself is customer evidence, not a clearance decision (see
-- UAT-M04-007). One declaration per application; resubmission (e.g. after
-- requires_information) is a version bump, not a new row.
CREATE TABLE IF NOT EXISTS aml.declaration_receipt (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  application_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  form_version text NOT NULL,
  declaration_consent_id uuid NOT NULL,
  restricted_answer_asset_id uuid NOT NULL,
  restricted_document_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  customer_signature_id uuid NOT NULL,
  received_at timestamptz NOT NULL,
  state text NOT NULL DEFAULT 'received',
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application(tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer(tenant_id, id),
  FOREIGN KEY (tenant_id, declaration_consent_id) REFERENCES customer.consent(tenant_id, id),
  FOREIGN KEY (tenant_id, restricted_answer_asset_id) REFERENCES core.object_asset(tenant_id, id),
  FOREIGN KEY (tenant_id, customer_signature_id) REFERENCES contract.customer_signature(tenant_id, id),
  CHECK (version > 0),
  CHECK (state IN ('received', 'requires_information')),
  CHECK (jsonb_typeof(restricted_document_ids) = 'array'),
  UNIQUE (tenant_id, application_id)
);

-- Internal audit access to a specific restricted case is always a scoped,
-- time-boxed grant (REQ-M04-010); it is never derived from aml_officer or
-- aml_supervisor role membership. No public operationId exists for this yet
-- (see contract-changes.md); the application service only exposes it as an
-- internal capability pending shared API registration.
CREATE TABLE IF NOT EXISTS aml.case_read_grant (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  auditor_actor_id uuid NOT NULL,
  granted_by uuid NOT NULL,
  reason_code text NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES aml.case_file(tenant_id, id),
  FOREIGN KEY (tenant_id, auditor_actor_id) REFERENCES core.actor(tenant_id, id),
  FOREIGN KEY (tenant_id, granted_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (length(reason_code) BETWEEN 1 AND 80),
  CHECK (expires_at > created_at)
);

DO $m04_rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['case_screening_link', 'case_evidence_link', 'declaration_receipt', 'case_read_grant']
  LOOP
    EXECUTE format('ALTER TABLE aml.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE aml.%I FORCE ROW LEVEL SECURITY', table_name);
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies WHERE schemaname = 'aml' AND tablename = table_name AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON aml.%I USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id())',
        table_name
      );
    END IF;
  END LOOP;
END
$m04_rls$;

DROP TRIGGER IF EXISTS guard_row ON aml.case_screening_link;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON aml.case_screening_link
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_row ON aml.case_evidence_link;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON aml.case_evidence_link
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_row ON aml.declaration_receipt;
CREATE TRIGGER guard_row BEFORE UPDATE ON aml.declaration_receipt
FOR EACH ROW EXECUTE FUNCTION core.touch_version();
DROP TRIGGER IF EXISTS guard_row ON aml.case_read_grant;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON aml.case_read_grant
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();

COMMENT ON TABLE aml.case_screening_link IS 'M04 full screening_ids set for a case; aml.case_file.screening_id keeps the baseline single triggering screening.';
COMMENT ON TABLE aml.case_evidence_link IS 'M04 case-level restricted evidence set backing AmlCase.restricted_evidence_ids.';
COMMENT ON TABLE aml.declaration_receipt IS 'M04 customer/staff AML declaration evidence; never itself a business_clearance decision (UAT-M04-007).';
COMMENT ON TABLE aml.case_read_grant IS 'M04 scoped, time-boxed internal-audit read grant for a single restricted case; distinct from aml_officer/aml_supervisor role access.';
