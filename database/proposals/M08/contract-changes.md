# M08 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml`、`events/registry.json` 或 `database/README.md`，由 integration steward 合併。

## 1. OpenAPI request／response 既有缺口

`RecommendationVersionCreate.supporting_report_version_ids` 允許空陣列（`minItems: 0`），但
`RecommendationVersion` 回應 schema 要求至少 1 項（`minItems: 1`）。任務包第4節描述「尚未產生正式報告時，
draft 可先記 `source_candidate_id` 並保留空 `supporting_report_version_ids`」，但本次 6 個已發布
operationId 中沒有任何一個會回傳「空陣列且 state=draft」的 `RecommendationVersion`（沒有
`get_recommendation_version`/`list_recommendation_versions`），所以沒有安全的方式同時滿足「接受空陣列」
與「回應永遠符合 schema」。

本次實作選擇：`create_recommendation_version` 在業務層要求 `supporting_report_version_ids` 至少 1 項
（422 `supporting_report_version_required`），比 OpenAPI 的 `minItems: 0` 更嚴格但不違反它；純草稿流程
需要 API/Event steward 另外決定要新增 `create_recommendation_version_draft`（回應不含完整
`RecommendationVersion`）還是放寬 `RecommendationVersion.supporting_report_version_ids` 允許 0 項並補上
明確的 `draft` 語意。未核定前不自行假設答案。

## 2. API／資料表 mapper 新增列（併入 `database/README.md` 共用表）

| API／事件欄位 | 資料層 |
|---|---|
| RecommendationSeries `instrument_ref` | `recommendation_series.instrument_refs`（命名差異，非結構差異） |
| RecommendationVersion `action` | `recommendation_version.action_type`；DB CHECK 本次提案新增 `hold` |
| RecommendationVersion `supersedes_version_id` | `recommendation_version.corrects_version_id` |
| RecommendationVersion `rationale_asset_id` | `recommendation_version.rationale_asset_id`（本提案新增欄位，FK → `core.object_asset`） |
| RecommendationVersion `supporting_report_version_ids[0]` | `recommendation_version.report_version_id`（既有單一 FK，視為主要支持報告） |
| RecommendationVersion `supporting_report_version_ids[1:]` | `publication.recommendation_version_support`（本提案新增子表） |
| Publication `state` | `publication.publication.status` |
| Publication `valid_until` | `publication.publication.expires_at` |
| Publication `correction_publication_id` | `publication.publication.correction_of_id` |
| Publication `audience_snapshot_id` | `publication.publication.audience_snapshot_id`（本提案新增欄位） |
| Publication `created_from_signature_event_id` | `publication.publication.created_from_signature_event_id`（本提案新增欄位） |
| Publication `recipient_count` | `publication.publication.recipient_count`（本提案新增欄位，去正規化自 `audience_snapshot.recipient_count`） |
| Publication `recommendation_version_ids` | 目前實作永遠只填一個元素（`publication.publication.recommendation_version_id`）；OpenAPI 允許多元素是為未來多版次合併發布保留，本次未使用，待真正需求出現再評估是否需要 join table。 |

## 3. 事件 producer／consumer 責任（已在 `events/registry.json` 核定，本次僅確認映射）

`publication.created`／`publication.withdrawn`／`publication.corrected` 的 payload 欄位已於
`events/schemas/` 核定，本次 producer 嚴格依該 schema 填值，未新增欄位。`report.signature_verified`／
`report.signature_revoked`／`entitlement.changed`／`delivery.status_changed` 的 consumer 端僅讀取既有
payload；`delivery.status_changed` 本次只落 inbox 收據，未對 M08 自有投影做進一步處理（見
`apps/api/src/modules/publication/service.py::apply_delivery_status_changed` 註解），待 M09/M13 實際需要
時再定義具體投影契約，不在此預先捏造欄位。

## 4. 在上述契約未合併前

`publication.recommendation_version_support`、`research_origin_ref`、`rationale_asset_id`、
`source_candidate_id`、`audience_snapshot_id`、`created_from_signature_event_id`、`recipient_count`
只在 `database/proposals/M08/001_publication.sql` 提案中；本模組的 application service／repository 使用
自有記憶體模型（`apps/api/src/modules/publication/domain.py`），尚未接到真實 PostgreSQL。真正接線時需同
步核對本檔第 2 節的 mapper 對應,不得各自發明欄位名稱。
