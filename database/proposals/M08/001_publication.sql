-- M08 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@31fe1c7 (PostgreSQL 16), tables created in database/migrations/0001_domains.sql.
SET TIME ZONE 'UTC';

-- Wire RecommendationSeriesCreate accepts a research_origin_ref (ExternalRef)
-- that the baseline row does not store; it is write-once provenance, never
-- surfaced on RecommendationSeries (OpenAPI response omits it on purpose).
ALTER TABLE publication.recommendation_series
  ADD COLUMN IF NOT EXISTS research_origin_ref jsonb,
  ADD CONSTRAINT m08_series_origin_ref_ck
    CHECK (research_origin_ref IS NULL OR jsonb_typeof(research_origin_ref) = 'object') NOT VALID;
COMMENT ON COLUMN publication.recommendation_series.research_origin_ref IS 'Write-once Pantheon/Agora research provenance; not part of the public wire response.';

-- Wire RecommendationVersionCreate.action includes "hold"; the baseline
-- check only allowed entry/update/exit/correction. The baseline constraint
-- is unnamed (auto-generated name), so it is located by definition rather
-- than a guessed name before being replaced.
DO $m08_action_type$
DECLARE
  old_constraint text;
BEGIN
  SELECT conname INTO old_constraint
  FROM pg_constraint
  WHERE conrelid = 'publication.recommendation_version'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%action_type%hold%' IS FALSE
    AND pg_get_constraintdef(oid) ILIKE '%action_type%'
  LIMIT 1;
  IF old_constraint IS NOT NULL THEN
    EXECUTE format('ALTER TABLE publication.recommendation_version DROP CONSTRAINT %I', old_constraint);
  END IF;
END
$m08_action_type$;
ALTER TABLE publication.recommendation_version
  ADD CONSTRAINT m08_recommendation_version_action_type_ck
    CHECK (action_type IN ('entry', 'update', 'exit', 'hold', 'correction'));

-- The wire response returns a rationale_asset_id (never raw text) and a
-- source_candidate_id tying the message back to its Pantheon/Agora origin;
-- neither existed on the baseline row.
ALTER TABLE publication.recommendation_version
  ADD COLUMN IF NOT EXISTS rationale_asset_id uuid,
  ADD COLUMN IF NOT EXISTS source_candidate_id uuid,
  ADD CONSTRAINT m08_recommendation_version_rationale_fk
    FOREIGN KEY (tenant_id, rationale_asset_id) REFERENCES core.object_asset(tenant_id, id) NOT VALID;
COMMENT ON COLUMN publication.recommendation_version.rationale_asset_id IS 'Immutable reviewed rationale object; never the raw text column.';
COMMENT ON COLUMN publication.recommendation_version.source_candidate_id IS 'Pantheon/Agora source candidate reference; separates research origin time from publication time (REQ-M08-012). No cross-module FK: M05 owns that table.';

-- OpenAPI's supporting_report_version_ids is an array (1-20); the baseline
-- row only has a single report_version_id FK. This additive child table
-- carries any report beyond the first (e.g. a correction citing the
-- original report plus an addendum) without widening the parent row.
-- report_version_id on the parent remains the first/primary support.
CREATE TABLE IF NOT EXISTS publication.recommendation_version_support (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  recommendation_version_id uuid NOT NULL,
  report_version_id uuid NOT NULL,
  position integer NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, recommendation_version_id) REFERENCES publication.recommendation_version(tenant_id, id),
  FOREIGN KEY (tenant_id, report_version_id) REFERENCES report.report_version(tenant_id, id),
  CHECK (version > 0),
  CHECK (position >= 1),
  UNIQUE (tenant_id, recommendation_version_id, report_version_id),
  UNIQUE (tenant_id, recommendation_version_id, position)
);
COMMENT ON TABLE publication.recommendation_version_support IS 'Additional supporting report versions beyond the primary report_version_id FK (REQ-M08-003/004).';

-- Signature verification is the only publish trigger; the publication row
-- needs its own audience_snapshot and the producing M07 event recorded so a
-- duplicate signature event can be proven to have published exactly once
-- (REQ-M08-008), and a denormalized recipient_count for cheap list reads.
ALTER TABLE publication.publication
  ADD COLUMN IF NOT EXISTS audience_snapshot_id uuid,
  ADD COLUMN IF NOT EXISTS created_from_signature_event_id uuid,
  ADD COLUMN IF NOT EXISTS recipient_count integer NOT NULL DEFAULT 0,
  ADD CONSTRAINT m08_publication_audience_snapshot_fk
    FOREIGN KEY (tenant_id, audience_snapshot_id) REFERENCES publication.audience_snapshot(tenant_id, id) NOT VALID,
  ADD CONSTRAINT m08_publication_recipient_count_ck
    CHECK (recipient_count >= 0);
COMMENT ON COLUMN publication.publication.audience_snapshot_id IS 'Set once the snapshot is sealed in the same publish transaction; never recomputed afterwards.';
COMMENT ON COLUMN publication.publication.created_from_signature_event_id IS 'M07 report.signature_verified event_id that produced this publication; proves single-publish-per-event.';
COMMENT ON COLUMN publication.publication.recipient_count IS 'Denormalized copy of audience_snapshot.recipient_count for list reads; the snapshot row remains the source of truth.';

DO $m08_rls$
BEGIN
  ALTER TABLE publication.recommendation_version_support ENABLE ROW LEVEL SECURITY;
  ALTER TABLE publication.recommendation_version_support FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'publication' AND tablename = 'recommendation_version_support' AND policyname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON publication.recommendation_version_support
      USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id());
  END IF;
END
$m08_rls$;

DROP TRIGGER IF EXISTS guard_row ON publication.recommendation_version_support;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON publication.recommendation_version_support
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
