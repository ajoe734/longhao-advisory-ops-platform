import type { ApiProblem, Page, Publication, RecommendationSeries, RecommendationVersion } from "./types";

export class M08ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M08Api {
  createSeries(body: Record<string, unknown>, key: string): Promise<Versioned<RecommendationSeries>>;
  createVersion(seriesId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<RecommendationVersion>>;
  listPublications(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<Publication>>;
  getPublication(publicationId: string): Promise<Versioned<Publication>>;
  withdrawPublication(publicationId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Publication>>;
  linkCorrection(publicationId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Publication>>;
}

export class FetchM08Api implements M08Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  createSeries(body: Record<string, unknown>, key: string) {
    return this.command<RecommendationSeries>("POST", "/recommendation-series", body, key);
  }
  createVersion(seriesId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<RecommendationVersion>("POST", `/recommendation-series/${encodeURIComponent(seriesId)}/versions`, body, key, etag);
  }
  listPublications(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<Publication>>("/publications", params);
  }
  getPublication(publicationId: string) {
    return this.versioned<Publication>(`/publications/${encodeURIComponent(publicationId)}`);
  }
  withdrawPublication(publicationId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Publication>("POST", `/publications/${encodeURIComponent(publicationId)}/withdrawal`, body, key, etag);
  }
  linkCorrection(publicationId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Publication>("POST", `/publications/${encodeURIComponent(publicationId)}/correction-links`, body, key, etag);
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
    if (!response.ok) throw new M08ApiError(body as ApiProblem);
    return body as T;
  }
}
