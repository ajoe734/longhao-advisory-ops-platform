import type {
  ApiProblem,
  CommissionAdjustment,
  CommissionPolicy,
  CommissionPreview,
  CommissionRun,
  CommissionEntry,
  Page,
  PayoutBatch,
} from "./types";

export class M12ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M12Api {
  createCommissionPolicy(body: Record<string, unknown>, key: string): Promise<Versioned<CommissionPolicy>>;
  approveCommissionPolicy(policyId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<CommissionPolicy>>;
  previewCommissions(body: Record<string, unknown>, key: string): Promise<Versioned<CommissionPreview>>;
  createCommissionRun(body: Record<string, unknown>, key: string): Promise<Versioned<CommissionRun>>;
  getCommissionRun(runId: string): Promise<Versioned<CommissionRun>>;
  listCommissionEntries(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<CommissionEntry>>;
  approveCommissionRun(runId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<CommissionRun>>;
  createCommissionAdjustment(body: Record<string, unknown>, key: string): Promise<Versioned<CommissionAdjustment>>;
  createCommissionPayoutBatch(body: Record<string, unknown>, key: string): Promise<Versioned<PayoutBatch>>;
  getCommissionPayoutBatch(batchId: string): Promise<Versioned<PayoutBatch>>;
  approveCommissionPayoutBatch(batchId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<PayoutBatch>>;
  acknowledgeCommissionPayoutBatch(batchId: string, body: Record<string, unknown>, key: string): Promise<Versioned<PayoutBatch>>;
}

export class FetchM12Api implements M12Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  createCommissionPolicy(body: Record<string, unknown>, key: string) {
    return this.command<CommissionPolicy>("POST", "/commissions/policies", body, key);
  }
  approveCommissionPolicy(policyId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<CommissionPolicy>("POST", `/commissions/policies/${encodeURIComponent(policyId)}/approval`, body, key, etag);
  }
  previewCommissions(body: Record<string, unknown>, key: string) {
    return this.command<CommissionPreview>("POST", "/commissions/previews", body, key);
  }
  createCommissionRun(body: Record<string, unknown>, key: string) {
    return this.command<CommissionRun>("POST", "/commissions/runs", body, key);
  }
  getCommissionRun(runId: string) {
    return this.versioned<CommissionRun>(`/commissions/runs/${encodeURIComponent(runId)}`);
  }
  listCommissionEntries(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<CommissionEntry>>("/commissions/entries", params);
  }
  approveCommissionRun(runId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<CommissionRun>("POST", `/commissions/runs/${encodeURIComponent(runId)}/approval`, body, key, etag);
  }
  createCommissionAdjustment(body: Record<string, unknown>, key: string) {
    return this.command<CommissionAdjustment>("POST", "/commissions/adjustments", body, key);
  }
  createCommissionPayoutBatch(body: Record<string, unknown>, key: string) {
    return this.command<PayoutBatch>("POST", "/commissions/payout-batches", body, key);
  }
  getCommissionPayoutBatch(batchId: string) {
    return this.versioned<PayoutBatch>(`/commissions/payout-batches/${encodeURIComponent(batchId)}`);
  }
  approveCommissionPayoutBatch(batchId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<PayoutBatch>("POST", `/commissions/payout-batches/${encodeURIComponent(batchId)}/approval`, body, key, etag);
  }
  acknowledgeCommissionPayoutBatch(batchId: string, body: Record<string, unknown>, key: string) {
    return this.command<PayoutBatch>("POST", `/commissions/payout-batches/${encodeURIComponent(batchId)}/acknowledgement`, body, key);
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
    if (!response.ok) throw new M12ApiError(body as ApiProblem);
    return body as T;
  }
}
