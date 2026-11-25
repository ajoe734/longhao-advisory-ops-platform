import rawScreens from "./registry.json";

export type ModuleState = "loaded" | "missing" | "broken";

export interface ModuleStatus {
  module_id: string;
  slug: string;
  title: string;
  status: ModuleState;
  error_code: string | null;
}

export interface Readiness {
  status: "ready" | "degraded" | "not_ready";
  environment: string;
  provider_mode: "synthetic" | "configured";
  module_count: number;
  loaded_modules: number;
  missing_module_count: number;
  broken_module_count: number;
  release_gate_passed: boolean;
  modules?: ModuleStatus[];
}

export interface ScreenDefinition {
  id: string;
  moduleId: string;
  group: string;
  path: string;
  title: string;
}

export const screens = rawScreens satisfies ScreenDefinition[];

if (screens.length !== 34) {
  throw new Error("The shared UI registry must contain all 34 contracted screens.");
}

export function currentPath(): string {
  const raw = window.location.hash.slice(1) || "/customers";
  return raw.length > 1 ? raw.replace(/\/$/, "") : raw;
}

function routePattern(template: string): RegExp {
  const escaped = template
    .split("/")
    .map((part) => (part.startsWith(":") ? "[^/]+" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))
    .join("/");
  return new RegExp(`^${escaped}$`);
}

export function matchScreen(path: string): ScreenDefinition | undefined {
  return screens.find((screen) => routePattern(screen.path).test(path));
}

export async function fetchReadiness(signal: AbortSignal): Promise<Readiness> {
  const response = await fetch("/health/ready", {headers: {Accept: "application/json"}, signal});
  const body = (await response.json()) as Readiness;
  if (!response.ok && response.status !== 503) {
    throw new Error("READINESS_REQUEST_FAILED");
  }
  return body;
}
