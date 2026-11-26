# M06 Agora 研究覆核工作台與 Servant 協作模組

此目錄實作 OpenAPI 1.0.0 的 7 個 M06 operationId：`list_review_cases`、`get_review_case`、`assign_review_case`、
`open_servant_conversation`、`request_servant_revision`、`receive_servant_revision`、`get_revision_request`。
`domain.py` 與 `service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app 掛載的
FastAPI router；`ports.py` 的 `FakeServant`／`FakeArtifactDiff` 只接受合成 fixture，production composition 會拒絕
fake adapter。

關鍵控制：可信任 context 決定 tenant/actor；分析師須為案件 `assignee_id` 才能開對話或送修訂，`research_supervisor`
才能指派；CAS（If-Match）比對先於業務前提；payload-bound idempotency；修正請求綁定 `base_artifact_ref` /
`base_content_sha256` 到目前候選版本；Servant 回呼只建立新 `RevisionResult`，不覆寫已審查的候選內容；webhook
`conversation_ref` 不符時整筆回呼被 `quarantined` 並留 security audit，不丟 500 也不套用內容；候選被取代
（`research.candidate_superseded`）時案件標 `stale`，撤銷任何視為 ready 的標記；AML/KYC 分類的證據種類
（見 `RESTRICTED_EVIDENCE_KINDS`）在送往 Servant 前即被拒絕。

`automation.py`（`AgoraDraftAutomationService`）只接受最小化差異點與證據 UUID，保存 model/prompt/ruleset 版本並
產生 `untrusted_draft`；`assert_no_privileged_capability()` 確保 LLM/automation 永遠沒有 sign/publish/payment/
entitlement 能力，沒有核准 provider 時使用 deterministic local fallback，production 會拒絕 fake。

`workers/tasks/agora_review/`：`M06EventConsumer` 消費 `research.candidate_validated`／`research.candidate_superseded`
（inbox hash/sequence dedupe，mirrors M01 pattern）；`ServantDispatchPublisher` + `OutboxPublisher` 把
`review.revision_requested` 轉為唯一一次外部 Servant job 送出（timeout 先查狀態，不重送出第二個 job），並把
`review.revision_received` 轉送到（目前 `NOT_CONNECTED` 的）M07 下游 bus。

外部狀態：Agora/Servant 對話與修訂 API、M05 `research.candidate` 即時查詢、M07 report-version 回填（
`current_report_version_id`）均為 `NOT_CONNECTED`。本模組只有 adapter port 與 local fake；沒有修改或呼叫外部
repository／production endpoint。

整合者需提供：shared PostgreSQL repository/UoW、FastAPI auth context resolver、真實 Agora/Servant adapter 與
webhook HMAC secret reference、metrics exporter，以及套用 `database/proposals/M06` 後的 migration 編號。共用
OpenAPI 的 `Revision` schema尚未能表達版本比較用的 `significant_diff`/`diff_summary`，也沒有指派
`professional_reviewer_id`/`compliance_review_required` 的命令；模組提供 `get_review_case_workspace()` 內部投影
補上前者，後兩個欄位維持安全預設值（`null`/`false`），直到 API steward 核准對應契約變更。
