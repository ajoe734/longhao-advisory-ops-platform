import type { ApiProblem, Application, Customer, MergeCase, Page } from "./types";

export class M01ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M01Api {
  listCustomers(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<Customer>>;
  getCustomer(customerId: string): Promise<Versioned<Customer>>;
  updateCustomer(customerId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Customer>>;
  linkIdentity(customerId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Record<string, unknown>>>;
  createApplication(body: Record<string, unknown>, key: string): Promise<Versioned<Application>>;
  getApplication(applicationId: string): Promise<Versioned<Application>>;
  withdrawApplication(applicationId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Application>>;
  recordConsent(customerId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Record<string, unknown>>>;
  proposeMerge(body: Record<string, unknown>, key: string): Promise<Versioned<MergeCase>>;
  listMerges(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<MergeCase>>;
  getMerge(mergeCaseId: string): Promise<Versioned<MergeCase>>;
  decideMerge(mergeCaseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<MergeCase>>;
}

export class FetchM01Api implements M01Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listCustomers(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<Customer>>("/customers", params);
  }
  getCustomer(customerId: string) { return this.versioned<Customer>(`/customers/${encodeURIComponent(customerId)}`); }
  updateCustomer(customerId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Customer>("PATCH", `/customers/${encodeURIComponent(customerId)}`, body, key, etag);
  }
  linkIdentity(customerId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Record<string, unknown>>("POST", `/customers/${encodeURIComponent(customerId)}/identity-links`, body, key, etag);
  }
  createApplication(body: Record<string, unknown>, key: string) { return this.command<Application>("POST", "/applications", body, key); }
  getApplication(applicationId: string) { return this.versioned<Application>(`/applications/${encodeURIComponent(applicationId)}`); }
  withdrawApplication(applicationId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Application>("POST", `/applications/${encodeURIComponent(applicationId)}/withdrawal`, body, key, etag);
  }
  recordConsent(customerId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Record<string, unknown>>("POST", `/customers/${encodeURIComponent(customerId)}/consents`, body, key, etag);
  }
  proposeMerge(body: Record<string, unknown>, key: string) { return this.command<MergeCase>("POST", "/customer-merges", body, key); }
  listMerges(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<MergeCase>>("/customer-merges", params);
  }
  getMerge(mergeCaseId: string) { return this.versioned<MergeCase>(`/customer-merges/${encodeURIComponent(mergeCaseId)}`); }
  decideMerge(mergeCaseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<MergeCase>("POST", `/customer-merges/${encodeURIComponent(mergeCaseId)}/decision`, body, key, etag);
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
    if (!response.ok) throw new M01ApiError(body as ApiProblem);
    return body as T;
  }
}
