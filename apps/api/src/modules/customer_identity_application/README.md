# M01 客戶身分與申請模組

此目錄實作 OpenAPI 1.0.0 的 13 個 M01 operationId。`domain.py` 與 `service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app 掛載的 FastAPI router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter。

關鍵控制：可信任 context 決定 tenant/actor、自有資源與 staff ABAC、CAS、payload-bound idempotency、maker-checker、不可逆合併 alias、append-only consent、無 PII 事件、audit/outbox 原子提交。OpenAPI `blocked/applied` 分別映射資料層 `restricted/executed`。

`automation.py` 只接受 keyed contact fingerprints／evidence UUID，保存 model/prompt/ruleset/source 版本並產生 `untrusted_draft`；它沒有合併命令權。沒有核准 LLM provider 時使用 deterministic local fallback，production 會拒絕 fake。

外部狀態：LegendFlow 真實 issuer/audience、證據物件、M02/M10/M11/M13 projections 均為 `NOT_CONNECTED`。本模組只有 adapter port 與 local fake；沒有修改或呼叫外部 repository／production endpoint。

整合者需提供：shared PostgreSQL repository/UoW、FastAPI auth context resolver、KMS/HMAC secret reference、object evidence adapter、metrics exporter，以及套用 `database/proposals/M01` 後的 migration 編號。共用 OpenAPI 尚未能在 `Customer` response 表達各 360 source 的 degraded/last-success 狀態，也沒有 identity revoke operation/event；模組提供 `get_customer_360()` 與 fail-closed internal revoke command，但 production route/topic 必須等 API/event steward 合併相容契約，不能私設隱藏端點。
