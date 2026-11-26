import type { ApiProblem, CompanyAcceptance, Contract, ContractPackage, Entitlement, EntitlementDecision, Page, SignatureSession } from "./types";

export class M02ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M02Api {
  createContractPackage(body: Record<string, unknown>, key: string): Promise<Versioned<ContractPackage>>;
  approveContractPackage(packageVersionId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<ContractPackage>>;
  getContractPackage(packageVersionId: string): Promise<Versioned<ContractPackage>>;
  createCustomerSignature(applicationId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<SignatureSession>>;
  decideCompanyAcceptance(applicationId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<CompanyAcceptance>>;
  getContract(contractId: string): Promise<Versioned<Contract>>;
  createContractAmendment(contractId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Record<string, unknown>>>;
  terminateContract(contractId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Contract>>;
  listCustomerEntitlements(customerId: string, params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<Entitlement>>;
  evaluateEntitlements(contractId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<EntitlementDecision>>;
  suspendEntitlement(entitlementId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Entitlement>>;
}

export class FetchM02Api implements M02Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  createContractPackage(body: Record<string, unknown>, key: string) {
    return this.command<ContractPackage>("POST", "/contract-packages", body, key);
  }
  approveContractPackage(packageVersionId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<ContractPackage>("POST", `/contract-packages/${encodeURIComponent(packageVersionId)}/approval`, body, key, etag);
  }
  getContractPackage(packageVersionId: string) { return this.versioned<ContractPackage>(`/contract-packages/${encodeURIComponent(packageVersionId)}`); }
  createCustomerSignature(applicationId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<SignatureSession>("POST", `/applications/${encodeURIComponent(applicationId)}/customer-signature`, body, key, etag);
  }
  decideCompanyAcceptance(applicationId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<CompanyAcceptance>("POST", `/applications/${encodeURIComponent(applicationId)}/company-acceptance`, body, key, etag);
  }
  getContract(contractId: string) { return this.versioned<Contract>(`/contracts/${encodeURIComponent(contractId)}`); }
  createContractAmendment(contractId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Record<string, unknown>>("POST", `/contracts/${encodeURIComponent(contractId)}/amendments`, body, key, etag);
  }
  terminateContract(contractId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Contract>("POST", `/contracts/${encodeURIComponent(contractId)}/termination`, body, key, etag);
  }
  listCustomerEntitlements(customerId: string, params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<Entitlement>>(`/customers/${encodeURIComponent(customerId)}/entitlements`, params);
  }
  evaluateEntitlements(contractId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<EntitlementDecision>("POST", `/contracts/${encodeURIComponent(contractId)}/entitlement-evaluations`, body, key, etag);
  }
  suspendEntitlement(entitlementId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Entitlement>("POST", `/entitlements/${encodeURIComponent(entitlementId)}/suspension`, body, key, etag);
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
    if (!response.ok) throw new M02ApiError(body as ApiProblem);
    return body as T;
  }
}
