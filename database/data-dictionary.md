# 資料字典

版本 1.0.0。由 `tools/build_schema.py` 生成，與 `migrations/0001_domains.sql` 同一來源。

所有業務表的 PK 為 `(tenant_id, id)`；跨表 FK 也包含 `tenant_id`。`required=false` 的欄位可為 null；金額需使用 Decimal，API 以十進位字串傳遞。時間儲存 UTC，台灣營運日期轉換在服務層明確執行。

保存類型是 `core.retention_policy.policy_code` 的分類，不是自動套用的天數。政策未核准／起算事件未發生／legal hold 未解除時禁止刪除。

## core.actor

**寫入負責：** SHARED。**用途：** 操作人或服務身分；不儲存認證秘密。

**保存分類：** `employment_access`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `subject_ref` | `text` | 是 | internal | SSO 供應商 subject 或服務主體；不可當成顯示姓名。 |
| `actor_type` | `text` | 是 | internal | human 或 service |
| `display_label` | `text` | 是 | personal | 最小必要顯示名稱 |
| `enabled` | `boolean` | 是 | internal | 是否可發起新操作 預設 `true`。 |
| `role_codes` | `jsonb` | 是 | internal | 核准角色代碼；真正授權須經伺服器 RBAC/ABAC。 預設 `'[]'::jsonb`。 |
| `departed_at` | `timestamptz` | 否 | internal | 員工離職生效時點；觸發歸戶與權限撤銷。 |

**約束：**

- `CHECK (actor_type IN ('human','service'))`
- UNIQUE `(tenant_id, subject_ref)`。

## core.staff_credential

**寫入負責：** SHARED。**用途：** 分析師及招攬人員資格核驗版本。

**保存分類：** `employment_access`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `credential_type` | `text` | 是 | internal | analyst / sales / compliance / aml |
| `verification_ref` | `text` | 是 | internal | 受限資格查核證據引用 |
| `valid_from` | `timestamptz` | 是 | internal | 有效開始 |
| `valid_until` | `timestamptz` | 否 | internal | 有效截止 |
| `revoked_at` | `timestamptz` | 否 | internal | 資格撤銷生效時間 |

**約束：**

- `CHECK (valid_until IS NULL OR valid_until > valid_from)`

## core.credential_revocation

**寫入負責：** SHARED。**用途：** 資格撤銷使用新事件，不更新不可變資格證據。

**保存分類：** `employment_access`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `credential_id` | `uuid` | 是 | internal | 指向 core.staff_credential，必須同一 tenant。 FK: `core.staff_credential`。 |
| `revoked_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `revoked_at` | `timestamptz` | 是 | internal | 撤銷生效時間 |
| `reason_code` | `text` | 是 | internal | 撤銷原因代碼 |

## core.retention_policy

**寫入負責：** SHARED。**用途：** 資料類型的保存政策版本；不把提案天數當成核准法定天數。

**保存分類：** `policy`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `policy_code` | `text` | 是 | internal | 業務保存類型 |
| `policy_version` | `integer` | 是 | internal | 政策版本 |
| `trigger_event` | `text` | 是 | internal | 起算事件，例如 contract_obligations_ended 或 report_provided |
| `duration_days` | `integer` | 否 | internal | 核准的最低保存日數；null 代表尚未核定，禁止自動刪除。 |
| `legal_basis_ref` | `text` | 否 | internal | 法源或內規控制索引 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_at` | `timestamptz` | 否 | internal | 核准時間 |

**約束：**

- `CHECK (duration_days IS NULL OR duration_days > 0)`
- `CHECK ((approved_at IS NULL) = (approved_by IS NULL))`
- UNIQUE `(tenant_id, policy_code, policy_version)`。

## core.object_asset

**寫入負責：** SHARED。**用途：** 加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。

**保存分類：** `per_asset`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `storage_key` | `text` | 是 | internal | 私有 bucket/key；下載另用短效授權 |
| `object_version` | `text` | 是 | internal | 物件儲存版本 |
| `sha256` | `char(64)` | 是 | internal | 實際 bytes SHA-256；不等同另一個簽章容器的 hash。 |
| `byte_length` | `bigint` | 是 | internal | 長度 |
| `media_type` | `text` | 是 | internal | MIME 類型 |
| `classification` | `text` | 是 | internal | public / internal / personal / restricted_aml |
| `key_ref` | `text` | 是 | internal | KMS key reference；不是密鑰 |
| `retention_policy_id` | `uuid` | 是 | internal | 指向 core.retention_policy，必須同一 tenant。 FK: `core.retention_policy`。 |
| `retention_started_at` | `timestamptz` | 否 | internal | 保存起算事件時間；未發生時 null。 |
| `delete_not_before` | `timestamptz` | 否 | internal | 不得早於此時點刪除；null 禁止排程刪除。 |

**約束：**

- `CHECK (byte_length >= 0)`
- `CHECK (classification IN ('public','internal','personal','restricted_aml'))`
- UNIQUE `(tenant_id, storage_key, object_version)`。

## core.legal_hold

**寫入負責：** SHARED。**用途：** 司法、爭議或稽核保存指示；解除以新事件表達。

**保存分類：** `audit`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `target_type` | `text` | 是 | internal | 資源類型 |
| `target_id` | `uuid` | 是 | internal | 被保全資源 |
| `action` | `text` | 是 | internal | hold 或 release |
| `reason_code` | `text` | 是 | internal | 保全理由代碼；詳細理由留在受控案件 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `prior_hold_id` | `uuid` | 否 | internal | 指向 core.legal_hold，必須同一 tenant。 FK: `core.legal_hold`。 |
| `effective_at` | `timestamptz` | 是 | internal | 生效時間 |

**約束：**

- `CHECK (action IN ('hold','release'))`
- `CHECK (action <> 'release' OR prior_hold_id IS NOT NULL)`

## core.audit_event

**寫入負責：** SHARED。**用途：** 不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。

**保存分類：** `audit`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `action` | `text` | 是 | internal | 動作代碼 |
| `resource_type` | `text` | 是 | internal | 資源類型 |
| `resource_id` | `uuid` | 是 | internal | 資源 ID |
| `correlation_id` | `uuid` | 是 | internal | 全鏈路追蹤 ID |
| `outcome` | `text` | 是 | internal | success / denied / error |
| `classification` | `text` | 是 | internal | internal/personal/financial/restricted_aml |
| `redacted_changes` | `jsonb` | 是 | internal | 僅欄位名稱與遮罩值；禁止原始 KYC/AML/秘密。 預設 `'{}'::jsonb`。 |
| `occurred_at` | `timestamptz` | 是 | internal | 實際發生時間 |
| `prior_hash` | `char(64)` | 否 | internal | 同一封存鏈前一證據 hash |
| `event_hash` | `char(64)` | 是 | internal | canonical audit bytes 的摘要 |

**約束：**

- `CHECK (outcome IN ('success','denied','error'))`
- `CHECK (classification IN ('internal','personal','financial','restricted_aml'))`
- `CHECK (resource_type NOT LIKE 'aml.%' OR classification='restricted_aml')`

## core.outbox

**寫入負責：** SHARED。**用途：** 與業務異動同一 DB transaction 提交的待送事件。

**保存分類：** `integration`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：pending / leased / published / retry / dead_letter 預設 `'pending'`。 |
| `event_id` | `uuid` | 是 | internal | 穩定事件 ID |
| `event_type` | `text` | 是 | internal | 事件名稱；與 events/registry.json 一致 |
| `schema_version` | `text` | 是 | internal | 事件 schema SemVer 預設 `'1.0.0'`。 |
| `producer` | `text` | 是 | internal | 權威 producer 模組代碼 |
| `aggregate_type` | `text` | 是 | internal | aggregate 類型 |
| `aggregate_id` | `uuid` | 是 | internal | aggregate ID |
| `aggregate_version` | `integer` | 是 | internal | aggregate 資料版本 |
| `sequence_no` | `bigint` | 是 | internal | 同 producer/aggregate 事件順序 |
| `idempotency_key` | `text` | 是 | internal | 穩定命令／事件去重 key |
| `classification` | `text` | 是 | internal | 事件資料分類 |
| `payload_sha256` | `char(64)` | 是 | internal | canonical payload SHA-256 |
| `payload` | `jsonb` | 是 | internal | 只含 IDs、版本、狀態與 safe amount；禁止敏感原文。 |
| `correlation_id` | `uuid` | 是 | internal | trace |
| `causation_id` | `uuid` | 否 | internal | 上游事件或命令 ID |
| `occurred_at` | `timestamptz` | 是 | internal | 業務實際時間 |
| `emitted_at` | `timestamptz` | 是 | internal | 首次建立外送 envelope 的真實時間；重試不變 |
| `available_at` | `timestamptz` | 否 | internal | 允許送出時間 |
| `lease_until` | `timestamptz` | 否 | internal | worker 租約 |
| `lease_owner` | `text` | 否 | internal | worker ID |
| `attempt_count` | `integer` | 是 | internal | 嘗試次數 預設 `0`。 |
| `last_error_code` | `text` | 否 | internal | 非敏感錯誤代碼 |

**約束：**

- `CHECK (status IN ('pending','leased','published','retry','dead_letter'))`
- `CHECK (attempt_count >= 0)`
- `CHECK (aggregate_version > 0 AND sequence_no > 0)`
- UNIQUE `(tenant_id, event_id)`。
- UNIQUE `(tenant_id, producer, aggregate_type, aggregate_id, sequence_no)`。
- UNIQUE `(tenant_id, producer, idempotency_key)`。

## core.inbox

**寫入負責：** SHARED。**用途：** consumer 去重與 event fingerprint；同 ID 不同 payload 必須拒收。

**保存分類：** `integration`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：received / leased / applied / rejected / retry / dead_letter 預設 `'received'`。 |
| `consumer` | `text` | 是 | internal | consumer 名稱 |
| `producer` | `text` | 是 | internal | 來源服務 |
| `event_id` | `uuid` | 是 | internal | 來源事件 ID |
| `payload_sha256` | `char(64)` | 是 | internal | raw 或約定 canonical payload hash |
| `event_type` | `text` | 是 | internal | 來源事件名稱 |
| `schema_version` | `text` | 是 | internal | 來源 schema SemVer |
| `aggregate_type` | `text` | 是 | internal | 來源 aggregate 類型 |
| `aggregate_id` | `uuid` | 是 | internal | 來源 aggregate ID |
| `aggregate_version` | `integer` | 是 | internal | 來源版本 |
| `sequence_no` | `bigint` | 是 | internal | 來源 aggregate 事件順序 |
| `received_at` | `timestamptz` | 是 | internal | 接收時間 |
| `lease_until` | `timestamptz` | 否 | internal | 處理租約 |
| `applied_at` | `timestamptz` | 否 | internal | 交易內成功套用時間 |
| `result_code` | `text` | 否 | internal | 結果代碼 |

**約束：**

- `CHECK (status IN ('received','leased','applied','rejected','retry','dead_letter'))`
- UNIQUE `(tenant_id, consumer, producer, event_id)`。

## core.idempotency_record

**寫入負責：** SHARED。**用途：** 命令重送收據；必須綁 tenant、actor、operation、key 與 request hash。

**保存分類：** `integration`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `operation` | `text` | 是 | internal | API operationId |
| `idempotency_key` | `text` | 是 | internal | 呼叫端提供 key；不可含 PII |
| `request_sha256` | `char(64)` | 是 | internal | 請求指紋 |
| `response_status` | `integer` | 否 | internal | 已確定的 HTTP 狀態 |
| `redacted_response` | `jsonb` | 否 | internal | 僅無秘密的重播回應或 resource refs |
| `completed_at` | `timestamptz` | 否 | internal | 完成時間 |
| `expires_at` | `timestamptz` | 是 | internal | 收據保存截止；不得短於安全重試窗 |

**約束：**

- `CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599)`
- UNIQUE `(tenant_id, actor_id, operation, idempotency_key)`。

## core.job_lease

**寫入負責：** SHARED。**用途：** 排程任務租約與 fencing token。

**保存分類：** `integration`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `job_key` | `text` | 是 | internal | tenant 範圍內排程 key |
| `worker_id` | `text` | 是 | internal | 持租 worker |
| `fence_token` | `bigint` | 是 | internal | 每次接管遞增 token |
| `lease_until` | `timestamptz` | 是 | internal | 租約到期 |
| `checkpoint_at` | `timestamptz` | 否 | internal | 最後成功 checkpoint |
| `checkpoint` | `jsonb` | 是 | internal | 安全的游標 預設 `'{}'::jsonb`。 |

**約束：**

- UNIQUE `(tenant_id, job_key)`。

## customer.customer

**寫入負責：** M01。**用途：** 客戶管理主檔；潛在、申辦、已簽與已生效客戶共用身份。

**保存分類：** `customer`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：prospect / applicant / active / inactive / restricted / merged 預設 `'prospect'`。 |
| `pii_ciphertext` | `bytea` | 否 | personal | 最小客戶識別資料；application-layer envelope encryption。 |
| `pii_key_ref` | `text` | 否 | internal | 解密所需 KMS reference |
| `lookup_hmac` | `char(64)` | 否 | internal | 身分正規化後 keyed HMAC；不得用可逆 raw id 或無鹽 hash。 |
| `merged_into_id` | `uuid` | 否 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `first_effective_paid_at` | `timestamptz` | 否 | internal | 首次付費服務生效日期；與首次造訪分離。 |
| `locale` | `text` | 否 | internal | 客戶語系 |

**約束：**

- `CHECK (status IN ('prospect','applicant','active','inactive','restricted','merged'))`
- `CHECK ((pii_ciphertext IS NULL) = (pii_key_ref IS NULL))`
- `CHECK (merged_into_id IS NULL OR merged_into_id <> id)`
- UNIQUE `(tenant_id, lookup_hmac)`。

## customer.external_identity

**寫入負責：** M01。**用途：** 經驗證外部登入身分；合併須證據與審核。

**保存分類：** `customer`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `provider` | `text` | 是 | internal | identity provider，例如 legendflow/line/oidc |
| `subject_hmac` | `char(64)` | 是 | internal | provider subject 的 keyed HMAC |
| `verified_at` | `timestamptz` | 是 | internal | 完成身份連結驗證時間 |
| `assurance_level` | `text` | 是 | internal | 已核准 assurance code |
| `proof_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |

**約束：**

- UNIQUE `(tenant_id, provider, subject_hmac)`。

## customer.application

**寫入負責：** M01。**用途：** 簽約申辦流程；客戶簽署先於 KYC，不代表公司已接受。

**保存分類：** `contract`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：draft / customer_signed / due_diligence_pending / review_in_progress / accepted / rejected / withdrawn 預設 `'draft'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `service_code` | `text` | 是 | internal | 受申請服務，商品價格版本由 contract 固定 |
| `package_version_id` | `uuid` | 是 | internal | 指向 contract.package_version，必須同一 tenant。 FK: `contract.package_version`。 |
| `service_selections` | `jsonb` | 是 | internal | 所選服務、報價與稅類版本的服務端快照；可含多服務。 |
| `intent` | `text` | 是 | internal | new/renewal/upgrade/reactivation 預設 `'new'`。 |
| `preceding_contract_id` | `uuid` | 否 | internal | 指向 contract.contract，必須同一 tenant。 FK: `contract.contract`。 |
| `source_session_ref` | `text` | 否 | internal | 去識別漏斗 session 參考 |
| `submitted_at` | `timestamptz` | 否 | internal | 送審時間 |
| `decision_at` | `timestamptz` | 否 | internal | 公司決定時間 |
| `decision_reason_code` | `text` | 否 | internal | 公司決定代碼；客戶可見與受限理由分開。 |

**約束：**

- `CHECK (status IN ('draft','customer_signed','due_diligence_pending','review_in_progress','accepted','rejected','withdrawn'))`
- `CHECK (intent IN ('new','renewal','upgrade','reactivation'))`
- `CHECK (jsonb_typeof(service_selections)='array' AND jsonb_array_length(service_selections)>0)`

## customer.consent

**寫入負責：** M01。**用途：** 個資告知、行銷同意、撤回事件；不能用簽約強迫行銷同意。

**保存分類：** `customer`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `purpose` | `text` | 是 | internal | 同意目的 |
| `notice_version` | `text` | 是 | internal | 所見告知版本 |
| `granted` | `boolean` | 是 | internal | true 同意／false 撤回 |
| `recorded_at` | `timestamptz` | 是 | internal | 客戶作出意思表示時間 |
| `evidence_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `channel` | `text` | 是 | internal | 取得來源 |

## customer.merge_case

**寫入負責：** M01。**用途：** 重複客戶合併申請，核准後保留來源 ID alias 與原證據。

**保存分類：** `customer`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：proposed / approved / rejected / executed / cancelled 預設 `'proposed'`。 |
| `source_customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `target_customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `proposed_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `reviewed_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `evidence_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `decided_at` | `timestamptz` | 否 | internal | 覆核時間 |
| `executed_at` | `timestamptz` | 否 | internal | 完成交易時間 |
| `reason_code` | `text` | 是 | internal | 合併理由代碼 |

**約束：**

- `CHECK (status IN ('proposed','approved','rejected','executed','cancelled'))`
- `CHECK (source_customer_id <> target_customer_id)`
- `CHECK (reviewed_by IS NULL OR reviewed_by <> proposed_by)`
- `CHECK (status NOT IN ('approved','executed') OR reviewed_by IS NOT NULL)`

## contract.package_version

**寫入負責：** M02。**用途：** 申請暨投顧契約及揭露文件完整版本。

**保存分類：** `contract`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `package_code` | `text` | 是 | internal | 模板識別碼 |
| `package_version` | `integer` | 是 | internal | 模板版本 |
| `asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `package_sha256` | `char(64)` | 是 | internal | 完整展示文件 hash |
| `effective_from` | `timestamptz` | 是 | internal | 模板啟用時間 |
| `proposed_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_at` | `timestamptz` | 否 | internal | 模板核准時間 |
| `acceptance_conditions` | `jsonb` | 是 | internal | 公司接受與生效條件；須核定正式文字。 |
| `approval_status` | `text` | 是 | internal | draft/approved；draft 不得供正式簽署 |

**約束：**

- `CHECK (approval_status IN ('draft','approved','retired'))`
- `CHECK (approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by))`
- UNIQUE `(tenant_id, package_code, package_version)`。

## contract.customer_signature

**寫入負責：** M02。**用途：** 客戶先簽申請／契約封包的不可變證據。

**保存分類：** `contract`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `application_id` | `uuid` | 是 | internal | 指向 customer.application，必須同一 tenant。 FK: `customer.application`。 |
| `package_version_id` | `uuid` | 是 | internal | 指向 contract.package_version，必須同一 tenant。 FK: `contract.package_version`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `content_sha256` | `char(64)` | 是 | internal | 簽署前精確內容摘要 |
| `signed_file_sha256` | `char(64)` | 是 | internal | 簽後容器或檔案摘要；不同於 content hash |
| `signed_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `provider` | `text` | 是 | internal | 簽章 provider |
| `provider_transaction_id` | `text` | 是 | internal | 供應商交易 ID |
| `assurance_level` | `text` | 是 | internal | 身份驗證方式的核准代碼 |
| `customer_signed_at` | `timestamptz` | 是 | internal | 客戶真正簽署時點 |
| `evidence_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |

**約束：**

- UNIQUE `(tenant_id, provider, provider_transaction_id)`。

## contract.acceptance_decision

**寫入負責：** M02。**用途：** 公司接受、拒絕或要求補件的完整決策歷史；accept才另產生company_acceptance。

**保存分類：** `contract`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `application_id` | `uuid` | 是 | internal | 指向 customer.application，必須同一 tenant。 FK: `customer.application`。 |
| `customer_signature_id` | `uuid` | 是 | internal | 指向 contract.customer_signature，必須同一 tenant。 FK: `contract.customer_signature`。 |
| `decision` | `text` | 是 | internal | accept/reject/request_changes |
| `decided_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `decided_at` | `timestamptz` | 是 | internal | 公司決定真實時間 |
| `safe_reason_code` | `text` | 是 | internal | 可供申辦工作流使用的理由代碼；不含AML調查內情 |
| `evidence_snapshot` | `jsonb` | 是 | internal | 決策依據的版本引用；受限內容留在M04。 |

**約束：**

- `CHECK (decision IN ('accept','reject','request_changes'))`

## contract.company_acceptance

**寫入負責：** M02。**用途：** 公司完成必要審查後接受；與客戶簽署分離。

**保存分類：** `contract`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `decision_id` | `uuid` | 是 | internal | 指向 contract.acceptance_decision，必須同一 tenant。 FK: `contract.acceptance_decision`。 |
| `application_id` | `uuid` | 是 | internal | 指向 customer.application，必須同一 tenant。 FK: `customer.application`。 |
| `customer_signature_id` | `uuid` | 是 | internal | 指向 contract.customer_signature，必須同一 tenant。 FK: `contract.customer_signature`。 |
| `kyc_submission_id` | `uuid` | 是 | internal | 指向 kyc.submission，必須同一 tenant。 FK: `kyc.submission`。 |
| `suitability_assessment_id` | `uuid` | 是 | internal | 指向 kyc.suitability_assessment，必須同一 tenant。 FK: `kyc.suitability_assessment`。 |
| `aml_clearance_id` | `uuid` | 是 | internal | 指向 aml.business_clearance，必須同一 tenant。 FK: `aml.business_clearance`。 |
| `accepted_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `accepted_at` | `timestamptz` | 是 | internal | 公司接受真實時間 |
| `company_signed_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `conditions_snapshot` | `jsonb` | 是 | internal | 本次公司接受檢查條件與版本；不能改寫原評估。 |

**約束：**

- UNIQUE `(tenant_id, application_id)`。
- UNIQUE `(tenant_id, decision_id)`。

## contract.contract

**寫入負責：** M02。**用途：** 法律關係及服務契約狀態；不把付款或前端權限當成成立證據。

**保存分類：** `contract`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：pending_company_acceptance / accepted / effective / suspended / expired / terminated 預設 `'pending_company_acceptance'`。 |
| `application_id` | `uuid` | 是 | internal | 指向 customer.application，必須同一 tenant。 FK: `customer.application`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `customer_signature_id` | `uuid` | 是 | internal | 指向 contract.customer_signature，必須同一 tenant。 FK: `contract.customer_signature`。 |
| `company_acceptance_id` | `uuid` | 否 | internal | 指向 contract.company_acceptance，必須同一 tenant。 FK: `contract.company_acceptance`。 |
| `contract_number` | `text` | 是 | internal | 不可重複契約編號 |
| `effective_at` | `timestamptz` | 否 | internal | 依核准條款生效時間 |
| `ends_at` | `timestamptz` | 否 | internal | 約定服務終止時間 |
| `obligations_ended_at` | `timestamptz` | 否 | internal | 權利義務全部終了；保存起算依政策。 |
| `currency` | `text` | 是 | internal | ISO currency，例 TWD |
| `terms_snapshot` | `jsonb` | 是 | internal | 價格、稅類、服務、退款條款與版本快照 |

**約束：**

- `CHECK (status IN ('pending_company_acceptance','accepted','effective','suspended','expired','terminated'))`
- `CHECK (ends_at IS NULL OR effective_at IS NULL OR ends_at > effective_at)`
- `CHECK (status NOT IN ('accepted','effective') OR company_acceptance_id IS NOT NULL)`
- UNIQUE `(tenant_id, contract_number)`。
- UNIQUE `(tenant_id, application_id)`。

## contract.contract_service

**寫入負責：** M02。**用途：** 一客多契約多服務及續約服務鏈。

**保存分類：** `contract`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `contract_id` | `uuid` | 是 | internal | 指向 contract.contract，必須同一 tenant。 FK: `contract.contract`。 |
| `service_code` | `text` | 是 | internal | 服務權益代碼 |
| `service_chain_id` | `uuid` | 是 | internal | 續訂關聯鏈；不因改方案或換業務歸零。 |
| `predecessor_service_id` | `uuid` | 否 | internal | 指向 contract.contract_service，必須同一 tenant。 FK: `contract.contract_service`。 |
| `starts_at` | `timestamptz` | 是 | internal | 服務區間 [start,end) 開始 |
| `ends_at` | `timestamptz` | 是 | internal | 服務區間結束 |
| `auto_renew` | `boolean` | 是 | internal | 本期付款前客戶授權之自動續訂 預設 `false`。 |
| `untaxed_contract_amount` | `numeric(20,6)` | 是 | internal | 已確認稅別後未稅契約額；未履約部分不直接當收入。 |
| `tax_profile_ref` | `text` | 是 | internal | 財會核准稅務設定版本 |
| `features_snapshot` | `jsonb` | 是 | internal | 核准的服務權益快照 |

**約束：**

- `CHECK (ends_at > starts_at)`
- `CHECK (untaxed_contract_amount >= 0)`

## contract.entitlement

**寫入負責：** M02。**用途：** 權限來源為有效契約與審查結果；配送時重新檢查。

**保存分類：** `contract`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：locked / eligible / active / suspended / expired / revoked 預設 `'locked'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `contract_service_id` | `uuid` | 是 | internal | 指向 contract.contract_service，必須同一 tenant。 FK: `contract.contract_service`。 |
| `feature_code` | `text` | 是 | internal | 可用權益 |
| `valid_from` | `timestamptz` | 是 | internal | 使用權限開始 |
| `valid_until` | `timestamptz` | 是 | internal | 使用權限結束 |
| `payment_condition_ref` | `text` | 否 | internal | 付款或核准授信條件證據 |
| `restriction_code` | `text` | 否 | internal | 安全可見限制代碼；不放 AML 內情 |

**約束：**

- `CHECK (status IN ('locked','eligible','active','suspended','expired','revoked'))`
- `CHECK (valid_until > valid_from)`
- `CHECK (status <> 'active' OR payment_condition_ref IS NOT NULL)`
- UNIQUE `(tenant_id, contract_service_id, feature_code)`。

## contract.entitlement_decision

**寫入負責：** M02。**用途：** 每次啟停權限的決策紀錄。

**保存分類：** `audit`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `entitlement_id` | `uuid` | 是 | internal | 指向 contract.entitlement，必須同一 tenant。 FK: `contract.entitlement`。 |
| `from_status` | `text` | 是 | internal | 舊狀態 |
| `to_status` | `text` | 是 | internal | 新狀態 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `reason_code` | `text` | 是 | internal | 規則決策代碼 |
| `gate_snapshot` | `jsonb` | 是 | internal | 公司接受、合約、KYC、適合度、AML safe clearance、付款條件版本 |
| `effective_at` | `timestamptz` | 是 | internal | 生效時間 |

## kyc.submission

**寫入負責：** M03。**用途：** 版本化線上 KYC 回答與證據；先保留客戶原始填答。

**保存分類：** `kyc`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：not_started / in_progress / submitted / incomplete / under_review / approved / rejected / refresh_required 預設 `'not_started'`。 |
| `application_id` | `uuid` | 是 | internal | 指向 customer.application，必須同一 tenant。 FK: `customer.application`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `submission_version` | `integer` | 是 | internal | 同一申請送件版本 |
| `answers_ciphertext` | `bytea` | 否 | personal | KYC 答案加密；不得送 general LLM |
| `key_ref` | `text` | 否 | internal | KMS ref |
| `submitted_at` | `timestamptz` | 否 | internal | 送件時間 |
| `reviewed_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `reviewed_at` | `timestamptz` | 否 | internal | 審查時間 |
| `valid_until` | `timestamptz` | 否 | internal | 覆核有效截止 |
| `decision_reason_code` | `text` | 否 | internal | 審查代碼 |

**約束：**

- `CHECK (status IN ('not_started','in_progress','submitted','incomplete','under_review','approved','rejected','refresh_required'))`
- `CHECK ((answers_ciphertext IS NULL) = (key_ref IS NULL))`
- `CHECK (status <> 'approved' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))`
- UNIQUE `(tenant_id, application_id, submission_version)`。

## kyc.submission_document

**寫入負責：** M03。**用途：** 線上補件、驗證用途與結果；保留原件版本。

**保存分類：** `kyc`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `submission_id` | `uuid` | 是 | internal | 指向 kyc.submission，必須同一 tenant。 FK: `kyc.submission`。 |
| `asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `document_type` | `text` | 是 | internal | 核准文件種類 |
| `verification_state` | `text` | 是 | internal | pending/verified/rejected |
| `verified_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `verified_at` | `timestamptz` | 否 | internal | 驗證時間 |

**約束：**

- `CHECK (verification_state IN ('pending','verified','rejected'))`

## kyc.suitability_assessment

**寫入負責：** M03。**用途：** 版本化適合度評估，條件適合必須附限制。

**保存分類：** `kyc`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：pending / suitable / conditional / not_suitable / reassessment_due 預設 `'pending'`。 |
| `submission_id` | `uuid` | 是 | internal | 指向 kyc.submission，必須同一 tenant。 FK: `kyc.submission`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `questionnaire_version` | `text` | 是 | internal | 問卷版本 |
| `rule_version` | `text` | 是 | internal | 計分及判斷規則版本 |
| `answers_snapshot` | `jsonb` | 是 | internal | 需加密的原始答案引用與問題版本，不存明文個資。 |
| `restrictions` | `jsonb` | 是 | internal | 條件適合的限制代碼；供 entitlement 使用。 預設 `'[]'::jsonb`。 |
| `assessed_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `assessed_at` | `timestamptz` | 否 | internal | 最終評估時點 |
| `valid_until` | `timestamptz` | 否 | internal | 重評期限 |

**約束：**

- `CHECK (status IN ('pending','suitable','conditional','not_suitable','reassessment_due'))`
- `CHECK (status <> 'conditional' OR jsonb_array_length(restrictions) > 0)`

## aml.screening

**寫入負責：** M04。**用途：** 名單比對供應商查核；模糊命中不是自動認定或自動清除。

**保存分類：** `aml`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：pending / clear / possible_match / confirmed_match / error 預設 `'pending'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `submission_id` | `uuid` | 是 | internal | 指向 kyc.submission，必須同一 tenant。 FK: `kyc.submission`。 |
| `provider` | `text` | 是 | internal | 名單 provider |
| `provider_request_id` | `text` | 是 | internal | 查核唯一請求 |
| `list_version` | `text` | 是 | internal | 名單版本 |
| `request_sha256` | `char(64)` | 是 | internal | 查核輸入 fingerprint |
| `restricted_result_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `screened_at` | `timestamptz` | 否 | internal | 查核時間 |

**約束：**

- `CHECK (status IN ('pending','clear','possible_match','confirmed_match','error'))`
- UNIQUE `(tenant_id, provider, provider_request_id)`。

## aml.case_file

**寫入負責：** M04。**用途：** AML/CFT 受限覆核案件，禁止 CRM 讀內情。

**保存分類：** `aml`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：open / under_review / escalated / cleared / rejected / closed 預設 `'open'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `screening_id` | `uuid` | 是 | internal | 指向 aml.screening，必須同一 tenant。 FK: `aml.screening`。 |
| `owner_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `risk_level` | `text` | 是 | internal | low/medium/high/undetermined |
| `restricted_notes_ciphertext` | `bytea` | 否 | restricted_aml | 案件註記加密 |
| `key_ref` | `text` | 否 | internal | KMS ref |
| `next_review_at` | `timestamptz` | 否 | internal | 下一個必要覆核時間 |

**約束：**

- `CHECK (status IN ('open','under_review','escalated','cleared','rejected','closed'))`
- `CHECK (risk_level IN ('low','medium','high','undetermined'))`

## aml.case_action

**寫入負責：** M04。**用途：** AML 調查、EDD、申報與決定之受限軌跡。

**保存分類：** `aml`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `case_id` | `uuid` | 是 | internal | 指向 aml.case_file，必須同一 tenant。 FK: `aml.case_file`。 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `action_code` | `text` | 是 | internal | 受限動作代碼 |
| `evidence_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `occurred_at` | `timestamptz` | 是 | internal | 動作時間 |
| `restricted_detail_ciphertext` | `bytea` | 否 | restricted_aml | 受限詳情 |

## aml.business_clearance

**寫入負責：** M04。**用途：** 供公司接受服務查核的最小結果；不暴露比對名單、申報或案件細節。

**保存分類：** `aml`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `application_id` | `uuid` | 是 | internal | 指向 customer.application，必須同一 tenant。 FK: `customer.application`。 |
| `case_id` | `uuid` | 否 | internal | 指向 aml.case_file，必須同一 tenant。 FK: `aml.case_file`。 |
| `decision` | `text` | 是 | internal | approved / pending / denied / expired |
| `policy_version` | `text` | 是 | internal | 核定依據政策，M02不需讀受限screening |
| `decided_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `decided_at` | `timestamptz` | 是 | internal | 核定時間 |
| `valid_until` | `timestamptz` | 是 | internal | 有效期限 |
| `safe_reason_code` | `text` | 是 | internal | 僅 generic review_required / approved 等安全代碼 |

**約束：**

- `CHECK (decision IN ('approved','pending','denied','expired'))`
- `CHECK (valid_until > decided_at)`

## research.candidate

**寫入負責：** M05。**用途：** Pantheon Persona 自主研究或人員研究候選輸入；非正式對客報告。

**保存分類：** `research`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：received / validating / quarantined / ready / rejected / superseded 預設 `'received'`。 |
| `producer` | `text` | 是 | internal | 外部 producer |
| `external_candidate_id` | `text` | 是 | internal | 來源候選識別碼 |
| `external_version` | `integer` | 是 | internal | 來源版本 |
| `origin` | `text` | 是 | internal | persona_autonomous / analyst_requested |
| `persona_ref` | `text` | 否 | internal | Persona 引用 |
| `pool_ref` | `text` | 否 | internal | 研究／交易 pool 引用 |
| `artifact_ref` | `text` | 是 | internal | Pantheon artifact 指標 |
| `content_sha256` | `char(64)` | 是 | internal | 實際候選內容 hash |
| `snapshot_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `source_occurred_at` | `timestamptz` | 是 | internal | 來源真實發生時間 |
| `received_at` | `timestamptz` | 是 | internal | 本平台接收時間 |
| `lineage` | `jsonb` | 是 | internal | ticket/plan/run/model/prompt/data/evidence 版本引用 |

**約束：**

- `CHECK (status IN ('received','validating','quarantined','ready','rejected','superseded'))`
- `CHECK (origin IN ('persona_autonomous','analyst_requested'))`
- UNIQUE `(tenant_id, producer, external_candidate_id, external_version)`。

## research.candidate_evidence

**寫入負責：** M05。**用途：** 候選報告採用來源快照、時間與授權範圍。

**保存分類：** `research`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `candidate_id` | `uuid` | 是 | internal | 指向 research.candidate，必須同一 tenant。 FK: `research.candidate`。 |
| `evidence_ref` | `text` | 是 | internal | Pantheon evidence/bundle reference |
| `evidence_sha256` | `char(64)` | 是 | internal | 可驗證內容摘要 |
| `event_time` | `timestamptz` | 是 | internal | 來源事件時間 |
| `available_time` | `timestamptz` | 是 | internal | 研究當時可取得時間 |
| `license_scope` | `text` | 是 | internal | 授權或傳閱限制 |
| `snapshot_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |

**約束：**

- UNIQUE `(tenant_id, candidate_id, evidence_ref)`。

## review.case_file

**寫入負責：** M06。**用途：** 分析師覆核工作項；非交易治理批准。

**保存分類：** `research`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：queued / assigned / reviewing / changes_requested / ready_to_sign / rejected / closed 預設 `'queued'`。 |
| `candidate_id` | `uuid` | 是 | internal | 指向 research.candidate，必須同一 tenant。 FK: `research.candidate`。 |
| `analyst_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `current_revision_no` | `integer` | 是 | internal | 目前採用修訂 預設 `1`。 |
| `priority` | `text` | 是 | internal | normal/urgent |
| `due_at` | `timestamptz` | 否 | internal | 工作期限 |
| `stale` | `boolean` | 是 | internal | 候選新版本到達或證據變更 預設 `false`。 |

**約束：**

- `CHECK (status IN ('queued','assigned','reviewing','changes_requested','ready_to_sign','rejected','closed'))`
- `CHECK (priority IN ('normal','urgent'))`
- UNIQUE `(tenant_id, candidate_id)`。

## review.conversation

**寫入負責：** M06。**用途：** Agora Servant 對話參照；不複製所有 Pantheon 知識。

**保存分類：** `research`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `case_id` | `uuid` | 是 | internal | 指向 review.case_file，必須同一 tenant。 FK: `review.case_file`。 |
| `provider` | `text` | 是 | internal | agora |
| `external_conversation_id` | `text` | 是 | internal | 外部 conversation |
| `scoped_context_ref` | `text` | 是 | internal | 最小上下文引用 |
| `created_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `last_synced_at` | `timestamptz` | 否 | internal | 最後同步時間 |

**約束：**

- UNIQUE `(tenant_id, provider, external_conversation_id)`。

## review.revision_request

**寫入負責：** M06。**用途：** 分析師提出修訂並綁精確輸入版本。

**保存分類：** `research`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：requested / accepted / running / completed / failed / cancelled / stale 預設 `'requested'`。 |
| `case_id` | `uuid` | 是 | internal | 指向 review.case_file，必須同一 tenant。 FK: `review.case_file`。 |
| `conversation_id` | `uuid` | 是 | internal | 指向 review.conversation，必須同一 tenant。 FK: `review.conversation`。 |
| `requested_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `base_revision_no` | `integer` | 是 | internal | 被修改版本 |
| `instruction_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `external_job_ref` | `text` | 否 | internal | Servant job |
| `requested_at` | `timestamptz` | 是 | internal | 請求時間 |

**約束：**

- `CHECK (status IN ('requested','accepted','running','completed','failed','cancelled','stale'))`

## review.revision_result

**寫入負責：** M06。**用途：** 修訂產物不可變，新稿不覆寫已審稿。

**保存分類：** `research`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `request_id` | `uuid` | 是 | internal | 指向 review.revision_request，必須同一 tenant。 FK: `review.revision_request`。 |
| `revision_no` | `integer` | 是 | internal | 新修訂編號 |
| `artifact_ref` | `text` | 是 | internal | 外部產物 |
| `content_sha256` | `char(64)` | 是 | internal | 新產物內容摘要 |
| `asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `produced_at` | `timestamptz` | 是 | internal | 產生時間 |
| `evidence_refs` | `jsonb` | 是 | internal | 本次引用來源；可對回 research evidence |

**約束：**

- UNIQUE `(tenant_id, request_id, revision_no)`。

## report.report

**寫入負責：** M07。**用途：** 正式投顧研究報告的穩定識別。

**保存分類：** `report`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `review_case_id` | `uuid` | 是 | internal | 指向 review.case_file，必須同一 tenant。 FK: `review.case_file`。 |
| `report_number` | `text` | 是 | internal | 正式報告編號 |
| `title` | `text` | 是 | internal | 標題，不含客戶個資 |
| `instrument_refs` | `jsonb` | 是 | internal | 研究標的 ID／市場；並不等同可交易指令。 |
| `report_type` | `text` | 是 | internal | initial/update/exit/supplement |

**約束：**

- `CHECK (report_type IN ('initial','update','exit','supplement'))`
- UNIQUE `(tenant_id, report_number)`。

## report.report_version

**寫入負責：** M07。**用途：** 不可變報告正文版本及簽前核定 bundle；狀態投影可前進。

**保存分類：** `report`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：draft / in_review / changes_requested / ready_to_sign / signature_pending / signed / superseded / withdrawn 預設 `'draft'`。 |
| `report_id` | `uuid` | 是 | internal | 指向 report.report，必須同一 tenant。 FK: `report.report`。 |
| `report_version_no` | `integer` | 是 | internal | 報告版號 |
| `candidate_id` | `uuid` | 是 | internal | 指向 research.candidate，必須同一 tenant。 FK: `research.candidate`。 |
| `revision_result_id` | `uuid` | 否 | internal | 指向 review.revision_result，必須同一 tenant。 FK: `review.revision_result`。 |
| `content_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `content_sha256` | `char(64)` | 是 | internal | 核准報告原 bytes hash |
| `bundle_sha256` | `char(64)` | 是 | internal | 包含 channel 文案與 audience 規則版本的 canonical bundle hash |
| `bundle_manifest_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `bundle_manifest` | `jsonb` | 是 | internal | 確定的訊息版本、正文、受眾規則、風險揭露引用 |
| `review_policy_version` | `text` | 是 | internal | 需要哪些 professional/compliance review 的政策版本 |
| `requires_compliance_review` | `boolean` | 是 | internal | 是否須法遵先行覆核 預設 `true`。 |
| `valid_from` | `timestamptz` | 是 | internal | 研究建議開始有效時間 |
| `valid_until` | `timestamptz` | 是 | internal | 到期不可再發送 |
| `frozen_at` | `timestamptz` | 否 | internal | 簽前凍結時間 |

**約束：**

- `CHECK (status IN ('draft','in_review','changes_requested','ready_to_sign','signature_pending','signed','superseded','withdrawn'))`
- `CHECK (valid_until > valid_from)`
- `CHECK (status NOT IN ('ready_to_sign','signature_pending','signed') OR frozen_at IS NOT NULL)`
- UNIQUE `(tenant_id, report_id, report_version_no)`。

## report.review_decision

**寫入負責：** M07。**用途：** 逐版本覆核，綁相同內容與 bundle；修訂後不能沿用。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `report_version_id` | `uuid` | 是 | internal | 指向 report.report_version，必須同一 tenant。 FK: `report.report_version`。 |
| `review_type` | `text` | 是 | internal | professional/compliance |
| `decision` | `text` | 是 | internal | approved/changes_requested/rejected |
| `reviewer_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `content_sha256` | `char(64)` | 是 | internal | 被審查正文 hash |
| `bundle_sha256` | `char(64)` | 是 | internal | 被審查 bundle hash |
| `reviewed_at` | `timestamptz` | 是 | internal | 真實覆核時間 |
| `evidence_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |

**約束：**

- `CHECK (review_type IN ('professional','compliance'))`
- `CHECK (decision IN ('approved','changes_requested','rejected'))`

## report.signature_request

**寫入負責：** M07。**用途：** 簽章 provider 呼叫狀態；逾時需查詢，不可盲目重簽。

**保存分類：** `report`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：pending / submitted / verified / failed / expired 預設 `'pending'`。 |
| `report_version_id` | `uuid` | 是 | internal | 指向 report.report_version，必須同一 tenant。 FK: `report.report_version`。 |
| `signer_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `credential_id` | `uuid` | 是 | internal | 指向 core.staff_credential，必須同一 tenant。 FK: `core.staff_credential`。 |
| `bundle_sha256` | `char(64)` | 是 | internal | 待簽 bundle hash |
| `provider` | `text` | 是 | internal | 簽章供應商 |
| `provider_transaction_id` | `text` | 否 | internal | provider transaction |
| `requested_at` | `timestamptz` | 是 | internal | 要求時間 |
| `expires_at` | `timestamptz` | 是 | internal | 簽署挑戰到期 |

**約束：**

- `CHECK (status IN ('pending','submitted','verified','failed','expired'))`
- UNIQUE `(tenant_id, provider, provider_transaction_id)`。

## report.signature

**寫入負責：** M07。**用途：** 已驗證分析師簽章證據；永不改內容。撤銷用 revocation。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `request_id` | `uuid` | 是 | internal | 指向 report.signature_request，必須同一 tenant。 FK: `report.signature_request`。 |
| `report_version_id` | `uuid` | 是 | internal | 指向 report.report_version，必須同一 tenant。 FK: `report.report_version`。 |
| `signer_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `credential_id` | `uuid` | 是 | internal | 指向 core.staff_credential，必須同一 tenant。 FK: `core.staff_credential`。 |
| `content_sha256` | `char(64)` | 是 | internal | 被簽報告正文 hash |
| `bundle_sha256` | `char(64)` | 是 | internal | 被簽完整服務訊息 bundle hash |
| `signed_file_sha256` | `char(64)` | 是 | internal | 簽後 PDF/容器 hash |
| `signed_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `verification_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `signed_at` | `timestamptz` | 是 | internal | 實際簽章時間 |
| `verified_at` | `timestamptz` | 是 | internal | 驗簽成功時間 |

**約束：**

- `CHECK (verified_at >= signed_at)`
- UNIQUE `(tenant_id, request_id)`。
- UNIQUE `(tenant_id, report_version_id)`。

## report.revocation

**寫入負責：** M07。**用途：** 撤銷或重驗失敗保留原簽章證據。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `signature_id` | `uuid` | 是 | internal | 指向 report.signature，必須同一 tenant。 FK: `report.signature`。 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `reason_code` | `text` | 是 | internal | 撤銷理由 |
| `revoked_at` | `timestamptz` | 是 | internal | 真實撤銷時間 |
| `evidence_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |

## publication.recommendation_series

**寫入負責：** M08。**用途：** 帶進、追蹤、帶出的建議序列，與 Persona 交易及客戶交易分離。

**保存分類：** `report`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `series_code` | `text` | 是 | internal | 建議序列碼 |
| `instrument_refs` | `jsonb` | 是 | internal | 標的引用 |
| `strategy_ref` | `text` | 否 | internal | 可選外部策略參考 |

**約束：**

- UNIQUE `(tenant_id, series_code)`。

## publication.recommendation_version

**寫入負責：** M08。**用途：** 正式訊息與同時有效報告連結；重大判斷改變需新簽證。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `series_id` | `uuid` | 是 | internal | 指向 publication.recommendation_series，必須同一 tenant。 FK: `publication.recommendation_series`。 |
| `recommendation_version_no` | `integer` | 是 | internal | 序列版號 |
| `action_type` | `text` | 是 | internal | entry/update/exit/correction |
| `report_version_id` | `uuid` | 是 | internal | 指向 report.report_version，必須同一 tenant。 FK: `report.report_version`。 |
| `channel_content` | `jsonb` | 是 | internal | 已覆核 channel 文案或物件引用；簽章後禁止 LLM 改寫投資判斷。 |
| `content_sha256` | `char(64)` | 是 | internal | channel canonical content hash |
| `audience_rule_version` | `text` | 是 | internal | 固定受眾規則版本 |
| `valid_from` | `timestamptz` | 是 | internal | 開始可用 |
| `valid_until` | `timestamptz` | 是 | internal | 到期不再發送 |
| `corrects_version_id` | `uuid` | 否 | internal | 指向 publication.recommendation_version，必須同一 tenant。 FK: `publication.recommendation_version`。 |

**約束：**

- `CHECK (action_type IN ('entry','update','exit','correction'))`
- `CHECK (valid_until > valid_from)`
- UNIQUE `(tenant_id, series_id, recommendation_version_no)`。

## publication.publication

**寫入負責：** M08。**用途：** 驗簽完成即建立發布，不加第二個 Publish 人工作業。

**保存分類：** `report`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：queued / publishing / published / partially_failed / expired / corrected / withdrawn 預設 `'queued'`。 |
| `recommendation_version_id` | `uuid` | 是 | internal | 指向 publication.recommendation_version，必須同一 tenant。 FK: `publication.recommendation_version`。 |
| `signature_id` | `uuid` | 是 | internal | 指向 report.signature，必須同一 tenant。 FK: `report.signature`。 |
| `bundle_sha256` | `char(64)` | 是 | internal | 必須等於簽章 bundle |
| `queued_at` | `timestamptz` | 是 | internal | 自動排入發布時間 |
| `published_at` | `timestamptz` | 否 | internal | 平台 feed 成功發布時間 |
| `expires_at` | `timestamptz` | 是 | internal | 期限；不得超過報告與訊息有效期 |
| `correction_of_id` | `uuid` | 否 | internal | 指向 publication.publication，必須同一 tenant。 FK: `publication.publication`。 |
| `withdrawal_reason_code` | `text` | 否 | internal | 撤回理由 |

**約束：**

- `CHECK (status IN ('queued','publishing','published','partially_failed','expired','corrected','withdrawn'))`
- UNIQUE `(tenant_id, recommendation_version_id)`。

## publication.audience_snapshot

**寫入負責：** M08。**用途：** 發布當下受眾規則及快照；不取代送出時再次查權限。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `publication_id` | `uuid` | 是 | internal | 指向 publication.publication，必須同一 tenant。 FK: `publication.publication`。 |
| `rule_version` | `text` | 是 | internal | 受眾規則版本 |
| `evaluated_at` | `timestamptz` | 是 | internal | 評估時間 |
| `snapshot_sha256` | `char(64)` | 是 | internal | 收件人與權益版本快照 hash |
| `recipient_count` | `integer` | 是 | internal | 人數 |
| `snapshot_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |

**約束：**

- `CHECK (recipient_count >= 0)`
- UNIQUE `(tenant_id, publication_id)`。

## publication.recipient

**寫入負責：** M08。**用途：** 逐收件人權益證據；不存姓名或手機。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `snapshot_id` | `uuid` | 是 | internal | 指向 publication.audience_snapshot，必須同一 tenant。 FK: `publication.audience_snapshot`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `entitlement_id` | `uuid` | 是 | internal | 指向 contract.entitlement，必須同一 tenant。 FK: `contract.entitlement`。 |
| `entitlement_version` | `integer` | 是 | internal | 發布當下權益版本 |
| `eligibility_reason` | `text` | 是 | internal | 安全的符合理由 |

**約束：**

- UNIQUE `(tenant_id, snapshot_id, customer_id, entitlement_id)`。

## delivery.message

**寫入負責：** M09。**用途：** 每 recipient/channel 一個邏輯消息；供應商重試另建 attempt。

**保存分類：** `report`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：queued / accepted / sent / delivered / failed / expired / suppressed / unknown 預設 `'queued'`。 |
| `publication_id` | `uuid` | 是 | internal | 指向 publication.publication，必須同一 tenant。 FK: `publication.publication`。 |
| `recipient_id` | `uuid` | 是 | internal | 指向 publication.recipient，必須同一 tenant。 FK: `publication.recipient`。 |
| `channel` | `text` | 是 | internal | legendflow/line/sms/email |
| `channel_binding_id` | `uuid` | 否 | internal | 指向 delivery.channel_binding，必須同一 tenant。 FK: `delivery.channel_binding`。 |
| `rendered_sha256` | `char(64)` | 是 | internal | 已核准文字 bytes hash |
| `rendered_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `provider_idempotency_key` | `text` | 是 | internal | 同一邏輯消息穩定 key |
| `expires_at` | `timestamptz` | 是 | internal | 建議有效期限 |
| `last_authorized_at` | `timestamptz` | 否 | internal | 送出前最後權限查核時間 |
| `checked_entitlement_version` | `integer` | 否 | internal | 送出前權益版本 |
| `read_at` | `timestamptz` | 否 | internal | 僅 provider 可證實閱讀才填；沒有回執時 null。 |
| `suppression_reason` | `text` | 否 | internal | 退訂/到期/撤回等安全原因 |

**約束：**

- `CHECK (status IN ('queued','accepted','sent','delivered','failed','expired','suppressed','unknown'))`
- `CHECK (channel IN ('legendflow','line','sms','email'))`
- `CHECK (channel='legendflow' OR channel_binding_id IS NOT NULL)`
- UNIQUE `(tenant_id, publication_id, recipient_id, channel)`。
- UNIQUE `(tenant_id, provider_idempotency_key)`。

## delivery.channel_binding

**寫入負責：** M09。**用途：** 經驗證會員/LINE/手機/郵件收件身分；路由不可使用模型產生的新地址。

**保存分類：** `customer`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：verified / unsubscribed / blocked / revoked 預設 `'verified'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `channel` | `text` | 是 | internal | web/line/sms/email |
| `provider` | `text` | 是 | internal | 通路provider |
| `destination_ciphertext` | `bytea` | 是 | personal | 實際收件address/subject加密 |
| `key_ref` | `text` | 是 | internal | KMS reference |
| `destination_hmac` | `char(64)` | 是 | internal | 正規化收件subject的keyed HMAC |
| `proof_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `external_identity_id` | `uuid` | 否 | internal | 指向 customer.external_identity，必須同一 tenant。 FK: `customer.external_identity`。 |
| `verified_at` | `timestamptz` | 是 | internal | 身份與channel同一性核驗時間 |
| `service_delivery_enabled` | `boolean` | 是 | internal | 客戶服務通知通路授權 預設 `true`。 |

**約束：**

- `CHECK (status IN ('verified','unsubscribed','blocked','revoked'))`
- `CHECK (channel IN ('web','line','sms','email'))`
- UNIQUE `(tenant_id, provider, channel, destination_hmac)`。

## delivery.attempt

**寫入負責：** M09。**用途：** 各次對 provider 實際傳送嘗試。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `message_id` | `uuid` | 是 | internal | 指向 delivery.message，必須同一 tenant。 FK: `delivery.message`。 |
| `attempt_no` | `integer` | 是 | internal | 遞增嘗試編號 |
| `provider` | `text` | 是 | internal | 供應商 |
| `provider_message_id` | `text` | 否 | internal | provider ID |
| `outcome` | `text` | 是 | internal | accepted/rejected/timeout/unknown |
| `started_at` | `timestamptz` | 是 | internal | 開始呼叫 |
| `finished_at` | `timestamptz` | 否 | internal | 呼叫結束 |
| `error_code` | `text` | 否 | internal | 安全錯誤碼 |

**約束：**

- `CHECK (outcome IN ('accepted','rejected','timeout','unknown'))`
- UNIQUE `(tenant_id, message_id, attempt_no)`。

## delivery.receipt

**寫入負責：** M09。**用途：** 回呼事件不可變，亂序回呼不可把 delivered 降級為 sent。

**保存分類：** `report`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `message_id` | `uuid` | 是 | internal | 指向 delivery.message，必須同一 tenant。 FK: `delivery.message`。 |
| `provider` | `text` | 是 | internal | 供應商 |
| `provider_event_id` | `text` | 是 | internal | 回呼唯一 ID |
| `receipt_type` | `text` | 是 | internal | accepted/sent/delivered/read/failed |
| `provider_occurred_at` | `timestamptz` | 是 | internal | provider 事件時間 |
| `received_at` | `timestamptz` | 是 | internal | 平台收到時間 |
| `payload_sha256` | `char(64)` | 是 | internal | 驗證後回呼摘要 |

**約束：**

- `CHECK (receipt_type IN ('accepted','sent','delivered','read','failed'))`
- UNIQUE `(tenant_id, provider, provider_event_id)`。

## crm.marketing_attribution

**寫入負責：** M10。**用途：** 客戶最初來源及後續可稽核 attribution；不因換業務清除。

**保存分類：** `crm`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `source_type` | `text` | 是 | internal | web/campaign/referral/manual/unknown |
| `campaign_ref` | `text` | 否 | internal | campaign reference |
| `channel_ref` | `text` | 否 | internal | 流量來源 |
| `acquired_at` | `timestamptz` | 是 | internal | 取得潛客時間 |
| `evidence_sha256` | `char(64)` | 是 | internal | source evidence hash |
| `evidence_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `supersedes_id` | `uuid` | 否 | internal | 指向 crm.marketing_attribution，必須同一 tenant。 FK: `crm.marketing_attribution`。 |

## crm.opportunity

**寫入負責：** M10。**用途：** 首次申辦、續訂、升級或重新開發機會。

**保存分類：** `crm`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：open / qualifying / proposed / awaiting_customer / converted / lost / cancelled 預設 `'open'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `kind` | `text` | 是 | internal | new/renewal/upgrade/reactivation |
| `owner_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `contract_service_id` | `uuid` | 否 | internal | 指向 contract.contract_service，必須同一 tenant。 FK: `contract.contract_service`。 |
| `target_service_code` | `text` | 是 | internal | 建議服務 |
| `due_at` | `timestamptz` | 否 | internal | 預定跟進時間 |
| `loss_reason_code` | `text` | 否 | internal | 流失原因 |

**約束：**

- `CHECK (status IN ('open','qualifying','proposed','awaiting_customer','converted','lost','cancelled'))`
- `CHECK (kind IN ('new','renewal','upgrade','reactivation'))`

## crm.activity

**寫入負責：** M10。**用途：** 真實業務行為及客戶回應證據；AI 建議、群發或分配名字不能單獨計獎。

**保存分類：** `crm`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `opportunity_id` | `uuid` | 否 | internal | 指向 crm.opportunity，必須同一 tenant。 FK: `crm.opportunity`。 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `activity_type` | `text` | 是 | internal | call/meeting/conversation/customer_request/automated_campaign |
| `occurred_at` | `timestamptz` | 是 | internal | 實際接觸時間 |
| `two_way` | `boolean` | 是 | internal | 有客戶回應 |
| `evidence_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `outcome_code` | `text` | 是 | internal | 結果代碼 |
| `qualifies_for_claim` | `boolean` | 是 | internal | 僅候選證據，最終仍須 conversion 核定 預設 `false`。 |

## crm.conversion

**寫入負責：** M10。**用途：** 單筆交易銷售歸因；不是單純維護人欄位。

**保存分類：** `commission`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `opportunity_id` | `uuid` | 是 | internal | 指向 crm.opportunity，必須同一 tenant。 FK: `crm.opportunity`。 |
| `contract_service_id` | `uuid` | 是 | internal | 指向 contract.contract_service，必須同一 tenant。 FK: `contract.contract_service`。 |
| `sales_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `qualifying_activity_id` | `uuid` | 否 | internal | 指向 crm.activity，必須同一 tenant。 FK: `crm.activity`。 |
| `conversion_type` | `text` | 是 | internal | self_service/sales_assisted |
| `decision_status` | `text` | 是 | internal | proposed/approved/rejected/disputed |
| `decided_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `converted_at` | `timestamptz` | 是 | internal | 完成續訂/升級時間 |
| `attribution_id` | `uuid` | 否 | internal | 指向 crm.marketing_attribution，必須同一 tenant。 FK: `crm.marketing_attribution`。 |
| `policy_version` | `text` | 是 | internal | 歸因規則版本 |

**約束：**

- `CHECK (conversion_type IN ('self_service','sales_assisted'))`
- `CHECK (decision_status IN ('proposed','approved','rejected','disputed'))`
- `CHECK (conversion_type <> 'sales_assisted' OR (sales_actor_id IS NOT NULL AND qualifying_activity_id IS NOT NULL))`
- UNIQUE `(tenant_id, contract_service_id)`。

## crm.work_item

**寫入負責：** M10。**用途：** 業務要執行的具體名單工作；不只顯示分數。

**保存分類：** `crm`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：open / assigned / in_progress / done / snoozed / cancelled 預設 `'open'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `opportunity_id` | `uuid` | 否 | internal | 指向 crm.opportunity，必須同一 tenant。 FK: `crm.opportunity`。 |
| `owner_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `reason_code` | `text` | 是 | internal | incomplete_application/renewal/upgrade/recovery/service_risk 等 |
| `next_action` | `text` | 是 | internal | 具體下一步 |
| `due_at` | `timestamptz` | 是 | internal | 到期時間 |
| `dedupe_key` | `text` | 是 | internal | 避免同一客戶同類工作重複 |
| `explanation` | `jsonb` | 是 | internal | 規則來源與可解釋證據 IDs；非 AML 明細。 |

**約束：**

- `CHECK (status IN ('open','assigned','in_progress','done','snoozed','cancelled'))`
- UNIQUE `(tenant_id, dedupe_key)`。

## crm.suppression

**寫入負責：** M10。**用途：** 限制招攬但不停止必要服務通知。

**保存分類：** `crm`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `scope` | `text` | 是 | internal | marketing/all_sales/channel_code |
| `reason_code` | `text` | 是 | internal | opt_out/complaint/hold/temporary |
| `valid_from` | `timestamptz` | 是 | internal | 開始 |
| `valid_until` | `timestamptz` | 否 | internal | 截止，null 為直到另行撤銷 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |

**約束：**

- `CHECK (valid_until IS NULL OR valid_until > valid_from)`

## ownership.policy_version

**寫入負責：** M11。**用途：** Pool 判定政策；七天未聯絡不是既定規則。

**保存分類：** `policy`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `policy_code` | `text` | 是 | internal | pool policy |
| `policy_version` | `integer` | 是 | internal | 版本 |
| `approval_status` | `text` | 是 | internal | draft/approved/retired |
| `proposed_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `lapse_basis` | `text` | 是 | internal | proposed_all_paid_services_ended；需依決策登錄核定 |
| `lapse_days` | `integer` | 是 | internal | 連續中斷天數 預設 `7`。 |
| `departure_immediate` | `boolean` | 是 | internal | 離職即歸公司 預設 `true`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_at` | `timestamptz` | 否 | internal | 核准時間 |

**約束：**

- `CHECK (approval_status IN ('draft','approved','retired'))`
- `CHECK (lapse_days > 0)`
- `CHECK (approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by))`
- UNIQUE `(tenant_id, policy_code, policy_version)`。

## ownership.assignment

**寫入負責：** M11。**用途：** 維護關係的有效時間；不直接決定所有交易佣金。

**保存分類：** `commission`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `owner_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `owner_type` | `text` | 是 | internal | company_pool/sales |
| `valid_from` | `timestamptz` | 是 | internal | 歸屬生效 |
| `valid_until` | `timestamptz` | 否 | internal | 歸屬終止，null 仍有效 |
| `reason_code` | `text` | 是 | internal | assignment/departure/service_lapse/redevelopment |
| `policy_version_id` | `uuid` | 是 | internal | 指向 ownership.policy_version，必須同一 tenant。 FK: `ownership.policy_version`。 |
| `assigned_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `predecessor_id` | `uuid` | 否 | internal | 指向 ownership.assignment，必須同一 tenant。 FK: `ownership.assignment`。 |

**約束：**

- `CHECK (owner_type IN ('company_pool','sales'))`
- `CHECK ((owner_type = 'sales') = (owner_actor_id IS NOT NULL))`
- `CHECK (valid_until IS NULL OR valid_until > valid_from)`

## ownership.pool_entry

**寫入負責：** M11。**用途：** 客戶回公司 Pool 的證據與重新開發狀態。

**保存分類：** `commission`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `assignment_id` | `uuid` | 是 | internal | 指向 ownership.assignment，必須同一 tenant。 FK: `ownership.assignment`。 |
| `reason_code` | `text` | 是 | internal | staff_departure/all_paid_services_lapsed |
| `all_paid_services_ended_at` | `timestamptz` | 否 | internal | 全部付費服務中斷起點；離職時可 null |
| `entered_at` | `timestamptz` | 是 | internal | 真正歸公司時間 |
| `claimed_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `claimed_at` | `timestamptz` | 否 | internal | 新業務接手時間 |
| `claim_activity_id` | `uuid` | 否 | internal | 指向 crm.activity，必須同一 tenant。 FK: `crm.activity`。 |
| `evaluation_snapshot` | `jsonb` | 是 | internal | 所查全部 service ids/status/period/policy；有年度 active 服務不可用月訂中斷判全客流失。 |

**約束：**

- `CHECK (reason_code IN ('staff_departure','all_paid_services_lapsed'))`
- `CHECK (reason_code <> 'all_paid_services_lapsed' OR all_paid_services_ended_at IS NOT NULL)`

## support.case_file

**寫入負責：** M13。**用途：** 線上線下統一客服案件；客服 owner 不等於業務歸屬。

**保存分類：** `support`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：new / triaged / assigned / waiting_customer / waiting_internal / resolved / closed / reopened 預設 `'new'`。 |
| `customer_id` | `uuid` | 否 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `case_number` | `text` | 是 | internal | 案件號 |
| `category` | `text` | 是 | internal | service/complaint/refund/research/access/security |
| `priority` | `text` | 是 | internal | low/normal/high/critical |
| `owner_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `contract_id` | `uuid` | 否 | internal | 指向 contract.contract，必須同一 tenant。 FK: `contract.contract`。 |
| `publication_id` | `uuid` | 否 | internal | 指向 publication.publication，必須同一 tenant。 FK: `publication.publication`。 |
| `sla_policy_version` | `text` | 是 | internal | SLA 規則版本 |
| `first_response_due_at` | `timestamptz` | 是 | internal | 首次回應期限 |
| `resolution_due_at` | `timestamptz` | 否 | internal | 解決期限 |
| `resolved_at` | `timestamptz` | 否 | internal | 解決時間 |
| `resolution_code` | `text` | 否 | internal | 結案分類 |

**約束：**

- `CHECK (status IN ('new','triaged','assigned','waiting_customer','waiting_internal','resolved','closed','reopened'))`
- UNIQUE `(tenant_id, case_number)`。

## support.interaction

**寫入負責：** M13。**用途：** 官網、訊息、郵件、電話、現場之原始接觸與人工/AI摘要來源。

**保存分類：** `support`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `case_id` | `uuid` | 是 | internal | 指向 support.case_file，必須同一 tenant。 FK: `support.case_file`。 |
| `channel` | `text` | 是 | internal | web/line/email/phone/onsite |
| `direction` | `text` | 是 | internal | inbound/outbound/internal |
| `actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `external_message_id` | `text` | 否 | internal | 來源訊息 ID |
| `content_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `occurred_at` | `timestamptz` | 是 | internal | 接觸時間 |
| `recording_consent_ref` | `text` | 否 | internal | 錄音同意/告知參考 |
| `ai_draft` | `boolean` | 是 | internal | 是 AI 草稿則禁止當已送出 預設 `false`。 |

**約束：**

- `CHECK (channel IN ('web','line','email','phone','onsite'))`
- `CHECK (direction IN ('inbound','outbound','internal'))`
- UNIQUE `(tenant_id, channel, external_message_id)`。

## support.sla_event

**寫入負責：** M13。**用途：** SLA 暫停、恢復、升級的 append-only 事件。

**保存分類：** `support`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `case_id` | `uuid` | 是 | internal | 指向 support.case_file，必須同一 tenant。 FK: `support.case_file`。 |
| `action` | `text` | 是 | internal | started/paused/resumed/escalated/met/breached |
| `occurred_at` | `timestamptz` | 是 | internal | 發生時間 |
| `actor_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `reason_code` | `text` | 是 | internal | 理由 |

## support.response_draft

**寫入負責：** M13。**用途：** 客服回覆草稿與來源版本；草稿永遠不代表可直接對外發送。

**保存分類：** `support`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：queued / drafted / requires_human / blocked / failed 預設 `'queued'`。 |
| `case_id` | `uuid` | 是 | internal | 指向 support.case_file，必須同一 tenant。 FK: `support.case_file`。 |
| `created_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `content_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `content_sha256` | `char(64)` | 否 | internal | 草稿精確內容hash；產生後不得原地改寫 |
| `knowledge_version` | `text` | 是 | internal | 核准知識庫版本 |
| `model_version` | `text` | 是 | internal | 固定模型或manual |
| `prompt_version` | `text` | 是 | internal | prompt版本或manual |
| `source_refs` | `jsonb` | 是 | internal | 核准來源UUID列表 |
| `contains_new_investment_judgment` | `boolean` | 是 | internal | 偵測新投資判斷時阻擋客服發送 預設 `false`。 |
| `supersedes_id` | `uuid` | 否 | internal | 指向 support.response_draft，必須同一 tenant。 FK: `support.response_draft`。 |

**約束：**

- `CHECK (status IN ('queued','drafted','requires_human','blocked','failed'))`
- `CHECK ((content_asset_id IS NULL) = (content_sha256 IS NULL))`
- `CHECK (status NOT IN ('drafted','requires_human') OR content_asset_id IS NOT NULL)`

## support.response_approval

**寫入負責：** M13。**用途：** 精確草稿的核准或駁回證據；low-risk template由受控policy service記錄。

**保存分類：** `support`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `draft_id` | `uuid` | 是 | internal | 指向 support.response_draft，必須同一 tenant。 FK: `support.response_draft`。 |
| `draft_content_sha256` | `char(64)` | 是 | internal | 被決定之草稿hash |
| `decision` | `text` | 是 | internal | approve/reject |
| `approval_mode` | `text` | 是 | internal | human_reviewed/approved_low_risk_template |
| `reviewer_id` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `template_version` | `text` | 是 | internal | 核准低風險模板版本；human-only可填manual |
| `policy_version` | `text` | 是 | internal | 自動回覆或人工覆核政策 |
| `evidence_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `approved_at` | `timestamptz` | 是 | internal | 決策真實時點 |
| `valid_until` | `timestamptz` | 是 | internal | 核定的發送期限 |

**約束：**

- `CHECK (decision IN ('approve','reject'))`
- `CHECK (approval_mode IN ('human_reviewed','approved_low_risk_template'))`
- `CHECK (valid_until>approved_at)`

## support.response_dispatch

**寫入負責：** M13。**用途：** 已核准客服回覆的邏輯發送，與人工記錄interaction分開。

**保存分類：** `support`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：queued / accepted / sent / delivered / failed / unknown / cancelled / expired 預設 `'queued'`。 |
| `case_id` | `uuid` | 是 | internal | 指向 support.case_file，必須同一 tenant。 FK: `support.case_file`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `draft_id` | `uuid` | 是 | internal | 指向 support.response_draft，必須同一 tenant。 FK: `support.response_draft`。 |
| `content_sha256` | `char(64)` | 是 | internal | 核准精確內容hash |
| `channel_binding_id` | `uuid` | 是 | internal | 指向 delivery.channel_binding，必須同一 tenant。 FK: `delivery.channel_binding`。 |
| `channel` | `text` | 是 | internal | web/line/sms/email |
| `approval_id` | `uuid` | 是 | internal | 指向 support.response_approval，必須同一 tenant。 FK: `support.response_approval`。 |
| `approval_mode` | `text` | 是 | internal | human_reviewed/approved_low_risk_template |
| `idempotency_key` | `text` | 是 | internal | 邏輯回覆穩定key |
| `requested_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `publication_reference_ids` | `jsonb` | 是 | internal | 只連結已發布且客戶有權限之內容；不可加入新投資判斷。 |
| `provider_message_ref` | `text` | 否 | internal | 外部消息識別 |
| `accepted_at` | `timestamptz` | 否 | internal | provider接受時間 |
| `sent_at` | `timestamptz` | 否 | internal | provider確認送出 |
| `delivered_at` | `timestamptz` | 否 | internal | provider確認送達 |
| `read_at` | `timestamptz` | 否 | internal | 僅可證實閱讀才填 |
| `valid_until` | `timestamptz` | 是 | internal | 回覆不得再送出的期限 |
| `outbound_interaction_id` | `uuid` | 否 | internal | 指向 support.interaction，必須同一 tenant。 FK: `support.interaction`。 |

**約束：**

- `CHECK (status IN ('queued','accepted','sent','delivered','failed','unknown','cancelled','expired'))`
- `CHECK (channel IN ('web','line','sms','email'))`
- `CHECK (approval_mode IN ('human_reviewed','approved_low_risk_template'))`
- UNIQUE `(tenant_id, idempotency_key)`。

## support.response_attempt

**寫入負責：** M13。**用途：** 客服外送provider各次呼叫結果；未知狀態先查詢再重試。

**保存分類：** `support`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `response_id` | `uuid` | 是 | internal | 指向 support.response_dispatch，必須同一 tenant。 FK: `support.response_dispatch`。 |
| `attempt_no` | `integer` | 是 | internal | 嘗試序號 |
| `provider` | `text` | 是 | internal | 通路provider |
| `provider_request_id` | `text` | 是 | internal | 供應商請求/去重key |
| `outcome` | `text` | 是 | internal | accepted/rejected/timeout/unknown |
| `started_at` | `timestamptz` | 是 | internal | 呼叫開始 |
| `finished_at` | `timestamptz` | 否 | internal | 呼叫結束 |
| `error_code` | `text` | 否 | internal | 安全錯誤代碼 |

**約束：**

- `CHECK (outcome IN ('accepted','rejected','timeout','unknown'))`
- UNIQUE `(tenant_id, response_id, attempt_no)`。

## support.response_receipt

**寫入負責：** M13。**用途：** 客服發送回條不可變；accepted/sent/delivered/read 分開。

**保存分類：** `support`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `response_id` | `uuid` | 是 | internal | 指向 support.response_dispatch，必須同一 tenant。 FK: `support.response_dispatch`。 |
| `provider` | `text` | 是 | internal | 通路provider |
| `event_id` | `text` | 是 | internal | 來源回條唯一ID |
| `provider_message_ref` | `text` | 是 | internal | provider消息ID |
| `provider_status` | `text` | 是 | internal | accepted/sent/delivered/failed/read |
| `provider_occurred_at` | `timestamptz` | 是 | internal | provider真實時間 |
| `received_at` | `timestamptz` | 是 | internal | 平台收到時間 |
| `provider_receipt_asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `payload_sha256` | `char(64)` | 是 | internal | 驗證過的回條hash |

**約束：**

- `CHECK (provider_status IN ('accepted','sent','delivered','failed','read'))`
- UNIQUE `(tenant_id, provider, event_id)`。

## finance.invoice

**寫入負責：** M14。**用途：** 應收/發票文件與稅別；不得預設所有服務都是5%營業稅。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：draft / issued / paid / voided / credited 預設 `'draft'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `contract_id` | `uuid` | 是 | internal | 指向 contract.contract，必須同一 tenant。 FK: `contract.contract`。 |
| `invoice_number` | `text` | 是 | internal | 內部或電子發票識別碼 |
| `currency` | `text` | 是 | internal | ISO currency |
| `untaxed_amount` | `numeric(20,6)` | 是 | internal | 經核准稅類之未稅金額 |
| `tax_amount` | `numeric(20,6)` | 是 | internal | 稅額 |
| `gross_amount` | `numeric(20,6)` | 是 | internal | 應付總額 |
| `tax_profile_ref` | `text` | 是 | internal | 核准稅務映射版本 |
| `issued_at` | `timestamptz` | 否 | internal | 開立時間 |
| `due_at` | `timestamptz` | 否 | internal | 到期時間 |

**約束：**

- `CHECK (status IN ('draft','issued','paid','voided','credited'))`
- `CHECK (gross_amount = untaxed_amount + tax_amount)`
- `CHECK (untaxed_amount >= 0 AND tax_amount >= 0)`
- UNIQUE `(tenant_id, invoice_number)`。

## finance.invoice_line

**寫入負責：** M14。**用途：** 逐服務帳款明細；升級增額不重複算原方案。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `invoice_id` | `uuid` | 是 | internal | 指向 finance.invoice，必須同一 tenant。 FK: `finance.invoice`。 |
| `contract_service_id` | `uuid` | 是 | internal | 指向 contract.contract_service，必須同一 tenant。 FK: `contract.contract_service`。 |
| `line_no` | `integer` | 是 | internal | 項次 |
| `untaxed_amount` | `numeric(20,6)` | 是 | internal | 未稅服務額 |
| `tax_amount` | `numeric(20,6)` | 是 | internal | 稅額 |
| `description_code` | `text` | 是 | internal | 受控服務描述 |

**約束：**

- `CHECK (untaxed_amount >= 0 AND tax_amount >= 0)`
- UNIQUE `(tenant_id, invoice_id, line_no)`。

## finance.payment

**寫入負責：** M14。**用途：** 收款與授權；收現不直接算營收，未接受可為暫收款。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：pending / authorized / paid / failed / refund_pending / refunded / disputed 預設 `'pending'`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `invoice_id` | `uuid` | 否 | internal | 指向 finance.invoice，必須同一 tenant。 FK: `finance.invoice`。 |
| `application_id` | `uuid` | 否 | internal | 指向 customer.application，必須同一 tenant。 FK: `customer.application`。 |
| `provider` | `text` | 是 | internal | 支付供應商 |
| `provider_transaction_id` | `text` | 是 | internal | 供應商交易號 |
| `amount` | `numeric(20,6)` | 是 | internal | 實際收款或授權金額 |
| `currency` | `text` | 是 | internal | 幣別 |
| `paid_at` | `timestamptz` | 否 | internal | 收款時間 |
| `payment_method_ref` | `text` | 否 | internal | tokenized method reference，禁止 PAN/CVV |

**約束：**

- `CHECK (status IN ('pending','authorized','paid','failed','refund_pending','refunded','disputed'))`
- `CHECK (amount >= 0)`
- `CHECK (invoice_id IS NOT NULL OR application_id IS NOT NULL)`
- UNIQUE `(tenant_id, provider, provider_transaction_id)`。

## finance.settlement

**寫入負責：** M14。**用途：** 支付商入帳／銀行結算，手續費分列。

**保存分類：** `accounting`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `payment_id` | `uuid` | 是 | internal | 指向 finance.payment，必須同一 tenant。 FK: `finance.payment`。 |
| `provider_settlement_id` | `text` | 是 | internal | 結算唯一 ID |
| `gross_amount` | `numeric(20,6)` | 是 | internal | 原收款總額 |
| `fee_amount` | `numeric(20,6)` | 是 | internal | 支付費用 |
| `net_amount` | `numeric(20,6)` | 是 | internal | 銀行淨入帳 |
| `currency` | `text` | 是 | internal | 幣別 |
| `settled_at` | `timestamptz` | 是 | internal | 銀行入帳時間 |
| `bank_line_ref` | `text` | 是 | internal | 銀行明細引用 |

**約束：**

- `CHECK (net_amount = gross_amount - fee_amount)`
- `CHECK (fee_amount >= 0)`
- UNIQUE `(tenant_id, provider_settlement_id)`。

## finance.reconciliation_item

**寫入負責：** M14。**用途：** 金流、發票、銀行與總帳差異處理。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：unmatched / matched / exception / resolved 預設 `'unmatched'`。 |
| `source_type` | `text` | 是 | internal | payment/settlement/bank/invoice/gl |
| `source_ref` | `text` | 是 | internal | 來源識別 |
| `payment_id` | `uuid` | 否 | internal | 指向 finance.payment，必須同一 tenant。 FK: `finance.payment`。 |
| `difference_amount` | `numeric(20,6)` | 是 | internal | 差異金額 |
| `currency` | `text` | 是 | internal | 幣別 |
| `reason_code` | `text` | 否 | internal | 差異代碼 |
| `resolved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `resolved_at` | `timestamptz` | 否 | internal | 解決時間 |

**約束：**

- `CHECK (status IN ('unmatched','matched','exception','resolved'))`
- UNIQUE `(tenant_id, source_type, source_ref)`。

## finance.service_schedule

**寫入負責：** M14。**用途：** 已接受契約的服務收入認列分期表，與收現分離。

**保存分類：** `accounting`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `contract_service_id` | `uuid` | 是 | internal | 指向 contract.contract_service，必須同一 tenant。 FK: `contract.contract_service`。 |
| `schedule_version` | `integer` | 是 | internal | 分期表版本 |
| `period_start` | `timestamptz` | 是 | internal | 本次履約區間開始 |
| `period_end` | `timestamptz` | 是 | internal | 區間截止 |
| `untaxed_amount` | `numeric(20,6)` | 是 | internal | 本區間應認列未稅額 |
| `recognition_policy_version` | `text` | 是 | internal | 財會核准之認列政策 |
| `approved_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |

**約束：**

- `CHECK (period_end > period_start)`
- `CHECK (untaxed_amount >= 0)`
- UNIQUE `(tenant_id, contract_service_id, schedule_version, period_start)`。

## finance.revenue_run

**寫入負責：** M14。**用途：** 收入認列批次之計算、覆核及正式過帳；dry-run不可進入付款/計獎。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：queued / calculated / review_pending / approved / posted / failed 預設 `'queued'`。 |
| `period_start` | `date` | 是 | internal | 會計期間開始 |
| `period_end` | `date` | 是 | internal | 會計期間結束，exclusive |
| `cutoff_at` | `timestamptz` | 是 | internal | 本批次資料截止時間 |
| `recognition_policy_version` | `text` | 是 | internal | 財會核准認列政策版本 |
| `dry_run` | `boolean` | 是 | internal | 僅試算則不能正式過帳 |
| `calculation_manifest_sha256` | `char(64)` | 是 | internal | 輸入資料與計算manifest hash |
| `currency` | `text` | 是 | internal | 幣別 |
| `recognized_untaxed_total` | `numeric(20,6)` | 是 | internal | 本批計算收入 |
| `deferred_remaining` | `numeric(20,6)` | 是 | internal | 剩餘預收負債 |
| `entry_count` | `integer` | 是 | internal | 本批明細數 預設 `0`。 |
| `exception_count` | `integer` | 是 | internal | 未解例外數 預設 `0`。 |
| `prepared_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_at` | `timestamptz` | 否 | internal | 覆核時間 |
| `posted_at` | `timestamptz` | 否 | internal | 過帳時間 |
| `run_key` | `text` | 是 | internal | 輸入與政策的去重key |

**約束：**

- `CHECK (status IN ('queued','calculated','review_pending','approved','posted','failed'))`
- `CHECK (period_end > period_start)`
- `CHECK (approved_by IS NULL OR approved_by<>prepared_by)`
- `CHECK (entry_count>=0 AND exception_count>=0)`
- `CHECK (status NOT IN ('approved','posted') OR (NOT dry_run AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND exception_count=0))`
- UNIQUE `(tenant_id, run_key)`。

## finance.revenue_recognition

**寫入負責：** M14。**用途：** 財會確認的收入／更正；獎金只消費 eligible 收入。

**保存分類：** `accounting`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `run_id` | `uuid` | 是 | internal | 指向 finance.revenue_run，必須同一 tenant。 FK: `finance.revenue_run`。 |
| `schedule_id` | `uuid` | 是 | internal | 指向 finance.service_schedule，必須同一 tenant。 FK: `finance.service_schedule`。 |
| `recognition_key` | `text` | 是 | internal | 認列去重 key |
| `recognized_at` | `timestamptz` | 是 | internal | 收入入帳時間 |
| `untaxed_amount` | `numeric(20,6)` | 是 | internal | 實際已履約未稅收入；更正以新列負數 |
| `currency` | `text` | 是 | internal | 幣別 |
| `confirmed_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `commission_eligible` | `boolean` | 是 | internal | 可計獎標記 |
| `reverses_id` | `uuid` | 否 | internal | 指向 finance.revenue_recognition，必須同一 tenant。 FK: `finance.revenue_recognition`。 |
| `policy_version` | `text` | 是 | internal | 認列政策 |

**約束：**

- UNIQUE `(tenant_id, recognition_key)`。

## finance.refund

**寫入負責：** M14。**用途：** 退款簽核與 provider 結果；失敗及未知不可當成功。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：requested / checking / approval_pending / approved / submitted / confirmed / failed / reconciliation_required / closed / rejected 預設 `'requested'`。 |
| `payment_id` | `uuid` | 是 | internal | 指向 finance.payment，必須同一 tenant。 FK: `finance.payment`。 |
| `customer_id` | `uuid` | 是 | internal | 指向 customer.customer，必須同一 tenant。 FK: `customer.customer`。 |
| `case_id` | `uuid` | 否 | internal | 指向 support.case_file，必須同一 tenant。 FK: `support.case_file`。 |
| `amount` | `numeric(20,6)` | 是 | internal | 退款實際金額 |
| `currency` | `text` | 是 | internal | 幣別 |
| `prepared_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `provider_refund_id` | `text` | 否 | internal | provider 退款交易 |
| `confirmed_at` | `timestamptz` | 否 | internal | provider 已證實退款時間 |
| `reason_code` | `text` | 是 | internal | 退款理由 |
| `idempotency_key` | `text` | 是 | internal | 固定退款命令 key |

**約束：**

- `CHECK (status IN ('requested','checking','approval_pending','approved','submitted','confirmed','failed','reconciliation_required','closed','rejected'))`
- `CHECK (amount > 0)`
- `CHECK (approved_by IS NULL OR approved_by <> prepared_by)`
- `CHECK (status NOT IN ('approved','submitted','confirmed','closed') OR approved_by IS NOT NULL)`
- UNIQUE `(tenant_id, idempotency_key)`。

## finance.refund_allocation

**寫入負責：** M14。**用途：** 退款拆分已認列收入沖減與未履約負債返還，避免獎金重扣。

**保存分類：** `accounting`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `refund_id` | `uuid` | 是 | internal | 指向 finance.refund，必須同一 tenant。 FK: `finance.refund`。 |
| `recognition_id` | `uuid` | 否 | internal | 指向 finance.revenue_recognition，必須同一 tenant。 FK: `finance.revenue_recognition`。 |
| `allocation_type` | `text` | 是 | internal | earned_revenue/unearned_prepayment/tax/fee |
| `amount` | `numeric(20,6)` | 是 | internal | 歸屬金額，非負 |
| `currency` | `text` | 是 | internal | 幣別 |

**約束：**

- `CHECK (allocation_type IN ('earned_revenue','unearned_prepayment','tax','fee'))`
- `CHECK (amount >= 0)`
- `CHECK ((allocation_type = 'earned_revenue') = (recognition_id IS NOT NULL))`

## finance.journal

**寫入負責：** M14。**用途：** 會計分錄憑證；posted 必須平衡且 maker/checker。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：draft / approved / posted / reversed 預設 `'draft'`。 |
| `journal_number` | `text` | 是 | internal | 分錄編號 |
| `source_type` | `text` | 是 | internal | payment/revenue/refund/commission/adjustment |
| `source_id` | `uuid` | 是 | internal | 來源 ID |
| `currency` | `text` | 是 | internal | 幣別 |
| `accounting_date` | `date` | 是 | internal | 會計日，以 Asia/Taipei 期間判斷 |
| `prepared_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `posted_at` | `timestamptz` | 否 | internal | 過帳時間 |
| `reverses_id` | `uuid` | 否 | internal | 指向 finance.journal，必須同一 tenant。 FK: `finance.journal`。 |

**約束：**

- `CHECK (status IN ('draft','approved','posted','reversed'))`
- `CHECK (approved_by IS NULL OR approved_by <> prepared_by)`
- `CHECK (status NOT IN ('approved','posted') OR approved_by IS NOT NULL)`
- UNIQUE `(tenant_id, journal_number)`。
- UNIQUE `(tenant_id, source_type, source_id)`。

## finance.journal_line

**寫入負責：** M14。**用途：** 雙式簿記借貸明細；全憑證延遲檢查平衡。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `journal_id` | `uuid` | 是 | internal | 指向 finance.journal，必須同一 tenant。 FK: `finance.journal`。 |
| `line_no` | `integer` | 是 | internal | 項次 |
| `account_code` | `text` | 是 | internal | 財會核准科目 |
| `debit` | `numeric(20,6)` | 是 | internal | 借方 |
| `credit` | `numeric(20,6)` | 是 | internal | 貸方 |
| `cost_center` | `text` | 否 | internal | 部門歸屬 |

**約束：**

- `CHECK (debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0))`
- UNIQUE `(tenant_id, journal_id, line_no)`。

## finance.accounting_export

**寫入負責：** M14。**用途：** 匯出會計系統的批次檔與對帳回條。

**保存分類：** `accounting`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：prepared / submitted / acknowledged / rejected / reconciled 預設 `'prepared'`。 |
| `period_key` | `text` | 是 | internal | 會計期間 YYYY-MM |
| `destination` | `text` | 是 | internal | 會計 ERP |
| `asset_id` | `uuid` | 是 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `export_sha256` | `char(64)` | 是 | internal | 匯出 hash |
| `external_batch_id` | `text` | 否 | internal | 外部批號 |
| `prepared_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `journal_ids` | `jsonb` | 是 | internal | 本批次 journal refs |

**約束：**

- `CHECK (status IN ('prepared','submitted','acknowledged','rejected','reconciled'))`
- `CHECK (approved_by IS NULL OR approved_by <> prepared_by)`
- UNIQUE `(tenant_id, destination, export_sha256)`。

## finance.control_exception

**寫入負責：** M14。**用途：** 內控例外、補正、覆核與稽核證據索引；避免散落試算表。

**保存分類：** `audit`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：open / assigned / remediating / awaiting_review / closed / waived 預設 `'open'`。 |
| `control_code` | `text` | 是 | internal | 控制項代碼 |
| `resource_type` | `text` | 是 | internal | 資源類型 |
| `resource_id` | `uuid` | 是 | internal | 資源 ID |
| `severity` | `text` | 是 | internal | low/medium/high/critical |
| `owner_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `reviewer_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `due_at` | `timestamptz` | 是 | internal | 補正期限 |
| `evidence_asset_id` | `uuid` | 否 | internal | 指向 core.object_asset，必須同一 tenant。 FK: `core.object_asset`。 |
| `resolution_code` | `text` | 否 | internal | 關閉或限期例外理由 |

**約束：**

- `CHECK (status IN ('open','assigned','remediating','awaiting_review','closed','waived'))`
- `CHECK (owner_actor_id IS NULL OR reviewer_id IS NULL OR owner_actor_id <> reviewer_id)`

## commission.policy_version

**寫入負責：** M12。**用途：** 獎金演算法、來源年齡係數及未核准參數的版本化政策。

**保存分類：** `commission`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `policy_code` | `text` | 是 | internal | 政策碼 |
| `policy_version` | `integer` | 是 | internal | 版本 |
| `approval_status` | `text` | 是 | internal | draft/approved/retired |
| `proposed_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `base_pool_rate` | `numeric(9,6)` | 否 | internal | 獎金池率；未核准為 null，不能偷偷預設3%。 |
| `marketing_year1` | `numeric(9,6)` | 是 | internal | 用戶已定第一年係數 預設 `1.0`。 |
| `marketing_year2` | `numeric(9,6)` | 是 | internal | 第二年係數 預設 `0.5`。 |
| `marketing_auto_tail` | `numeric(9,6)` | 是 | internal | 第三年起合格連續自動續訂係數 預設 `0.25`。 |
| `assisted_marketing_share` | `numeric(9,6)` | 是 | internal | 介入續約行銷池比例 預設 `0.3`。 |
| `assisted_sales_share` | `numeric(9,6)` | 是 | internal | 介入續約業務池比例 預設 `0.7`。 |
| `decay_interaction` | `text` | 是 | internal | unresolved/marketing_only/whole_pool |
| `tail_lineage_rule` | `text` | 是 | internal | unresolved / 另行核准的服務鏈規則 |
| `effective_from` | `timestamptz` | 是 | internal | 政策計算開始 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_at` | `timestamptz` | 否 | internal | 政策核准時間 |

**約束：**

- `CHECK (approval_status IN ('draft','approved','retired'))`
- `CHECK (base_pool_rate IS NULL OR base_pool_rate BETWEEN 0 AND 1)`
- `CHECK (assisted_marketing_share + assisted_sales_share = 1)`
- `CHECK (marketing_year1 BETWEEN 0 AND 1 AND marketing_year2 BETWEEN 0 AND 1 AND marketing_auto_tail BETWEEN 0 AND 1)`
- `CHECK (approval_status <> 'approved' OR (base_pool_rate IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by AND decay_interaction <> 'unresolved' AND tail_lineage_rule <> 'unresolved'))`
- UNIQUE `(tenant_id, policy_code, policy_version)`。

## commission.calculation_run

**寫入負責：** M12。**用途：** 可重現的試算或正式計獎批次；未核准政策只可 simulation。

**保存分類：** `commission`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：queued / calculating / calculated / review_pending / approved / rejected / exported / failed 預設 `'queued'`。 |
| `policy_version_id` | `uuid` | 是 | internal | 指向 commission.policy_version，必須同一 tenant。 FK: `commission.policy_version`。 |
| `mode` | `text` | 是 | internal | simulation/live |
| `period_key` | `text` | 是 | internal | YYYY-MM |
| `run_key` | `text` | 是 | internal | 唯一計算資料與政策快照 key |
| `period_start` | `date` | 是 | internal | 計獎區間開始 |
| `period_end` | `date` | 是 | internal | 計獎區間結束，exclusive |
| `revenue_cutoff_at` | `timestamptz` | 是 | internal | 收入/退款資料截止 |
| `input_sha256` | `char(64)` | 是 | internal | 輸入收入、退款、歸因、年齡快照 hash |
| `prepared_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_at` | `timestamptz` | 否 | internal | 覆核時間 |
| `started_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `started_at` | `timestamptz` | 是 | internal | 開始 |
| `completed_at` | `timestamptz` | 否 | internal | 計算完成 |

**約束：**

- `CHECK (status IN ('queued','calculating','calculated','review_pending','approved','rejected','exported','failed'))`
- `CHECK (mode IN ('simulation','live'))`
- `CHECK (period_end>period_start)`
- `CHECK (approved_by IS NULL OR approved_by<>prepared_by)`
- `CHECK (status NOT IN ('approved','exported') OR (mode='live' AND approved_by IS NOT NULL AND approved_at IS NOT NULL))`
- UNIQUE `(tenant_id, run_key)`。

## commission.entry

**寫入負責：** M12。**用途：** 逐筆收入切分行銷、業務、公司留存之計算明細，原始列不可改。

**保存分類：** `commission`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `run_id` | `uuid` | 是 | internal | 指向 commission.calculation_run，必須同一 tenant。 FK: `commission.calculation_run`。 |
| `recognition_id` | `uuid` | 是 | internal | 指向 finance.revenue_recognition，必須同一 tenant。 FK: `finance.revenue_recognition`。 |
| `conversion_id` | `uuid` | 否 | internal | 指向 crm.conversion，必須同一 tenant。 FK: `crm.conversion`。 |
| `assignment_id` | `uuid` | 否 | internal | 指向 ownership.assignment，必須同一 tenant。 FK: `ownership.assignment`。 |
| `attribution_id` | `uuid` | 否 | internal | 指向 crm.marketing_attribution，必須同一 tenant。 FK: `crm.marketing_attribution`。 |
| `beneficiary_type` | `text` | 是 | internal | marketing/sales/company |
| `beneficiary_actor_id` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `eligible_untaxed_amount` | `numeric(20,6)` | 是 | internal | 本筆可計獎未稅淨服務收入 |
| `base_pool_amount` | `numeric(20,6)` | 是 | internal | 全筆獎金池 |
| `award_amount` | `numeric(20,6)` | 是 | internal | 該受益者分配額 |
| `currency` | `text` | 是 | internal | 幣別 |
| `customer_year` | `integer` | 是 | internal | 以核准周年時鐘計算的來源年 |
| `age_coefficient` | `numeric(9,6)` | 是 | internal | 實際年齡係數 |
| `calculation_snapshot` | `jsonb` | 是 | internal | 政策版本、原始收入、退款、歸因證據、四捨五入與留存計算 |

**約束：**

- `CHECK (beneficiary_type IN ('marketing','sales','company'))`
- `CHECK (beneficiary_type <> 'sales' OR beneficiary_actor_id IS NOT NULL)`
- `CHECK (award_amount >= 0 AND base_pool_amount >= 0)`
- `CHECK (customer_year >= 1)`
- UNIQUE `(tenant_id, run_id, recognition_id, beneficiary_type)`。

## commission.adjustment

**寫入負責：** M12。**用途：** 退款/歸因修正以原受領人與原政策沖回；不直接扣薪。

**保存分類：** `commission`。**歷史保護：** append-only，UPDATE/DELETE 被拒絕。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `original_entry_id` | `uuid` | 是 | internal | 指向 commission.entry，必須同一 tenant。 FK: `commission.entry`。 |
| `refund_allocation_id` | `uuid` | 否 | internal | 指向 finance.refund_allocation，必須同一 tenant。 FK: `finance.refund_allocation`。 |
| `adjustment_amount` | `numeric(20,6)` | 是 | internal | 有正負號之調整金額 |
| `currency` | `text` | 是 | internal | 幣別 |
| `reason_code` | `text` | 是 | internal | refund/attribution_correction/calculation_correction |
| `prepared_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_at` | `timestamptz` | 是 | internal | 覆核時間 |
| `recovery_method` | `text` | 是 | internal | ledger_offset/manual_review，不代表可自動從薪資扣除 |
| `dedupe_key` | `text` | 是 | internal | 調整唯一鍵 |

**約束：**

- `CHECK (prepared_by <> approved_by)`
- UNIQUE `(tenant_id, dedupe_key)`。

## commission.payout_batch

**寫入負責：** M12。**用途：** 正式獎金支出批次；試算結果不可付款。

**保存分類：** `commission`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `status` | `text` | 是 | internal | 狀態：draft / approval_pending / approved / exported / paid / reversed 預設 `'draft'`。 |
| `period_key` | `text` | 是 | internal | 期間 |
| `prepared_by` | `uuid` | 是 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `approved_by` | `uuid` | 否 | internal | 指向 core.actor，必須同一 tenant。 FK: `core.actor`。 |
| `currency` | `text` | 是 | internal | 幣別 |
| `total_amount` | `numeric(20,6)` | 是 | internal | 付款批次合計 |
| `payroll_export_ref` | `text` | 否 | internal | 薪資/應付匯出引用 |
| `paid_at` | `timestamptz` | 否 | internal | 實際付款時間 |

**約束：**

- `CHECK (status IN ('draft','approval_pending','approved','exported','paid','reversed'))`
- `CHECK (total_amount >= 0)`
- `CHECK (approved_by IS NULL OR approved_by <> prepared_by)`
- `CHECK (status NOT IN ('approved','exported','paid') OR approved_by IS NOT NULL)`

## commission.payout_line

**寫入負責：** M12。**用途：** 獎金列與付款批次關係，避免一筆獎金重複支付。

**保存分類：** `commission`。**歷史保護：** 版本化更新，version 由 DB 遞增；依服務狀態機操作。

| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |
|---|---|---|---|---|
| `tenant_id` | `uuid` | 是 | internal | 組織隔離鍵；由可信任服務在交易開始設定。 |
| `id` | `uuid` | 是 | internal | 資源識別碼；在同一租戶內唯一。 預設 `gen_random_uuid()`。 |
| `version` | `integer` | 是 | internal | CAS 樂觀鎖版本；更新時由資料庫遞增。 預設 `1`。 |
| `created_at` | `timestamptz` | 是 | internal | 建立的 UTC 絕對時間。 預設 `clock_timestamp()`。 |
| `updated_at` | `timestamptz` | 是 | internal | 最近更新時間；歷史證據列不可更新。 預設 `clock_timestamp()`。 |
| `batch_id` | `uuid` | 是 | internal | 指向 commission.payout_batch，必須同一 tenant。 FK: `commission.payout_batch`。 |
| `entry_id` | `uuid` | 是 | internal | 指向 commission.entry，必須同一 tenant。 FK: `commission.entry`。 |
| `amount` | `numeric(20,6)` | 是 | internal | 本批次支付數額 |
| `adjustment_ids` | `jsonb` | 是 | internal | 本次採用的已核准 adjustment IDs |

**約束：**

- `CHECK (amount >= 0)`
- UNIQUE `(tenant_id, entry_id)`。
