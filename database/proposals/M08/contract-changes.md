# M08 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml`、`events/registry.json` 或 `database/README.md`，由 integration steward 合併。

## 1. M07/M08 循環版本引用的相容建立順序

**GAP-M07-M08-VERSION-LINK；需 API schema steward review。** `RecommendationVersionCreate` 相容新增
可選 UUID `client_version_id`。它解開 M07 與 M08 都需要另一方 server ID 的循環，而不放寬 M08 已有的
`supporting_report_version_ids` 至少一項規則：

1. 呼叫端先產生 `client_version_id`。
2. M07 `create_report_version` 在既有 `recommendation_version_ids` 填入這個 UUID，建立完整、不可變的
   manifest；M07 只保存該參照，絕不跨模組寫 M08 資料。
3. 呼叫端收到 M07 `report_version_id` 後，呼叫 M08 `create_recommendation_version`，同時帶
   `client_version_id` 和該 `supporting_report_version_id`。M08 將 client UUID 作為自己的 version ID，並以
   tenant 範圍唯一性拒絕重複值（`409 client_version_id_conflict`）。
4. M07 驗簽事件的 `bundle_manifest_sha256` 是唯一發布依據。M08 adapter/fake 必須由
   `report.signature_verified` 的簽章結果登錄原值；不可由 tenant、report ID 或 content hash 推導另一個 digest。

既有請求省略 `client_version_id` 時仍由 M08 產生 ID，保持相容，但不能用在上述循環關聯流程。此變更也取代了
先前「接受空 supporting reports 的 draft operation」待決方案：目前沒有對應 draft read/write operation，故不以
空集合繞過已簽報告依據要求。

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
| Publication `correction_publication_id` | 不是同一列的直接欄位映射。`correction_of_id` 命名語意是「這列更正了誰」，所以要存在**替代（新）那則** publication 列上，指回被取代的原列（`replacement.correction_of_id = original.id`），寫入時機是 `link_publication_correction` 呼叫、在替代列已存在之後的一次 UPDATE（見下方 `immutable_publication` trigger 調整）。API 回應中**原列**的 `correction_publication_id`（指向替代列，方向相反）是查詢時反查 `SELECT id FROM publication.publication WHERE correction_of_id = :original_id` 算出來的，不是原列自己的欄位，避免兩個方向各存一個欄位又要保持一致。上一版提案把 API 欄位直接映射到同一列的 `correction_of_id` 方向是反的，已更正。 |
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

## 4. 與 `0003_business_guards.sql` 既有 trigger 的相容性

- `m08_publication_audience_snapshot_fk`（`publication.publication.audience_snapshot_id` →
  `publication.audience_snapshot.id`）宣告為 `DEFERRABLE INITIALLY DEFERRED`：`publication`／
  `audience_snapshot` 互相參照（`audience_snapshot.publication_id` 已是 baseline 的
  `DEFERRABLE INITIALLY IMMEDIATE`），真正接線時兩列要在同一 transaction 內插入，任一方向都需要
  deferred 檢查才不會在交易中途卡住。`audience_snapshot_id`／`recipient_count` 必須在單一 INSERT 之前算好
  再寫入，不透過事後 UPDATE 設定；因此兩者都不需要、也沒有放進下面的 `immutable_publication` 白名單。
- `001_publication.sql` 重新建立 `immutable_publication` trigger，在 baseline 的
  `{status,published_at,withdrawal_reason_code}` 之外多加 `correction_of_id`：這是本模組唯一一個天生要
  在列已存在很久之後才 UPDATE 的欄位（更正關聯建立時，替代列早已是已發布的獨立列），baseline 名單不處理
  就會被 `0003_business_guards.sql:27-28` 的 `ERRCODE='23514'` 擋下來。

## 5. 在上述契約未合併前

`publication.recommendation_version_support`、`research_origin_ref`、`rationale_asset_id`、
`source_candidate_id`、`audience_snapshot_id`、`created_from_signature_event_id`、`recipient_count`
只在 `database/proposals/M08/001_publication.sql` 提案中；本模組的 application service／repository 使用
自有記憶體模型（`apps/api/src/modules/publication/domain.py`），尚未接到真實 PostgreSQL。真正接線時需同
步核對本檔第 2 節的 mapper 對應,不得各自發明欄位名稱。
