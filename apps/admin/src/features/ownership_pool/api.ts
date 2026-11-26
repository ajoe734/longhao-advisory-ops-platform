import type { ApiProblem, DepartureReceipt, MaintenanceAssignment, Page, PoolEntry, PoolEvaluationResult } from "./types";

export class M11ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M11Api {
  listCompanyPool(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<PoolEntry>>;
  getOwnershipHistory(customerId: string, params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<MaintenanceAssignment>>;
  assignMaintenanceOwner(customerId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<MaintenanceAssignment>>;
  claimPoolCustomer(poolEntryId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<MaintenanceAssignment>>;
  releaseMaintenanceOwner(assignmentId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<PoolEntry>>;
  recordEmployeeDeparture(body: Record<string, unknown>, key: string): Promise<Versioned<DepartureReceipt>>;
  evaluateCompanyPool(body: Record<string, unknown>, key: string): Promise<Versioned<PoolEvaluationResult>>;
}

export class FetchM11Api implements M11Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listCompanyPool(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<PoolEntry>>("/ownership/company-pool", params);
  }
  getOwnershipHistory(customerId: string, params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<MaintenanceAssignment>>(`/customers/${encodeURIComponent(customerId)}/ownership-history`, params);
  }
  assignMaintenanceOwner(customerId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<MaintenanceAssignment>("POST", `/customers/${encodeURIComponent(customerId)}/maintenance-assignments`, body, key, etag);
  }
  claimPoolCustomer(poolEntryId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<MaintenanceAssignment>("POST", `/ownership/pool-entries/${encodeURIComponent(poolEntryId)}/claim`, body, key, etag);
  }
  releaseMaintenanceOwner(assignmentId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<PoolEntry>("POST", `/ownership/assignments/${encodeURIComponent(assignmentId)}/release`, body, key, etag);
  }
  recordEmployeeDeparture(body: Record<string, unknown>, key: string) {
    return this.command<DepartureReceipt>("POST", "/ownership/departures", body, key);
  }
  evaluateCompanyPool(body: Record<string, unknown>, key: string) {
    return this.command<PoolEvaluationResult>("POST", "/ownership/pool-evaluations", body, key);
  }

  private async query<T>(path: string, values: Record<string, unknown>): Promise<T> {
    const query = new URLSearchParams();
    Object.entries(values).forEach(([name, value]) => value !== undefined && query.set(name === "asOf" ? "as_of" : name, String(value)));
    const response = await this.fetcher(`${this.baseUrl}${path}${query.size ? `?${query}` : ""}`, { credentials: "same-origin" });
    return this.read<T>(response);
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
    if (!response.ok) throw new M11ApiError(body as ApiProblem);
    return body as T;
  }
}
