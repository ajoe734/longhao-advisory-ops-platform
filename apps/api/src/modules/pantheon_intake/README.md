# M05 Pantheon 候選接收模組

此目錄實作 OpenAPI 的 5 個 M05 operationId（`ingest_research_candidate`、`list_research_candidates`、`get_research_candidate`、`validate_research_candidate`、`reject_research_candidate`）。`domain.py`／`service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 與可掛載 FastAPI router；`ports.py`／`adapter.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter。

關鍵控制：

- `adapter.py` 的 `PantheonAdapter.translate()` 是本平台對 Pantheon 原生信封的版本閘；未知 `schema_version` 回 422 `unsupported_schema` 並保留安全 receipt（UAT-M05-008），不假設供應商已實作真實端點。
- `ports.py` 的 `WebhookSignaturePort` 驗 HMAC-SHA256 + timestamp skew + nonce 重放；`adapter.py` 的 `resolve_pantheon_adapter_context()` 是 composition-root 層的驗證點（簽章通過才核發 `pantheon_adapter` integration principal），不在 service 內解簽章。`SsrfSafeEvidenceFetcher` 只允許 allowlist 內 https host、拒絕私有/link-local/metadata 位址，且手動重新驗證每一次 redirect（UAT-M05-009）。
- `service.py` 以 `(tenant, producer, external_event_id)` 做來源去重（UAT-M05-003），以 `(tenant, producer, external_candidate_id)` 做 lineage head；同版本不同 hash 進 quarantine 且不覆寫原列（UAT-M05-004），新版本覆蓋舊 lineage head 並發 `research.candidate_superseded`（UAT-M05-011）。
- `validate_research_candidate` 只接受 worker 回報的 fetch/hash/evidence/license 結果，不自行對外發請求；`customer_distribution_denied` 是非阻斷性旗標，缺證據／hash 不符才會 quarantine（UAT-M05-005、UAT-M05-010）。
- trade/source/report/received 四個時間戳各自保存，不把 `source_committed_at` 設成 `source_occurred_at`（UAT-M05-007）；`received_at` 固定為伺服器收件時間，不接受 client 回填。
- `review_case_id` 由 `deterministic_review_case_id()` 在首次驗證/拒絕時指定；M05 從不寫 `review.case_file`（M06 擁有），只保證 ID 事先一致以免跨模組往返。

`automation.py` 只接受 minimized `candidate_id`/`validation_failure_codes`/`evidence_refs`，保存 model/prompt/ruleset/source 版本並產生 `untrusted_draft`；它沒有 validate/reject 命令權。沒有核准 LLM provider 時使用 deterministic local fallback，production 會拒絕 fake。

外部狀態：Pantheon／Agora／LegendFlow 真實端點、真實物件下載、M06 review case 投影均為 `NOT_CONNECTED`。本模組只有 adapter port 與 local fake；沒有修改或呼叫外部 repository／production endpoint。

整合者需提供：shared PostgreSQL repository/UoW、FastAPI auth context resolver、真實 HMAC key store、SSRF-safe HTTP 傳輸層、metrics exporter，以及套用 `database/proposals/M05` 後的 migration 編號。`database/catalog.json` 的 `research.candidate.origin` CHECK 仍是 `persona_autonomous`/`analyst_requested`，與 OpenAPI/事件的 `persona_autonomous`/`analyst_initiated` 不同字面值；`domain.py` 的 `origin_to_wire`/`origin_from_wire` 是唯一轉換點，兩端契約都不應被本模組私自改字面值。
