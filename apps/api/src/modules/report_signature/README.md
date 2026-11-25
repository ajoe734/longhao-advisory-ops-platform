# M07 正式研究報告、覆核與分析師簽章模組

此目錄實作 OpenAPI 1.0.0 的 8 個 M07 operationId。`domain.py` 與 `service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app 掛載的 FastAPI router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter。

關鍵控制：actor/tenant/SoD 先驗（reviewer 不能覆核自己撰寫的版本）、CAS、payload-bound idempotency、report_content_sha256 與 bundle_manifest_sha256 分離驗證、frozen 包內容不可覆寫（修改一律建立新 `report_version`）、簽章 provider 回呼以 `signature_request_id` 去重且 tamper payload 一律 quarantine、撤銷只追加 `report.revocation` 證據並重發 `report.signature_revoked`、audit/outbox 原子提交。

`report.version_frozen` 由本模組同時 producer／consumer（自身投影一致性檢查）；`research.candidate_superseded`（M05）與 `review.revision_received`（M06，`stale=true`）消費後標記對應 `source_candidate_id` 為 superseded，`request_analyst_signature` 與 `freeze_report_version` 會拒絕沿用過期來源簽署（REQ-M07-007）。

`automation.py` 只接受已核定 channel 文案與雜湊、保存 model/prompt/ruleset 版本並產生 `untrusted_draft` 一致性檢查；它沒有簽章、覆核或發布命令權。沒有核准 LLM provider 時使用 deterministic local fallback，production 會拒絕 fake。

外部狀態：真實簽章 provider（憑證、MFA、精確檔案驗簽）、M05/M06 來源血緣、M08 建議／通路／受眾 manifest、M14 不可變物件儲存均為 `NOT_CONNECTED`。本模組只有 adapter port 與 local fake；沒有修改或呼叫外部 repository／production endpoint。

整合者需提供：shared PostgreSQL repository/UoW、FastAPI auth context resolver（含 webhook HMAC 驗證後把 `signature_adapter` service principal 交給 controller）、真實簽章 provider adapter、M14 物件儲存 adapter、metrics exporter，以及套用 `database/proposals/M07` 後的 migration 編號。
