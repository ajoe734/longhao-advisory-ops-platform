import type { ApiProblem, DocumentStatus, KycSubmission, SuitabilityAssessment, UploadTicket } from "./types";

export class M03ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M03Api {
  createKycSubmission(applicationId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<KycSubmission>>;
  requestKycUpload(submissionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<UploadTicket>>;
  submitKyc(submissionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<KycSubmission>>;
  getKycSubmission(submissionId: string): Promise<Versioned<KycSubmission>>;
  decideKyc(submissionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<KycSubmission>>;
  assessSuitability(applicationId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<SuitabilityAssessment>>;
  requestDueDiligenceRefresh(customerId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<KycSubmission>>;
  getDocumentStatus(assetId: string): Promise<Versioned<DocumentStatus>>;
}

export class FetchM03Api implements M03Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  createKycSubmission(applicationId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<KycSubmission>("POST", `/applications/${encodeURIComponent(applicationId)}/kyc-submissions`, body, key, etag);
  }
  requestKycUpload(submissionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<UploadTicket>("POST", `/kyc-submissions/${encodeURIComponent(submissionId)}/document-uploads`, body, key, etag);
  }
  submitKyc(submissionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<KycSubmission>("POST", `/kyc-submissions/${encodeURIComponent(submissionId)}/submit`, body, key, etag);
  }
  getKycSubmission(submissionId: string) {
    return this.versioned<KycSubmission>(`/kyc-submissions/${encodeURIComponent(submissionId)}`);
  }
  decideKyc(submissionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<KycSubmission>("POST", `/kyc-submissions/${encodeURIComponent(submissionId)}/decision`, body, key, etag);
  }
  assessSuitability(applicationId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<SuitabilityAssessment>("POST", `/applications/${encodeURIComponent(applicationId)}/suitability-assessments`, body, key, etag);
  }
  requestDueDiligenceRefresh(customerId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<KycSubmission>("POST", `/customers/${encodeURIComponent(customerId)}/due-diligence-refreshes`, body, key, etag);
  }
  getDocumentStatus(assetId: string) {
    return this.versioned<DocumentStatus>(`/documents/${encodeURIComponent(assetId)}/status`);
  }

  private async versioned<T>(path: string): Promise<Versioned<T>> {
    const response = await this.fetcher(`${this.baseUrl}${path}`, { credentials: "same-origin" });
    return { value: await this.read<T>(response), etag: response.headers.get("ETag") ?? "", replayed: false };
  }

  private async command<T>(method: string, path: string, body: Record<string, unknown>, key: string, etag: string): Promise<Versioned<T>> {
    const headers: Record<string, string> = { "Content-Type": "application/json", "Idempotency-Key": key, "If-Match": etag };
    const response = await this.fetcher(`${this.baseUrl}${path}`, { method, headers, body: JSON.stringify(body), credentials: "same-origin" });
    return {
      value: await this.read<T>(response),
      etag: response.headers.get("ETag") ?? "",
      replayed: response.headers.get("Idempotency-Replayed") === "true",
    };
  }

  private async read<T>(response: Response): Promise<T> {
    const body = await response.json();
    if (!response.ok) throw new M03ApiError(body as ApiProblem);
    return body as T;
  }
}
