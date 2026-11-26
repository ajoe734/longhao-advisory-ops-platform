export type CandidateState = "received" | "validating" | "accepted" | "quarantined" | "rejected";
export type CandidateOrigin = "persona_autonomous" | "analyst_initiated";

export interface ExternalRef {
  system: string;
  external_id: string;
  kind: string;
  version?: string;
  checksum_sha256?: string;
}

export interface ResearchCandidate {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  external_event_id: string;
  origin: CandidateOrigin;
  artifact_ref: ExternalRef;
  artifact_content_sha256: string;
  evidence_bundle_refs: ExternalRef[];
  state: CandidateState;
  source_created_at: string;
  source_available_at: string;
  received_at: string;
  review_case_id: string | null;
  validation_failure_codes: string[];
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

// 對接健康面板使用的彙總；真實指標匯出尚未接線（NOT_CONNECTED），畫面只渲染已注入的快照。
export interface AdapterHealthSnapshot {
  source_latency_p95_ms: number | null;
  schema_rejection_count: number;
  duplicate_rate: number | null;
  dead_letter_count: number;
}
