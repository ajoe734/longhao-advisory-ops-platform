export type OpportunityType = "new_subscription" | "renewal" | "upgrade" | "retention" | "reactivation";
export type OpportunityState = "open" | "qualified" | "proposal" | "awaiting_customer" | "converted" | "lost" | "suppressed";

export interface Opportunity {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  type: OpportunityType;
  service_code: string;
  owner_id: string;
  state: OpportunityState;
  contract_id: string | null;
  due_at: string;
  converted_at: string | null;
}

export interface OpportunityCreate {
  customer_id: string;
  type: OpportunityType;
  service_code: string;
  contract_id?: string | null;
  reason_code: string;
  owner_id: string;
  due_at: string;
}

export type SalesActivityType = "two_way_message" | "completed_call" | "meeting" | "needs_resolution" | "proposal" | "follow_up";
export type SalesActivityOutcome = "contacted" | "needs_identified" | "proposal_requested" | "follow_up" | "declined" | "converted";

export interface SalesActivity {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  opportunity_id: string;
  customer_id: string;
  actor_id: string;
  activity_type: SalesActivityType;
  occurred_at: string;
  evidence_asset_id: string;
  qualifies_for_attribution: boolean;
  attribution_review_reason: string | null;
}

export interface SalesActivityCreate {
  activity_type: SalesActivityType;
  occurred_at: string;
  customer_response_evidence_id?: string | null;
  activity_evidence_asset_id: string;
  needs_summary: string;
  next_action: string;
  next_action_due_at: string;
  outcome: SalesActivityOutcome;
}

export type NextActionType = "call" | "message" | "meeting" | "complete_documents" | "renewal_offer" | "upgrade_assessment" | "resolve_issue";

export interface NextActionRequest {
  owner_id: string;
  action_type: NextActionType;
  due_at: string;
  reason_code: string;
  note?: string;
}

export type ConversionType = "new_subscription" | "renewal" | "upgrade" | "reactivation";
export type ConversionState = "claimed" | "under_review" | "approved" | "rejected" | "disputed" | "reversed";

export interface Conversion {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  opportunity_id: string;
  contract_id: string;
  conversion_type: ConversionType;
  claimed_salesperson_id: string;
  approved_salesperson_id: string | null;
  state: ConversionState;
  evidence_activity_ids: string[];
  decision_at: string | null;
  finance_transaction_ref: string;
}

export interface ConversionClaim {
  application_id: string;
  contract_id: string;
  finance_transaction_ref: string;
  sales_activity_ids: string[];
  conversion_type: ConversionType;
  claimed_salesperson_id: string;
  reason_code: string;
}

export interface ConversionDecision {
  decision: "approve" | "reject" | "dispute";
  reason_code: string;
  approved_salesperson_id?: string | null;
  qualifying_activity_ids?: string[];
  causality_evidence_asset_id: string;
}

export type Worklist = "incomplete_application" | "pending_review" | "unpaid" | "renewal" | "upgrade" | "retention" | "reactivation" | "company_pool";
export type WorkItemState = "open" | "in_progress" | "completed" | "suppressed" | "cancelled";

export interface CrmWorkItem {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  opportunity_id: string | null;
  worklist: Worklist;
  owner_id: string | null;
  action_type: string;
  due_at: string;
  state: WorkItemState;
  reason_codes: string[];
  evidence_refs: string[];
  suggestion_origin: "rule" | "llm" | "human";
  suppression_applied: boolean;
}

export type SuppressionScope = "marketing_contact" | "sales_contact" | "all_nonessential_contact";
export type SuppressionReasonCategory = "opt_out" | "open_complaint" | "refund_pending" | "customer_request" | "restricted_process";

export interface Suppression {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string;
  scope: SuppressionScope;
  reason_category: SuppressionReasonCategory;
  active: boolean;
  expires_at: string | null;
}

export interface SuppressionRequest {
  customer_id: string;
  scope: SuppressionScope;
  reason_category: SuppressionReasonCategory;
  reason_ref: string;
  expires_at: string | null;
}

export type FunnelStage =
  | "lead" | "application_started" | "customer_signed" | "due_diligence" | "accepted"
  | "paid" | "active" | "renewal" | "upgrade" | "churn";

export interface FunnelMetric {
  stage: FunnelStage;
  count: number;
  cohort_key: string;
  service_code: string;
  period_start: string;
  period_end: string;
}

export interface FunnelMetrics {
  items: FunnelMetric[];
  as_of: string;
  attribution_policy_version: string;
  data_complete_through: string;
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
