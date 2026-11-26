import type { ApiProblem, AdvisoryReport, ReportVersion, ReviewDecision, SignatureSession, SignatureRevocation } from "./types";

export class M07ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M07Api {
  createReport(body: Record<string, unknown>, key: string): Promise<Versioned<AdvisoryReport>>;
  createReportVersion(reportId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ReportVersion>>;
  getReportVersion(reportVersionId: string): Promise<Versioned<ReportVersion>>;
  recordReview(reportVersionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ReviewDecision>>;
  freezeReportVersion(reportVersionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ReportVersion>>;
  requestSignature(reportVersionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<SignatureSession>>;
  revokeSignature(signatureId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<SignatureRevocation>>;
}

export class FetchM07Api implements M07Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  createReport(body: Record<string, unknown>, key: string) { return this.command<AdvisoryReport>("POST", "/reports", body, key); }
  createReportVersion(reportId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ReportVersion>("POST", `/reports/${encodeURIComponent(reportId)}/versions`, body, key, etag);
  }
  getReportVersion(reportVersionId: string) { return this.versioned<ReportVersion>(`/report-versions/${encodeURIComponent(reportVersionId)}`); }
  recordReview(reportVersionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ReviewDecision>("POST", `/report-versions/${encodeURIComponent(reportVersionId)}/reviews`, body, key, etag);
  }
  freezeReportVersion(reportVersionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ReportVersion>("POST", `/report-versions/${encodeURIComponent(reportVersionId)}/freeze`, body, key, etag);
  }
  requestSignature(reportVersionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<SignatureSession>("POST", `/report-versions/${encodeURIComponent(reportVersionId)}/signature`, body, key, etag);
  }
  revokeSignature(signatureId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<SignatureRevocation>("POST", `/signatures/${encodeURIComponent(signatureId)}/revocation`, body, key, etag);
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
    if (!response.ok) throw new M07ApiError(body as ApiProblem);
    return body as T;
  }
}
