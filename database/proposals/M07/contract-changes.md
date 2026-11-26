# M07 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. `ReportCreate`/`AdvisoryReport` 的 `report_type` 列舉已含 `correction`，但 `database/catalog.json`／`database/data-dictionary.md` 的 `report.report.report_type` 說明與 baseline CHECK 尚未同步；本模組的 `database/proposals/M07/001_report_signature.sql` 先在 DB 端補齊，請 steward 同步更新 catalog/dictionary 文字，避免下一位讀者誤判契約範圍。
2. `AdvisoryReport` 回應 schema 沒有 `preceding_report_id` 欄位（`ReportCreate` 請求可填，但回應 additionalProperties:false 排除它）。本模組目前只在內部模型保存、不在 `to_api()` 輸出；若之後需要在工作台顯示更正鏈，需先由 API steward 在 `AdvisoryReport` schema 新增此欄位，不在本模組私自多回傳未契約化欄位。
3. `ReportVersion` schema 沒有獨立的 `Signature` 子物件或 `get_signature` operation；簽章證據（provider ref、content/signed-file hash）目前只能透過 `ReportVersion.signature_id`／`signed_file_sha256` 間接取得。本模組 `service.py` 保留 `Signature.to_internal_api()` 作為管理介面內部投影，未對外公開為新 API；若未來需要獨立查詢，應由 API steward 評估新增 `get_signature` operationId。
4. `receive_analyst_signature` 的安全模型假設 HMAC webhook 驗證（`X-Webhook-Signature`／`X-Webhook-Timestamp`／`X-Webhook-Nonce`）已在平台邊界完成，本模組的 `ActorContext(principal_type="service", roles={"signature_adapter"})` 只代表「已通過邊界驗證」之後的信任身分；FastAPI context resolver 接線時必須先驗證 HMAC 才能建立這個 principal，不能把裸 webhook request 直接映射成 `signature_adapter`。

## 本模組已自行以 proposal 補齊、未要求 steward 動契約的事項

- `report.report_version.supersedes_version_id`、`required_review_ids`、`report.review_decision.reason_code`、`report.signature_request.redirect_url`/`last_receipt_id`、`report.revocation.publication_withdrawal_event_id` 均只是 DB 端補充欄位，不改變 OpenAPI/events 對外契約，已在 `001_report_signature.sql` 處理，不在此重複列為待辦。
