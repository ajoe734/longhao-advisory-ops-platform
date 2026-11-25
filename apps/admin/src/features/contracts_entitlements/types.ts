export type PackageState = "draft" | "approved" | "retired";
export type SignatureSessionStatus = "pending" | "verified" | "failed" | "expired";
export type ContractState = "pending_company_acceptance" | "accepted" | "effective" | "suspended" | "expired" | "terminated";
export type EntitlementState = "locked" | "eligible" | "active" | "suspended" | "expired" | "revoked";
export type EntitlementDecisionWord = "grant" | "keep" | "suspend" | "expire" | "revoke" | "deny";

export interface DocumentRef {
  asset_id: string;
  sha256: string;
  media_type: string;
  classification: string;
  scan_status: "pending" | "clean" | "rejected";
}

export interface ContractPackage {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  package_code: string;
  version_label: string;
  documents: DocumentRef[];
  state: PackageState;
  approved_by: string | null;
  effective_from: string;
  acceptance_terms_version: string;
}

export interface SignatureSession {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  purpose: string;
  subject_id: string;
  provider_session_ref: string;
  redirect_url: string;
  expires_at: string;
  status: SignatureSessionStatus;
}

export interface CompanyAcceptance {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  application_id: string;
  decision: "accept" | "reject" | "request_changes";
  actor_id: string;
  decided_at: string;
  decision_evidence_ids: string[];
  intended_effective_at: string | null;
  contract_id: string | null;
}

export interface ServiceSelection {
  service_code: string;
  offer_version: string;
  quantity: number;
  billing_interval: "month" | "year" | "one_time";
  quoted_price: { currency: string; amount: string };
  tax_mapping_version: string;
}

export interface Contract {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  application_id: string;
  package_version_id: string;
  state: ContractState;
  customer_signed_at: string;
  company_accepted_at: string | null;
  effective_at: string | null;
  service_starts_at: string | null;
  service_ends_at: string | null;
  terminated_at: string | null;
  service_selections: ServiceSelection[];
  preceding_contract_id: string | null;
}

export interface Entitlement {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  contract_id: string;
  service_code: string;
  state: EntitlementState;
  valid_from: string;
  valid_until: string;
  last_decision_id: string;
  restriction_codes: string[];
}

export interface GateSnapshot {
  gate: string;
  result: "pass" | "pending" | "fail" | "expired";
  record_id: string;
  record_version: number;
  evaluated_at: string;
}

export interface EntitlementDecision {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  contract_id: string;
  decision: EntitlementDecisionWord;
  policy_version: string;
  condition_snapshots: GateSnapshot[];
  entitlement_ids: string[];
  decided_at: string;
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
  | { kind: "stale"; value: T; message: string }
  | { kind: "conflict"; value: T; currentVersion: number; message: string }
  | { kind: "processing"; value?: T; receiptId: string }
  | { kind: "error"; problem: ApiProblem }
  | { kind: "ready"; value: T; message?: string };
