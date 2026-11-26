import type { ApiProblem, Page, ResearchCandidate } from "./types";

export class M05ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M05Api {
  listCandidates(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<ResearchCandidate>>;
  getCandidate(candidateId: string): Promise<Versioned<ResearchCandidate>>;
  rejectCandidate(candidateId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ResearchCandidate>>;
}

export class FetchM05Api implements M05Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listCandidates(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<ResearchCandidate>>("/research-candidates", params);
  }

  getCandidate(candidateId: string) {
    return this.versioned<ResearchCandidate>(`/research-candidates/${encodeURIComponent(candidateId)}`);
  }

  rejectCandidate(candidateId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ResearchCandidate>("POST", `/research-candidates/${encodeURIComponent(candidateId)}/rejection`, body, key, etag);
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
    if (!response.ok) throw new M05ApiError(body as ApiProblem);
    return body as T;
  }
}
