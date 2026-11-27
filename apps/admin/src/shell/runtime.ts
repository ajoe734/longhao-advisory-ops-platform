// Shared admin-shell runtime: development identity, authorized fetch, async view state.
import { useCallback, useEffect, useRef, useState } from "react";

export interface DevIdentity {
  key: string;
  label: string;
  group: string;
  roles: string[];
  actor_type: "human" | "customer" | "service";
  actor_id: string;
  authorization: string;
}

const STORAGE_KEY = "longhao.admin.identity";

/** Development identities, or null when the backend does not offer them (production). */
export async function fetchDevIdentities(signal?: AbortSignal): Promise<DevIdentity[] | null> {
  const response = await fetch("/internal/platform/dev-identities", { headers: { Accept: "application/json" }, signal });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("DEV_IDENTITIES_UNAVAILABLE");
  const body = (await response.json()) as { items: DevIdentity[] };
  return body.items;
}

export function storedIdentityKey(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeIdentityKey(key: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, key);
  } catch {
    /* local convenience only */
  }
}

/** fetch that adds the selected identity's Authorization header to same-origin API calls. */
export function authorizedFetch(identity: DevIdentity | null): typeof fetch {
  return (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers ?? {});
    if (identity && !headers.has("Authorization")) headers.set("Authorization", identity.authorization);
    return fetch(input, { ...init, headers });
  };
}

export function idempotencyKey(operation: string): string {
  return `${operation}:${crypto.randomUUID()}`;
}

/** Development stand-in for an upload: registers synthetic evidence the modules will recognise. */
export async function attachSyntheticEvidence(fetcher: typeof fetch, label: string, classification = "internal"): Promise<string> {
  const response = await fetcher("/internal/platform/world/assets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, classification }),
  });
  if (!response.ok) throw new Error(`EVIDENCE_UPLOAD_FAILED_${response.status}`);
  return ((await response.json()) as { asset_id: string }).asset_id;
}

export function addDays(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString();
}

/**
 * Runs ``load`` and keeps its result as a view state. ``load`` must resolve to a
 * module view state (ready/empty/...) and map its own failures with the
 * module's failedState, so each screen keeps its module's exact types.
 */
export function useViewState<S>(load: () => Promise<S>, initial: S, deps: unknown[]): [S, () => void, (next: S) => void] {
  const [state, setState] = useState<S>(initial);
  const [tick, setTick] = useState(0);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    setState(initial);
    load().then((next) => { if (live.current) setState(next); });
    return () => { live.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((value) => value + 1), []);
  return [state, reload, setState];
}

/** Path parameter of the current hash route, e.g. "/customers/:id" -> id. */
export function routeParam(path: string, template: string): string | null {
  const parts = path.split("/");
  const names = template.split("/");
  const index = names.findIndex((part) => part.startsWith(":"));
  if (index < 0 || parts.length !== names.length) return null;
  const value = decodeURIComponent(parts[index] ?? "");
  return value && value !== "synthetic-id" ? value : null;
}

export function navigate(path: string): void {
  window.location.hash = path;
}
