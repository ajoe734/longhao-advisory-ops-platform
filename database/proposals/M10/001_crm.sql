-- M10 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@48ae302308de64159ee25ed506fb6f2e548cf886 (PostgreSQL 16).
-- Additive only: every change below either adds a nullable/defaulted column,
-- widens an existing CHECK, or creates a brand-new table. No existing row's
-- current values are invalidated.
SET TIME ZONE 'UTC';

-- `OpportunityCreate.type` publishes a `retention` value that the baseline
-- `crm.opportunity.kind` CHECK does not accept yet.
DO $m10_opportunity_kind$
DECLARE
  constraint_name text;
BEGIN
  SELECT con.conname INTO constraint_name
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'crm' AND rel.relname = 'opportunity' AND con.contype = 'c'
    AND pg_get_constraintdef(con.oid) ILIKE '%kind%IN%';
  IF constraint_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE crm.opportunity DROP CONSTRAINT %I', constraint_name);
  END IF;
  ALTER TABLE crm.opportunity ADD CONSTRAINT crm_opportunity_kind_ck
    CHECK (kind IN ('new', 'renewal', 'upgrade', 'reactivation', 'retention'));
END
$m10_opportunity_kind$;

-- `OpportunityCreate.reason_code` is the reason the opportunity was *opened*
-- (e.g. renewal_due, upgrade_eligible); the baseline `loss_reason_code`
-- column only ever captures why a *lost* opportunity closed, so it cannot
-- also hold the open-time reason without overwriting it on loss.
ALTER TABLE crm.opportunity ADD COLUMN IF NOT EXISTS open_reason_code text;
COMMENT ON COLUMN crm.opportunity.open_reason_code IS 'OpportunityCreate.reason_code：建立時的業務原因，與結案時填寫的 loss_reason_code 分開保存。';

-- `Opportunity.converted_at` has no baseline column; it must stay null until
-- a `crm.conversion` row for this opportunity is actually approved.
ALTER TABLE crm.opportunity ADD COLUMN IF NOT EXISTS converted_at timestamptz;
COMMENT ON COLUMN crm.opportunity.converted_at IS 'Opportunity.converted_at：對應 conversion 核定通過(approved)的時間；核定前維持 null，不得用送件/申請時間代填。';

-- `CrmWorkItem` needs an explicit worklist bucket, a multi-reason explanation,
-- an evidence list, a suggestion origin and an optional free-text note; the
-- baseline table only has a single `reason_code` and an unstructured
-- `explanation` jsonb.
ALTER TABLE crm.work_item
  ADD COLUMN IF NOT EXISTS worklist text,
  ADD COLUMN IF NOT EXISTS reason_codes text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS evidence_refs uuid[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS suggestion_origin text NOT NULL DEFAULT 'rule',
  ADD COLUMN IF NOT EXISTS note text;
ALTER TABLE crm.work_item ADD CONSTRAINT crm_work_item_worklist_ck
  CHECK (worklist IN ('incomplete_application', 'pending_review', 'unpaid', 'renewal', 'upgrade', 'retention', 'reactivation', 'company_pool')) NOT VALID;
ALTER TABLE crm.work_item ADD CONSTRAINT crm_work_item_suggestion_origin_ck
  CHECK (suggestion_origin IN ('rule', 'llm', 'human')) NOT VALID;
COMMENT ON COLUMN crm.work_item.worklist IS 'CrmWorkItem.worklist：名單分類，決定業務今日工作台的分組。';
COMMENT ON COLUMN crm.work_item.reason_codes IS 'CrmWorkItem.reason_codes：完整具體原因清單；reason_code 保留第一個/主要原因做舊相容索引。';
COMMENT ON COLUMN crm.work_item.evidence_refs IS 'CrmWorkItem.evidence_refs：佐證 object_asset／來源事件 id，不含原始個資。';
COMMENT ON COLUMN crm.work_item.suggestion_origin IS 'rule（規則引擎）/llm（草稿待人工採用）/human（業務自建）。';
COMMENT ON COLUMN crm.work_item.note IS 'NextActionRequest.note：業務指派下一步時的自由文字備註。';

-- `claim_conversion`/`decide_conversion` need columns the baseline
-- `crm.conversion` row does not carry: the API's `application_id`,
-- `finance_transaction_ref`, `decision_at`, the full evidence activity set,
-- the originally claimed salesperson (kept distinct from the eventually
-- *approved* `sales_actor_id` so disputed claims never silently overwrite
-- each other), an append-only claim history for disputed/competing claims,
-- and the API's own `conversion_type` vocabulary (new_subscription/renewal/
-- upgrade/reactivation), which is a different axis from the baseline
-- `conversion_type` column (self_service/sales_assisted attribution outcome).
ALTER TABLE crm.conversion
  ADD COLUMN IF NOT EXISTS application_id uuid,
  ADD COLUMN IF NOT EXISTS conversion_kind text,
  ADD COLUMN IF NOT EXISTS claimed_salesperson_id uuid,
  ADD COLUMN IF NOT EXISTS claim_history jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS evidence_activity_ids uuid[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS finance_transaction_ref uuid,
  ADD COLUMN IF NOT EXISTS decision_at timestamptz;
ALTER TABLE crm.conversion ADD CONSTRAINT crm_conversion_kind_ck
  CHECK (conversion_kind IN ('new_subscription', 'renewal', 'upgrade', 'reactivation')) NOT VALID;
COMMENT ON COLUMN crm.conversion.application_id IS '指向 customer.application，必須同一 tenant；ConversionClaim.application_id。';
COMMENT ON COLUMN crm.conversion.conversion_kind IS 'Conversion/ConversionClaim.conversion_type：new_subscription/renewal/upgrade/reactivation。與既有 conversion_type（self_service/sales_assisted 歸因結果）是不同軸，不可混用。';
COMMENT ON COLUMN crm.conversion.claimed_salesperson_id IS '最初提出 claim_conversion 的業務；核定後可能與 sales_actor_id（核准歸因）不同，兩者分開保存供稽核。';
COMMENT ON COLUMN crm.conversion.claim_history IS '每次 claim_conversion 呼叫的 append-only 記錄（actor/activity_ids/claimed_at），供爭議認領稽核；decide_conversion 的裁定不覆寫此陣列。';
COMMENT ON COLUMN crm.conversion.evidence_activity_ids IS 'Conversion.evidence_activity_ids：claim 與 decide 累積引用的 crm.activity id 全集。';
COMMENT ON COLUMN crm.conversion.finance_transaction_ref IS 'ConversionClaim/Conversion.finance_transaction_ref：對應的財務交易參照（M14）。';
COMMENT ON COLUMN crm.conversion.decision_at IS 'Conversion.decision_at：decide_conversion 核定時間；pending 時為 null。';

DO $m10_conversion_status$
DECLARE
  constraint_name text;
BEGIN
  SELECT con.conname INTO constraint_name
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'crm' AND rel.relname = 'conversion' AND con.contype = 'c'
    AND pg_get_constraintdef(con.oid) ILIKE '%decision_status%IN%';
  IF constraint_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE crm.conversion DROP CONSTRAINT %I', constraint_name);
  END IF;
  -- `under_review` marks >=2 competing/disputed claims pending the same
  -- decision; `reversed` is the post-approval state after a matching
  -- `finance.refund_confirmed` claws the attribution back. Neither is ever
  -- reachable directly from `decide_conversion`'s own request enum
  -- (approve/reject/dispute), so widening this CHECK does not open any new
  -- directly-reachable client-triggered transition.
  ALTER TABLE crm.conversion ADD CONSTRAINT crm_conversion_decision_status_ck
    CHECK (decision_status IN ('proposed', 'under_review', 'approved', 'rejected', 'disputed', 'reversed'));
END
$m10_conversion_status$;

-- `SuppressionRequest.reason_ref` cites the evidence (complaint/consent/
-- legal-hold record) behind the suppression; the baseline table has no
-- column for it.
ALTER TABLE crm.suppression ADD COLUMN IF NOT EXISTS reason_ref uuid;
COMMENT ON COLUMN crm.suppression.reason_ref IS 'SuppressionRequest.reason_ref：對應 support.case／consent／legal-hold 等證據紀錄，不是任意自由文字。';

-- `SalesActivityCreate` separately proves (a) the business's own activity
-- evidence and (b) the customer's two-way response, and carries a free-text
-- needs summary; the baseline table only has one `evidence_asset_id` and no
-- summary column. `late_entry` support closes UAT-M10-010 (activity recorded
-- after a conversion already decided must never silently backdate credit).
ALTER TABLE crm.activity
  ADD COLUMN IF NOT EXISTS customer_response_evidence_id uuid,
  ADD COLUMN IF NOT EXISTS needs_summary text,
  ADD COLUMN IF NOT EXISTS late_entry boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS late_entry_approved_by uuid,
  ADD COLUMN IF NOT EXISTS late_entry_approved_at timestamptz;
COMMENT ON COLUMN crm.activity.customer_response_evidence_id IS '指向 core.object_asset，必須同一 tenant；SalesActivityCreate.customer_response_evidence_id，與 evidence_asset_id（業務自身活動證據）分開保存，缺一不視為雙向。';
COMMENT ON COLUMN crm.activity.needs_summary IS 'SalesActivityCreate.needs_summary：業務記錄的客戶需求摘要（人工輸入或人工採用的 LLM 草稿）。';
COMMENT ON COLUMN crm.activity.late_entry IS 'true 代表 occurred_at 早於建檔時間且相關 conversion 已先核定；需 late_entry_approved_by 才能計入歸因證據。';
COMMENT ON COLUMN crm.activity.late_entry_approved_by IS '指向 core.actor，必須同一 tenant；核准 late_entry 採計的主管。';
COMMENT ON COLUMN crm.activity.late_entry_approved_at IS 'late_entry 核准時間；核准前該活動不得計入任何 conversion 的 evidence_activity_ids。';

DO $m10_rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['marketing_attribution', 'opportunity', 'activity', 'conversion', 'work_item', 'suppression']
  LOOP
    EXECUTE format('ALTER TABLE crm.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE crm.%I FORCE ROW LEVEL SECURITY', table_name);
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies WHERE schemaname = 'crm' AND tablename = table_name AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON crm.%I USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id())',
        table_name
      );
    END IF;
  END LOOP;
END
$m10_rls$;

-- Worklist sweeps run often and always filter by (tenant_id, worklist,
-- status); conversion reversal lookups by contract_service_id need the
-- unique index that already exists from the baseline, so none is repeated
-- here.
CREATE INDEX IF NOT EXISTS ix_m10_work_item_worklist_open
  ON crm.work_item (tenant_id, worklist, status)
  WHERE status IN ('open', 'assigned', 'in_progress', 'snoozed');
CREATE INDEX IF NOT EXISTS ix_m10_opportunity_owner_open
  ON crm.opportunity (tenant_id, owner_actor_id, status)
  WHERE status NOT IN ('converted', 'lost', 'cancelled');
