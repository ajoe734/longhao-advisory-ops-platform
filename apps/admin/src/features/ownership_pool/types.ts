export type AssignmentState = "active" | "released" | "departure" | "superseded";
export type PoolEntryState = "available" | "claimed" | "suppressed";
export type PoolEntryReason = "unassigned_web" | "employee_departure" | "paid_service_lapse" | "manual_release";

export interface MaintenanceAssignment {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  salesperson_id: string;
  valid_from: string;
  valid_until: string | null;
  reason_code: string;
  state: AssignmentState;
  source_pool_entry_id: string | null;
  commission_entitlement_granted: false;
}

export interface PoolEntry {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  entered_at: string;
  entry_reason: PoolEntryReason;
  paid_service_lapse_started_at: string | null;
  state: PoolEntryState;
  claimed_by: string | null;
  policy_version: string;
}

export interface DepartureReceipt {
  departure_id: string;
  employee_id: string;
  effective_at: string;
  assignments_closed: number;
  pool_entries_created: number;
  access_revocation_event_id: string;
}

export interface PoolEvaluationResult {
  evaluation_id: string;
  policy_version: string;
  dry_run: boolean;
  rule_status: "proposed" | "approved";
  evaluated_count: number;
  eligible_count: number;
  blocked_count: number;
  evidence_export_asset_id: string;
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
