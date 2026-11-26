-- M05 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev @ research.candidate / research.candidate_evidence from migrations/0001_domains.sql.
SET TIME ZONE 'UTC';

-- The baseline research.candidate row only carries the fields section 7 of the
-- module spec already lists (status/producer/external_candidate_id/external_version/
-- origin/persona_ref/pool_ref/artifact_ref/content_sha256/snapshot_asset_id/
-- source_occurred_at/received_at/lineage). The OpenAPI CandidateCreate/ResearchCandidate
-- contract additionally requires external_event_id, source_available_at,
-- review_case_id and validation_failure_codes (all required response fields), plus
-- ticket_ref/plan_ref/license_scope_ref/data_scope from the request and the
-- supersede/conflict bookkeeping REQ-M05-003/004/011 depend on.
ALTER TABLE research.candidate
  ADD COLUMN IF NOT EXISTS external_event_id uuid,
  ADD COLUMN IF NOT EXISTS source_available_at timestamptz,
  ADD COLUMN IF NOT EXISTS source_committed_at timestamptz,
  ADD COLUMN IF NOT EXISTS ticket_ref jsonb,
  ADD COLUMN IF NOT EXISTS plan_ref jsonb,
  ADD COLUMN IF NOT EXISTS license_scope_ref text,
  ADD COLUMN IF NOT EXISTS data_scope text NOT NULL DEFAULT 'research_only',
  ADD COLUMN IF NOT EXISTS review_case_id uuid,
  ADD COLUMN IF NOT EXISTS validation_failure_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS supersedes_candidate_id uuid,
  ADD COLUMN IF NOT EXISTS superseded_by_candidate_id uuid,
  ADD COLUMN IF NOT EXISTS customer_distribution_allowed boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS validated_snapshot_asset_id uuid,
  ADD COLUMN IF NOT EXISTS conflicting_receipts jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD CONSTRAINT m05_candidate_external_event_id_required
    CHECK (external_event_id IS NOT NULL) NOT VALID,
  ADD CONSTRAINT m05_candidate_source_available_at_required
    CHECK (source_available_at IS NOT NULL) NOT VALID,
  ADD CONSTRAINT m05_candidate_ticket_ref_required
    CHECK (ticket_ref IS NOT NULL AND jsonb_typeof(ticket_ref) = 'object') NOT VALID,
  ADD CONSTRAINT m05_candidate_license_scope_ref_required
    CHECK (license_scope_ref IS NOT NULL AND length(license_scope_ref) BETWEEN 1 AND 160) NOT VALID,
  ADD CONSTRAINT m05_candidate_data_scope_ck
    CHECK (data_scope = 'research_only') NOT VALID,
  ADD CONSTRAINT m05_candidate_validation_codes_array_ck
    CHECK (jsonb_typeof(validation_failure_codes) = 'array') NOT VALID,
  ADD CONSTRAINT m05_candidate_conflicting_receipts_array_ck
    CHECK (jsonb_typeof(conflicting_receipts) = 'array') NOT VALID,
  ADD CONSTRAINT m05_candidate_supersedes_ck
    CHECK (supersedes_candidate_id IS NULL OR supersedes_candidate_id <> id) NOT VALID;

ALTER TABLE research.candidate
  ADD FOREIGN KEY (tenant_id, supersedes_candidate_id) REFERENCES research.candidate (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE,
  ADD FOREIGN KEY (tenant_id, superseded_by_candidate_id) REFERENCES research.candidate (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE,
  ADD FOREIGN KEY (tenant_id, validated_snapshot_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;

-- UAT-M05-003: the same Pantheon source_event_id must resolve to the same candidate.
ALTER TABLE research.candidate
  ADD CONSTRAINT m05_candidate_external_event_unique UNIQUE (tenant_id, producer, external_event_id);

-- UAT-M05-011: exactly one non-superseded row may lead a (producer, external_candidate_id)
-- lineage at a time; a higher external_version supersedes the previous head in the same
-- transaction that inserts the new row, so this partial index never sees two heads.
CREATE UNIQUE INDEX IF NOT EXISTS ux_m05_candidate_lineage_head
  ON research.candidate (tenant_id, producer, external_candidate_id)
  WHERE status <> 'superseded';

COMMENT ON COLUMN research.candidate.external_event_id IS 'Pantheon來源事件ID；同ID重送回原candidate（UAT-M05-003）。';
COMMENT ON COLUMN research.candidate.source_available_at IS '研究於來源端實際可取得時間；不得晚於received_at代表的伺服器收件時間被回填。';
COMMENT ON COLUMN research.candidate.source_committed_at IS '報告提交／定稿時間；與source_occurred_at（交易/研究事件時間）分別保存，不互相覆寫（UAT-M05-007）。';
COMMENT ON COLUMN research.candidate.ticket_ref IS 'ExternalRef：{system,external_id,kind,version?,checksum_sha256?}。';
COMMENT ON COLUMN research.candidate.plan_ref IS 'ExternalRef，可為null。';
COMMENT ON COLUMN research.candidate.license_scope_ref IS '授權範圍引用；customer_distribution_allowed由此與驗證結果共同決定，預設不允許對客發布。';
COMMENT ON COLUMN research.candidate.data_scope IS '固定research_only；非正式對客報告。';
COMMENT ON COLUMN research.candidate.review_case_id IS '預先指定給M06 review.case_file的ID；M05從不寫該表，只保證ID一致。';
COMMENT ON COLUMN research.candidate.validation_failure_codes IS '累積的驗證旗標（阻斷與非阻斷皆含，例如customer_distribution_denied）。';
COMMENT ON COLUMN research.candidate.supersedes_candidate_id IS '指向此候選取代的上一版本（同一lineage）。';
COMMENT ON COLUMN research.candidate.superseded_by_candidate_id IS '指向取代此候選的新版本；set表示本列已是superseded歷史。';
COMMENT ON COLUMN research.candidate.customer_distribution_allowed IS '僅license_scope_ref核准且驗證確認時為true；預設false，不自動對客發布。';
COMMENT ON COLUMN research.candidate.validated_snapshot_asset_id IS '驗證時worker實際擷取快照之core.object_asset；與原始provenance snapshot_asset_id分別保存。';
COMMENT ON COLUMN research.candidate.conflicting_receipts IS '同版本不同hash的衝突提交摘要（UAT-M05-004）；不覆寫原列content_sha256。';

-- Core identity/content facts are immutable once written; only status, version,
-- validation bookkeeping and lineage pointers may change after insert.
CREATE OR REPLACE FUNCTION research.m05_guard_candidate_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.producer IS DISTINCT FROM NEW.producer
     OR OLD.external_candidate_id IS DISTINCT FROM NEW.external_candidate_id
     OR OLD.external_version IS DISTINCT FROM NEW.external_version
     OR OLD.external_event_id IS DISTINCT FROM NEW.external_event_id
     OR OLD.content_sha256 IS DISTINCT FROM NEW.content_sha256
     OR OLD.received_at IS DISTINCT FROM NEW.received_at
     OR OLD.source_occurred_at IS DISTINCT FROM NEW.source_occurred_at THEN
    RAISE EXCEPTION 'research candidate identity and content facts are immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS m05_guard_candidate_identity ON research.candidate;
CREATE TRIGGER m05_guard_candidate_identity
BEFORE UPDATE ON research.candidate
FOR EACH ROW EXECUTE FUNCTION research.m05_guard_candidate_identity();
