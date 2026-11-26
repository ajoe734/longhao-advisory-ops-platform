export type PolicyState = "draft" | "approved" | "retired";
export type RunState = "queued" | "calculating" | "calculated" | "review_pending" | "approved" | "rejected" | "exported" | "failed";
export type EntryState = "calculated" | "approved" | "exported" | "adjusted";
export type PayoutBatchState = "prepared" | "approval_pending" | "approved" | "exported" | "acknowledged" | "failed";
export type RecipientType = "marketing" | "sales" | "company_retained";

export interface Money { amount: string; currency: string; }

export interface CommissionPolicy {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  policy_code: string;
  state: PolicyState;
  effective_from: string;
  effective_until: string | null;
  base_pool_rate: string | null;
  allocation_interpretation: string;
  age_anchor: string;
  service_lineage_rule: string;
  year3_assisted_marketing_rule: string;
  full_policy_asset_id: string;
  policy_sha256: string;
  approved_by: string | null;
  approved_at: string | null;
  unresolved_parameters: string[];
}

export interface CommissionLineSnapshot {
  recognition_id: string;
  contract_id: string;
  customer_id: string;
  basis: Money;
  base_pool_rate: string;
  base_pool: Money;
  marketing_share: string;
  marketing_age_coefficient: string;
  sales_share: string;
  marketing_award: Money;
  sales_award: Money;
  company_retained: Money;
  conversion_id: string | null;
  marketing_attribution_id: string | null;
  salesperson_id: string | null;
  policy_id: string;
  rule_trace_codes: string[];
}

export interface CommissionPreview {
  preview_id: string;
  policy_id: string;
  lines: CommissionLineSnapshot[];
  live_payable: boolean;
  unapproved_assumptions: string[];
  control_total: Money;
}

export interface CommissionRun {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  policy_id: string;
  period_start: string;
  period_end: string;
  revenue_cutoff_at: string;
  state: RunState;
  basis_total: Money;
  pool_total: Money;
  marketing_total: Money;
  sales_total: Money;
  company_retained_total: Money;
  prepared_by: string;
  approved_by: string | null;
  entry_count: number;
  exception_count: number;
}

export interface CommissionEntry {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  run_id: string;
  calculation: CommissionLineSnapshot;
  recipient_type: RecipientType;
  recipient_id: string | null;
  amount: Money;
  state: EntryState;
  original_entry_id: string | null;
}

export interface CommissionAdjustment {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  original_entry_id: string;
  refund_id: string;
  original_recipient_id: string | null;
  amount: Money;
  reason_code: string;
  state: string;
  payroll_offset_allowed: boolean;
}

export interface PayoutBatch {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  run_ids: string[];
  state: PayoutBatchState;
  total: Money;
  line_count: number;
  export_asset_id: string | null;
  control_sha256: string;
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
