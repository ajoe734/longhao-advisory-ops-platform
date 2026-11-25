export type ReportState = "draft" | "active" | "withdrawn" | "archived";
export type ReportVersionState =
  | "draft" | "in_review" | "changes_requested" | "ready_to_sign"
  | "signature_pending" | "signed" | "superseded" | "withdrawn";
export type SignatureSessionState = "pending" | "verified" | "failed" | "expired";

export interface InstrumentRef {
  market: string;
  instrument_code: string;
  instrument_type: "equity" | "etf" | "fund" | "bond" | "other_approved";
}

export interface ChannelText {
  channel: "legendflow" | "line" | "sms" | "email";
  locale: "zh-TW";
  body: string;
  body_sha256: string;
  template_version: string;
  recommendation_version_ids: string[];
}

export interface AudiencePolicy {
  policy_id: string;
  policy_version: string;
  service_codes: string[];
  required_entitlement_state: "active";
  restriction_policy_version: string;
  audience_policy_sha256: string;
}

export interface AdvisoryReport {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  review_case_id: string;
  title: string;
  instrument_refs: InstrumentRef[];
  report_type: "initial" | "update" | "exit" | "supplement" | "correction";
  current_version_id: string | null;
  state: ReportState;
}

export interface ReportVersion {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  report_id: string;
  version_number: number;
  source_candidate_id: string;
  report_asset_id: string;
  report_content_sha256: string;
  bundle_manifest_asset_id: string;
  bundle_manifest_sha256: string;
  signed_file_sha256: string | null;
  state: ReportVersionState;
  channel_texts: ChannelText[];
  audience_policy: AudiencePolicy;
  valid_from: string;
  valid_until: string;
  frozen_at: string | null;
  signed_at: string | null;
  signature_id: string | null;
  required_review_ids: string[];
}

export interface ReviewDecision {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  report_version_id: string;
  review_type: "professional" | "compliance";
  decision: "approve" | "request_changes" | "reject";
  reviewer_id: string;
  reviewed_at: string;
  report_content_sha256: string;
  bundle_manifest_sha256: string;
  checklist_result_asset_id: string;
}

export interface SignatureSession {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  purpose: "customer_application" | "analyst_report";
  subject_id: string;
  provider_session_ref: string;
  redirect_url: string;
  expires_at: string;
  status: SignatureSessionState;
}

export interface SignatureRevocation {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  signature_id: string;
  reason_code: string;
  evidence_asset_id: string;
  effective_at: string;
  revoked_by: string;
  publication_withdrawal_event_id: string;
}

export interface ReportWorkbench {
  report: AdvisoryReport;
  versions: (ReportVersion & { reviews: ReviewDecision[] })[];
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
