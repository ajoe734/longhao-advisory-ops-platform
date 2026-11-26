export type ScreeningState = "pending" | "clear" | "possible_match" | "confirmed_match" | "error";
export type CaseState = "open" | "under_review" | "escalated" | "cleared" | "rejected" | "closed";
export type RiskLevel = "low" | "standard" | "high";
export type ClearanceDecision = "pending" | "approved" | "denied" | "expired";

export interface AmlScreening {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  application_id: string;
  customer_id: string;
  provider_ref: string;
  policy_version: string;
  list_version: string | null;
  state: ScreeningState;
  result_asset_id: string | null;
  screened_at: string | null;
  expires_at: string | null;
  case_id: string | null;
}

export interface AmlCase {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  application_id: string;
  screening_ids: string[];
  state: CaseState;
  risk_level: RiskLevel;
  assignee_id: string | null;
  restricted_evidence_ids: string[];
  opened_at: string;
  next_review_due_at: string | null;
}

export interface BusinessClearance {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  application_id: string;
  customer_id: string;
  decision: ClearanceDecision;
  decided_at: string | null;
  expires_at: string | null;
  policy_version: string;
}

export interface AmlDeclarationReceipt {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  application_id: string;
  received_at: string;
  state: "received" | "requires_information";
  is_business_clearance: false;
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

// Every M04 restricted screen must cover all eight states below; a server
// `allowed_actions` list, never a client-side role guess, decides which
// buttons render enabled.
export type ViewState<T> =
  | { kind: "loading" }
  | { kind: "empty"; message: string }
  | { kind: "denied"; correlationId?: string }
  | { kind: "stale"; value: T; message: string; lastSuccessAt?: string | null }
  | { kind: "conflict"; value: T; currentVersion: number; message: string }
  | { kind: "processing"; value?: T; receiptId: string }
  | { kind: "error"; problem: ApiProblem }
  | { kind: "ready"; value: T; message?: string };
