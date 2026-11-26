export type CaseState = "new" | "triaged" | "assigned" | "waiting_customer" | "waiting_internal" | "resolved" | "closed" | "reopened";
export type CaseCategory = "application" | "contract" | "access" | "research_question" | "billing" | "refund" | "complaint" | "technical" | "other";
export type CasePriority = "normal" | "high" | "urgent";
export type IntakeChannel = "web" | "line" | "email" | "phone" | "onsite";
export type InteractionDirection = "inbound" | "outbound" | "internal";
export type DraftState = "queued" | "drafted" | "requires_human" | "blocked" | "failed";
export type DispatchState = "queued" | "accepted" | "sent" | "delivered" | "failed" | "unknown" | "cancelled" | "expired";
export type ApprovalMode = "human_reviewed" | "approved_low_risk_template";
export type ApprovalDecision = "approve" | "reject";
export type EscalationQueue = "analyst" | "finance" | "compliance" | "technical" | "service_supervisor";

export interface ServiceCase {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  customer_id: string | null;
  intake_channel: IntakeChannel;
  category: CaseCategory;
  subject: string;
  state: CaseState;
  priority: CasePriority;
  assignee_id: string | null;
  maintenance_owner_id: string | null;
  first_response_due_at: string;
  resolution_due_at: string | null;
  sla_paused_reason: string | null;
  linked_contract_id: string | null;
  linked_publication_id: string | null;
  linked_refund_id: string | null;
}

export interface CaseInteraction {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  case_id: string;
  channel: IntakeChannel;
  direction: InteractionDirection;
  occurred_at: string;
  recorded_by: string;
  content_asset_id: string;
  summary: string;
  publication_reference_ids: string[];
}

export interface ResponseDraft {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  case_id: string;
  state: DraftState;
  text: string | null;
  citations: string[];
  model_version: string;
  prompt_version: string;
  contains_new_investment_judgment: boolean;
  send_authorized: false;
}

export interface ServiceResponse {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  case_id: string;
  draft_id: string;
  content_sha256: string;
  channel_binding_id: string;
  channel: "web" | "line" | "email" | "sms";
  approval_mode: ApprovalMode;
  approval_id: string;
  state: DispatchState;
  provider_message_ref: string | null;
  accepted_at: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  valid_until: string;
  outbound_interaction_id: string | null;
}

export interface ServiceResponseApproval {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  draft_id: string;
  draft_content_sha256: string;
  decision: ApprovalDecision;
  approval_mode: ApprovalMode;
  reviewer_id: string;
  template_version: string;
  policy_version: string;
  evidence_asset_id: string;
  approved_at: string;
  valid_until: string;
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
