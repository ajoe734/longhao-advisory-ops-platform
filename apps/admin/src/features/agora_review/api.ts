import type { ApiProblem, Conversation, Page, Revision, ReviewCase } from "./types";

export class M06ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M06Api {
  listReviewCases(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<ReviewCase>>;
  getReviewCase(reviewCaseId: string): Promise<Versioned<ReviewCase>>;
  assignReviewCase(reviewCaseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ReviewCase>>;
  openServantConversation(reviewCaseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Conversation>>;
  requestServantRevision(reviewCaseId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Revision>>;
  getRevisionRequest(revisionId: string): Promise<Versioned<Revision>>;
}

export class FetchM06Api implements M06Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listReviewCases(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<ReviewCase>>("/review-cases", params);
  }
  getReviewCase(reviewCaseId: string) {
    return this.versioned<ReviewCase>(`/review-cases/${encodeURIComponent(reviewCaseId)}`);
  }
  assignReviewCase(reviewCaseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ReviewCase>("POST", `/review-cases/${encodeURIComponent(reviewCaseId)}/assignment`, body, key, etag);
  }
  openServantConversation(reviewCaseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Conversation>("POST", `/review-cases/${encodeURIComponent(reviewCaseId)}/conversations`, body, key, etag);
  }
  requestServantRevision(reviewCaseId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Revision>("POST", `/review-cases/${encodeURIComponent(reviewCaseId)}/revision-requests`, body, key, etag);
  }
  getRevisionRequest(revisionId: string) {
    return this.versioned<Revision>(`/revision-requests/${encodeURIComponent(revisionId)}`);
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
    if (!response.ok) throw new M06ApiError(body as ApiProblem);
    return body as T;
  }
}
