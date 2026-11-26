export type ReviewCaseState =
  | "queued" | "assigned" | "under_review" | "revision_requested" | "ready_to_sign" | "signed" | "rejected" | "stale";
export type ConversationStatus = "opening" | "active" | "closed" | "failed";
export type RevisionState = "queued" | "running" | "completed" | "failed" | "stale" | "cancelled";

export interface ExternalRef {
  system: string;
  external_id: string;
  kind: string;
  version?: string | null;
  checksum_sha256?: string | null;
}

export interface ReviewCase {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  candidate_id: string;
  current_artifact_ref: ExternalRef;
  current_report_version_id: string | null;
  state: ReviewCaseState;
  assignee_id: string | null;
  professional_reviewer_id: string | null;
  compliance_review_required: boolean;
  pending_revision_ids: string[];
  source_version_stale: boolean;
}

export interface Conversation {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  review_case_id: string;
  external_conversation_ref: ExternalRef;
  scoped_artifact_ref: ExternalRef;
  status: ConversationStatus;
  audit_transcript_asset_id: string | null;
}

export interface Revision {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  review_case_id: string;
  conversation_id: string;
  base_artifact_ref: ExternalRef;
  state: RevisionState;
  result_artifact_ref: ExternalRef | null;
  result_content_sha256: string | null;
  requested_by: string;
  completed_at: string | null;
}

export interface ServantHealth {
  status: "available" | "degraded";
  last_success_at: string | null;
}

/** ``Revision`` plus the version-compare-only diff projection; not an OpenAPI schema. */
export interface WorkspaceRevision extends Revision {
  significant_diff: boolean;
  diff_summary: string[];
}

export interface ReviewCaseWorkspaceModel {
  case: ReviewCase;
  servant_health: ServantHealth;
  conversations: Conversation[];
  revisions: WorkspaceRevision[];
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
