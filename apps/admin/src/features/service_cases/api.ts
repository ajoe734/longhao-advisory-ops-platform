import type { ApiProblem, CaseInteraction, Page, ResponseDraft, ServiceCase, ServiceResponse, ServiceResponseApproval } from "./types";

export class M13ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M13Api {
  listCases(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<ServiceCase>>;
  getCase(caseId: string): Promise<Versioned<ServiceCase>>;
  createCase(body: Record<string, unknown>, key: string): Promise<Versioned<ServiceCase>>;
  addInteraction(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<CaseInteraction>>;
  assignCase(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ServiceCase>>;
  escalateCase(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ServiceCase>>;
  draftResponse(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ResponseDraft>>;
  getDraft(draftId: string): Promise<Versioned<ResponseDraft>>;
  approveDraft(draftId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ServiceResponseApproval>>;
  sendResponse(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ServiceResponse>>;
  getResponse(responseId: string): Promise<Versioned<ServiceResponse>>;
  resolveCase(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ServiceCase>>;
  reopenCase(caseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ServiceCase>>;
}

export class FetchM13Api implements M13Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listCases(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<ServiceCase>>("/service-cases", params);
  }
  getCase(caseId: string) { return this.versioned<ServiceCase>(`/service-cases/${encodeURIComponent(caseId)}`); }
  createCase(body: Record<string, unknown>, key: string) { return this.command<ServiceCase>("POST", "/service-cases", body, key); }
  addInteraction(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<CaseInteraction>("POST", `/service-cases/${encodeURIComponent(caseId)}/interactions`, body, key, etag);
  }
  assignCase(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ServiceCase>("POST", `/service-cases/${encodeURIComponent(caseId)}/assignment`, body, key, etag);
  }
  escalateCase(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ServiceCase>("POST", `/service-cases/${encodeURIComponent(caseId)}/escalations`, body, key, etag);
  }
  draftResponse(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ResponseDraft>("POST", `/service-cases/${encodeURIComponent(caseId)}/response-drafts`, body, key, etag);
  }
  getDraft(draftId: string) { return this.versioned<ResponseDraft>(`/service-response-drafts/${encodeURIComponent(draftId)}`); }
  approveDraft(draftId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ServiceResponseApproval>("POST", `/service-response-drafts/${encodeURIComponent(draftId)}/approval`, body, key, etag);
  }
  sendResponse(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ServiceResponse>("POST", `/service-cases/${encodeURIComponent(caseId)}/responses`, body, key, etag);
  }
  getResponse(responseId: string) { return this.versioned<ServiceResponse>(`/service-responses/${encodeURIComponent(responseId)}`); }
  resolveCase(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ServiceCase>("POST", `/service-cases/${encodeURIComponent(caseId)}/resolution`, body, key, etag);
  }
  reopenCase(caseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ServiceCase>("POST", `/service-cases/${encodeURIComponent(caseId)}/reopen`, body, key, etag);
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
    if (!response.ok) throw new M13ApiError(body as ApiProblem);
    return body as T;
  }
}
