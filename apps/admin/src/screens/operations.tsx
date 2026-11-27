// UI-19..UI-34: CRM, ownership, commissions, service, finance (M10-M14) and platform screens.
import { useEffect, useMemo, useState } from "react";
import { FetchM10Api, FunnelDashboard, OpportunityDetail, SalesWorkbench, failedState as m10Failed } from "../features/crm";
import type { CrmWorkItem, FunnelMetrics, NextActionRequest, Opportunity, Page as M10Page, ViewState as M10View } from "../features/crm/types";
import { CompanyPoolWorkbench, FetchM11Api, MaintenanceTimeline, PolicyPreview, failedState as m11Failed } from "../features/ownership_pool";
import type { MaintenanceAssignment, PoolEntry, PoolEvaluationResult, ViewState as M11View } from "../features/ownership_pool/types";
import { CommissionWorkbench, EntriesView, FetchM12Api } from "../features/commissions";
import { AppealRefundPanel, CaseDetail, FetchM13Api, ServiceCaseQueue, failedState as m13Failed, type AppealRefundModel, type CaseDetailModel } from "../features/service_cases";
import type { Page as M13Page, ServiceCase, ViewState as M13View } from "../features/service_cases/types";
import { AuditEvidenceWorkbench, FetchM14Api, FinanceWorkbench, RefundApproval, failedState as m14Failed } from "../features/finance_control";
import type { AuditEventRow, EvidenceExport, Page as M14Page, ReconciliationItem, Refund, ViewState as M14View } from "../features/finance_control/types";
import { addDays, attachSyntheticEvidence, fetchDevIdentities, idempotencyKey, navigate, routeParam, useViewState, type DevIdentity } from "../shell/runtime";
import { ActionError, ApiGap, IdLookup, describe, type ScreenProps } from "./common";

const etagOf = (row: unknown) => `"${(row as { version?: number }).version ?? 1}"`;

// ------------------------------------------------------------------- M10
export function WorklistScreen({ fetcher, identity }: ScreenProps) {
  const api = useMemo(() => new FetchM10Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M10View<M10Page<CrmWorkItem>>>(
    () => api.listWorklists({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page }
      : { kind: "empty" as const, message: "工作清單沒有待辦。" }).catch((failure) => m10Failed<M10Page<CrmWorkItem>>(failure)),
    { kind: "loading" }, [api],
  );
  const assign = async (item: CrmWorkItem, body: NextActionRequest) => {
    try {
      const opportunityId = (item as unknown as { opportunity_id: string }).opportunity_id;
      const current = await api.getOpportunity(opportunityId);
      await api.setNextAction(opportunityId, current.etag, body, idempotencyKey("set_next_action"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); throw failure; }
  };
  return <><ActionError error={error} /><SalesWorkbench state={state} currentActorId={identity?.actor_id ?? ""} onReload={reload} onAssign={assign} /></>;
}

export function OpportunityScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM10Api("/api/v1", fetcher), [fetcher]);
  const [state, reload] = useViewState<M10View<Opportunity>>(
    () => id ? api.getOpportunity(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m10Failed<Opportunity>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="業務機會 ID" base="/crm/opportunities" hint="也可以從 CRM 工作清單開啟。" />;
  const withEtag = async (run: (etag: string) => Promise<unknown>) => {
    const current = await api.getOpportunity(id);
    await run(current.etag);
    reload();
  };
  return <OpportunityDetail state={state} conversion={null} onReload={reload}
    onRecordActivity={(body) => withEtag((etag) => api.recordSalesActivity(id, etag, body, idempotencyKey("record_sales_activity")))}
    onSetNextAction={(body) => withEtag((etag) => api.setNextAction(id, etag, body, idempotencyKey("set_next_action")))}
    onClaimConversion={(body) => withEtag((etag) => api.claimConversion(id, etag, body, idempotencyKey("claim_conversion")))}
    onDecideConversion={async () => undefined} />;
}

export function FunnelScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM10Api("/api/v1", fetcher), [fetcher]);
  const [state, reload] = useViewState<M10View<FunnelMetrics>>(
    () => api.getFunnelMetrics().then((value) => ({ kind: "ready" as const, value })).catch((failure) => m10Failed<FunnelMetrics>(failure)),
    { kind: "loading" }, [api],
  );
  return <FunnelDashboard state={state} onReload={reload} />;
}

// ------------------------------------------------------------------- M11
export function CompanyPoolScreen({ fetcher, identity }: ScreenProps) {
  const api = useMemo(() => new FetchM11Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<M11View<PoolEvaluationResult>>({ kind: "empty", message: "選擇政策版本與基準時間後試算。" });
  const [state, reload] = useViewState<M11View<PoolEntry[]>>(
    () => api.listCompanyPool({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page.items }
      : { kind: "empty" as const, message: "公司 Pool 目前沒有客戶。" }).catch((failure) => m11Failed<PoolEntry[]>(failure)),
    { kind: "loading" }, [api],
  );
  const claim = async (entry: PoolEntry, firstContactDueAt: string) => {
    try {
      await api.claimPoolCustomer((entry as unknown as { id: string }).id, etagOf(entry), { salesperson_id: identity?.actor_id, first_contact_due_at: firstContactDueAt, reason_code: "pool_claim" }, idempotencyKey("claim_pool_customer"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  const runPreview = async (policyVersion: string, asOf: string) => {
    try {
      const result = await api.evaluateCompanyPool({ as_of: asOf, policy_version: policyVersion, dry_run: true }, idempotencyKey("evaluate_company_pool"));
      setPreview({ kind: "ready", value: result.value });
    } catch (failure) { setPreview(m11Failed<PoolEvaluationResult>(failure)); }
  };
  return <><ActionError error={error} /><CompanyPoolWorkbench state={state} onClaim={claim} onReload={reload} /><PolicyPreview state={preview} onPreview={runPreview} /></>;
}

export function OwnershipTransfersScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM11Api("/api/v1", fetcher), [fetcher]);
  const [customerId, setCustomerId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M11View<MaintenanceAssignment[]>>(
    () => customerId ? api.getOwnershipHistory(customerId).then((page) => page.items.length ? { kind: "ready" as const, value: page.items }
      : { kind: "empty" as const, message: "此客戶沒有維護歸屬紀錄。" }).catch((failure) => m11Failed<MaintenanceAssignment[]>(failure))
      : Promise.resolve({ kind: "empty", message: "輸入客戶 ID 以檢視歸屬與交接。" }),
    { kind: "loading" }, [api, customerId],
  );
  const release = async (assignmentId: string, etag: string) => {
    try {
      await api.releaseMaintenanceOwner(assignmentId, etag, { reason_code: "handoff" }, idempotencyKey("release_maintenance_owner"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return (
    <>
      <form className="lookup" onSubmit={(event) => { event.preventDefault(); setCustomerId(String(new FormData(event.currentTarget).get("customer") ?? "").trim()); }}>
        <label>客戶 ID<input name="customer" placeholder="UUID" /></label><button type="submit">查詢</button>
      </form>
      <ActionError error={error} />
      <MaintenanceTimeline state={state} onRelease={release} onReload={reload} />
    </>
  );
}

// ------------------------------------------------------------------- M12
export function CommissionsScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM12Api("/api/v1", fetcher), [fetcher]);
  return <CommissionWorkbench api={api} />;
}

export function CommissionEntryScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM12Api("/api/v1", fetcher), [fetcher]);
  return <EntriesView api={api} focusEntryId={id ?? undefined} />;
}

// ------------------------------------------------------------------- M13
export function SupportQueueScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM13Api("/api/v1", fetcher), [fetcher]);
  const [state, reload] = useViewState<M13View<M13Page<ServiceCase>>>(
    () => api.listCases({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page }
      : { kind: "empty" as const, message: "沒有客服案件。" }).catch((failure) => m13Failed<M13Page<ServiceCase>>(failure)),
    { kind: "loading" }, [api],
  );
  return <ServiceCaseQueue state={state} onReload={reload} onOpenCase={(id) => navigate(`/support/${id}`)} />;
}

export function SupportCaseScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM13Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M13View<CaseDetailModel>>(
    () => id ? api.getCase(id).then((result) => ({ kind: "ready" as const, value: { case: result.value, interactions: [] } as CaseDetailModel }))
      .catch((failure) => m13Failed<CaseDetailModel>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="客服案件 ID" base="/support" />;
  const act = async (run: (etag: string) => Promise<unknown>) => {
    try {
      const current = await api.getCase(id);
      await run(current.etag);
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  const escalate = () => act(async (etag) => api.escalateCase(id, etag, { target_queue: "service_supervisor", reason_code: "needs_supervisor", due_at: addDays(1), minimum_necessary_context_asset_id: await attachSyntheticEvidence(fetcher, `escalation context ${id}`) }, idempotencyKey("escalate_service_case")));
  const resolve = () => act(async (etag) => api.resolveCase(id, etag, { resolution_code: "answered", resolution_summary: "已回覆客戶並確認處理完畢。", customer_notice_evidence_id: await attachSyntheticEvidence(fetcher, `resolution notice ${id}`) }, idempotencyKey("resolve_service_case")));
  const draft = () => act((etag) => api.draftResponse(id, etag, { knowledge_version: "kb-2026.11", approved_source_refs: [], intent: "status", requested_tone: "professional", model_policy_version: "local-drafting-v1" }, idempotencyKey("draft_service_response")));
  const panel: M13View<AppealRefundModel> = state.kind === "ready" ? { kind: "ready", value: { case: state.value.case, draft: null, response: null } } : { kind: "loading" };
  return <><ActionError error={error} /><CaseDetail state={state} onReload={reload} onEscalate={escalate} onResolve={resolve} onDraftResponse={draft} /><AppealRefundPanel state={panel} onReload={reload} onApproveDraft={() => setError("請在草稿產生後由不同客服人員核准。")} onSendResponse={() => setError("回覆需引用已核准的草稿。")} /></>;
}

// ------------------------------------------------------------------- M14
export function ReconciliationScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM14Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M14View<M14Page<ReconciliationItem>>>(
    () => api.listReconciliationItems({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page }
      : { kind: "empty" as const, message: "沒有待對帳項目（匯入清算檔後產生）。" }).catch((failure) => m14Failed<M14Page<ReconciliationItem>>(failure)),
    { kind: "loading" }, [api],
  );
  const resolve = async (itemId: string, decision: "match" | "correct_mapping" | "accept_timing" | "reject" | "escalate", reasonCode: string) => {
    try {
      const row = state.kind === "ready" ? state.value.items.find((item) => (item as unknown as { id: string }).id === itemId) : undefined;
      await api.resolveReconciliationItem(itemId, etagOf(row), { decision, reason_code: reasonCode, evidence_asset_id: await attachSyntheticEvidence(fetcher, `reconciliation ${itemId}`) }, idempotencyKey("resolve_reconciliation_item"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><FinanceWorkbench state={state} onResolve={resolve} onReload={reload} /></>;
}

export function RefundScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM14Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M14View<Refund>>(
    () => id ? api.getRefund(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m14Failed<Refund>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="退款 ID" base="/finance/refunds" />;
  const decide = async (decision: "approve" | "reject" | "request_changes", reasonCode: string) => {
    try {
      const current = await api.getRefund(id);
      await api.approveRefund(id, current.etag, { decision, reason_code: reasonCode }, idempotencyKey("approve_refund"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><RefundApproval state={state} onDecision={decide} onReload={reload} /></>;
}

function JsonRecord({ title, value }: { title: string; value: unknown }) {
  return <section className="panel"><h3>{title}</h3><pre className="json">{JSON.stringify(value, null, 2)}</pre></section>;
}

export function RevenueScreen({ fetcher }: ScreenProps) {
  return <FinanceRecordScreen fetcher={fetcher} label="收入認列批次 ID" gap="M14 沒有列出收入認列批次的 API，只能依批次 ID 查詢。" load={(api, id) => api.getRevenueRun(id)} />;
}

export function CloseScreen({ fetcher }: ScreenProps) {
  return <FinanceRecordScreen fetcher={fetcher} label="會計匯出 ID" gap="M14 沒有關帳流程或匯出列表 API，只能依會計匯出 ID 查詢。" load={(api, id) => api.getAccountingExport(id)} />;
}

function FinanceRecordScreen({ fetcher, label, gap, load }: { fetcher: typeof fetch; label: string; gap: string; load: (api: FetchM14Api, id: string) => Promise<{ value: unknown }> }) {
  const api = useMemo(() => new FetchM14Api("/api/v1", fetcher), [fetcher]);
  const [id, setId] = useState("");
  const [record, setRecord] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const open = async (value: string) => {
    setId(value);
    try { setRecord((await load(api, value)).value); setError(null); } catch (failure) { setRecord(null); setError(describe(failure)); }
  };
  return (
    <>
      <ApiGap>{gap}</ApiGap>
      <form className="lookup" onSubmit={(event) => { event.preventDefault(); void open(String(new FormData(event.currentTarget).get("id") ?? "").trim()); }}>
        <label>{label}<input name="id" placeholder="UUID" defaultValue={id} /></label><button type="submit">查詢</button>
      </form>
      <ActionError error={error} />
      {record !== null && <JsonRecord title={label} value={record} />}
    </>
  );
}

export function AuditEvidenceScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM14Api("/api/v1", fetcher), [fetcher]);
  const [exportState, setExportState] = useState<M14View<EvidenceExport> | null>(null);
  const [state, reload] = useViewState<M14View<M14Page<AuditEventRow>>>(
    () => api.listAuditEvents({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page }
      : { kind: "empty" as const, message: "沒有稽核事件。" }).catch((failure) => m14Failed<M14Page<AuditEventRow>>(failure)),
    { kind: "loading" }, [api],
  );
  const createExport = async (scope: string, subjectId: string, purpose: string) => {
    try {
      const approval = await attachSyntheticEvidence(fetcher, `evidence export approval ${subjectId}`);
      const result = await api.createEvidenceExport({ scope, subject_id: subjectId, purpose, include_restricted_aml: false, approval_record_id: approval, expires_at: addDays(7) }, idempotencyKey("create_evidence_export"));
      setExportState({ kind: "ready", value: result.value });
    } catch (failure) { setExportState(m14Failed<EvidenceExport>(failure)); }
  };
  return <AuditEvidenceWorkbench auditState={state} exportState={exportState} onCreateExport={createExport} onReload={reload} />;
}

// --------------------------------------------------------------- platform
export function AccessScreen(_props: ScreenProps) {
  const [identities, setIdentities] = useState<DevIdentity[] | null>(null);
  useEffect(() => { fetchDevIdentities().then(setIdentities).catch(() => setIdentities(null)); }, []);
  if (!identities) return <section className="panel"><p>此環境未提供開發身分；存取由正式身分提供者管理。</p></section>;
  const groups = [...new Set(identities.map((row) => row.group))];
  return (
    <section className="panel">
      <h3>開發用合成身分（{identities.length}）</h3>
      <p>右上角可切換身分；每個身分只具備表列角色，職責分離由各模組在伺服器端檢查。</p>
      {groups.map((group) => (
        <table key={group} className="data">
          <caption>{group}</caption>
          <thead><tr><th>身分</th><th>角色</th><th>類型</th></tr></thead>
          <tbody>{identities.filter((row) => row.group === group).map((row) => (
            <tr key={row.key}><td>{row.label}</td><td>{row.roles.join(", ")}</td><td>{row.actor_type}</td></tr>
          ))}</tbody>
        </table>
      ))}
    </section>
  );
}

interface WorldStatus {
  bound_modules: string[];
  workers: string[];
  relayed_events: number;
  pending_retries: { event_id: string; target: string; attempts: number }[];
  recent_deliveries: { event_type: string; producer: string; target: string; status: string; code: string; at: string }[];
  failures: { event_type: string | null; target: string; error: string }[];
}

export function OperationsScreen({ fetcher }: ScreenProps) {
  const [status, setStatus] = useState<WorldStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => fetcher("/internal/platform/world").then(async (response) => {
    if (!response.ok) throw new Error(response.status === 403 ? "需要平台管理或稽核角色" : `HTTP ${response.status}`);
    setStatus((await response.json()) as WorldStatus);
    setError(null);
  }).catch((failure: unknown) => setError(describe(failure)));
  useEffect(() => { void load(); }, [fetcher]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <section className="panel error" role="alert"><p>{error}</p></section>;
  if (!status) return <section className="panel" aria-busy="true"><p>載入中…</p></section>;
  return (
    <>
      <section className="panel">
        <h3>模組事件轉送（開發環境）</h3>
        <p>已轉送事件 {status.relayed_events}・背景作業 {status.workers.length}・等待重送 {status.pending_retries.length}・失敗 {status.failures.length}</p>
        <button onClick={() => void load()}>重新整理</button>
      </section>
      {status.failures.length > 0 && (
        <section className="panel error"><h3>失敗</h3><ul>{status.failures.map((row, index) => <li key={index}>{row.target}：{row.error}</li>)}</ul></section>
      )}
      <section className="panel">
        <h3>最近投遞</h3>
        <table className="data">
          <thead><tr><th>事件</th><th>來源</th><th>目的</th><th>結果</th><th>代碼</th></tr></thead>
          <tbody>{[...status.recent_deliveries].reverse().map((row, index) => (
            <tr key={index}><td>{row.event_type}</td><td>{row.producer}</td><td>{row.target}</td><td>{row.status}</td><td>{row.code}</td></tr>
          ))}</tbody>
        </table>
      </section>
      <section className="panel"><h3>背景作業</h3><p>{status.workers.join("、")}</p></section>
    </>
  );
}
