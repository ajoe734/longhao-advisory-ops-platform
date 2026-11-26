-- M13 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@48ae302308de64159ee25ed506fb6f2e548cf886 (PostgreSQL 16).
SET TIME ZONE 'UTC';

-- The baseline support.case_file has a single owner_actor_id column, which is
-- the customer-service assignee (wire ServiceCase.assignee_id). Maintenance
-- ownership (REQ-M13-012) must never move just because the service-case
-- assignee changes, so it needs its own column projected only from
-- ownership.assignment_changed, never written by assign/resolve commands.
ALTER TABLE support.case_file
  ADD COLUMN IF NOT EXISTS maintenance_owner_id uuid,
  ADD COLUMN IF NOT EXISTS linked_refund_id uuid,
  ADD COLUMN IF NOT EXISTS sla_paused_reason text,
  ADD COLUMN IF NOT EXISTS escalated_queue text,
  ADD COLUMN IF NOT EXISTS escalated_due_at timestamptz,
  ADD COLUMN IF NOT EXISTS escalation_evidence_asset_id uuid,
  ADD CONSTRAINT m13_case_file_maintenance_owner_fk
    FOREIGN KEY (tenant_id, maintenance_owner_id) REFERENCES core.actor(tenant_id, id) NOT VALID,
  ADD CONSTRAINT m13_case_file_linked_refund_fk
    FOREIGN KEY (tenant_id, linked_refund_id) REFERENCES finance.refund(tenant_id, id) NOT VALID,
  ADD CONSTRAINT m13_case_file_escalation_evidence_fk
    FOREIGN KEY (tenant_id, escalation_evidence_asset_id) REFERENCES core.object_asset(tenant_id, id) NOT VALID,
  ADD CONSTRAINT m13_case_file_escalated_queue_ck
    CHECK (escalated_queue IS NULL OR escalated_queue IN ('analyst', 'finance', 'compliance', 'technical', 'service_supervisor')) NOT VALID,
  ADD CONSTRAINT m13_case_file_escalation_pair_ck
    CHECK ((escalated_queue IS NULL) = (escalated_due_at IS NULL)) NOT VALID;

-- The baseline category/priority comments ("service/complaint/..." and
-- "low/normal/high/critical") predate the frozen OpenAPI enums and were never
-- enforced by a CHECK. This proposal brings the DB in line with the wire
-- contract (api/openapi.yaml ServiceCaseCreate.category/priority) rather than
-- inventing a parallel enum.
ALTER TABLE support.case_file
  ADD CONSTRAINT m13_case_file_category_ck
    CHECK (category IN ('application', 'contract', 'access', 'research_question', 'billing', 'refund', 'complaint', 'technical', 'other')) NOT VALID,
  ADD CONSTRAINT m13_case_file_priority_ck
    CHECK (priority IN ('normal', 'high', 'urgent')) NOT VALID;

COMMENT ON COLUMN support.case_file.owner_actor_id IS '客服承辦人（wire ServiceCase.assignee_id）；與業務維護人分離。';
COMMENT ON COLUMN support.case_file.category IS 'application/contract/access/research_question/billing/refund/complaint/technical/other，依api/openapi.yaml ServiceCaseCreate.category。';
COMMENT ON COLUMN support.case_file.priority IS 'normal/high/urgent，依api/openapi.yaml ServiceCaseCreate.priority。';
COMMENT ON COLUMN support.case_file.maintenance_owner_id IS '業務維護／轉換歸屬；只由ownership.assignment_changed投影更新，assign/resolve不得變更。';
COMMENT ON COLUMN support.case_file.linked_refund_id IS '指向 finance.refund；只能由客服提出escalation後，經finance.refund_confirmed事件回填，客服無approve/execute權限。';
COMMENT ON COLUMN support.case_file.sla_paused_reason IS '合規允許的SLA暫停理由版本化代碼；waiting_internal本身不自動暫停。';
COMMENT ON COLUMN support.case_file.escalated_queue IS '目前升級目標佇列；AML/compliance僅留安全狀態，不帶案情內容。';
COMMENT ON COLUMN support.case_file.escalated_due_at IS '升級回覆期限。';
COMMENT ON COLUMN support.case_file.escalation_evidence_asset_id IS '升級所需最小必要內容的物件參照；不得將原始案件全文放入本欄。';

-- Resolving a case must leave an immutable record even when the case is later
-- reopened and resolved again (REQ-M13-011); the baseline case_file only has
-- one mutable resolved_at/resolution_code pair. support.case_resolved's event
-- payload already requires a distinct resolution_record_id, confirming this
-- child table is the intended evidentiary shape.
CREATE TABLE IF NOT EXISTS support.case_resolution (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  resolution_code text NOT NULL,
  resolution_summary text NOT NULL,
  customer_notice_evidence_id uuid NOT NULL,
  resolved_by uuid NOT NULL,
  resolved_at timestamptz NOT NULL,
  linked_refund_id uuid,
  follow_up_due_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES support.case_file(tenant_id, id),
  FOREIGN KEY (tenant_id, customer_notice_evidence_id) REFERENCES core.object_asset(tenant_id, id),
  FOREIGN KEY (tenant_id, resolved_by) REFERENCES core.actor(tenant_id, id),
  FOREIGN KEY (tenant_id, linked_refund_id) REFERENCES finance.refund(tenant_id, id),
  CHECK (version > 0),
  CHECK (length(resolution_code) BETWEEN 1 AND 80),
  CHECK (length(resolution_summary) BETWEEN 1 AND 4000)
);
COMMENT ON TABLE support.case_resolution IS 'M13 不可變結案證據；reopen後再次resolve新增一列，不覆寫或刪除原紀錄。';
COMMENT ON COLUMN support.case_resolution.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.case_resolution.id IS '資源識別碼；在同一租戶內唯一；對應support.case_resolved事件的resolution_record_id。';
COMMENT ON COLUMN support.case_resolution.case_id IS '指向 support.case_file，必須同一 tenant。';
COMMENT ON COLUMN support.case_resolution.resolution_code IS '結案分類代碼。';
COMMENT ON COLUMN support.case_resolution.resolution_summary IS '處理結果摘要。';
COMMENT ON COLUMN support.case_resolution.customer_notice_evidence_id IS '客戶告知證據；指向 core.object_asset。';
COMMENT ON COLUMN support.case_resolution.resolved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN support.case_resolution.resolved_at IS '結案真實時點。';
COMMENT ON COLUMN support.case_resolution.linked_refund_id IS '指向 finance.refund（若適用）。';
COMMENT ON COLUMN support.case_resolution.follow_up_due_at IS '後續追蹤期限（若適用）。';

-- support.response_dispatch needs its own worker lease so a sender can lease,
-- bound-retry and dead-letter sends independently of the generic outbox
-- (which only carries the four support.* domain events, not the external
-- channel send itself).
ALTER TABLE support.response_dispatch
  ADD COLUMN IF NOT EXISTS lease_owner text,
  ADD COLUMN IF NOT EXISTS lease_until timestamptz,
  ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_error_code text,
  ADD CONSTRAINT m13_response_dispatch_attempt_count_ck CHECK (attempt_count >= 0) NOT VALID;
COMMENT ON COLUMN support.response_dispatch.lease_owner IS '目前持有發送lease的worker識別；null代表可被租用。';
COMMENT ON COLUMN support.response_dispatch.lease_until IS 'lease到期時間；逾期視為可再被租用。';
COMMENT ON COLUMN support.response_dispatch.next_attempt_at IS '下次允許重試時間（指數退避）。';
COMMENT ON COLUMN support.response_dispatch.attempt_count IS '已嘗試送出次數；達上限轉failed，不盲目重送unknown結果。';
COMMENT ON COLUMN support.response_dispatch.last_error_code IS '最近一次失敗的安全錯誤代碼。';

-- The baseline migration only defines core.touch_version()/core.reject_evidence_mutation()
-- without wiring them to any support.* table. M13 is the sole owner of this
-- schema, so this proposal wires CAS version-bump triggers to the three
-- mutable aggregates and append-only guards to the five evidentiary tables,
-- plus tenant-isolating RLS across all nine support.* tables.
DROP TRIGGER IF EXISTS m13_touch_version ON support.case_file;
CREATE TRIGGER m13_touch_version BEFORE UPDATE ON support.case_file
FOR EACH ROW EXECUTE FUNCTION core.touch_version();
DROP TRIGGER IF EXISTS m13_touch_version ON support.response_draft;
CREATE TRIGGER m13_touch_version BEFORE UPDATE ON support.response_draft
FOR EACH ROW EXECUTE FUNCTION core.touch_version();
DROP TRIGGER IF EXISTS m13_touch_version ON support.response_dispatch;
CREATE TRIGGER m13_touch_version BEFORE UPDATE ON support.response_dispatch
FOR EACH ROW EXECUTE FUNCTION core.touch_version();

DROP TRIGGER IF EXISTS m13_guard_evidence ON support.interaction;
CREATE TRIGGER m13_guard_evidence BEFORE UPDATE OR DELETE ON support.interaction
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS m13_guard_evidence ON support.sla_event;
CREATE TRIGGER m13_guard_evidence BEFORE UPDATE OR DELETE ON support.sla_event
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS m13_guard_evidence ON support.response_approval;
CREATE TRIGGER m13_guard_evidence BEFORE UPDATE OR DELETE ON support.response_approval
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS m13_guard_evidence ON support.response_attempt;
CREATE TRIGGER m13_guard_evidence BEFORE UPDATE OR DELETE ON support.response_attempt
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS m13_guard_evidence ON support.response_receipt;
CREATE TRIGGER m13_guard_evidence BEFORE UPDATE OR DELETE ON support.response_receipt
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS m13_guard_evidence ON support.case_resolution;
CREATE TRIGGER m13_guard_evidence BEFORE UPDATE OR DELETE ON support.case_resolution
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();

DO $m13_rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'case_file', 'interaction', 'sla_event', 'response_draft', 'response_approval',
    'response_dispatch', 'response_attempt', 'response_receipt', 'case_resolution'
  ]
  LOOP
    EXECUTE format('ALTER TABLE support.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE support.%I FORCE ROW LEVEL SECURITY', table_name);
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies WHERE schemaname = 'support' AND tablename = table_name AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON support.%I USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id())',
        table_name
      );
    END IF;
  END LOOP;
END
$m13_rls$;
