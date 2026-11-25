export type KycSubmissionState =
  | "not_started" | "in_progress" | "submitted" | "incomplete" | "under_review" | "approved" | "rejected" | "refresh_required";
export type SuitabilityState = "pending" | "suitable" | "conditional" | "not_suitable" | "reassessment_due";
export type ScanStatus = "pending" | "clean" | "rejected";

export interface KycSubmission {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  application_id: string;
  customer_id: string;
  form_version: string;
  state: KycSubmissionState;
  answer_snapshot_asset_id: string | null;
  answer_sha256: string | null;
  document_ids: string[];
  submitted_at: string | null;
  reviewer_id: string | null;
  reviewed_at: string | null;
  next_review_due_at: string | null;
  missing_field_codes: string[];
}

export interface UploadTicket {
  asset_id: string;
  upload_url: string;
  expires_at: string;
  max_size_bytes: number;
  scan_required: true;
  classification: "customer_confidential" | "aml_restricted";
}

export interface DocumentStatus {
  asset_id: string;
  scan_status: ScanStatus;
  rejection_code: "malware" | "mime_mismatch" | "oversized" | "invalid_format" | null;
  scanned_at: string | null;
  classification: "customer_confidential" | "aml_restricted" | "research_confidential" | "internal";
}

export interface SuitabilityAssessment {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  application_id: string;
  customer_id: string;
  kyc_submission_id: string;
  questionnaire_version: string;
  rule_version: string;
  state: SuitabilityState;
  conditions: string[];
  assessed_by: string | null;
  assessed_at: string | null;
  next_review_due_at: string | null;
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
