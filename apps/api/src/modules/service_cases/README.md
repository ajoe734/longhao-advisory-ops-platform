# M13 客服客訴模組

此目錄實作 OpenAPI 1.0.0 的 14 個 M13 operationId。`domain.py` 與 `service.py`
保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app
掛載的 FastAPI router；`ports.py`／`automation.py` 的 fake 只接受合成 fixture，
production composition（`ServiceCaseService(..., environment="production")`）
會拒絕任何 `is_fake=True` adapter。

關鍵控制：

- 受理：以 Idempotency-Key payload fingerprint 去重，同一 provider message 重送
  只回原 interaction（不新增案件/互動）；customer principal 只能自助紀錄
  `direction=inbound` 互動，不能冒充內部備註或已送出回覆。
- 身份與資料遮罩：`get_service_case` 對 `customer_id is None`（未驗證/未連結）
  的案件一律將 `linked_contract_id`/`linked_publication_id`/`linked_refund_id`
  隱藏為 `null`，不等客戶完成驗證前不揭露契約或付款關聯。
- AI 草稿不可直接送出：`ResponseDraft.send_authorized` 恆為 `false`；
  `draft_service_response` 只傳入最小化欄位給 `DraftingAssistantPort`
  （category/intent/tone/knowledge_version/approved_source_refs），
  絕不傳入案件原文；AML 升級（`escalated_queue == "compliance"`）的案件完全
  不呼叫 assistant，草稿直接標記 `blocked`。`approve_service_response_draft`
  與 `send_service_response` 對 `contains_new_investment_judgment=True` 的草稿
  一律硬性拒絕（`new_investment_judgment_blocked`），不只是建議轉人工。
- Maker-checker：`approval_mode=human_reviewed` 核准者必須與起草者不同
  （`maker_checker_violation`）；送出時以 `draft_content_sha256` 與
  `approval_id` 綁定核准當下精確內容，不接受事後竄改草稿。
- 退款、投資建議、AML 無直接執行權：本模組沒有任何 refund approve/execute
  方法；退款與投資建議只能透過 `escalate_service_case` 轉交 finance/analyst
  佇列，實際核准與出金在 M14／M07/M08，由 `finance.refund_confirmed` 事件
  回填 `linked_refund_id` 投影。
- SLA 不被內部等待或轉派清零：`assign_service_case`／`escalate_service_case`
  都不觸碰 `first_response_due_at`/`resolution_due_at`；`maintenance_owner_id`
  只由 `ownership.assignment_changed` consumer 投影，不受
  `assign_service_case`（服務承辦人）或 `resolve_service_case` 影響，兩者分離
  對應 REQ-M13-012。
- 結案需要證據：`resolve_service_case` 缺 `customer_notice_evidence_id` 等必填
  欄位時回 422；成功結案會寫入一筆不可變 `CaseResolution` 記錄
  （對應 `support.case_resolved` 事件的 `resolution_record_id`），reopen 不會
  刪除或覆寫這筆歷史紀錄。
- 成功時狀態、audit 與 outbox 在同一交易原子提交；外部呼叫（客服回覆 provider、
  M10 suppression）只在提交後由 worker 處理，不在 command handler 內同步打外。

外部狀態：客服回覆通路 provider（web/line/email/sms）、M10 suppression intake、
M14 退款核准/出金、M07/M08 正式報告/更正流程，均為 `NOT_CONNECTED`。本模組只提供
adapter port 與 local fake（`FakeObjectAssetStore`/`FakeKnowledgeBase`/
`FakeChannelBindings`/`FakeSuppressionRequests`/`LocalDraftingAssistant`），
沒有修改或呼叫任何外部 repository／production endpoint。

整合者需提供：shared PostgreSQL repository/UoW、FastAPI auth context resolver、
真實客服回覆 provider adapter、真實 M10 suppression intake client、知識庫
approved-source 查詢服務、metrics exporter，以及套用
`database/proposals/M13/` 後的 migration 編號。`support.case_file` 基線缺少
`maintenance_owner_id`、`linked_refund_id`、`sla_paused_reason`、escalation 安全
link 欄位；`support.response_dispatch` 基線缺少 worker lease 欄位；
`support.case_resolution` 為新增子表以保留重開案件的原始結案歷史——詳見
`database/proposals/M13/README.md`。
