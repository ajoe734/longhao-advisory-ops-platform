import type {
  ApiProblem,
  Conversion,
  ConversionClaim,
  ConversionDecision,
  CrmWorkItem,
  FunnelMetrics,
  NextActionRequest,
  Opportunity,
  OpportunityCreate,
  Page,
  SalesActivity,
  SalesActivityCreate,
  Suppression,
  SuppressionRequest,
} from "./types";

export class M10ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M10Api {
  listWorklists(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<CrmWorkItem>>;
  createOpportunity(body: OpportunityCreate, key: string): Promise<Versioned<Opportunity>>;
  listOpportunities(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<Opportunity>>;
  getOpportunity(opportunityId: string): Promise<Versioned<Opportunity>>;
  recordSalesActivity(opportunityId: string, etag: string, body: SalesActivityCreate, key: string): Promise<Versioned<SalesActivity>>;
  setNextAction(opportunityId: string, etag: string, body: NextActionRequest, key: string): Promise<Versioned<CrmWorkItem>>;
  claimConversion(opportunityId: string, etag: string, body: ConversionClaim, key: string): Promise<Versioned<Conversion>>;
  decideConversion(conversionId: string, etag: string, body: ConversionDecision, key: string): Promise<Versioned<Conversion>>;
  createSuppression(body: SuppressionRequest, key: string): Promise<Versioned<Suppression>>;
  getFunnelMetrics(): Promise<FunnelMetrics>;
}

export class FetchM10Api implements M10Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listWorklists(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<CrmWorkItem>>("/crm/worklists", params);
  }

  createOpportunity(body: OpportunityCreate, key: string) {
    return this.command<Opportunity>("POST", "/crm/opportunities", body, key);
  }

  listOpportunities(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<Opportunity>>("/crm/opportunities", params);
  }

  getOpportunity(opportunityId: string) {
    return this.command<Opportunity>("GET", `/crm/opportunities/${encodeURIComponent(opportunityId)}`, undefined, "");
  }

  recordSalesActivity(opportunityId: string, etag: string, body: SalesActivityCreate, key: string) {
    return this.command<SalesActivity>("POST", `/crm/opportunities/${encodeURIComponent(opportunityId)}/activities`, body, key, etag);
  }

  setNextAction(opportunityId: string, etag: string, body: NextActionRequest, key: string) {
    return this.command<CrmWorkItem>("POST", `/crm/opportunities/${encodeURIComponent(opportunityId)}/next-actions`, body, key, etag);
  }

  claimConversion(opportunityId: string, etag: string, body: ConversionClaim, key: string) {
    return this.command<Conversion>("POST", `/crm/opportunities/${encodeURIComponent(opportunityId)}/conversion-claims`, body, key, etag);
  }

  decideConversion(conversionId: string, etag: string, body: ConversionDecision, key: string) {
    return this.command<Conversion>("POST", `/crm/conversions/${encodeURIComponent(conversionId)}/decision`, body, key, etag);
  }

  createSuppression(body: SuppressionRequest, key: string) {
    return this.command<Suppression>("POST", "/crm/suppressions", body, key);
  }

  async getFunnelMetrics(): Promise<FunnelMetrics> {
    const response = await this.fetcher(`${this.baseUrl}/crm/funnel-metrics`, { credentials: "same-origin" });
    return this.read<FunnelMetrics>(response);
  }

  private async query<T>(path: string, values: Record<string, unknown>): Promise<T> {
    const query = new URLSearchParams();
    Object.entries(values).forEach(([name, value]) => value !== undefined && query.set(name === "asOf" ? "as_of" : name, String(value)));
    const response = await this.fetcher(`${this.baseUrl}${path}${query.size ? `?${query}` : ""}`, { credentials: "same-origin" });
    return this.read<T>(response);
  }

  private async command<T>(method: string, path: string, body: unknown, key: string, etag?: string): Promise<Versioned<T>> {
    const headers: Record<string, string> = {};
    if (method !== "GET") headers["Content-Type"] = "application/json";
    if (key) headers["Idempotency-Key"] = key;
    if (etag) headers["If-Match"] = etag;
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: method === "GET" ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    return {
      value: await this.read<T>(response),
      etag: response.headers.get("ETag") ?? "",
      replayed: response.headers.get("Idempotency-Replayed") === "true",
    };
  }

  private async read<T>(response: Response): Promise<T> {
    const body = await response.json();
    if (!response.ok) throw new M10ApiError(body as ApiProblem);
    return body as T;
  }
}
