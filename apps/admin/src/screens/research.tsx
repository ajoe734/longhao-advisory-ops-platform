// UI-11..UI-18: research candidates, review, report signature, publication, delivery (M05-M09).
import { useMemo, useState } from "react";
import { CandidateQueue, EvidenceInspector, FetchM05Api, failedState as m05Failed } from "../features/pantheon_intake";
import type { ResearchCandidate, ViewState as M05View } from "../features/pantheon_intake/types";
import { FetchM06Api, ReviewCaseWorkspaceView, ReviewQueue, failedState as m06Failed } from "../features/agora_review";
import type { ReviewCase, ReviewCaseWorkspaceModel, ViewState as M06View } from "../features/agora_review/types";
import { FetchM07Api, SignatureConfirmation, failedState as m07Failed } from "../features/report_signature";
import type { ReportVersion, ViewState as M07View } from "../features/report_signature/types";
import type { SigningCredential } from "../features/report_signature/SignatureConfirmation";
import { CorrectionWorkspace, FetchM08Api, PublicationMonitor, failedState as m08Failed } from "../features/publication";
import type { Page as M08Page, Publication, ViewState as M08View } from "../features/publication/types";
import { DeliveryMonitor, FetchM09Api, RedriveReconciliation, failedState as m09Failed } from "../features/legendflow_delivery";
import type { Delivery, Page as M09Page, ViewState as M09View } from "../features/legendflow_delivery/types";
import { attachSyntheticEvidence, idempotencyKey, navigate, routeParam, useViewState } from "../shell/runtime";
import { ActionError, ApiGap, IdLookup, describe, type ScreenProps } from "./common";

// ------------------------------------------------------------------- M05
export function CandidateQueueScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM05Api("/api/v1", fetcher), [fetcher]);
  const [filter, setFilter] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M05View<ResearchCandidate[]>>(
    () => api.listCandidates({ state: filter || undefined, limit: 50 }).then((page) => page.items.length
      ? { kind: "ready" as const, value: page.items } : { kind: "empty" as const, message: "沒有研究候選。" })
      .catch((failure) => m05Failed<ResearchCandidate[]>(failure)),
    { kind: "loading" }, [api, filter],
  );
  const reject = async (candidate: ResearchCandidate) => {
    try {
      const id = (candidate as unknown as { id: string }).id;
      const current = await api.getCandidate(id);
      await api.rejectCandidate(id, current.etag, { reason_code: "analyst_rejected" }, idempotencyKey("reject_research_candidate"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><CandidateQueue state={state} onSearch={(filters) => setFilter(filters.state)} onOpen={(id) => navigate(`/research/candidates/${id}`)} onReject={reject} /></>;
}

export function CandidateDetailScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM05Api("/api/v1", fetcher), [fetcher]);
  const [state] = useViewState<M05View<ResearchCandidate>>(
    () => id ? api.getCandidate(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m05Failed<ResearchCandidate>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="候選 ID" base="/research/candidates" />;
  return <EvidenceInspector state={state} />;
}

// ------------------------------------------------------------------- M06
export function ReviewQueueScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM06Api("/api/v1", fetcher), [fetcher]);
  const [filter, setFilter] = useState("");
  const [state] = useViewState<M06View<ReviewCase[]>>(
    () => api.listReviewCases({ state: filter || undefined, limit: 50 }).then((page) => page.items.length
      ? { kind: "ready" as const, value: page.items } : { kind: "empty" as const, message: "沒有覆核案件。" })
      .catch((failure) => m06Failed<ReviewCase[]>(failure)),
    { kind: "loading" }, [api, filter],
  );
  return <ReviewQueue state={state} onSearch={(filters) => setFilter(filters.state)} onOpen={(id) => navigate(`/reviews/${id}`)} />;
}

export function ReviewWorkspaceScreen({ path, template, fetcher, identity }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM06Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M06View<ReviewCaseWorkspaceModel>>(
    () => id ? api.getReviewCase(id).then((result) => ({
      kind: "ready" as const,
      value: { case: result.value, servant_health: { status: "unknown" }, conversations: [], revisions: [] } as unknown as ReviewCaseWorkspaceModel,
    })).catch((failure) => m06Failed<ReviewCaseWorkspaceModel>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="覆核案件 ID" base="/reviews" />;
  const assignToMe = async () => {
    try {
      const current = await api.getReviewCase(id);
      await api.assignReviewCase(id, current.etag, { assignee_id: identity?.actor_id, reason_code: "self_assigned" }, idempotencyKey("assign_review_case"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  const openConversation = async () => {
    try {
      const current = await api.getReviewCase(id);
      const value = current.value as unknown as { candidate_id: string; artifact_ref: Record<string, unknown> };
      await api.openServantConversation(id, current.etag, {
        candidate_id: value.candidate_id, artifact_ref: value.artifact_ref, existing_conversation_ref: null,
        allowed_evidence_refs: [], purpose: "review",
      }, idempotencyKey("open_servant_conversation"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return (
    <>
      <p><button onClick={assignToMe}>指派給我</button></p>
      <ActionError error={error} />
      <ReviewCaseWorkspaceView state={state} onOpenConversation={openConversation} onRequestRevision={() => setError("請先開啟 Servant 對話；修訂請求需引用對話 ID。")} onReload={reload} />
    </>
  );
}

// ------------------------------------------------------------------- M07
export function ReportVersionsScreen({ path, template }: ScreenProps) {
  const id = routeParam(path, template);
  return (
    <>
      <ApiGap>M07 沒有讀取報告或列出報告版本的 API，只能依報告版本 ID 開啟。</ApiGap>
      {id && <p>報告 ID：{id}</p>}
      <IdLookup label="報告版本 ID" base="/report-versions" hint="開啟後可在「簽章並自動發布」檢視版本與簽章。" />
    </>
  );
}

export function SignScreen({ path, template, fetcher, identity }: ScreenProps) {
  const id = routeParam(path.replace(/\/sign$/, "/sign"), template);
  const api = useMemo(() => new FetchM07Api("/api/v1", fetcher), [fetcher]);
  const [credentialId, setCredentialId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M07View<ReportVersion>>(
    () => id ? api.getReportVersion(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m07Failed<ReportVersion>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  if (!id) return <IdLookup label="報告版本 ID" base="/report-versions" hint="網址格式為 /report-versions/{id}/sign。" />;
  const signer: SigningCredential = {
    signerDisplayName: identity?.label ?? "未登入", credentialId: credentialId || "（請輸入資格憑證 ID）",
    qualificationStatus: credentialId ? "qualified" : "unknown",
  };
  const sign = async () => {
    try {
      const current = await api.getReportVersion(id);
      const value = current.value as unknown as { report_content_sha256: string; bundle_manifest_sha256: string; required_review_ids?: string[] };
      await api.requestSignature(id, current.etag, {
        report_content_sha256: value.report_content_sha256, bundle_manifest_sha256: value.bundle_manifest_sha256,
        credential_id: credentialId, required_review_ids: value.required_review_ids ?? [], signing_intent_confirmed: true,
      }, idempotencyKey("request_analyst_signature"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return (
    <>
      <section className="panel"><label>資格憑證 ID<input value={credentialId} onChange={(event) => setCredentialId(event.target.value)} /></label></section>
      <ActionError error={error} />
      <SignatureConfirmation state={state} signer={signer} onConfirmSign={sign} onReload={reload} />
    </>
  );
}

// ------------------------------------------------------------------- M08
export function PublicationScreen({ path, template, fetcher }: ScreenProps) {
  const id = routeParam(path, template);
  const api = useMemo(() => new FetchM08Api("/api/v1", fetcher), [fetcher]);
  const [error, setError] = useState<string | null>(null);
  const [list, reloadList] = useViewState<M08View<M08Page<Publication>>>(
    () => api.listPublications({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page }
      : { kind: "empty" as const, message: "尚無已簽章發布。" }).catch((failure) => m08Failed<M08Page<Publication>>(failure)),
    { kind: "loading" }, [api],
  );
  const [original, reload] = useViewState<M08View<Publication>>(
    () => id ? api.getPublication(id).then((result) => ({ kind: "ready" as const, value: result.value }))
      .catch((failure) => m08Failed<Publication>(failure)) : Promise.resolve({ kind: "empty", message: "" }),
    { kind: "loading" }, [api, id],
  );
  const withdraw = async (publication: Publication) => {
    try {
      const publicationId = (publication as unknown as { id: string }).id;
      const current = await api.getPublication(publicationId);
      const evidence = await attachSyntheticEvidence(fetcher, `withdrawal ${publicationId}`);
      await api.withdrawPublication(publicationId, current.etag, { reason_code: "analyst_withdrawal", evidence_asset_id: evidence, notify_recipients: true, notice_template_version: "withdrawal-notice-v1" }, idempotencyKey("withdraw_publication"));
      setError(null);
      reloadList();
    } catch (failure) { setError(describe(failure)); }
  };
  if (!id) {
    return <><ActionError error={error} /><PublicationMonitor state={list} onReload={reloadList} onWithdraw={withdraw} onLinkCorrection={(publication) => navigate(`/publications/${(publication as unknown as { id: string }).id}`)} /></>;
  }
  const correct = async (replacementPublicationId: string, replacementReportVersionId: string, reasonCode: string) => {
    try {
      const current = await api.getPublication(id);
      await api.linkCorrection(id, current.etag, { replacement_publication_id: replacementPublicationId, replacement_report_version_id: replacementReportVersionId, reason_code: reasonCode }, idempotencyKey("link_publication_correction"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return <><ActionError error={error} /><CorrectionWorkspace original={original} replacement={null} onReload={reload} onSubmit={correct} /></>;
}

// ------------------------------------------------------------------- M09
export function DeliveriesScreen({ fetcher }: ScreenProps) {
  const api = useMemo(() => new FetchM09Api("/api/v1", fetcher), [fetcher]);
  const [selected, setSelected] = useState<Delivery | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, reload] = useViewState<M09View<M09Page<Delivery>>>(
    () => api.listDeliveries({ limit: 50 }).then((page) => page.items.length ? { kind: "ready" as const, value: page }
      : { kind: "empty" as const, message: "尚無交付紀錄。" }).catch((failure) => m09Failed<M09Page<Delivery>>(failure)),
    { kind: "loading" }, [api],
  );
  const reconcile = async (reasonCode: string) => {
    if (!selected) return;
    try {
      const value = selected as unknown as { id: string; version: number };
      await api.reconcileDelivery(value.id, `"${value.version}"`, { reason_code: reasonCode }, idempotencyKey("reconcile_delivery"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  const redrive = async (reasonCode: string, approvedBy: string, expectedEventId: string) => {
    if (!selected) return;
    try {
      const value = selected as unknown as { dead_letter_id?: string; version: number };
      if (!value.dead_letter_id) throw new Error("此交付沒有 dead letter，可不需重送。");
      await api.redriveDeadLetter(value.dead_letter_id, `"${value.version}"`, { reason_code: reasonCode, approved_by: approvedBy, expected_event_id: expectedEventId, revalidate_entitlement: true, revalidate_content_expiry: true }, idempotencyKey("redrive_delivery_event"));
      setError(null);
      reload();
    } catch (failure) { setError(describe(failure)); }
  };
  return (
    <>
      <ActionError error={error} />
      <DeliveryMonitor state={state} onReload={reload} onSelect={setSelected} />
      {selected && <RedriveReconciliation state={{ kind: "ready", value: selected }} onReconcile={reconcile} onRedrive={redrive} />}
    </>
  );
}
