-- M14 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@48ae302308de64159ee25ed506fb6f2e548cf886 (PostgreSQL 16).
-- All 14 finance.* tables and core.legal_hold/core.audit_event already exist
-- in database/migrations/0001_domains.sql. This proposal only ALTERs those
-- tables to close gaps between the OpenAPI 1.0.0 wire contract (api/openapi.yaml)
-- and the current column set, plus two new tables for the untrusted
-- reconciliation-narration automation drafts. No table is dropped or recreated.
SET TIME ZONE 'UTC';

-- finance.payment: PaymentIntentCreate requires method/return_route and the
-- response requires checkout_url/expires_at while the intent is still pending.
ALTER TABLE finance.payment
  ADD COLUMN IF NOT EXISTS method text,
  ADD COLUMN IF NOT EXISTS return_route text,
  ADD COLUMN IF NOT EXISTS checkout_url text,
  ADD COLUMN IF NOT EXISTS expires_at timestamptz,
  ADD CONSTRAINT m14_payment_method_ck
    CHECK (method IS NULL OR method IN ('card','bank_transfer','approved_wallet')) NOT VALID,
  ADD CONSTRAINT m14_payment_return_route_ck
    CHECK (return_route IS NULL OR return_route IN ('application_progress','member_billing')) NOT VALID;
COMMENT ON COLUMN finance.payment.method IS 'PaymentIntentCreate.method; null once the row only represents a matured finance.payment fact.';
COMMENT ON COLUMN finance.payment.return_route IS 'PaymentIntentCreate.return_route.';
COMMENT ON COLUMN finance.payment.checkout_url IS 'PaymentIntent.checkout_url while state=pending; not meaningful after capture.';
COMMENT ON COLUMN finance.payment.expires_at IS 'PaymentIntent.expires_at; intent state projects to expired once past this and still pending.';

-- finance.invoice: InvoiceCreate requires planning dates distinct from the
-- actual issued_at/due_at event timestamps already on the table.
ALTER TABLE finance.invoice
  ADD COLUMN IF NOT EXISTS invoice_date date,
  ADD COLUMN IF NOT EXISTS due_date date,
  ADD CONSTRAINT m14_invoice_due_after_invoice_ck
    CHECK (due_date IS NULL OR invoice_date IS NULL OR due_date >= invoice_date) NOT VALID;
COMMENT ON COLUMN finance.invoice.invoice_date IS 'Invoice.invoice_date; the business billing date, distinct from issued_at (actual issuance event time).';
COMMENT ON COLUMN finance.invoice.due_date IS 'Invoice.due_date; the business due date, distinct from due_at (actual event time, currently unused by M14).';

-- finance.invoice_line: InvoiceLineCreate requires a per-line service period,
-- quantity and tax mapping; service_code is the wire name for description_code.
ALTER TABLE finance.invoice_line
  ADD COLUMN IF NOT EXISTS service_code text,
  ADD COLUMN IF NOT EXISTS service_period_start date,
  ADD COLUMN IF NOT EXISTS service_period_end date,
  ADD COLUMN IF NOT EXISTS quantity integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS tax_code text,
  ADD COLUMN IF NOT EXISTS tax_mapping_version text,
  ADD CONSTRAINT m14_invoice_line_period_ck
    CHECK (service_period_end IS NULL OR service_period_start IS NULL OR service_period_end > service_period_start) NOT VALID,
  ADD CONSTRAINT m14_invoice_line_quantity_ck
    CHECK (quantity >= 1);
COMMENT ON COLUMN finance.invoice_line.service_code IS 'InvoiceLineCreate.service_code; description_code remains the controlled display text.';
COMMENT ON COLUMN finance.invoice_line.service_period_start IS 'InvoiceLineCreate.service_period_start; backs finance.service_schedule generation.';
COMMENT ON COLUMN finance.invoice_line.service_period_end IS 'InvoiceLineCreate.service_period_end.';
COMMENT ON COLUMN finance.invoice_line.quantity IS 'InvoiceLineCreate.quantity; informational, not multiplied into untaxed_amount.';
COMMENT ON COLUMN finance.invoice_line.tax_code IS 'InvoiceLineCreate.tax_code; must be paired with an M14-approved tax_mapping_version before invoicing (REQ-M14-004).';
COMMENT ON COLUMN finance.invoice_line.tax_mapping_version IS 'Approved finance tax mapping version used for this line; never a guessed divisor.';

-- finance.reconciliation_item: ReconciliationItem requires settlement_id,
-- a specific difference_type enum and both expected/actual amounts; the
-- existing source_type/source_ref/difference_amount stay as the append-only
-- source-of-record for the dedup unique key and are populated by the mapper.
ALTER TABLE finance.reconciliation_item
  ADD COLUMN IF NOT EXISTS settlement_id text,
  ADD COLUMN IF NOT EXISTS difference_type text,
  ADD COLUMN IF NOT EXISTS expected_amount numeric(20,6),
  ADD COLUMN IF NOT EXISTS actual_amount numeric(20,6),
  ADD CONSTRAINT m14_reconciliation_difference_type_ck
    CHECK (difference_type IS NULL OR difference_type IN (
      'unmatched_payment','unmatched_settlement','amount_mismatch','duplicate','fee_mismatch','timing'
    )) NOT VALID;
COMMENT ON COLUMN finance.reconciliation_item.settlement_id IS 'ReconciliationItem.settlement_id; mapper-populated alias of source_ref when source_type=settlement.';
COMMENT ON COLUMN finance.reconciliation_item.difference_type IS 'ReconciliationItem.difference_type; wire enum, independent of the legacy reason_code free text.';
COMMENT ON COLUMN finance.reconciliation_item.expected_amount IS 'ReconciliationItem.expected_amount.';
COMMENT ON COLUMN finance.reconciliation_item.actual_amount IS 'ReconciliationItem.actual_amount; actual_amount - expected_amount reconciles with the legacy difference_amount.';

-- finance.revenue_run: persists which schedules were included/excluded at
-- calculation time so approve_revenue_run posts exactly what was reviewed,
-- and a later audit can re-verify calculation_manifest_sha256 independently.
ALTER TABLE finance.revenue_run
  ADD COLUMN IF NOT EXISTS calculation_detail jsonb NOT NULL DEFAULT '{"included_schedule_ids":[],"exception_schedule_ids":[]}'::jsonb,
  ADD CONSTRAINT m14_revenue_run_calculation_detail_ck
    CHECK (jsonb_typeof(calculation_detail) = 'object');
COMMENT ON COLUMN finance.revenue_run.calculation_detail IS 'Deterministic inputs hashed into calculation_manifest_sha256: {included_schedule_ids, exception_schedule_ids}.';

-- finance.revenue_recognition: finance.revenue_recognized payload requires
-- contract_id/contract_service_id, which are only reachable today via a
-- service_schedule -> invoice join; storing them avoids a cross-run join on
-- every event replay and keeps the event payload reproducible from this row alone.
ALTER TABLE finance.revenue_recognition
  ADD COLUMN IF NOT EXISTS contract_id uuid,
  ADD COLUMN IF NOT EXISTS contract_service_id uuid,
  ADD FOREIGN KEY (tenant_id, contract_id) REFERENCES contract.contract(tenant_id, id),
  ADD FOREIGN KEY (tenant_id, contract_service_id) REFERENCES contract.contract_service(tenant_id, id);
COMMENT ON COLUMN finance.revenue_recognition.contract_id IS 'finance.revenue_recognized payload field; denormalized from the schedule''s invoice for replay-safe event emission.';
COMMENT ON COLUMN finance.revenue_recognition.contract_service_id IS 'finance.revenue_recognized payload field.';

-- finance.refund: RefundCreate requires contract_id, customer_request_evidence_id
-- and calculation_policy_version which are not on the baseline row; the wire
-- Refund also separates requested_amount (existing "amount" column) from
-- approved_amount, and execute_refund needs a provider-call idempotency key
-- that is distinct from and must outlive the Idempotency-Key request header.
ALTER TABLE finance.refund
  ADD COLUMN IF NOT EXISTS contract_id uuid,
  ADD COLUMN IF NOT EXISTS customer_request_evidence_id uuid,
  ADD COLUMN IF NOT EXISTS calculation_policy_version text,
  ADD COLUMN IF NOT EXISTS approved_amount numeric(20,6),
  ADD COLUMN IF NOT EXISTS provider_idempotency_key text,
  ADD FOREIGN KEY (tenant_id, contract_id) REFERENCES contract.contract(tenant_id, id),
  ADD FOREIGN KEY (tenant_id, customer_request_evidence_id) REFERENCES core.object_asset(tenant_id, id),
  ADD CONSTRAINT m14_refund_approved_amount_ck
    CHECK (approved_amount IS NULL OR approved_amount > 0) NOT VALID;
CREATE UNIQUE INDEX IF NOT EXISTS ux_m14_refund_provider_idempotency_key
  ON finance.refund (tenant_id, provider_idempotency_key)
  WHERE provider_idempotency_key IS NOT NULL;
COMMENT ON COLUMN finance.refund.amount IS 'Maps to wire Refund.requested_amount; kept as "amount" for compatibility with the existing unique/check constraints.';
COMMENT ON COLUMN finance.refund.approved_amount IS 'Refund.approved_amount; snapshotted at approve_refund, independent of later requested_amount edits (there are none; refunds are immutable once requested).';
COMMENT ON COLUMN finance.refund.contract_id IS 'RefundCreate.contract_id.';
COMMENT ON COLUMN finance.refund.customer_request_evidence_id IS 'RefundCreate.customer_request_evidence_id.';
COMMENT ON COLUMN finance.refund.calculation_policy_version IS 'RefundCreate.calculation_policy_version.';
COMMENT ON COLUMN finance.refund.provider_idempotency_key IS 'RefundExecutionRequest.provider_idempotency_key; stable across execute_refund retries so the provider never sees two submissions (REQ-M14-006).';

-- finance.refund_allocation: the wire RefundAllocation groups unearned,
-- earned-reversal and tax amounts under one invoice_line_ref; this column
-- lets the mapper re-group the existing one-row-per-allocation_type rows.
ALTER TABLE finance.refund_allocation
  ADD COLUMN IF NOT EXISTS invoice_line_ref uuid;
COMMENT ON COLUMN finance.refund_allocation.invoice_line_ref IS 'RefundAllocation.invoice_line_ref; groups the earned_revenue/unearned_prepayment/tax rows that the wire represents as one allocation object.';

-- finance.accounting_export: AccountingExportRequest/AccountingExport need
-- the reporting period, mapping versions and control totals, and
-- acknowledge_accounting_export must be idempotent per external event_id
-- without re-posting a journal (REQ-M14-009).
ALTER TABLE finance.accounting_export
  ADD COLUMN IF NOT EXISTS period_start date,
  ADD COLUMN IF NOT EXISTS period_end date,
  ADD COLUMN IF NOT EXISTS account_mapping_version text,
  ADD COLUMN IF NOT EXISTS tax_mapping_version text,
  ADD COLUMN IF NOT EXISTS debit_total numeric(20,6),
  ADD COLUMN IF NOT EXISTS credit_total numeric(20,6),
  ADD COLUMN IF NOT EXISTS entry_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS accepted_journal_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS rejected_journal_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS applied_ack_event_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  DROP CONSTRAINT IF EXISTS accounting_export_status_check,
  ADD CONSTRAINT m14_accounting_export_status_ck
    CHECK (status IN ('prepared','submitted','approved','exported','partially_acknowledged','acknowledged','rejected','reconciled','failed')),
  ADD CONSTRAINT m14_accounting_export_totals_ck
    CHECK (debit_total IS NULL OR credit_total IS NULL OR debit_total = credit_total) NOT VALID;
COMMENT ON COLUMN finance.accounting_export.period_start IS 'AccountingExportRequest.period_start.';
COMMENT ON COLUMN finance.accounting_export.period_end IS 'AccountingExportRequest.period_end; period_key stays as the legacy YYYY-MM projection.';
COMMENT ON COLUMN finance.accounting_export.account_mapping_version IS 'AccountingExportRequest.account_mapping_version.';
COMMENT ON COLUMN finance.accounting_export.tax_mapping_version IS 'AccountingExportRequest.tax_mapping_version.';
COMMENT ON COLUMN finance.accounting_export.debit_total IS 'AccountingExport.debit_total; must equal credit_total before export is prepared.';
COMMENT ON COLUMN finance.accounting_export.credit_total IS 'AccountingExport.credit_total.';
COMMENT ON COLUMN finance.accounting_export.entry_count IS 'AccountingExport.entry_count.';
COMMENT ON COLUMN finance.accounting_export.accepted_journal_ids IS 'Per-journal ack tracking from acknowledge_accounting_export; drives partially_acknowledged vs acknowledged.';
COMMENT ON COLUMN finance.accounting_export.rejected_journal_ids IS 'Per-journal rejection tracking from acknowledge_accounting_export.';
COMMENT ON COLUMN finance.accounting_export.applied_ack_event_ids IS 'Dedup set of ExportAckRequest.event_id already applied; a resend never re-applies accepted/rejected sets (REQ-M14-009).';

-- finance.control_exception: ControlExceptionRequest/ControlException need a
-- free-text description, a plural evidence list, and an independent-closure
-- audit trail (closed_by/closure_evidence_id) that the baseline row lacks.
ALTER TABLE finance.control_exception
  ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS evidence_asset_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS closed_by uuid,
  ADD COLUMN IF NOT EXISTS closure_evidence_id uuid,
  ADD FOREIGN KEY (tenant_id, closed_by) REFERENCES core.actor(tenant_id, id),
  ADD FOREIGN KEY (tenant_id, closure_evidence_id) REFERENCES core.object_asset(tenant_id, id),
  DROP CONSTRAINT IF EXISTS control_exception_status_check,
  ADD CONSTRAINT m14_control_exception_status_ck
    CHECK (status IN ('open','assigned','remediating','awaiting_review','ready_for_review','closed','reopened','waived')),
  ADD CONSTRAINT m14_control_exception_closure_ck
    CHECK (closed_by IS NULL OR owner_actor_id IS NULL OR closed_by <> owner_actor_id) NOT VALID;
COMMENT ON COLUMN finance.control_exception.description IS 'ControlExceptionRequest.description.';
COMMENT ON COLUMN finance.control_exception.evidence_asset_ids IS 'ControlExceptionRequest.evidence_asset_ids; evidence_asset_id (singular) stays as the first/primary reference.';
COMMENT ON COLUMN finance.control_exception.closed_by IS 'ControlException.closed_by; must differ from owner_actor_id (remediation owner) at approve_closure (REQ-M14-012).';
COMMENT ON COLUMN finance.control_exception.closure_evidence_id IS 'ControlException.closure_evidence_id.';
COMMENT ON COLUMN finance.control_exception.resource_type IS 'Not on the wire ControlException; application sets a self-referential (''control_exception'', id) pair when no single finance aggregate is implicated.';

-- Minimized, untrusted reconciliation-narration automation drafts (M14
-- equivalent of M01's identity_automation_run/review). These tables carry no
-- resolution authority; resolve_reconciliation_item remains the only write
-- path for finance.reconciliation_item.
CREATE TABLE IF NOT EXISTS finance.reconciliation_automation_run (
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
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  FOREIGN KEY (tenant_id, requested_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (input_sha256 ~ '^[a-f0-9]{64}$'),
  CHECK (jsonb_typeof(source_refs) = 'array'),
  CHECK (jsonb_typeof(output_draft) = 'array'),
  CHECK (status IN ('untrusted_draft', 'reviewed'))
);

CREATE TABLE IF NOT EXISTS finance.reconciliation_automation_review (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  run_id uuid NOT NULL,
  reviewed_by uuid NOT NULL,
  action text NOT NULL,
  reason_code text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES finance.reconciliation_automation_run(tenant_id, id),
  FOREIGN KEY (tenant_id, reviewed_by) REFERENCES core.actor(tenant_id, id),
  CHECK (version > 0),
  CHECK (action IN ('adopted', 'modified', 'rejected'))
);

DO $m14_rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['reconciliation_automation_run', 'reconciliation_automation_review']
  LOOP
    EXECUTE format('ALTER TABLE finance.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE finance.%I FORCE ROW LEVEL SECURITY', table_name);
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies WHERE schemaname = 'finance' AND tablename = table_name AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON finance.%I USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id())',
        table_name
      );
    END IF;
  END LOOP;
END
$m14_rls$;

DROP TRIGGER IF EXISTS guard_row ON finance.reconciliation_automation_run;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON finance.reconciliation_automation_run
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();
DROP TRIGGER IF EXISTS guard_row ON finance.reconciliation_automation_review;
CREATE TRIGGER guard_row BEFORE UPDATE OR DELETE ON finance.reconciliation_automation_review
FOR EACH ROW EXECUTE FUNCTION core.reject_evidence_mutation();

COMMENT ON TABLE finance.reconciliation_automation_run IS 'M14 minimized untrusted reconciliation-narration automation draft with versioned provenance; no resolution authority.';
COMMENT ON TABLE finance.reconciliation_automation_review IS 'M14 immutable human adoption/modification/rejection evidence for automation drafts.';
