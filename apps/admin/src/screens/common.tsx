import { useState, type FormEvent, type ReactNode } from "react";
import { navigate, type DevIdentity } from "../shell/runtime";

export interface ScreenProps {
  path: string;
  template: string;
  fetcher: typeof fetch;
  identity: DevIdentity | null;
}

export type Screen = (props: ScreenProps) => ReactNode;

/** Detail screens reached without an id: look one up instead of showing an empty page. */
export function IdLookup({ label, base, hint }: { label: string; base: string; hint?: string }) {
  const [value, setValue] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (value.trim()) navigate(`${base}/${encodeURIComponent(value.trim())}`);
  };
  return (
    <section className="panel">
      <h3>輸入{label}</h3>
      {hint && <p>{hint}</p>}
      <form onSubmit={submit} className="lookup">
        <label>{label}<input value={value} onChange={(event) => setValue(event.target.value)} placeholder="UUID" /></label>
        <button type="submit">開啟</button>
      </form>
    </section>
  );
}

export function ApiGap({ children }: { children: ReactNode }) {
  return <p className="api-gap" role="note">API 缺口：{children}</p>;
}

export function ActionError({ error }: { error: string | null }) {
  return error ? <p role="alert" className="action-error">操作失敗：{error}</p> : null;
}

export function describe(error: unknown): string {
  if (error && typeof error === "object" && "problem" in error) {
    const problem = (error as { problem: { code?: string; detail?: string; correlation_id?: string } }).problem;
    return `${problem.code ?? "error"}${problem.detail ? `：${problem.detail}` : ""}${problem.correlation_id ? `（${problem.correlation_id}）` : ""}`;
  }
  return error instanceof Error ? error.message : String(error);
}
