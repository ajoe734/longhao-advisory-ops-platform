# M01 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. `Customer` 可選 `source_summaries[]`（`source`、`source_status=available|degraded|stale`、`last_success_at`、`error_code`）；degraded 時 items 必須為 null，不能偽裝空集合。
2. 新增 `revoke_external_identity` staff command：`POST /customers/{customer_id}/identity-links/{identity_id}/revocation`，角色 `cms_supervisor`，需 If-Match、Idempotency-Key、reason 及 1–20 evidence asset IDs；response 仍用 `ExternalIdentity(status=revoked)`。
3. 註冊 `customer.identity_revoked` 1.0.0，payload 只含 customer_id、identity_id、provider、reason_code，不含 subject、email、phone 或 evidence 原文；M09 consumer 用來撤銷 channel binding。producer commit 必須與 revoke state/audit/outbox 原子化。
4. `Application` 新增相容的 nullable gate evidence 欄位：`customer_signature_id`、`kyc_submission_id`、`suitability_assessment_id`、`business_clearance_id`。名稱與 M02 `CompanyAcceptanceRequest` 相同，因此 UI-04 可使用 M01 已投影的紀錄 ID，不得要求核決人重打 UUID。M01 收 `application.customer_signed`（M02）、`kyc.decision_recorded`／`kyc.refresh_required`／`suitability.assessed`（M03）與 `business_clearance.changed`（M04）後，在同一 tenant 的 application read model 更新 gate record ID 與安全狀態；M01 不讀取 AML 限制內容。M01 對這些 partial upstream streams 以前進 aggregate version 投影，重送以 inbox event_id 去重，較舊 aggregate version 僅記錄為 stale，不回寫新狀態。`finance.payment_confirmed` 與 `finance.refund_confirmed` 不屬本次 gate-ID 範圍，`payment_status` 仍維持既有預設直到另案註冊 consumer。
5. 新增 `GET /applications`，僅 `cms_operator` 可用，支援 `state`、`customer_id`、`cursor`、`limit`、`as_of`；讀取範圍鎖定 tenant，不能因 `platform_admin` 角色放寬。回應為 `ApplicationPage`，使用 ID cursor 與固定 `as_of` snapshot，屬向下相容新增。API schema steward 應在整合時核對 shared operation catalog、RBAC matrix 與 generated client 是否需同步更新。
6. `ownership.assignment_changed` 加列 M01 consumer。active 指派會將同 tenant `customer.maintenance_owner_id` 投影為 salesperson；released/departure 清空，superseded 不覆寫。相同 assignment aggregate 依 aggregate version 忽略亂序舊事件，並以 inbox 去重重送。

在上述契約未合併前，模組內 revoke 只可由測試／受控 application service 使用，不掛公開 production route，也不自造 topic。真 LegendFlow contract 仍是 `NOT_CONNECTED`。
