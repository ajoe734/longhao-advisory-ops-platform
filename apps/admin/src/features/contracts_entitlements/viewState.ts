import { M02ApiError } from "./api";
import type { ViewState } from "./types";

export function failedState<T>(error: unknown, previous?: T): ViewState<T> {
  if (error instanceof M02ApiError) {
    if (error.problem.status === 403 || error.problem.status === 404) return { kind: "denied", correlationId: error.problem.correlation_id };
    if (error.problem.status === 412 && previous && error.problem.current_version) {
      return { kind: "conflict", value: previous, currentVersion: error.problem.current_version, message: "資料已被其他人更新，請重載並檢視差異。" };
    }
    if (error.problem.status === 503) {
      return previous
        ? { kind: "stale", value: previous, message: "依賴服務暫時不可用，顯示的是上次成功結果。" }
        : { kind: "error", problem: error.problem };
    }
    return { kind: "error", problem: error.problem };
  }
  return {
    kind: "error",
    problem: { status: 500, code: "ui_unexpected_error", detail: "無法完成操作，請保留畫面並重試。", correlation_id: "unknown", retryable: true },
  };
}

export function isAllowed(allowedActions: string[], operationId: string): boolean {
  return allowedActions.includes(operationId);
}

export function newIdempotencyKey(prefix: string): string {
  return `${prefix}:${Date.now()}:${Math.random().toString(36).slice(2, 10)}`;
}
