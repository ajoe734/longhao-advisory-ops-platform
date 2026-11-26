import type { ApiProblem, CallbackReceipt, Delivery, Page } from "./types";

export class M09ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail);
  }
}

export interface Versioned<T> {
  value: T;
  etag: string;
  replayed: boolean;
}

export interface M09Api {
  listDeliveries(params?: { state?: string; cursor?: string; limit?: number; asOf?: string }): Promise<Page<Delivery>>;
  reconcileDelivery(deliveryId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<Delivery>>;
  redriveDeadLetter(deadLetterId: string, etag: string, body: Record<string, unknown>, key: string): Promise<Versioned<CallbackReceipt>>;
}

export class FetchM09Api implements M09Api {
  constructor(private readonly baseUrl = "/api/v1", private readonly fetcher: typeof fetch = fetch) {}

  listDeliveries(params: { state?: string; cursor?: string; limit?: number; asOf?: string } = {}) {
    return this.query<Page<Delivery>>("/deliveries", params);
  }

  reconcileDelivery(deliveryId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<Delivery>("POST", `/deliveries/${encodeURIComponent(deliveryId)}/reconciliation`, body, key, etag);
  }

  redriveDeadLetter(deadLetterId: string, etag: string, body: Record<string, unknown>, key: string) {
    return this.command<CallbackReceipt>("POST", `/dead-letters/${encodeURIComponent(deadLetterId)}/redrive`, body, key, etag);
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
    if (!response.ok) throw new M09ApiError(body as ApiProblem);
    return body as T;
  }
}
