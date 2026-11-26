export type SeriesState = "open" | "closed" | "withdrawn";
export type VersionState = "draft" | "under_review" | "signed" | "expired" | "withdrawn";
export type PublicationState = "queued" | "publishing" | "published" | "partially_failed" | "expired" | "corrected" | "withdrawn";

export interface InstrumentRef {
  market: string;
  instrument_code: string;
  instrument_type: "equity" | "etf" | "fund" | "bond" | "other_approved";
}

export interface RecommendationSeries {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  instrument_ref: InstrumentRef;
  strategy_scope_ref: string;
  state: SeriesState;
  latest_version_id: string | null;
}

export interface RecommendationVersion {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  series_id: string;
  version_number: number;
  action: "entry" | "update" | "exit" | "hold" | "correction";
  rationale_asset_id: string;
  supporting_report_version_ids: string[];
  state: VersionState;
  valid_from: string;
  valid_until: string;
  supersedes_version_id: string | null;
}

export interface Publication {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  report_version_id: string;
  signature_id: string;
  recommendation_version_ids: string[];
  bundle_manifest_sha256: string;
  state: PublicationState;
  created_from_signature_event_id: string;
  audience_snapshot_id: string;
  recipient_count: number;
  published_at: string | null;
  valid_until: string;
  correction_publication_id: string | null;
  withdrawal_reason_code: string | null;
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
