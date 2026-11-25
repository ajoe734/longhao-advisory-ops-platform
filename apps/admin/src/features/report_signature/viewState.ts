import { M07ApiError } from "./api";
import type { ViewState } from "./types";

export function failedState<T>(error: unknown, previous?: T): ViewState<T> {
  if (error instanceof M07ApiError) {
    if (error.problem.status === 403 || error.problem.status === 404) return { kind: "denied", correlationId: error.problem.correlation_id };
    if (error.problem.status === 412 && previous && error.problem.current_version) {
      return { kind: "conflict", value: previous, currentVersion: error.problem.current_version, message: "版本已變更，請重載並比較內容再操作。" };
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
