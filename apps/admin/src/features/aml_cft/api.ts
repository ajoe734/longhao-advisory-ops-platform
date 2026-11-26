import type { AmlCase, AmlDeclarationReceipt, AmlScreening, ApiProblem, BusinessClearance, Page } from "./types";

export class M04ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M04Api {
  submitDeclaration(applicationId: string, body: Record<string, unknown>, key: string, etag?: string): Promise<Versioned<AmlDeclarationReceipt>>;
  startScreening(body: Record<string, unknown>, key: string): Promise<Versioned<AmlScreening>>;
  listCases(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<AmlCase>>;
  getCase(caseId: string): Promise<Versioned<AmlCase>>;
  addCaseAction(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<AmlCase>>;
  decideCase(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<BusinessClearance>>;
  getBusinessClearance(applicationId: string): Promise<Versioned<BusinessClearance>>;
}

export class FetchM04Api implements M04Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  submitDeclaration(applicationId: string, body: Record<string, unknown>, key: string, etag?: string) {
    return this.command<AmlDeclarationReceipt>("POST", `/applications/${encodeURIComponent(applicationId)}/aml-declarations`, body, key, etag);
  }
  startScreening(body: Record<string, unknown>, key: string) {
    return this.command<AmlScreening>("POST", "/aml/screenings", body, key);
  }
  listCases(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<AmlCase>>("/aml/cases", params);
  }
  getCase(caseId: string) {
    return this.versioned<AmlCase>(`/aml/cases/${encodeURIComponent(caseId)}`);
  }
  addCaseAction(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<AmlCase>("POST", `/aml/cases/${encodeURIComponent(caseId)}/actions`, body, key, etag);
  }
  decideCase(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<BusinessClearance>("POST", `/aml/cases/${encodeURIComponent(caseId)}/decision`, body, key, etag);
  }
  getBusinessClearance(applicationId: string) {
    return this.versioned<BusinessClearance>(`/applications/${encodeURIComponent(applicationId)}/business-clearance`);
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
    if (!response.ok) throw new M04ApiError(body as ApiProblem);
    return body as T;
  }
}
