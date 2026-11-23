export type CustomerStatus = "prospect" | "applicant" | "active" | "inactive" | "blocked" | "merged";
export type ApplicationState = "draft" | "customer_signed" | "due_diligence_pending" | "review_in_progress" | "accepted" | "rejected" | "withdrawn";
export type MergeState = "proposed" | "approved" | "rejected" | "applied";

export interface Customer {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  status: CustomerStatus;
  display_name: string;
  masked_email: string | null;
  masked_phone: string | null;
  maintenance_owner_id?: string | null;
  original_marketing_attribution_id?: string | null;
  merged_into_customer_id?: string | null;
}

export interface Application {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  package_version_id: string;
  state: ApplicationState;
  customer_signed_at: string | null;
  company_accepted_at: string | null;
  kyc_status: "not_started" | "pending" | "approved" | "rejected" | "refresh_required";
  suitability_status: "pending" | "suitable" | "conditional" | "not_suitable";
  business_clearance: "pending" | "approved" | "denied" | "expired";
  payment_status: "not_required" | "pending" | "authorized" | "paid" | "refunded" | "disputed";
  contract_id?: string | null;
}

export interface MergeCase {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  source_customer_id: string;
  target_customer_id: string;
  status: MergeState;
  proposer_id: string;
  reviewer_id: string | null;
}

export interface SourceSlice<T = Record<string, unknown>> {
  source: string;
  source_status: "available" | "degraded" | "stale";
  last_success_at: string | null;
  items: T[] | null;
  error_code: string | null;
}

export interface Customer360 {
  customer: Customer;
  sources: SourceSlice[];
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
