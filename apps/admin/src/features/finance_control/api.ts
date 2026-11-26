import type {
  AccountingExport,
  ApiProblem,
  AuditEventRow,
  ControlException,
  EvidenceExport,
  LegalHold,
  Page,
  ReconciliationItem,
  Refund,
  RevenueRun,
} from "./types";

export class M14ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M14Api {
  listReconciliationItems(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<ReconciliationItem>>;
  resolveReconciliationItem(itemId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ReconciliationItem>>;
  getRevenueRun(runId: string): Promise<Versioned<RevenueRun>>;
  approveRevenueRun(runId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<RevenueRun>>;
  getRefund(refundId: string): Promise<Versioned<Refund>>;
  approveRefund(refundId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Refund>>;
  getAccountingExport(exportId: string): Promise<Versioned<AccountingExport>>;
  listAuditEvents(params?: { cursor?: string; limit?: number; asOf?: string }): Promise<Page<AuditEventRow>>;
  createEvidenceExport(body: Record<string, unknown>, key: string): Promise<Versioned<EvidenceExport>>;
  getEvidenceExport(exportId: string): Promise<Versioned<EvidenceExport>>;
  createLegalHold(body: Record<string, unknown>, key: string): Promise<Versioned<LegalHold>>;
  releaseLegalHold(holdId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<LegalHold>>;
  recordControlException(body: Record<string, unknown>, key: string): Promise<Versioned<ControlException>>;
  actOnControlException(exceptionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ControlException>>;
}

export class FetchM14Api implements M14Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listReconciliationItems(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<ReconciliationItem>>("/finance/reconciliation-items", params);
  }
  resolveReconciliationItem(itemId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ReconciliationItem>("POST", `/finance/reconciliation-items/${encodeURIComponent(itemId)}/resolution`, body, key, etag);
  }
  getRevenueRun(runId: string) { return this.versioned<RevenueRun>(`/finance/revenue-runs/${encodeURIComponent(runId)}`); }
  approveRevenueRun(runId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<RevenueRun>("POST", `/finance/revenue-runs/${encodeURIComponent(runId)}/approval`, body, key, etag);
  }
  getRefund(refundId: string) { return this.versioned<Refund>(`/finance/refunds/${encodeURIComponent(refundId)}`); }
  approveRefund(refundId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Refund>("POST", `/finance/refunds/${encodeURIComponent(refundId)}/approval`, body, key, etag);
  }
  getAccountingExport(exportId: string) { return this.versioned<AccountingExport>(`/finance/accounting-exports/${encodeURIComponent(exportId)}`); }
  listAuditEvents(params: { cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<AuditEventRow>>("/audit/events", params);
  }
  createEvidenceExport(body: Record<string, unknown>, key: string) { return this.command<EvidenceExport>("POST", "/audit/evidence-exports", body, key); }
  getEvidenceExport(exportId: string) { return this.versioned<EvidenceExport>(`/audit/evidence-exports/${encodeURIComponent(exportId)}`); }
  createLegalHold(body: Record<string, unknown>, key: string) { return this.command<LegalHold>("POST", "/audit/legal-holds", body, key); }
  releaseLegalHold(holdId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<LegalHold>("POST", `/audit/legal-holds/${encodeURIComponent(holdId)}/release`, body, key, etag);
  }
  recordControlException(body: Record<string, unknown>, key: string) { return this.command<ControlException>("POST", "/audit/control-exceptions", body, key); }
  actOnControlException(exceptionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ControlException>("POST", `/audit/control-exceptions/${encodeURIComponent(exceptionId)}/actions`, body, key, etag);
  }

  private async query<T>(path: string, values: Record<string, unknown>): Promise<T> {
    const query = new URLSearchParams();
    Object.entries(values).forEach(([name, value]) => value !== undefined && query.set(name === "asOf" ? "as_of" : name, String(value)));
    const response = await this.fetcher(`${this.baseUrl}${path}${query.size ? `?${query}` : ""}`, { credentials: "same-origin" });
    return this.read<T>(response);
  }

  private async versioned<T>(path: string): Promise<Versioned<T>> {
    const response = await this.fetcher(`${this.baseUrl}${path}`, { credentials: "same-origin" });
    return { value: await this.read<T>(response), etag: response.headers.get("ETag") ?? "", replayed: false };
  }

  private async command<T>(method: string, path: string, body: Record<string, unknown>, key: string, etag?: string): Promise<Versioned<T>> {
    const headers: Record<string, string> = { "Content-Type": "application/json", "Idempotency-Key": key };
    if (etag) headers["If-Match"] = etag;
    const response = await this.fetcher(`${this.baseUrl}${path}`, { method, headers, body: JSON.stringify(body), credentials: "same-origin" });
    return {
      value: await this.read<T>(response),
      etag: response.headers.get("ETag") ?? "",
      replayed: response.headers.get("Idempotency-Replayed") === "true",
    };
  }

  private async read<T>(response: Response): Promise<T> {
    const body = await response.json();
    if (!response.ok) throw new M14ApiError(body as ApiProblem);
    return body as T;
  }
}
