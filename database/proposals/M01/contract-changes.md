# M01 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. `Customer` 可選 `source_summaries[]`（`source`、`source_status=available|degraded|stale`、`last_success_at`、`error_code`）；degraded 時 items 必須為 null，不能偽裝空集合。
2. 新增 `revoke_external_identity` staff command：`POST /customers/{customer_id}/identity-links/{identity_id}/revocation`，角色 `cms_supervisor`，需 If-Match、Idempotency-Key、reason 及 1–20 evidence asset IDs；response 仍用 `ExternalIdentity(status=revoked)`。
3. 註冊 `customer.identity_revoked` 1.0.0，payload 只含 customer_id、identity_id、provider、reason_code，不含 subject、email、phone 或 evidence 原文；M09 consumer 用來撤銷 channel binding。producer commit 必須與 revoke state/audit/outbox 原子化。
4. `Application` 的 `kyc_status`／`suitability_status`／`business_clearance`／`payment_status` 目前只由 M01 在建立申辦時寫入 schema 預設值；`events/registry.json` 中 M01 的 consumer 清單只有 `application.customer_signed`、`application.company_decided`（皆 M02）與 `ownership.employee_departed`（M11），沒有 M03（KYC/適合度決定）、M04（business clearance 決定）或 M14（付款）的事件可供 M01 消費。需要 M03/M04/M14 各自註冊對應 producer 事件（例如 `kyc.decision_recorded`、`suitability.assessed`、`business_clearance.decided`、`payment.settled`）並列 M01 為 consumer，M01 才能把這四欄從預設值投影為實際狀態；在那之前這四欄維持 OpenAPI schema 預設值，不自行查詢或臆測其他模組進度。對應 SQL 欄位已在 `001_customer_identity_application.sql` 以該預設值新增，待上述事件契約合併後由 M01 補 consumer 投影，不需要再改表結構。

在上述契約未合併前，模組內 revoke 只可由測試／受控 application service 使用，不掛公開 production route，也不自造 topic。真 LegendFlow contract 仍是 `NOT_CONNECTED`。
