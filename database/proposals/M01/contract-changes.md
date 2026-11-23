# M01 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. `Customer` 可選 `source_summaries[]`（`source`、`source_status=available|degraded|stale`、`last_success_at`、`error_code`）；degraded 時 items 必須為 null，不能偽裝空集合。
2. 新增 `revoke_external_identity` staff command：`POST /customers/{customer_id}/identity-links/{identity_id}/revocation`，角色 `cms_supervisor`，需 If-Match、Idempotency-Key、reason 及 1–20 evidence asset IDs；response 仍用 `ExternalIdentity(status=revoked)`。
3. 註冊 `customer.identity_revoked` 1.0.0，payload 只含 customer_id、identity_id、provider、reason_code，不含 subject、email、phone 或 evidence 原文；M09 consumer 用來撤銷 channel binding。producer commit 必須與 revoke state/audit/outbox 原子化。

在上述契約未合併前，模組內 revoke 只可由測試／受控 application service 使用，不掛公開 production route，也不自造 topic。真 LegendFlow contract 仍是 `NOT_CONNECTED`。
