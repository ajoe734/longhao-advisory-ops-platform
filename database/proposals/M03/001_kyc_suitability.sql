-- M03 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@48ae302 (PostgreSQL 16).
SET TIME ZONE 'UTC';

-- The baseline kyc.submission_document table only tracks the human review
-- outcome (verification_state). The isolation-zone malware/MIME scan is a
-- separate, earlier gate: a document must never be readable or review-eligible
-- before scanning completes, and the scan outcome must never be inferred from
-- verification_state. These columns are additive and start NULL/pending so
-- existing synthetic rows remain valid.
ALTER TABLE kyc.submission_document
  ADD COLUMN IF NOT EXISTS scan_status text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS scan_rejection_code text,
  ADD COLUMN IF NOT EXISTS scanned_at timestamptz,
  ADD COLUMN IF NOT EXISTS classification text NOT NULL DEFAULT 'customer_confidential',
  ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS lease_owner text,
  ADD COLUMN IF NOT EXISTS lease_until timestamptz,
  ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_error_code text,
  ADD CONSTRAINT m03_submission_document_scan_status_ck
    CHECK (scan_status IN ('pending', 'clean', 'rejected')) NOT VALID,
  ADD CONSTRAINT m03_submission_document_scan_rejection_ck
    CHECK (
      (scan_status = 'rejected') = (scan_rejection_code IS NOT NULL)
      AND (scan_rejection_code IS NULL OR scan_rejection_code IN ('malware', 'mime_mismatch', 'oversized', 'invalid_format'))
    ) NOT VALID,
  ADD CONSTRAINT m03_submission_document_scanned_at_ck
    CHECK ((scan_status = 'pending') = (scanned_at IS NULL)) NOT VALID,
  ADD CONSTRAINT m03_submission_document_classification_ck
    CHECK (classification IN ('customer_confidential', 'aml_restricted', 'research_confidential', 'internal')) NOT VALID,
  ADD CONSTRAINT m03_submission_document_attempt_count_ck
    CHECK (attempt_count >= 0) NOT VALID,
  -- The human review decision can never move forward on an unscanned or
  -- rejected document; it must stay "pending" review until scanning clears it.
  ADD CONSTRAINT m03_submission_document_review_requires_clean_ck
    CHECK (verification_state = 'pending' OR scan_status = 'clean') NOT VALID;

-- Maker-checker for decide_kyc: the staff actor who submitted on a
-- customer's behalf (self_or_authorized_staff) can never also be the
-- actor who decides it; see m03.decide_kyc's maker_checker_violation gate.
ALTER TABLE kyc.submission
  ADD COLUMN IF NOT EXISTS submitted_by uuid,
  ADD CONSTRAINT m03_submission_submitted_by_fk
    FOREIGN KEY (tenant_id, submitted_by) REFERENCES core.actor(tenant_id, id) NOT VALID;

-- OCR is advisory only: it never overwrites kyc.submission.answers_ciphertext.
-- This immutable child records the untrusted draft, confidence and whether it
-- conflicts with the customer's own answer, plus full automation provenance.
CREATE TABLE IF NOT EXISTS kyc.document_extraction (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  submission_id uuid NOT NULL,
  document_id uuid NOT NULL,
  question_id text NOT NULL,
  ocr_value text NOT NULL,
  confidence numeric(7, 6) NOT NULL,
  conflicts_with_customer_answer boolean NOT NULL,
  model_version text NOT NULL,
  prompt_version text NOT NULL,
  ruleset_version text NOT NULL,
  input_sha256 char(64) NOT NULL,
  requested_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES kyc.submission(tenant_id, id),
  FOREIGN KEY (tenant_id, document_id) REFERENCES kyc.submission_document(tenant_id, id),
  FOREIGN KEY (tenant_id, requested_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (confidence >= 0 AND confidence <= 1),
  CHECK (input_sha256 ~ '^[a-f0-9]{64}$')
);

CREATE TABLE IF NOT EXISTS kyc.document_extraction_review (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  extraction_id uuid NOT NULL,
  reviewed_by uuid NOT NULL,
  action text NOT NULL,
  note text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, extraction_id) REFERENCES kyc.document_extraction(tenant_id, id),
  FOREIGN KEY (tenant_id, reviewed_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (action IN ('confirmed_customer_answer', 'adopted_ocr_value', 'escalated')),
  CHECK (length(note) BETWEEN 1 AND 300)
);

-- suitability_assessment.assessed_by already records who finalized the
-- decision, but it cannot distinguish "confirmed the deterministic rule
-- result" from "overrode it". This immutable child is the independent
-- maker-checker evidence UAT-M03-010 requires: the rule's own computed
-- decision plus who authorized the exception and why.
CREATE TABLE IF NOT EXISTS kyc.suitability_override (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  assessment_id uuid NOT NULL,
  authorizer_id uuid NOT NULL,
  computed_decision text NOT NULL,
  resulting_decision text NOT NULL,
  reason_code text NOT NULL,
  decided_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, assessment_id) REFERENCES kyc.suitability_assessment(tenant_id, id),
  FOREIGN KEY (tenant_id, authorizer_id) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (computed_decision IN ('suitable', 'conditional', 'not_suitable')),
  CHECK (resulting_decision IN ('suitable', 'conditional', 'not_suitable')),
  CHECK (computed_decision <> resulting_decision),
  CHECK (length(reason_code) BETWEEN 1 AND 80)
);

-- Continuous KYC due-diligence tracking. "approved -> refresh_required" on
-- kyc.submission is the business-visible transition; this case is the
-- scheduling/exception-queue record behind it (UAT-M03-009, UAT-M03-011).
CREATE TABLE IF NOT EXISTS kyc.refresh_case (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'pending',
  customer_id uuid NOT NULL,
  submission_id uuid NOT NULL,
  trigger_code text NOT NULL,
  reason_code text NOT NULL,
  due_at timestamptz NOT NULL,
  next_form_version text NOT NULL,
  requested_by uuid NOT NULL,
  attempt_count integer NOT NULL DEFAULT 0,
  lease_owner text,
  lease_until timestamptz,
  next_attempt_at timestamptz,
  last_error_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer(tenant_id, id),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES kyc.submission(tenant_id, id),
  FOREIGN KEY (tenant_id, requested_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (status IN ('pending', 'manual_required', 'completed')),
  CHECK (trigger_code IN ('scheduled', 'identity_change', 'risk_change', 'service_change', 'regulatory_request')),
  CHECK (attempt_count >= 0)
);
CREATE INDEX IF NOT EXISTS ix_m03_refresh_case_pending
  ON kyc.refresh_case (tenant_id, next_attempt_at)
  WHERE status = 'pending';

-- kyc.submission_document already has a guard_row BEFORE UPDATE trigger from
-- database/migrations/0002_isolation_indexes.sql; creating a second trigger
-- of the same name on the same table fails on PostgreSQL, so this proposal
-- does not redeclare it.
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON kyc.document_extraction
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON kyc.document_extraction_review
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON kyc.suitability_override
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
CREATE TRIGGER guard_row BEFORE UPDATE ON kyc.refresh_case
FOR EACH ROW EXECUTE FUNCTION core.touch_version();

DO $m03_rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['document_extraction', 'document_extraction_review', 'suitability_override', 'refresh_case']
  LOOP
    EXECUTE format('ALTER TABLE kyc.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE kyc.%I FORCE ROW LEVEL SECURITY', table_name);
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies WHERE schemaname = 'kyc' AND tablename = table_name AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON kyc.%I USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id())',
        table_name
      );
    END IF;
  END LOOP;
END
$m03_rls$;

COMMENT ON COLUMN kyc.submission_document.scan_status IS 'M03 隔離區防毒/MIME掃描結果：pending/clean/rejected，與人工verification_state分離。';
COMMENT ON COLUMN kyc.submission_document.scan_rejection_code IS 'M03 掃描拒絕安全代碼；不含可外洩細節。';
COMMENT ON COLUMN kyc.submission_document.scanned_at IS 'M03 掃描完成時間。';
COMMENT ON COLUMN kyc.submission_document.classification IS 'M03 物件資料分級，供下載授權與稽核使用。';
COMMENT ON COLUMN kyc.submission_document.attempt_count IS 'M03 DocumentScanWorker 掃描重試次數；僅計失敗，成功即清零。';
COMMENT ON COLUMN kyc.submission_document.lease_owner IS 'M03 目前持有掃描 lease 的 worker_id；完成或失敗後清空。';
COMMENT ON COLUMN kyc.submission_document.lease_until IS 'M03 掃描 lease 到期時間，過期才可被其他 worker 重新取用。';
COMMENT ON COLUMN kyc.submission_document.next_attempt_at IS 'M03 下次可重試掃描時間（指數後退）。';
COMMENT ON COLUMN kyc.submission_document.last_error_code IS 'M03 最近一次掃描失敗的安全代碼；不含可外洩細節。';
COMMENT ON COLUMN kyc.submission.submitted_by IS 'M03 代客戶送件的 staff actor；NULL 表示客戶本人送件。decide_kyc 的 maker-checker 檢查依此欄位拒絕同一 actor 自審。';
COMMENT ON TABLE kyc.document_extraction IS 'M03 OCR欄位抽取未信任草稿，保留model/prompt/ruleset版本與是否與客戶原答衝突；無合併/命令權。';
COMMENT ON TABLE kyc.document_extraction_review IS 'M03 審核者對OCR草稿的人工決定（確認/採用/升級），不可變。';
COMMENT ON TABLE kyc.suitability_override IS 'M03 適合度規則覆核例外：規則計算值、核定人、理由；maker-checker獨立證據。';
COMMENT ON TABLE kyc.refresh_case IS 'M03 持續KYC到期/風險/聯絡資料變更觸發之重評排程與exception queue。';
