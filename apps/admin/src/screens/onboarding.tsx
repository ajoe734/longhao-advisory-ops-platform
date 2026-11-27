// UI-01..UI-10: customers, applications, contracts, KYC/suitability, AML (M01-M04).
import { useMemo, useState } from "react";
import { ApplicationTimeline, Customer360View, CustomerSearch, FetchM01Api, failedState as m01Failed } from "../features/customer_identity_application";
import type { Application, Customer, Customer360, ViewState as M01View } from "../features/customer_identity_application/types";
import { AcceptanceWorkbench, ContractEntitlementDetail, FetchM02Api, failedState as m02Failed } from "../features/contracts_entitlements";
import type { CompanyAcceptance, Contract, Entitlement, ViewState as M02View } from "../features/contracts_entitlements/types";
import type { GateSummary } from "../features/contracts_entitlements/AcceptanceWorkbench";
import { FetchM03Api, KycWorkbench, SuitabilityDetail, failedState as m03Failed } from "../features/kyc_suitability";
import type { KycSubmission, SuitabilityAssessment, ViewState as M03View } from "../features/kyc_suitability/types";
import { AmlCaseDetail, AmlCaseList, FetchM04Api, failedState as m04Failed } from "../features/aml_cft";
import type { AmlCase, Page as M04Page, ViewState as M04View } from "../features/aml_cft/types";
import { addDays, attachSyntheticEvidence, idempotencyKey, navigate, routeParam, useViewState } from "../shell/runtime";
import { ActionError, ApiGap, IdLookup, describe, type ScreenProps } from "./common";

// ------------------------------------------------------------------- M01
export function CustomerSearchScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM01Api("/api/v1", fetcher), [fetcher]);
  const [filters, setFilters] = useState({ customerNumber: "", name: "", phoneTail: "" });
  const [state] = useViewState<M01View<Customer[]>>(
    () => api.listCustomers({ limit: 50 }).then((page) => {
      const items = page.items.filter((row) => {
        const text = JSON.stringify(row);
        return (!filters.name || text.includes(filters.name)) && (!filters.phoneTail || text.includes(filters.phoneTail))
          && (!filters.customerNumber || text.includes(filters.customerNumber));
      });
      return items.length ? { kind: "ready" as const, value: items } : { kind: "empty" as const, message: "沒有符合條件的客戶。" };
    }).catch((error) => m01Failed<Customer[]>(error)),
    { kind: "loading" }, [api, filters],
  );
  return <CustomerSearch state={state} onSearch={setFilters} onOpen={(id) => navigate(`/customers/${id}`)} />;
}

export function Customer360Screen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM01Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M01View<Customer360>>(
    () => id ? api.getCustomer(id).then((result) => ({ kind: "ready" as const, value: { customer: result.value, sources: [] } }))
      .catch((failure) => m01Failed<Customer360>(failure)) : Promise.resolve({ kind: "empty", message: "請輸入客戶 ID。" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="客戶 ID" base="/customers" hint="也可以從「客戶搜尋與總覽」點選客戶。" />;
  const edit = async () => {
    const name = window.prompt("新的顯示名稱");
    if (!name) return;
    try {
      const current = await api.getCustomer(id);
      await api.updateCustomer(id, current.etag, { display_name: name }, idempotencyKey("update_customer"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><Customer360View state={state} onReload={reload} onEdit={edit} /></>;
}

export function ApplicationScreen({ fetcher }: ScreenProps) {
  // UI-03 is the application queue; M01 has no list API, so applications open by id.
  const [lookup, setLookup] = useState<string | null>(null);
  const applicationId = lookup;
  const api = useMemo(() => new FetchM01Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M01View<Application>>(
    () => applicationId ? api.getApplication(applicationId).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m01Failed<Application>(failure)) : Promise.resolve({ kind: "empty", message: "請輸入申請 ID。" }),
    { kind: "loading" }, [api, applicationId],
  );
  const withdraw = async () => {
    if (!applicationId) return;
    try {
      const current = await api.getApplication(applicationId);
      await api.withdrawApplication(applicationId, current.etag, { reason_code: "customer_request" }, idempotencyKey("withdraw_application"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return (
    <>
      <ApiGap>M01 沒有申請列表 API，佇列只能依申請 ID 查詢。</ApiGap>
      <ApplicationIdForm onOpen={setLookup} />
      <ActionError error={error} />
      {applicationId && <ApplicationTimeline state={state} onWithdraw={withdraw} onReload={reload} />}
    </>
  );
}

function ApplicationIdForm({ onOpen }: { onOpen: (id: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <form className="lookup" onSubmit={(event) => { event.preventDefault(); if (value.trim()) onOpen(value.trim()); }}>
      <label>申請 ID<input value={value} onChange={(event) => setValue(event.target.value)} placeholder="UUID" /></label>
      <button type="submit">查詢</button>
    </form>
  );
}

// ------------------------------------------------------------------- M02
function gatesFrom(application: Application | null): GateSummary {
  const record = (application ?? {}) as unknown as Record<string, string>;
  const status = (name: string): string => record[name] ?? "";
  const kyc = record.kyc_status === "approved" ? "approved" : record.kyc_status === "rejected" ? "rejected" : "pending";
  const suitability = (["suitable", "conditional", "not_suitable"].includes(status("suitability_status")) ? status("suitability_status") : "pending") as GateSummary["suitability"];
  const clearance = (["approved", "denied", "expired"].includes(status("business_clearance")) ? status("business_clearance") : "pending") as GateSummary["clearance"];
  const payment = (["not_required", "pending", "authorized", "paid", "refunded", "disputed"].includes(status("payment_status")) ? status("payment_status") : "pending") as GateSummary["payment"];
  return { kyc, suitability, clearance, payment };
}

export function AcceptanceScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const m01 = useMemo(() => new FetchM01Api("/api/v1", fetcher), [fetcher]);
  const m02 = useMemo(() => new FetchM02Api("/api/v1", fetcher), [fetcher]);
  const [application, setApplication] = useState<Application | null>(null);
  const [refs, setRefs] = useState({ kyc_submission_id: "", suitability_assessment_id: "", business_clearance_id: "", customer_signature_id: "" });
  const [error, setError] = useState<string | null>(null);
  const [state, reload, setState] = useViewState<M02View<CompanyAcceptance>>(
    () => id ? m01.getApplication(id).then((result) => {
      setApplication(result.value);
      return { kind: "empty" as const, message: `申請狀態：${(result.value as unknown as { state: string }).state}。尚無公司承諾紀錄。` };
    }).catch((failure) => m02Failed<CompanyAcceptance>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [m01, id],
  );
  if (!id) return <IdLookup label="申請 ID" base="/applications" />;
  const decide = async (decision: "accept" | "reject" | "request_changes", reasonCode: string) => {
    try {
      const current = await m01.getApplication(id);
      const value = current.value as unknown as { package_version_id: string };
      const body: Record<string, unknown> = { decision, reason_code: reasonCode, package_version_id: value.package_version_id, customer_signature_id: refs.customer_signature_id };
      if (decision === "accept") Object.assign(body, refs, { intended_effective_at: new Date().toISOString(), payment_condition_version: "payment-terms-2026.11" });
      const result = await m02.decideCompanyAcceptance(id, current.etag, body, idempotencyKey("decide_company_acceptance"));
      setState({ kind: "ready", value: result.value });
      setError(null);
    } catch (failure) { setError(describe(failure)); }
  };
  return (
    <>
      <ApiGap>申請 API 只提供 gate 狀態、不提供 KYC 送件／適合度評估／AML 放行／簽署的紀錄 ID，承諾決定所需的 ID 需由核決人輸入。</ApiGap>
      <section className="panel">
        <h3>承諾所需的紀錄</h3>
        {(Object.keys(refs) as (keyof typeof refs)[]).map((name) => (
          <label key={name}>{name}<input value={refs[name]} onChange={(event) => setRefs({ ...refs, [name]: event.target.value })} /></label>
        ))}
      </section>
      <ActionError error={error} />
      <AcceptanceWorkbench state={state} gates={gatesFrom(application)} onDecide={decide} onReload={reload} />
    </>
  );
}

export function ContractScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM02Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [contract, reload] = useViewState<M02View<Contract>>(
    () => id ? api.getContract(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m02Failed<Contract>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  const customerId = contract.kind === "ready" ? (contract.value as unknown as { customer_id: string }).customer_id : null;
  const [entitlements, reloadEntitlements] = useViewState<M02View<Entitlement[]>>(
    () => customerId ? api.listCustomerEntitlements(customerId).then((page) => page.items.length
      ? { kind: "ready" as const, value: page.items.filter((row) => (row as unknown as { contract_id: string }).contract_id === id) }
      : { kind: "empty" as const, message: "此契約尚無服務權限。" }).catch((failure) => m02Failed<Entitlement[]>(failure))
      : Promise.resolve({ kind: "loading" }),
    { kind: "loading" }, [api, customerId],
  );
  if (!id) return <IdLookup label="契約 ID" base="/contracts" />;
  const suspend = async (entitlementId: string, etag: string, reasonCode: string, evidenceRef: string) => {
    try {
      const evidence = evidenceRef || await attachSyntheticEvidence(fetcher, `suspension evidence ${entitlementId}`);
      await api.suspendEntitlement(entitlementId, etag, { reason_code: reasonCode, evidence_ref: evidence }, idempotencyKey("suspend_entitlement"));
      setError(null);
      reloadEntitlements();
    } catch (failure) { setError(describe(failure)); }
  };
  const terminate = async (reasonCode: string, evidenceId: string) => {
    try {
      const current = await api.getContract(id);
      const evidence = evidenceId || await attachSyntheticEvidence(fetcher, `termination request ${id}`, "customer_confidential");
      await api.terminateContract(id, current.etag, { requested_at: new Date().toISOString(), effective_at: addDays(30), reason_code: reasonCode, customer_request_evidence_id: evidence, refund_evaluation_required: true }, idempotencyKey("terminate_contract"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><ContractEntitlementDetail contractState={contract} entitlementsState={entitlements} onSuspend={suspend} onTerminate={terminate} onReload={() => { reload(); reloadEntitlements(); }} /></>;
}

export function EntitlementsScreen({ fetcher }: ScreenProps) {
  const m01 = useMemo(() => new FetchM01Api("/api/v1", fetcher), [fetcher]);
  const m02 = useMemo(() => new FetchM02Api("/api/v1", fetcher), [fetcher]);
  const [state] = useViewState<M02View<Entitlement[]>>(
    async () => {
      try {
        const customers = await m01.listCustomers({ limit: 50 });
        const rows: Entitlement[] = [];
        for (const customer of customers.items) {
          const page = await m02.listCustomerEntitlements((customer as unknown as { id: string }).id);
          rows.push(...page.items);
        }
        return rows.length ? { kind: "ready", value: rows } : { kind: "empty", message: "目前沒有任何服務權限。" };
      } catch (failure) { return m02Failed<Entitlement[]>(failure); }
    },
    { kind: "loading" }, [m01, m02],
  );
  return <ContractEntitlementDetail contractState={{ kind: "empty", message: "全客戶服務權限一覽（選擇契約可看單一契約）。" }} entitlementsState={state} onSuspend={() => undefined} onTerminate={() => undefined} onReload={() => undefined} />;
}

// ------------------------------------------------------------------- M03
export function KycScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM03Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M03View<KycSubmission>>(
    () => id ? api.getKycSubmission(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m03Failed<KycSubmission>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="KYC 送件 ID" base="/kyc/submissions" />;
  const documentIds = state.kind === "ready" ? ((state.value as unknown as { document_ids?: string[] }).document_ids ?? []) : [];
  const decide = async (decision: "approve" | "reject" | "request_information") => {
    try {
      const current = await api.getKycSubmission(id);
      const evidence = documentIds.length ? documentIds : [await attachSyntheticEvidence(fetcher, `kyc verification ${id}`, "customer_confidential")];
      await api.decideKyc(id, current.etag, {
        decision, identity_verification_method: "manual_document_review", verification_evidence_ids: evidence,
        reason_code: decision === "approve" ? "evidence_confirmed" : "information_insufficient",
        ...(decision === "approve" ? { next_review_due_at: addDays(365) } : {}),
      }, idempotencyKey("decide_kyc"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><KycWorkbench state={state} documents={[]} onReload={reload} onDecide={decide} /></>;
}

export function SuitabilityScreen({ path, template }: ScreenProps) {
  const id = routeParam(path, template);
  if (!id) return <IdLookup label="適合度評估 ID" base="/suitability/assessments" />;
  const state: M03View<SuitabilityAssessment> = { kind: "empty", message: "M03 沒有提供依 ID 讀取適合度評估的 API。" };
  return <><ApiGap>M03 只有建立適合度評估的 API，沒有讀取既有評估的 API。</ApiGap><SuitabilityDetail state={state} onReload={() => undefined} onReassess={() => undefined} /></>;
}

// ------------------------------------------------------------------- M04
export function AmlQueueScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM04Api("/api/v1", fetcher), [fetcher]);
  const [state, reload] = useViewState<M04View<M04Page<AmlCase>>>(
    () => api.listCases({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page }
      : { kind: "empty" as const, message: "目前沒有受限 AML 案件（篩檢結果為 clear 時不會開案）。" })
      .catch((failure) => m04Failed<M04Page<AmlCase>>(failure)),
    { kind: "loading" }, [api],
  );
  return <AmlCaseList state={state} onSelect={(id) => navigate(`/aml/cases/${id}`)} onReload={reload} />;
}

export function AmlCaseScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM04Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M04View<AmlCase>>(
    () => id ? api.getCase(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m04Failed<AmlCase>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="AML 案件 ID" base="/aml/cases" />;
  const act = async (kind: "edd" | "approve" | "deny", reasonCode: string) => {
    try {
      const current = await api.getCase(id);
      const note = await attachSyntheticEvidence(fetcher, `aml ${kind} ${id}`, "aml_restricted");
      if (kind === "edd") {
        await api.addCaseAction(id, current.etag, { action: "request_edd", reason_code: reasonCode, restricted_note_asset_id: note }, idempotencyKey("add_aml_case_action"));
      } else {
        await api.decideCase(id, current.etag, { decision: kind, risk_level: kind === "approve" ? "standard" : "high", reason_code: reasonCode, restricted_evidence_ids: [note] }, idempotencyKey("decide_aml_case"));
      }
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><AmlCaseDetail state={state} onRequestEdd={(code) => act("edd", code)} onApprove={(code) => act("approve", code)} onDeny={(code) => act("deny", code)} onReload={reload} /></>;
}
