export interface Money {
  amount: string;
  currency: string;
}

export type InvoiceState = "draft" | "issued" | "partially_paid" | "paid" | "voided" | "credited";
export type ReconciliationState = "open" | "investigating" | "resolved" | "escalated";
export type RevenueRunState = "queued" | "calculated" | "review_pending" | "approved" | "posted" | "failed";
export type RefundState =
  | "requested"
  | "calculated"
  | "approval_pending"
  | "approved"
  | "submitted"
  | "confirmed"
  | "failed"
  | "reconciliation_required"
  | "accounting_adjusted"
  | "closed"
  | "rejected";
export type AccountingExportState = "prepared" | "approved" | "exported" | "partially_acknowledged" | "acknowledged" | "failed";
export type LegalHoldState = "active" | "released";
export type ControlExceptionState = "open" | "remediating" | "ready_for_review" | "closed" | "reopened";

export interface Invoice {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  contract_id: string;
  invoice_date: string;
  due_date: string;
  state: InvoiceState;
  untaxed_service_amount: Money;
  tax_amount: Money;
  total_amount: Money;
}

export interface ReconciliationItem {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  settlement_id: string;
  payment_id: string | null;
  difference_type: "unmatched_payment" | "unmatched_settlement" | "amount_mismatch" | "duplicate" | "fee_mismatch" | "timing";
  expected_amount: Money;
  actual_amount: Money;
  state: ReconciliationState;
  owner_id: string | null;
  resolved_by: string | null;
}

export interface RevenueRun {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  period_start: string;
  period_end: string;
  recognition_policy_version: string;
  state: RevenueRunState;
  recognized_untaxed_total: Money;
  deferred_remaining: Money;
  entry_count: number;
  exception_count: number;
  journal_ids: string[];
  calculation_manifest_sha256: string;
}

export interface RefundAllocation {
  invoice_line_ref: string;
  recognition_id: string | null;
  unearned_amount: Money;
  earned_reversal_amount: Money;
  tax_adjustment_amount: Money;
}

export interface Refund {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  contract_id: string;
  original_payment_id: string;
  state: RefundState;
  requested_amount: Money;
  approved_amount: Money | null;
  allocations: RefundAllocation[];
  prepared_by: string;
  approved_by: string | null;
  provider_refund_ref: string | null;
  confirmed_at: string | null;
}

export interface AccountingExport {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  state: AccountingExportState;
  account_mapping_version: string;
  tax_mapping_version: string;
  debit_total: Money;
  credit_total: Money;
  entry_count: number;
  export_asset_id: string | null;
  export_sha256: string | null;
  acknowledgement_ref: string | null;
}

export interface LegalHold {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  scope: string;
  subject_ids: string[];
  state: LegalHoldState;
  reason_code: string;
  authority_evidence_asset_id: string;
  created_by: string;
  review_due_at: string;
}

export interface ControlException {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  control_code: string;
  severity: "low" | "medium" | "high" | "critical";
  state: ControlExceptionState;
  remediation_owner_id: string;
  due_at: string;
  closed_by: string | null;
  closure_evidence_id: string | null;
}

export interface AuditEventRow {
  id: string;
  actor_id: string | null;
  actor_type: "staff" | "customer" | "service";
  action: string;
  aggregate_type: string;
  aggregate_id: string | null;
  occurred_at: string;
  correlation_id: string;
  outcome: "success" | "denied" | "failed";
  evidence_asset_ids: string[];
  classification: "internal" | "customer_confidential" | "aml_restricted" | "research_confidential";
}

export interface EvidenceExport {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  scope: string;
  subject_id: string;
  state: "queued" | "building" | "ready" | "failed" | "expired";
  manifest_asset_id: string | null;
  export_asset_id: string | null;
  manifest_sha256: string | null;
  expires_at: string;
  requester_id: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  as_of: string;
  total_estimate?: number | null;
}

export interface ApiProblem {
  status: number;
  code: string;
  detail: string;
  correlation_id: string;
  retryable: boolean;
  current_version?: number | null;
}

export type ViewState<T> =
  | { kind: "loading" }
  | { kind: "empty"; message: string }
  | { kind: "denied"; correlationId?: string }
  | { kind: "stale"; value: T; message: string; lastSuccessAt?: string | null }
  | { kind: "conflict"; value: T; currentVersion: number; message: string }
  | { kind: "processing"; value?: T; receiptId: string }
  | { kind: "error"; problem: ApiProblem }
  | { kind: "ready"; value: T; message?: string };
