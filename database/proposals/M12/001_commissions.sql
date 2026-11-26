-- M12 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@48ae302308de64159ee25ed506fb6f2e548cf886 (PostgreSQL 16).
-- Evidence source: apps/api/src/modules/commissions/{domain.py,service.py} (M12 implementation).
SET TIME ZONE 'UTC';

-- ---------------------------------------------------------------------------
-- commission.policy_version: the implemented CommissionPolicy carries several
-- versioned/approval-boundary fields the baseline table does not have yet.
-- All additions join the existing immutable_commission_terms protection
-- (see the trigger argument change below) except approval fields.
-- ---------------------------------------------------------------------------
ALTER TABLE commission.policy_version
  ADD COLUMN IF NOT EXISTS effective_until timestamptz,
  ADD COLUMN IF NOT EXISTS age_anchor text NOT NULL DEFAULT 'unresolved',
  ADD COLUMN IF NOT EXISTS year3_assisted_marketing_rule text NOT NULL DEFAULT 'unresolved',
  ADD COLUMN IF NOT EXISTS pool_policy_version text,
  ADD COLUMN IF NOT EXISTS recognition_policy_version text,
  ADD COLUMN IF NOT EXISTS rounding_mode text NOT NULL DEFAULT 'ROUND_HALF_UP',
  ADD COLUMN IF NOT EXISTS rounding_scale smallint NOT NULL DEFAULT 2,
  ADD COLUMN IF NOT EXISTS full_policy_asset_id uuid,
  ADD COLUMN IF NOT EXISTS notes text NOT NULL DEFAULT '',
  ADD CONSTRAINT m12_policy_version_effective_until_ck
    CHECK (effective_until IS NULL OR effective_until > effective_from) NOT VALID,
  ADD CONSTRAINT m12_policy_version_age_anchor_ck
    CHECK (age_anchor IN ('unresolved', 'first_paid_effective_start')) NOT VALID,
  ADD CONSTRAINT m12_policy_version_year3_rule_ck
    CHECK (year3_assisted_marketing_rule IN ('unresolved', 'zero_unless_continuous_auto', 'approved_custom_rule')) NOT VALID,
  ADD CONSTRAINT m12_policy_version_rounding_mode_ck
    CHECK (rounding_mode IN ('ROUND_HALF_UP', 'ROUND_HALF_EVEN')) NOT VALID,
  ADD CONSTRAINT m12_policy_version_rounding_scale_ck
    CHECK (rounding_scale BETWEEN 0 AND 6) NOT VALID,
  ADD CONSTRAINT m12_policy_version_notes_len_ck
    CHECK (length(notes) <= 4000) NOT VALID;
-- New draft/proposal writes must always set pool_policy_version,
-- recognition_policy_version and full_policy_asset_id (create_commission_policy
-- requires all three); existing synthetic rows start NULL until the steward
-- backfills them, then this becomes NOT NULL.
ALTER TABLE commission.policy_version
  ADD CONSTRAINT m12_policy_version_pool_version_len_ck
    CHECK (pool_policy_version IS NULL OR length(pool_policy_version) BETWEEN 1 AND 80) NOT VALID,
  ADD CONSTRAINT m12_policy_version_recognition_version_len_ck
    CHECK (recognition_policy_version IS NULL OR length(recognition_policy_version) BETWEEN 1 AND 80) NOT VALID;
ALTER TABLE commission.policy_version
  ADD FOREIGN KEY (tenant_id, full_policy_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
CREATE INDEX IF NOT EXISTS ix_commission_policy_version_full_policy_asset_id
  ON commission.policy_version (tenant_id, full_policy_asset_id);

COMMENT ON COLUMN commission.policy_version.effective_until IS '政策失效時間；null代表尚未訂定迄日。';
COMMENT ON COLUMN commission.policy_version.age_anchor IS 'unresolved/first_paid_effective_start；只擋live，不擋核准。';
COMMENT ON COLUMN commission.policy_version.year3_assisted_marketing_rule IS 'unresolved/zero_unless_continuous_auto/approved_custom_rule；只擋live，不擋核准。';
COMMENT ON COLUMN commission.policy_version.pool_policy_version IS '獎金池率決策來源版本標記。';
COMMENT ON COLUMN commission.policy_version.recognition_policy_version IS '收入認列政策來源版本標記。';
COMMENT ON COLUMN commission.policy_version.rounding_mode IS 'ROUND_HALF_UP/ROUND_HALF_EVEN。';
COMMENT ON COLUMN commission.policy_version.rounding_scale IS '結算四捨五入小數位，0到6。';
COMMENT ON COLUMN commission.policy_version.full_policy_asset_id IS '指向 core.object_asset 的完整政策文件版本。';
COMMENT ON COLUMN commission.policy_version.notes IS '提案備註，非業務規則來源。';

-- ---------------------------------------------------------------------------
-- commission.calculation_run: reproducibility and failure evidence fields the
-- implementation writes but the baseline table cannot store.
-- ---------------------------------------------------------------------------
ALTER TABLE commission.calculation_run
  ADD COLUMN IF NOT EXISTS policy_sha256 char(64),
  ADD COLUMN IF NOT EXISTS failure_code text,
  ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT 'TWD',
  ADD CONSTRAINT m12_calculation_run_policy_sha_ck
    CHECK (policy_sha256 IS NULL OR policy_sha256 ~ '^[a-f0-9]{64}$') NOT VALID,
  ADD CONSTRAINT m12_calculation_run_failure_ck
    CHECK (status <> 'failed' OR failure_code IS NOT NULL) NOT VALID;

COMMENT ON COLUMN commission.calculation_run.policy_sha256 IS '核算當下使用的政策版本內容hash，供重算與稽核比對。';
COMMENT ON COLUMN commission.calculation_run.failure_code IS '計算失敗原因碼；僅 status=failed 時填寫。';
COMMENT ON COLUMN commission.calculation_run.currency IS '本批次幣別；由收入事實決定，不是使用者自選。';

-- ---------------------------------------------------------------------------
-- commission.adjustment: original recipient + exceeds-award evidence the
-- implementation keeps to justify recovery_method and payroll_offset_allowed.
-- ---------------------------------------------------------------------------
ALTER TABLE commission.adjustment
  ADD COLUMN IF NOT EXISTS original_recipient_id uuid,
  ADD COLUMN IF NOT EXISTS exceeds_original_award boolean NOT NULL DEFAULT false;
ALTER TABLE commission.adjustment
  ADD FOREIGN KEY (tenant_id, original_recipient_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
CREATE INDEX IF NOT EXISTS ix_commission_adjustment_original_recipient_id
  ON commission.adjustment (tenant_id, original_recipient_id);

COMMENT ON COLUMN commission.adjustment.original_recipient_id IS '原entry受款人；退款沖回只給此人，不轉嫁新接手人（REQ-M12-007）。';
COMMENT ON COLUMN commission.adjustment.exceeds_original_award IS '調整金額是否超過原entry award_amount，用於判斷recovery_method。';

-- ---------------------------------------------------------------------------
-- commission.payout_batch: assembly/approval/acknowledgement evidence fields
-- the implementation writes (payroll_mapping_version, control_sha256,
-- export_asset_id). run_ids has no column; see the new junction table below.
-- ---------------------------------------------------------------------------
ALTER TABLE commission.payout_batch
  ADD COLUMN IF NOT EXISTS payroll_mapping_version text,
  ADD COLUMN IF NOT EXISTS control_sha256 char(64),
  ADD COLUMN IF NOT EXISTS export_asset_id uuid,
  ADD CONSTRAINT m12_payout_batch_mapping_version_len_ck
    CHECK (payroll_mapping_version IS NULL OR length(payroll_mapping_version) BETWEEN 1 AND 80) NOT VALID,
  ADD CONSTRAINT m12_payout_batch_control_sha_ck
    CHECK (control_sha256 IS NULL OR control_sha256 ~ '^[a-f0-9]{64}$') NOT VALID;
ALTER TABLE commission.payout_batch
  ADD FOREIGN KEY (tenant_id, export_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
CREATE INDEX IF NOT EXISTS ix_commission_payout_batch_export_asset_id
  ON commission.payout_batch (tenant_id, export_asset_id);

COMMENT ON COLUMN commission.payout_batch.payroll_mapping_version IS '建立批次時指定的薪酬對映版本。';
COMMENT ON COLUMN commission.payout_batch.control_sha256 IS '批次內容控制hash；建立時為preliminary值，組裝完成後覆寫為逐列總額hash，acknowledge比對用。';
COMMENT ON COLUMN commission.payout_batch.export_asset_id IS '付款回執證據資產（acknowledge_commission_payout_batch提供）。';

-- Which approved runs feed a payout batch; the implementation keeps this as
-- an in-memory tuple with no durable column today.
CREATE TABLE IF NOT EXISTS commission.payout_batch_run (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  batch_id uuid NOT NULL,
  run_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, batch_id) REFERENCES commission.payout_batch(tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES commission.calculation_run(tenant_id, id),
  CHECK (version > 0),
  UNIQUE (tenant_id, batch_id, run_id)
);
ALTER TABLE commission.payout_batch_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE commission.payout_batch_run FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON commission.payout_batch_run
  USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id());
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON commission.payout_batch_run
  FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
CREATE INDEX ix_commission_payout_batch_run_batch_id ON commission.payout_batch_run (tenant_id, batch_id);
CREATE INDEX ix_commission_payout_batch_run_run_id ON commission.payout_batch_run (tenant_id, run_id);
COMMENT ON TABLE commission.payout_batch_run IS 'M12 付款批次涵蓋的已核准run清單；建立後不可改列。';

-- Durable idempotent acknowledgement receipts; acknowledge_commission_payout_batch
-- currently dedupes on an in-memory ack_event_ids tuple only.
CREATE TABLE IF NOT EXISTS commission.payout_batch_ack (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  batch_id uuid NOT NULL,
  event_id uuid NOT NULL,
  external_batch_ref text NOT NULL,
  status text NOT NULL,
  acknowledgement_evidence_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, batch_id) REFERENCES commission.payout_batch(tenant_id, id),
  FOREIGN KEY (tenant_id, acknowledgement_evidence_id) REFERENCES core.object_asset(tenant_id, id),
  CHECK (version > 0),
  CHECK (status IN ('accepted', 'partial', 'rejected')),
  CHECK (length(external_batch_ref) BETWEEN 1 AND 160),
  UNIQUE (tenant_id, batch_id, event_id)
);
ALTER TABLE commission.payout_batch_ack ENABLE ROW LEVEL SECURITY;
ALTER TABLE commission.payout_batch_ack FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON commission.payout_batch_ack
  USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id());
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON commission.payout_batch_ack
  FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
CREATE INDEX ix_commission_payout_batch_ack_batch_id ON commission.payout_batch_ack (tenant_id, batch_id);
COMMENT ON TABLE commission.payout_batch_ack IS 'M12 付款回執去重證據；同event_id重送只回放既有列，部分成功不重複發放。';

-- ---------------------------------------------------------------------------
-- commission.payout_line: per-entry acceptance outcome from acknowledgement.
-- ---------------------------------------------------------------------------
ALTER TABLE commission.payout_line
  ADD COLUMN IF NOT EXISTS accepted boolean;
COMMENT ON COLUMN commission.payout_line.accepted IS 'null=待回執；true=財會確認已付；false=此列被拒絕，須另行處理，不可重複發放。';

-- ---------------------------------------------------------------------------
-- Baseline trigger argument fixes. These two 0003_business_guards.sql guards
-- use core.guard_fixed_record() with a mutable-column allowlist that predates
-- the implemented write paths; traced against the exact mutations each
-- command performs (see contract-changes.md for line references). The checks
-- themselves (maker<>checker, status machine, live-policy gate) are
-- unchanged -- only the set of columns the already-approved business flow is
-- allowed to touch after the initial insert.
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS immutable_commission_terms ON commission.policy_version;
CREATE TRIGGER immutable_commission_terms BEFORE UPDATE ON commission.policy_version FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{approval_status,approved_by,approved_at,base_pool_rate,effective_from}');

DROP TRIGGER IF EXISTS immutable_calculation ON commission.calculation_run;
CREATE TRIGGER immutable_calculation BEFORE UPDATE ON commission.calculation_run FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{status,completed_at,approved_by,approved_at,input_sha256,currency,failure_code}');
