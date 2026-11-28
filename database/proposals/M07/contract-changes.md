# M07 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. `ReportCreate`/`AdvisoryReport` 的 `report_type` 列舉已含 `correction`，但 `database/catalog.json`／`database/data-dictionary.md` 的 `report.report.report_type` 說明與 baseline CHECK 尚未同步；本模組的 `database/proposals/M07/001_report_signature.sql` 先在 DB 端補齊，請 steward 同步更新 catalog/dictionary 文字，避免下一位讀者誤判契約範圍。
2. `AdvisoryReport` 回應 schema 沒有 `preceding_report_id` 欄位（`ReportCreate` 請求可填，但回應 additionalProperties:false 排除它）。本模組目前只在內部模型保存、不在 `to_api()` 輸出；若之後需要在工作台顯示更正鏈，需先由 API steward 在 `AdvisoryReport` schema 新增此欄位，不在本模組私自多回傳未契約化欄位。
3. `ReportVersion` schema 沒有獨立的 `Signature` 子物件或 `get_signature` operation；簽章證據（provider ref、content/signed-file hash）目前只能透過 `ReportVersion.signature_id`／`signed_file_sha256` 間接取得。本模組 `service.py` 保留 `Signature.to_internal_api()` 作為管理介面內部投影，未對外公開為新 API；若未來需要獨立查詢，應由 API steward 評估新增 `get_signature` operationId。
4. `receive_analyst_signature` 的安全模型假設 HMAC webhook 驗證（`X-Webhook-Signature`／`X-Webhook-Timestamp`／`X-Webhook-Nonce`）已在平台邊界完成，本模組的 `ActorContext(principal_type="service", roles={"signature_adapter"})` 只代表「已通過邊界驗證」之後的信任身分；FastAPI context resolver 接線時必須先驗證 HMAC 才能建立這個 principal，不能把裸 webhook request 直接映射成 `signature_adapter`。
5. **GAP-M07-M08-VERSION-LINK（需 API schema steward review）**：M07 與 M08 都不能等待對方先產生 server ID。相容新增的建立順序改為：
   1. 呼叫端先產生 UUID `client_version_id`（不是服務端預先配置，也不從另一 tenant 重用）。
   2. M07 `create_report_version` 將該 UUID 放入既有 `recommendation_version_ids`，連同通路文字、受眾規則寫入不可變 manifest。
   3. M08 `create_recommendation_version` 帶相同 `client_version_id` 及 M07 回傳的 `report_version_id`；M08 以此 UUID 建立自己的 version，重複同 tenant UUID 回 `409 client_version_id_conflict`。
   4. 簽章完成後，M08 只接受 `report.signature_verified.payload.bundle_manifest_sha256` 作為權威 digest；其 adapter/fake 必須取得這個值，不得依 report ID 或 content hash 重新推導。

   既有未帶 `client_version_id` 的 M08 請求仍由服務端配置 ID，保持向下相容，但不能採用上述跨模組互相引用流程。M07 不嘗試直接寫入或驗證 M08 資料表；M08 建立時用其 tenant/ID 唯一性與 supporting report ID 驗證，維持模組資料 owner 邊界。

## 本模組已自行以 proposal 補齊、未要求 steward 動契約的事項

- `report.report_version.supersedes_version_id`、`required_review_ids`、`report.review_decision.reason_code`、`report.signature_request.redirect_url`/`last_receipt_id`、`report.revocation.publication_withdrawal_event_id` 均只是 DB 端補充欄位，不改變 OpenAPI/events 對外契約，已在 `001_report_signature.sql` 處理，不在此重複列為待辦。
- `GET /reports/{report_id}` 與 `GET /reports/{report_id}/versions` 是向下相容的 M07 read operation 新增；兩者沿用既有 `analyst`／`analyst_reviewer`／`compliance_reviewer` tenant-and-scope 檢查，並明確不授權 `platform_admin`。版本列表的 cursor 只可指向同一 report 的同 tenant 版本。

## GAP-M07-M08-VERSION-LINK 修復與驗證證據

| Finding／觸發情境 | 原始碼依據與修改位置 | 舊版定位 → 修正版結果 | 命令與結果 | 未驗項／限制 |
| --- | --- | --- | --- | --- |
| UI-15 讀取 report 或版本列表 | `api/openapi.yaml` 的 `get_report`／`list_report_versions`；`service.py` 同名 query；`http.py::ROUTES` | `d5b707d:.../http.py` 的 `ROUTES` 只有 `get_report_version`，沒有兩個 report read operation；新實作回傳 report、以同 report cursor 分頁，並拒絕 service principal（401）、`platform_admin`（403）和跨 tenant（404）。 | `python3 -m pytest tests/modules/report_signature -q`：56 passed（含 `test_report_read_and_version_list_preserve_existing_staff_and_tenant_boundaries`）。 | 是 in-memory repository／controller 驗證；真實 FastAPI auth resolver、PostgreSQL RLS 待 integration 環境。 |
| M07／M08 互相等待 server-generated version ID | `RecommendationVersionCreate.client_version_id`；`publication/service.py::create_recommendation_version`；兩份 `contract-changes.md` 的建立順序 | `44ce808:.../publication/service.py` 只以 `new_id()` 建議版本；新流程由呼叫端預先產生 UUID，M07 先凍結該 UUID，M08 以相同 UUID 建立版本並以 tenant 範圍回 `409 client_version_id_conflict` 拒絕重用。 | `python3 -m pytest tests/modules/publication -q`：48 passed（含 `test_client_supplied_recommendation_version_id_supports_prior_m07_manifest_link`）。 | M07/M08 仍以 adapter／fake 串接；外部 provider 尚未接線。 |
| M08 fake 重算 manifest digest，與 M07 已簽 manifest 不一致 | `publication/ports.py::FakeReportBundleRegistry.register`；`test_m07_contract_worker_http.py::test_verified_m07_manifest_digest_drives_m08_publication_with_client_stable_version_id` | `44ce808:.../ports.py::report_bundle_manifest_sha256` 從 tenant/report/content 重新計算；新 fake 強制呼叫端提供 M07 已驗簽 digest，並在 publish 時與 `report.signature_verified` payload 的同一值比對。 | `python3 -m pytest tests/modules/report_signature -q`：56 passed；跨模組測試從真 M07 outbox envelope 消費並成功發布。 | 所驗證的是本 repo fake/adapter contract；真 M07 provider adapter 為 `NOT_CONNECTED`。 |

同一輪亦執行 `python3 scripts/validate_contracts.py`（passed：142 operations、193 schemas）與
`python3 scripts/validate_docs.py`（passed：14 modules、142 operations、182 acceptance cases）。直接一次執行
`python3 -m pytest tests/modules/report_signature tests/modules/publication -q` 會因兩個既有同名 `conftest.py`
收集到錯誤的 module 而於 collection 失敗；上表所列分模組命令為本 task 規定且實際成功的驗證入口。
