export type DeliveryState = "queued" | "accepted" | "sent" | "delivered" | "failed" | "expired" | "suppressed" | "unknown";
export type DeliveryChannel = "legendflow" | "line" | "sms" | "email";
export type ReadCapability = "unsupported" | "supported" | "unknown";

export interface Delivery {
  id: string;
  tenant_id: string;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  publication_id: string;
  customer_id: string;
  channel: DeliveryChannel;
  content_sha256: string;
  state: DeliveryState;
  provider_message_ref: string | null;
  attempt_count: number;
  accepted_at: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  read_capability: ReadCapability;
  last_entitlement_decision_id: string | null;
  last_error_code: string | null;
}

export interface CallbackReceipt {
  receipt_id: string;
  event_id: string;
  status: "accepted" | "duplicate" | "quarantined";
  received_at: string;
  correlation_id: string;
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
