-- Generated from database/tools/build_schema.py; version 1.0.
-- PostgreSQL 16+; UTC; synthetic data only.
BEGIN;
SET TIME ZONE 'UTC';
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS customer;
CREATE SCHEMA IF NOT EXISTS contract;
CREATE SCHEMA IF NOT EXISTS kyc;
CREATE SCHEMA IF NOT EXISTS aml;
CREATE SCHEMA IF NOT EXISTS research;
CREATE SCHEMA IF NOT EXISTS review;
CREATE SCHEMA IF NOT EXISTS report;
CREATE SCHEMA IF NOT EXISTS publication;
CREATE SCHEMA IF NOT EXISTS delivery;
CREATE SCHEMA IF NOT EXISTS crm;
CREATE SCHEMA IF NOT EXISTS ownership;
CREATE SCHEMA IF NOT EXISTS support;
CREATE SCHEMA IF NOT EXISTS finance;
CREATE SCHEMA IF NOT EXISTS commission;
CREATE TABLE core.tenant (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION core.current_tenant_id() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION core.touch_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id <> OLD.tenant_id OR NEW.id <> OLD.id THEN
    RAISE EXCEPTION 'immutable identity' USING ERRCODE='23514';
  END IF;
  NEW.version := OLD.version + 1;
  NEW.updated_at := clock_timestamp();
  RETURN NEW;
END $$;
CREATE FUNCTION core.reject_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'append-only evidence: %', TG_TABLE_NAME USING ERRCODE='23514';
END $$;

CREATE TABLE core.actor (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  subject_ref text NOT NULL,
  actor_type text NOT NULL,
  display_label text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  role_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
  departed_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (actor_type IN ('human','service')),
  UNIQUE (tenant_id, subject_ref)
);
COMMENT ON TABLE core.actor IS '操作人或服務身分；不儲存認證秘密。';
COMMENT ON COLUMN core.actor.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.actor.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.actor.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.actor.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.actor.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.actor.subject_ref IS 'SSO 供應商 subject 或服務主體；不可當成顯示姓名。';
COMMENT ON COLUMN core.actor.actor_type IS 'human 或 service';
COMMENT ON COLUMN core.actor.display_label IS '最小必要顯示名稱';
COMMENT ON COLUMN core.actor.enabled IS '是否可發起新操作';
COMMENT ON COLUMN core.actor.role_codes IS '核准角色代碼；真正授權須經伺服器 RBAC/ABAC。';
COMMENT ON COLUMN core.actor.departed_at IS '員工離職生效時點；觸發歸戶與權限撤銷。';

CREATE TABLE core.staff_credential (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_id uuid NOT NULL,
  credential_type text NOT NULL,
  verification_ref text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz,
  revoked_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (valid_until IS NULL OR valid_until > valid_from)
);
COMMENT ON TABLE core.staff_credential IS '分析師及招攬人員資格核驗版本。';
COMMENT ON COLUMN core.staff_credential.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.staff_credential.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.staff_credential.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.staff_credential.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.staff_credential.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.staff_credential.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN core.staff_credential.credential_type IS 'analyst / sales / compliance / aml';
COMMENT ON COLUMN core.staff_credential.verification_ref IS '受限資格查核證據引用';
COMMENT ON COLUMN core.staff_credential.valid_from IS '有效開始';
COMMENT ON COLUMN core.staff_credential.valid_until IS '有效截止';
COMMENT ON COLUMN core.staff_credential.revoked_at IS '資格撤銷生效時間';

CREATE TABLE core.credential_revocation (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  credential_id uuid NOT NULL,
  revoked_by uuid NOT NULL,
  revoked_at timestamptz NOT NULL,
  reason_code text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE core.credential_revocation IS '資格撤銷使用新事件，不更新不可變資格證據。';
COMMENT ON COLUMN core.credential_revocation.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.credential_revocation.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.credential_revocation.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.credential_revocation.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.credential_revocation.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.credential_revocation.credential_id IS '指向 core.staff_credential，必須同一 tenant。';
COMMENT ON COLUMN core.credential_revocation.revoked_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN core.credential_revocation.revoked_at IS '撤銷生效時間';
COMMENT ON COLUMN core.credential_revocation.reason_code IS '撤銷原因代碼';

CREATE TABLE core.retention_policy (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  policy_code text NOT NULL,
  policy_version integer NOT NULL,
  trigger_event text NOT NULL,
  duration_days integer,
  legal_basis_ref text,
  approved_by uuid,
  approved_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (duration_days IS NULL OR duration_days > 0),
  CHECK ((approved_at IS NULL) = (approved_by IS NULL)),
  UNIQUE (tenant_id, policy_code, policy_version)
);
COMMENT ON TABLE core.retention_policy IS '資料類型的保存政策版本；不把提案天數當成核准法定天數。';
COMMENT ON COLUMN core.retention_policy.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.retention_policy.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.retention_policy.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.retention_policy.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.retention_policy.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.retention_policy.policy_code IS '業務保存類型';
COMMENT ON COLUMN core.retention_policy.policy_version IS '政策版本';
COMMENT ON COLUMN core.retention_policy.trigger_event IS '起算事件，例如 contract_obligations_ended 或 report_provided';
COMMENT ON COLUMN core.retention_policy.duration_days IS '核准的最低保存日數；null 代表尚未核定，禁止自動刪除。';
COMMENT ON COLUMN core.retention_policy.legal_basis_ref IS '法源或內規控制索引';
COMMENT ON COLUMN core.retention_policy.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN core.retention_policy.approved_at IS '核准時間';

CREATE TABLE core.object_asset (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  storage_key text NOT NULL,
  object_version text NOT NULL,
  sha256 char(64) NOT NULL,
  byte_length bigint NOT NULL,
  media_type text NOT NULL,
  classification text NOT NULL,
  key_ref text NOT NULL,
  retention_policy_id uuid NOT NULL,
  retention_started_at timestamptz,
  delete_not_before timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (byte_length >= 0),
  CHECK (classification IN ('public','internal','personal','restricted_aml')),
  UNIQUE (tenant_id, storage_key, object_version)
);
COMMENT ON TABLE core.object_asset IS '加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。';
COMMENT ON COLUMN core.object_asset.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.object_asset.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.object_asset.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.object_asset.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.object_asset.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.object_asset.storage_key IS '私有 bucket/key；下載另用短效授權';
COMMENT ON COLUMN core.object_asset.object_version IS '物件儲存版本';
COMMENT ON COLUMN core.object_asset.sha256 IS '實際 bytes SHA-256；不等同另一個簽章容器的 hash。';
COMMENT ON COLUMN core.object_asset.byte_length IS '長度';
COMMENT ON COLUMN core.object_asset.media_type IS 'MIME 類型';
COMMENT ON COLUMN core.object_asset.classification IS 'public / internal / personal / restricted_aml';
COMMENT ON COLUMN core.object_asset.key_ref IS 'KMS key reference；不是密鑰';
COMMENT ON COLUMN core.object_asset.retention_policy_id IS '指向 core.retention_policy，必須同一 tenant。';
COMMENT ON COLUMN core.object_asset.retention_started_at IS '保存起算事件時間；未發生時 null。';
COMMENT ON COLUMN core.object_asset.delete_not_before IS '不得早於此時點刪除；null 禁止排程刪除。';

CREATE TABLE core.legal_hold (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  target_type text NOT NULL,
  target_id uuid NOT NULL,
  action text NOT NULL,
  reason_code text NOT NULL,
  actor_id uuid NOT NULL,
  prior_hold_id uuid,
  effective_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (action IN ('hold','release')),
  CHECK (action <> 'release' OR prior_hold_id IS NOT NULL)
);
COMMENT ON TABLE core.legal_hold IS '司法、爭議或稽核保存指示；解除以新事件表達。';
COMMENT ON COLUMN core.legal_hold.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.legal_hold.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.legal_hold.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.legal_hold.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.legal_hold.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.legal_hold.target_type IS '資源類型';
COMMENT ON COLUMN core.legal_hold.target_id IS '被保全資源';
COMMENT ON COLUMN core.legal_hold.action IS 'hold 或 release';
COMMENT ON COLUMN core.legal_hold.reason_code IS '保全理由代碼；詳細理由留在受控案件';
COMMENT ON COLUMN core.legal_hold.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN core.legal_hold.prior_hold_id IS '指向 core.legal_hold，必須同一 tenant。';
COMMENT ON COLUMN core.legal_hold.effective_at IS '生效時間';

CREATE TABLE core.audit_event (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_id uuid,
  action text NOT NULL,
  resource_type text NOT NULL,
  resource_id uuid NOT NULL,
  correlation_id uuid NOT NULL,
  outcome text NOT NULL,
  classification text NOT NULL,
  redacted_changes jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL,
  prior_hash char(64),
  event_hash char(64) NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (outcome IN ('success','denied','error')),
  CHECK (classification IN ('internal','personal','financial','restricted_aml')),
  CHECK (resource_type NOT LIKE 'aml.%' OR classification='restricted_aml')
);
COMMENT ON TABLE core.audit_event IS '不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。';
COMMENT ON COLUMN core.audit_event.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.audit_event.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.audit_event.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.audit_event.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.audit_event.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.audit_event.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN core.audit_event.action IS '動作代碼';
COMMENT ON COLUMN core.audit_event.resource_type IS '資源類型';
COMMENT ON COLUMN core.audit_event.resource_id IS '資源 ID';
COMMENT ON COLUMN core.audit_event.correlation_id IS '全鏈路追蹤 ID';
COMMENT ON COLUMN core.audit_event.outcome IS 'success / denied / error';
COMMENT ON COLUMN core.audit_event.classification IS 'internal/personal/financial/restricted_aml';
COMMENT ON COLUMN core.audit_event.redacted_changes IS '僅欄位名稱與遮罩值；禁止原始 KYC/AML/秘密。';
COMMENT ON COLUMN core.audit_event.occurred_at IS '實際發生時間';
COMMENT ON COLUMN core.audit_event.prior_hash IS '同一封存鏈前一證據 hash';
COMMENT ON COLUMN core.audit_event.event_hash IS 'canonical audit bytes 的摘要';

CREATE TABLE core.outbox (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'pending',
  event_id uuid NOT NULL,
  event_type text NOT NULL,
  schema_version text NOT NULL DEFAULT '1.0.0',
  producer text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id uuid NOT NULL,
  aggregate_version integer NOT NULL,
  sequence_no bigint NOT NULL,
  idempotency_key text NOT NULL,
  classification text NOT NULL,
  payload_sha256 char(64) NOT NULL,
  payload jsonb NOT NULL,
  correlation_id uuid NOT NULL,
  causation_id uuid,
  occurred_at timestamptz NOT NULL,
  emitted_at timestamptz NOT NULL,
  available_at timestamptz,
  lease_until timestamptz,
  lease_owner text,
  attempt_count integer NOT NULL DEFAULT 0,
  last_error_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('pending','leased','published','retry','dead_letter')),
  CHECK (attempt_count >= 0),
  CHECK (aggregate_version > 0 AND sequence_no > 0),
  UNIQUE (tenant_id, event_id),
  UNIQUE (tenant_id, producer, aggregate_type, aggregate_id, sequence_no),
  UNIQUE (tenant_id, producer, idempotency_key)
);
COMMENT ON TABLE core.outbox IS '與業務異動同一 DB transaction 提交的待送事件。';
COMMENT ON COLUMN core.outbox.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.outbox.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.outbox.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.outbox.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.outbox.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.outbox.status IS '狀態：pending / leased / published / retry / dead_letter';
COMMENT ON COLUMN core.outbox.event_id IS '穩定事件 ID';
COMMENT ON COLUMN core.outbox.event_type IS '事件名稱；與 events/registry.json 一致';
COMMENT ON COLUMN core.outbox.schema_version IS '事件 schema SemVer';
COMMENT ON COLUMN core.outbox.producer IS '權威 producer 模組代碼';
COMMENT ON COLUMN core.outbox.aggregate_type IS 'aggregate 類型';
COMMENT ON COLUMN core.outbox.aggregate_id IS 'aggregate ID';
COMMENT ON COLUMN core.outbox.aggregate_version IS 'aggregate 資料版本';
COMMENT ON COLUMN core.outbox.sequence_no IS '同 producer/aggregate 事件順序';
COMMENT ON COLUMN core.outbox.idempotency_key IS '穩定命令／事件去重 key';
COMMENT ON COLUMN core.outbox.classification IS '事件資料分類';
COMMENT ON COLUMN core.outbox.payload_sha256 IS 'canonical payload SHA-256';
COMMENT ON COLUMN core.outbox.payload IS '只含 IDs、版本、狀態與 safe amount；禁止敏感原文。';
COMMENT ON COLUMN core.outbox.correlation_id IS 'trace';
COMMENT ON COLUMN core.outbox.causation_id IS '上游事件或命令 ID';
COMMENT ON COLUMN core.outbox.occurred_at IS '業務實際時間';
COMMENT ON COLUMN core.outbox.emitted_at IS '首次建立外送 envelope 的真實時間；重試不變';
COMMENT ON COLUMN core.outbox.available_at IS '允許送出時間';
COMMENT ON COLUMN core.outbox.lease_until IS 'worker 租約';
COMMENT ON COLUMN core.outbox.lease_owner IS 'worker ID';
COMMENT ON COLUMN core.outbox.attempt_count IS '嘗試次數';
COMMENT ON COLUMN core.outbox.last_error_code IS '非敏感錯誤代碼';

CREATE TABLE core.inbox (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'received',
  consumer text NOT NULL,
  producer text NOT NULL,
  event_id uuid NOT NULL,
  payload_sha256 char(64) NOT NULL,
  event_type text NOT NULL,
  schema_version text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id uuid NOT NULL,
  aggregate_version integer NOT NULL,
  sequence_no bigint NOT NULL,
  received_at timestamptz NOT NULL,
  lease_until timestamptz,
  applied_at timestamptz,
  result_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('received','leased','applied','rejected','retry','dead_letter')),
  UNIQUE (tenant_id, consumer, producer, event_id)
);
COMMENT ON TABLE core.inbox IS 'consumer 去重與 event fingerprint；同 ID 不同 payload 必須拒收。';
COMMENT ON COLUMN core.inbox.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.inbox.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.inbox.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.inbox.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.inbox.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.inbox.status IS '狀態：received / leased / applied / rejected / retry / dead_letter';
COMMENT ON COLUMN core.inbox.consumer IS 'consumer 名稱';
COMMENT ON COLUMN core.inbox.producer IS '來源服務';
COMMENT ON COLUMN core.inbox.event_id IS '來源事件 ID';
COMMENT ON COLUMN core.inbox.payload_sha256 IS 'raw 或約定 canonical payload hash';
COMMENT ON COLUMN core.inbox.event_type IS '來源事件名稱';
COMMENT ON COLUMN core.inbox.schema_version IS '來源 schema SemVer';
COMMENT ON COLUMN core.inbox.aggregate_type IS '來源 aggregate 類型';
COMMENT ON COLUMN core.inbox.aggregate_id IS '來源 aggregate ID';
COMMENT ON COLUMN core.inbox.aggregate_version IS '來源版本';
COMMENT ON COLUMN core.inbox.sequence_no IS '來源 aggregate 事件順序';
COMMENT ON COLUMN core.inbox.received_at IS '接收時間';
COMMENT ON COLUMN core.inbox.lease_until IS '處理租約';
COMMENT ON COLUMN core.inbox.applied_at IS '交易內成功套用時間';
COMMENT ON COLUMN core.inbox.result_code IS '結果代碼';

CREATE TABLE core.idempotency_record (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_id uuid NOT NULL,
  operation text NOT NULL,
  idempotency_key text NOT NULL,
  request_sha256 char(64) NOT NULL,
  response_status integer,
  redacted_response jsonb,
  completed_at timestamptz,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
  UNIQUE (tenant_id, actor_id, operation, idempotency_key)
);
COMMENT ON TABLE core.idempotency_record IS '命令重送收據；必須綁 tenant、actor、operation、key 與 request hash。';
COMMENT ON COLUMN core.idempotency_record.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.idempotency_record.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.idempotency_record.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.idempotency_record.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.idempotency_record.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.idempotency_record.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN core.idempotency_record.operation IS 'API operationId';
COMMENT ON COLUMN core.idempotency_record.idempotency_key IS '呼叫端提供 key；不可含 PII';
COMMENT ON COLUMN core.idempotency_record.request_sha256 IS '請求指紋';
COMMENT ON COLUMN core.idempotency_record.response_status IS '已確定的 HTTP 狀態';
COMMENT ON COLUMN core.idempotency_record.redacted_response IS '僅無秘密的重播回應或 resource refs';
COMMENT ON COLUMN core.idempotency_record.completed_at IS '完成時間';
COMMENT ON COLUMN core.idempotency_record.expires_at IS '收據保存截止；不得短於安全重試窗';

CREATE TABLE core.job_lease (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  job_key text NOT NULL,
  worker_id text NOT NULL,
  fence_token bigint NOT NULL,
  lease_until timestamptz NOT NULL,
  checkpoint_at timestamptz,
  checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, job_key)
);
COMMENT ON TABLE core.job_lease IS '排程任務租約與 fencing token。';
COMMENT ON COLUMN core.job_lease.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN core.job_lease.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN core.job_lease.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN core.job_lease.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN core.job_lease.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN core.job_lease.job_key IS 'tenant 範圍內排程 key';
COMMENT ON COLUMN core.job_lease.worker_id IS '持租 worker';
COMMENT ON COLUMN core.job_lease.fence_token IS '每次接管遞增 token';
COMMENT ON COLUMN core.job_lease.lease_until IS '租約到期';
COMMENT ON COLUMN core.job_lease.checkpoint_at IS '最後成功 checkpoint';
COMMENT ON COLUMN core.job_lease.checkpoint IS '安全的游標';

CREATE TABLE customer.customer (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'prospect',
  pii_ciphertext bytea,
  pii_key_ref text,
  lookup_hmac char(64),
  merged_into_id uuid,
  first_effective_paid_at timestamptz,
  locale text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('prospect','applicant','active','inactive','restricted','merged')),
  CHECK ((pii_ciphertext IS NULL) = (pii_key_ref IS NULL)),
  CHECK (merged_into_id IS NULL OR merged_into_id <> id),
  UNIQUE (tenant_id, lookup_hmac)
);
COMMENT ON TABLE customer.customer IS '客戶管理主檔；潛在、申辦、已簽與已生效客戶共用身份。';
COMMENT ON COLUMN customer.customer.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN customer.customer.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN customer.customer.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN customer.customer.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN customer.customer.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN customer.customer.status IS '狀態：prospect / applicant / active / inactive / restricted / merged';
COMMENT ON COLUMN customer.customer.pii_ciphertext IS '最小客戶識別資料；application-layer envelope encryption。';
COMMENT ON COLUMN customer.customer.pii_key_ref IS '解密所需 KMS reference';
COMMENT ON COLUMN customer.customer.lookup_hmac IS '身分正規化後 keyed HMAC；不得用可逆 raw id 或無鹽 hash。';
COMMENT ON COLUMN customer.customer.merged_into_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN customer.customer.first_effective_paid_at IS '首次付費服務生效日期；與首次造訪分離。';
COMMENT ON COLUMN customer.customer.locale IS '客戶語系';

CREATE TABLE customer.external_identity (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  provider text NOT NULL,
  subject_hmac char(64) NOT NULL,
  verified_at timestamptz NOT NULL,
  assurance_level text NOT NULL,
  proof_asset_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, provider, subject_hmac)
);
COMMENT ON TABLE customer.external_identity IS '經驗證外部登入身分；合併須證據與審核。';
COMMENT ON COLUMN customer.external_identity.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN customer.external_identity.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN customer.external_identity.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN customer.external_identity.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN customer.external_identity.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN customer.external_identity.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN customer.external_identity.provider IS 'identity provider，例如 legendflow/line/oidc';
COMMENT ON COLUMN customer.external_identity.subject_hmac IS 'provider subject 的 keyed HMAC';
COMMENT ON COLUMN customer.external_identity.verified_at IS '完成身份連結驗證時間';
COMMENT ON COLUMN customer.external_identity.assurance_level IS '已核准 assurance code';
COMMENT ON COLUMN customer.external_identity.proof_asset_id IS '指向 core.object_asset，必須同一 tenant。';

CREATE TABLE customer.application (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'draft',
  customer_id uuid NOT NULL,
  service_code text NOT NULL,
  package_version_id uuid NOT NULL,
  service_selections jsonb NOT NULL,
  intent text NOT NULL DEFAULT 'new',
  preceding_contract_id uuid,
  source_session_ref text,
  submitted_at timestamptz,
  decision_at timestamptz,
  decision_reason_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('draft','customer_signed','due_diligence_pending','review_in_progress','accepted','rejected','withdrawn')),
  CHECK (intent IN ('new','renewal','upgrade','reactivation')),
  CHECK (jsonb_typeof(service_selections)='array' AND jsonb_array_length(service_selections)>0)
);
COMMENT ON TABLE customer.application IS '簽約申辦流程；客戶簽署先於 KYC，不代表公司已接受。';
COMMENT ON COLUMN customer.application.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN customer.application.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN customer.application.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN customer.application.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN customer.application.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN customer.application.status IS '狀態：draft / customer_signed / due_diligence_pending / review_in_progress / accepted / rejected / withdrawn';
COMMENT ON COLUMN customer.application.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN customer.application.service_code IS '受申請服務，商品價格版本由 contract 固定';
COMMENT ON COLUMN customer.application.package_version_id IS '指向 contract.package_version，必須同一 tenant。';
COMMENT ON COLUMN customer.application.service_selections IS '所選服務、報價與稅類版本的服務端快照；可含多服務。';
COMMENT ON COLUMN customer.application.intent IS 'new/renewal/upgrade/reactivation';
COMMENT ON COLUMN customer.application.preceding_contract_id IS '指向 contract.contract，必須同一 tenant。';
COMMENT ON COLUMN customer.application.source_session_ref IS '去識別漏斗 session 參考';
COMMENT ON COLUMN customer.application.submitted_at IS '送審時間';
COMMENT ON COLUMN customer.application.decision_at IS '公司決定時間';
COMMENT ON COLUMN customer.application.decision_reason_code IS '公司決定代碼；客戶可見與受限理由分開。';

CREATE TABLE customer.consent (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  purpose text NOT NULL,
  notice_version text NOT NULL,
  granted boolean NOT NULL,
  recorded_at timestamptz NOT NULL,
  evidence_asset_id uuid NOT NULL,
  channel text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE customer.consent IS '個資告知、行銷同意、撤回事件；不能用簽約強迫行銷同意。';
COMMENT ON COLUMN customer.consent.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN customer.consent.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN customer.consent.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN customer.consent.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN customer.consent.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN customer.consent.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN customer.consent.purpose IS '同意目的';
COMMENT ON COLUMN customer.consent.notice_version IS '所見告知版本';
COMMENT ON COLUMN customer.consent.granted IS 'true 同意／false 撤回';
COMMENT ON COLUMN customer.consent.recorded_at IS '客戶作出意思表示時間';
COMMENT ON COLUMN customer.consent.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN customer.consent.channel IS '取得來源';

CREATE TABLE customer.merge_case (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'proposed',
  source_customer_id uuid NOT NULL,
  target_customer_id uuid NOT NULL,
  proposed_by uuid NOT NULL,
  reviewed_by uuid,
  evidence_asset_id uuid NOT NULL,
  decided_at timestamptz,
  executed_at timestamptz,
  reason_code text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('proposed','approved','rejected','executed','cancelled')),
  CHECK (source_customer_id <> target_customer_id),
  CHECK (reviewed_by IS NULL OR reviewed_by <> proposed_by),
  CHECK (status NOT IN ('approved','executed') OR reviewed_by IS NOT NULL)
);
COMMENT ON TABLE customer.merge_case IS '重複客戶合併申請，核准後保留來源 ID alias 與原證據。';
COMMENT ON COLUMN customer.merge_case.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN customer.merge_case.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN customer.merge_case.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN customer.merge_case.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN customer.merge_case.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN customer.merge_case.status IS '狀態：proposed / approved / rejected / executed / cancelled';
COMMENT ON COLUMN customer.merge_case.source_customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN customer.merge_case.target_customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN customer.merge_case.proposed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN customer.merge_case.reviewed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN customer.merge_case.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN customer.merge_case.decided_at IS '覆核時間';
COMMENT ON COLUMN customer.merge_case.executed_at IS '完成交易時間';
COMMENT ON COLUMN customer.merge_case.reason_code IS '合併理由代碼';

CREATE TABLE contract.package_version (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  package_code text NOT NULL,
  package_version integer NOT NULL,
  asset_id uuid NOT NULL,
  package_sha256 char(64) NOT NULL,
  effective_from timestamptz NOT NULL,
  proposed_by uuid NOT NULL,
  approved_by uuid,
  approved_at timestamptz,
  acceptance_conditions jsonb NOT NULL,
  approval_status text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (approval_status IN ('draft','approved','retired')),
  CHECK (approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by)),
  UNIQUE (tenant_id, package_code, package_version)
);
COMMENT ON TABLE contract.package_version IS '申請暨投顧契約及揭露文件完整版本。';
COMMENT ON COLUMN contract.package_version.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.package_version.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.package_version.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.package_version.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.package_version.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.package_version.package_code IS '模板識別碼';
COMMENT ON COLUMN contract.package_version.package_version IS '模板版本';
COMMENT ON COLUMN contract.package_version.asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN contract.package_version.package_sha256 IS '完整展示文件 hash';
COMMENT ON COLUMN contract.package_version.effective_from IS '模板啟用時間';
COMMENT ON COLUMN contract.package_version.proposed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN contract.package_version.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN contract.package_version.approved_at IS '模板核准時間';
COMMENT ON COLUMN contract.package_version.acceptance_conditions IS '公司接受與生效條件；須核定正式文字。';
COMMENT ON COLUMN contract.package_version.approval_status IS 'draft/approved；draft 不得供正式簽署';

CREATE TABLE contract.customer_signature (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  application_id uuid NOT NULL,
  package_version_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  content_sha256 char(64) NOT NULL,
  signed_file_sha256 char(64) NOT NULL,
  signed_asset_id uuid NOT NULL,
  provider text NOT NULL,
  provider_transaction_id text NOT NULL,
  assurance_level text NOT NULL,
  customer_signed_at timestamptz NOT NULL,
  evidence_asset_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, provider, provider_transaction_id)
);
COMMENT ON TABLE contract.customer_signature IS '客戶先簽申請／契約封包的不可變證據。';
COMMENT ON COLUMN contract.customer_signature.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.customer_signature.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.customer_signature.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.customer_signature.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.customer_signature.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.customer_signature.application_id IS '指向 customer.application，必須同一 tenant。';
COMMENT ON COLUMN contract.customer_signature.package_version_id IS '指向 contract.package_version，必須同一 tenant。';
COMMENT ON COLUMN contract.customer_signature.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN contract.customer_signature.content_sha256 IS '簽署前精確內容摘要';
COMMENT ON COLUMN contract.customer_signature.signed_file_sha256 IS '簽後容器或檔案摘要；不同於 content hash';
COMMENT ON COLUMN contract.customer_signature.signed_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN contract.customer_signature.provider IS '簽章 provider';
COMMENT ON COLUMN contract.customer_signature.provider_transaction_id IS '供應商交易 ID';
COMMENT ON COLUMN contract.customer_signature.assurance_level IS '身份驗證方式的核准代碼';
COMMENT ON COLUMN contract.customer_signature.customer_signed_at IS '客戶真正簽署時點';
COMMENT ON COLUMN contract.customer_signature.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';

CREATE TABLE contract.acceptance_decision (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  application_id uuid NOT NULL,
  customer_signature_id uuid NOT NULL,
  decision text NOT NULL,
  decided_by uuid NOT NULL,
  decided_at timestamptz NOT NULL,
  safe_reason_code text NOT NULL,
  evidence_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (decision IN ('accept','reject','request_changes'))
);
COMMENT ON TABLE contract.acceptance_decision IS '公司接受、拒絕或要求補件的完整決策歷史；accept才另產生company_acceptance。';
COMMENT ON COLUMN contract.acceptance_decision.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.acceptance_decision.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.acceptance_decision.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.acceptance_decision.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.acceptance_decision.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.acceptance_decision.application_id IS '指向 customer.application，必須同一 tenant。';
COMMENT ON COLUMN contract.acceptance_decision.customer_signature_id IS '指向 contract.customer_signature，必須同一 tenant。';
COMMENT ON COLUMN contract.acceptance_decision.decision IS 'accept/reject/request_changes';
COMMENT ON COLUMN contract.acceptance_decision.decided_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN contract.acceptance_decision.decided_at IS '公司決定真實時間';
COMMENT ON COLUMN contract.acceptance_decision.safe_reason_code IS '可供申辦工作流使用的理由代碼；不含AML調查內情';
COMMENT ON COLUMN contract.acceptance_decision.evidence_snapshot IS '決策依據的版本引用；受限內容留在M04。';

CREATE TABLE contract.company_acceptance (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  decision_id uuid NOT NULL,
  application_id uuid NOT NULL,
  customer_signature_id uuid NOT NULL,
  kyc_submission_id uuid NOT NULL,
  suitability_assessment_id uuid NOT NULL,
  aml_clearance_id uuid NOT NULL,
  accepted_by uuid NOT NULL,
  accepted_at timestamptz NOT NULL,
  company_signed_asset_id uuid,
  conditions_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, application_id),
  UNIQUE (tenant_id, decision_id)
);
COMMENT ON TABLE contract.company_acceptance IS '公司完成必要審查後接受；與客戶簽署分離。';
COMMENT ON COLUMN contract.company_acceptance.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.company_acceptance.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.company_acceptance.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.company_acceptance.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.company_acceptance.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.company_acceptance.decision_id IS '指向 contract.acceptance_decision，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.application_id IS '指向 customer.application，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.customer_signature_id IS '指向 contract.customer_signature，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.kyc_submission_id IS '指向 kyc.submission，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.suitability_assessment_id IS '指向 kyc.suitability_assessment，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.aml_clearance_id IS '指向 aml.business_clearance，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.accepted_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.accepted_at IS '公司接受真實時間';
COMMENT ON COLUMN contract.company_acceptance.company_signed_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN contract.company_acceptance.conditions_snapshot IS '本次公司接受檢查條件與版本；不能改寫原評估。';

CREATE TABLE contract.contract (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'pending_company_acceptance',
  application_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  customer_signature_id uuid NOT NULL,
  company_acceptance_id uuid,
  contract_number text NOT NULL,
  effective_at timestamptz,
  ends_at timestamptz,
  obligations_ended_at timestamptz,
  currency text NOT NULL,
  terms_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('pending_company_acceptance','accepted','effective','suspended','expired','terminated')),
  CHECK (ends_at IS NULL OR effective_at IS NULL OR ends_at > effective_at),
  CHECK (status NOT IN ('accepted','effective') OR company_acceptance_id IS NOT NULL),
  UNIQUE (tenant_id, contract_number),
  UNIQUE (tenant_id, application_id)
);
COMMENT ON TABLE contract.contract IS '法律關係及服務契約狀態；不把付款或前端權限當成成立證據。';
COMMENT ON COLUMN contract.contract.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.contract.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.contract.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.contract.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.contract.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.contract.status IS '狀態：pending_company_acceptance / accepted / effective / suspended / expired / terminated';
COMMENT ON COLUMN contract.contract.application_id IS '指向 customer.application，必須同一 tenant。';
COMMENT ON COLUMN contract.contract.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN contract.contract.customer_signature_id IS '指向 contract.customer_signature，必須同一 tenant。';
COMMENT ON COLUMN contract.contract.company_acceptance_id IS '指向 contract.company_acceptance，必須同一 tenant。';
COMMENT ON COLUMN contract.contract.contract_number IS '不可重複契約編號';
COMMENT ON COLUMN contract.contract.effective_at IS '依核准條款生效時間';
COMMENT ON COLUMN contract.contract.ends_at IS '約定服務終止時間';
COMMENT ON COLUMN contract.contract.obligations_ended_at IS '權利義務全部終了；保存起算依政策。';
COMMENT ON COLUMN contract.contract.currency IS 'ISO currency，例 TWD';
COMMENT ON COLUMN contract.contract.terms_snapshot IS '價格、稅類、服務、退款條款與版本快照';

CREATE TABLE contract.contract_service (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  contract_id uuid NOT NULL,
  service_code text NOT NULL,
  service_chain_id uuid NOT NULL,
  predecessor_service_id uuid,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  auto_renew boolean NOT NULL DEFAULT false,
  untaxed_contract_amount numeric(20,6) NOT NULL,
  tax_profile_ref text NOT NULL,
  features_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (ends_at > starts_at),
  CHECK (untaxed_contract_amount >= 0)
);
COMMENT ON TABLE contract.contract_service IS '一客多契約多服務及續約服務鏈。';
COMMENT ON COLUMN contract.contract_service.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.contract_service.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.contract_service.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.contract_service.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.contract_service.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.contract_service.contract_id IS '指向 contract.contract，必須同一 tenant。';
COMMENT ON COLUMN contract.contract_service.service_code IS '服務權益代碼';
COMMENT ON COLUMN contract.contract_service.service_chain_id IS '續訂關聯鏈；不因改方案或換業務歸零。';
COMMENT ON COLUMN contract.contract_service.predecessor_service_id IS '指向 contract.contract_service，必須同一 tenant。';
COMMENT ON COLUMN contract.contract_service.starts_at IS '服務區間 [start,end) 開始';
COMMENT ON COLUMN contract.contract_service.ends_at IS '服務區間結束';
COMMENT ON COLUMN contract.contract_service.auto_renew IS '本期付款前客戶授權之自動續訂';
COMMENT ON COLUMN contract.contract_service.untaxed_contract_amount IS '已確認稅別後未稅契約額；未履約部分不直接當收入。';
COMMENT ON COLUMN contract.contract_service.tax_profile_ref IS '財會核准稅務設定版本';
COMMENT ON COLUMN contract.contract_service.features_snapshot IS '核准的服務權益快照';

CREATE TABLE contract.entitlement (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'locked',
  customer_id uuid NOT NULL,
  contract_service_id uuid NOT NULL,
  feature_code text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz NOT NULL,
  payment_condition_ref text,
  restriction_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('locked','eligible','active','suspended','expired','revoked')),
  CHECK (valid_until > valid_from),
  CHECK (status <> 'active' OR payment_condition_ref IS NOT NULL),
  UNIQUE (tenant_id, contract_service_id, feature_code)
);
COMMENT ON TABLE contract.entitlement IS '權限來源為有效契約與審查結果；配送時重新檢查。';
COMMENT ON COLUMN contract.entitlement.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.entitlement.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.entitlement.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.entitlement.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.entitlement.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.entitlement.status IS '狀態：locked / eligible / active / suspended / expired / revoked';
COMMENT ON COLUMN contract.entitlement.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN contract.entitlement.contract_service_id IS '指向 contract.contract_service，必須同一 tenant。';
COMMENT ON COLUMN contract.entitlement.feature_code IS '可用權益';
COMMENT ON COLUMN contract.entitlement.valid_from IS '使用權限開始';
COMMENT ON COLUMN contract.entitlement.valid_until IS '使用權限結束';
COMMENT ON COLUMN contract.entitlement.payment_condition_ref IS '付款或核准授信條件證據';
COMMENT ON COLUMN contract.entitlement.restriction_code IS '安全可見限制代碼；不放 AML 內情';

CREATE TABLE contract.entitlement_decision (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  entitlement_id uuid NOT NULL,
  from_status text NOT NULL,
  to_status text NOT NULL,
  actor_id uuid NOT NULL,
  reason_code text NOT NULL,
  gate_snapshot jsonb NOT NULL,
  effective_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE contract.entitlement_decision IS '每次啟停權限的決策紀錄。';
COMMENT ON COLUMN contract.entitlement_decision.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN contract.entitlement_decision.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN contract.entitlement_decision.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN contract.entitlement_decision.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN contract.entitlement_decision.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN contract.entitlement_decision.entitlement_id IS '指向 contract.entitlement，必須同一 tenant。';
COMMENT ON COLUMN contract.entitlement_decision.from_status IS '舊狀態';
COMMENT ON COLUMN contract.entitlement_decision.to_status IS '新狀態';
COMMENT ON COLUMN contract.entitlement_decision.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN contract.entitlement_decision.reason_code IS '規則決策代碼';
COMMENT ON COLUMN contract.entitlement_decision.gate_snapshot IS '公司接受、合約、KYC、適合度、AML safe clearance、付款條件版本';
COMMENT ON COLUMN contract.entitlement_decision.effective_at IS '生效時間';

CREATE TABLE kyc.submission (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'not_started',
  application_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  submission_version integer NOT NULL,
  answers_ciphertext bytea,
  key_ref text,
  submitted_at timestamptz,
  reviewed_by uuid,
  reviewed_at timestamptz,
  valid_until timestamptz,
  decision_reason_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('not_started','in_progress','submitted','incomplete','under_review','approved','rejected','refresh_required')),
  CHECK ((answers_ciphertext IS NULL) = (key_ref IS NULL)),
  CHECK (status <> 'approved' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
  UNIQUE (tenant_id, application_id, submission_version)
);
COMMENT ON TABLE kyc.submission IS '版本化線上 KYC 回答與證據；先保留客戶原始填答。';
COMMENT ON COLUMN kyc.submission.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN kyc.submission.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN kyc.submission.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN kyc.submission.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN kyc.submission.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN kyc.submission.status IS '狀態：not_started / in_progress / submitted / incomplete / under_review / approved / rejected / refresh_required';
COMMENT ON COLUMN kyc.submission.application_id IS '指向 customer.application，必須同一 tenant。';
COMMENT ON COLUMN kyc.submission.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN kyc.submission.submission_version IS '同一申請送件版本';
COMMENT ON COLUMN kyc.submission.answers_ciphertext IS 'KYC 答案加密；不得送 general LLM';
COMMENT ON COLUMN kyc.submission.key_ref IS 'KMS ref';
COMMENT ON COLUMN kyc.submission.submitted_at IS '送件時間';
COMMENT ON COLUMN kyc.submission.reviewed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN kyc.submission.reviewed_at IS '審查時間';
COMMENT ON COLUMN kyc.submission.valid_until IS '覆核有效截止';
COMMENT ON COLUMN kyc.submission.decision_reason_code IS '審查代碼';

CREATE TABLE kyc.submission_document (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  submission_id uuid NOT NULL,
  asset_id uuid NOT NULL,
  document_type text NOT NULL,
  verification_state text NOT NULL,
  verified_by uuid,
  verified_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (verification_state IN ('pending','verified','rejected'))
);
COMMENT ON TABLE kyc.submission_document IS '線上補件、驗證用途與結果；保留原件版本。';
COMMENT ON COLUMN kyc.submission_document.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN kyc.submission_document.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN kyc.submission_document.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN kyc.submission_document.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN kyc.submission_document.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN kyc.submission_document.submission_id IS '指向 kyc.submission，必須同一 tenant。';
COMMENT ON COLUMN kyc.submission_document.asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN kyc.submission_document.document_type IS '核准文件種類';
COMMENT ON COLUMN kyc.submission_document.verification_state IS 'pending/verified/rejected';
COMMENT ON COLUMN kyc.submission_document.verified_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN kyc.submission_document.verified_at IS '驗證時間';

CREATE TABLE kyc.suitability_assessment (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'pending',
  submission_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  questionnaire_version text NOT NULL,
  rule_version text NOT NULL,
  answers_snapshot jsonb NOT NULL,
  restrictions jsonb NOT NULL DEFAULT '[]'::jsonb,
  assessed_by uuid,
  assessed_at timestamptz,
  valid_until timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('pending','suitable','conditional','not_suitable','reassessment_due')),
  CHECK (status <> 'conditional' OR jsonb_array_length(restrictions) > 0)
);
COMMENT ON TABLE kyc.suitability_assessment IS '版本化適合度評估，條件適合必須附限制。';
COMMENT ON COLUMN kyc.suitability_assessment.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN kyc.suitability_assessment.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN kyc.suitability_assessment.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN kyc.suitability_assessment.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN kyc.suitability_assessment.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN kyc.suitability_assessment.status IS '狀態：pending / suitable / conditional / not_suitable / reassessment_due';
COMMENT ON COLUMN kyc.suitability_assessment.submission_id IS '指向 kyc.submission，必須同一 tenant。';
COMMENT ON COLUMN kyc.suitability_assessment.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN kyc.suitability_assessment.questionnaire_version IS '問卷版本';
COMMENT ON COLUMN kyc.suitability_assessment.rule_version IS '計分及判斷規則版本';
COMMENT ON COLUMN kyc.suitability_assessment.answers_snapshot IS '需加密的原始答案引用與問題版本，不存明文個資。';
COMMENT ON COLUMN kyc.suitability_assessment.restrictions IS '條件適合的限制代碼；供 entitlement 使用。';
COMMENT ON COLUMN kyc.suitability_assessment.assessed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN kyc.suitability_assessment.assessed_at IS '最終評估時點';
COMMENT ON COLUMN kyc.suitability_assessment.valid_until IS '重評期限';

CREATE TABLE aml.screening (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'pending',
  customer_id uuid NOT NULL,
  submission_id uuid NOT NULL,
  provider text NOT NULL,
  provider_request_id text NOT NULL,
  list_version text NOT NULL,
  request_sha256 char(64) NOT NULL,
  restricted_result_asset_id uuid,
  screened_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('pending','clear','possible_match','confirmed_match','error')),
  UNIQUE (tenant_id, provider, provider_request_id)
);
COMMENT ON TABLE aml.screening IS '名單比對供應商查核；模糊命中不是自動認定或自動清除。';
COMMENT ON COLUMN aml.screening.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN aml.screening.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN aml.screening.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN aml.screening.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN aml.screening.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN aml.screening.status IS '狀態：pending / clear / possible_match / confirmed_match / error';
COMMENT ON COLUMN aml.screening.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN aml.screening.submission_id IS '指向 kyc.submission，必須同一 tenant。';
COMMENT ON COLUMN aml.screening.provider IS '名單 provider';
COMMENT ON COLUMN aml.screening.provider_request_id IS '查核唯一請求';
COMMENT ON COLUMN aml.screening.list_version IS '名單版本';
COMMENT ON COLUMN aml.screening.request_sha256 IS '查核輸入 fingerprint';
COMMENT ON COLUMN aml.screening.restricted_result_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN aml.screening.screened_at IS '查核時間';

CREATE TABLE aml.case_file (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'open',
  customer_id uuid NOT NULL,
  screening_id uuid NOT NULL,
  owner_actor_id uuid,
  risk_level text NOT NULL,
  restricted_notes_ciphertext bytea,
  key_ref text,
  next_review_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('open','under_review','escalated','cleared','rejected','closed')),
  CHECK (risk_level IN ('low','medium','high','undetermined'))
);
COMMENT ON TABLE aml.case_file IS 'AML/CFT 受限覆核案件，禁止 CRM 讀內情。';
COMMENT ON COLUMN aml.case_file.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN aml.case_file.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN aml.case_file.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN aml.case_file.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN aml.case_file.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN aml.case_file.status IS '狀態：open / under_review / escalated / cleared / rejected / closed';
COMMENT ON COLUMN aml.case_file.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN aml.case_file.screening_id IS '指向 aml.screening，必須同一 tenant。';
COMMENT ON COLUMN aml.case_file.owner_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN aml.case_file.risk_level IS 'low/medium/high/undetermined';
COMMENT ON COLUMN aml.case_file.restricted_notes_ciphertext IS '案件註記加密';
COMMENT ON COLUMN aml.case_file.key_ref IS 'KMS ref';
COMMENT ON COLUMN aml.case_file.next_review_at IS '下一個必要覆核時間';

CREATE TABLE aml.case_action (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  actor_id uuid NOT NULL,
  action_code text NOT NULL,
  evidence_asset_id uuid,
  occurred_at timestamptz NOT NULL,
  restricted_detail_ciphertext bytea,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE aml.case_action IS 'AML 調查、EDD、申報與決定之受限軌跡。';
COMMENT ON COLUMN aml.case_action.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN aml.case_action.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN aml.case_action.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN aml.case_action.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN aml.case_action.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN aml.case_action.case_id IS '指向 aml.case_file，必須同一 tenant。';
COMMENT ON COLUMN aml.case_action.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN aml.case_action.action_code IS '受限動作代碼';
COMMENT ON COLUMN aml.case_action.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN aml.case_action.occurred_at IS '動作時間';
COMMENT ON COLUMN aml.case_action.restricted_detail_ciphertext IS '受限詳情';

CREATE TABLE aml.business_clearance (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  application_id uuid NOT NULL,
  case_id uuid,
  decision text NOT NULL,
  policy_version text NOT NULL,
  decided_by uuid NOT NULL,
  decided_at timestamptz NOT NULL,
  valid_until timestamptz NOT NULL,
  safe_reason_code text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (decision IN ('approved','pending','denied','expired')),
  CHECK (valid_until > decided_at)
);
COMMENT ON TABLE aml.business_clearance IS '供公司接受服務查核的最小結果；不暴露比對名單、申報或案件細節。';
COMMENT ON COLUMN aml.business_clearance.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN aml.business_clearance.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN aml.business_clearance.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN aml.business_clearance.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN aml.business_clearance.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN aml.business_clearance.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN aml.business_clearance.application_id IS '指向 customer.application，必須同一 tenant。';
COMMENT ON COLUMN aml.business_clearance.case_id IS '指向 aml.case_file，必須同一 tenant。';
COMMENT ON COLUMN aml.business_clearance.decision IS 'approved / pending / denied / expired';
COMMENT ON COLUMN aml.business_clearance.policy_version IS '核定依據政策，M02不需讀受限screening';
COMMENT ON COLUMN aml.business_clearance.decided_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN aml.business_clearance.decided_at IS '核定時間';
COMMENT ON COLUMN aml.business_clearance.valid_until IS '有效期限';
COMMENT ON COLUMN aml.business_clearance.safe_reason_code IS '僅 generic review_required / approved 等安全代碼';

CREATE TABLE research.candidate (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'received',
  producer text NOT NULL,
  external_candidate_id text NOT NULL,
  external_version integer NOT NULL,
  origin text NOT NULL,
  persona_ref text,
  pool_ref text,
  artifact_ref text NOT NULL,
  content_sha256 char(64) NOT NULL,
  snapshot_asset_id uuid NOT NULL,
  source_occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  lineage jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('received','validating','quarantined','ready','rejected','superseded')),
  CHECK (origin IN ('persona_autonomous','analyst_requested')),
  UNIQUE (tenant_id, producer, external_candidate_id, external_version)
);
COMMENT ON TABLE research.candidate IS 'Pantheon Persona 自主研究或人員研究候選輸入；非正式對客報告。';
COMMENT ON COLUMN research.candidate.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN research.candidate.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN research.candidate.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN research.candidate.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN research.candidate.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN research.candidate.status IS '狀態：received / validating / quarantined / ready / rejected / superseded';
COMMENT ON COLUMN research.candidate.producer IS '外部 producer';
COMMENT ON COLUMN research.candidate.external_candidate_id IS '來源候選識別碼';
COMMENT ON COLUMN research.candidate.external_version IS '來源版本';
COMMENT ON COLUMN research.candidate.origin IS 'persona_autonomous / analyst_requested';
COMMENT ON COLUMN research.candidate.persona_ref IS 'Persona 引用';
COMMENT ON COLUMN research.candidate.pool_ref IS '研究／交易 pool 引用';
COMMENT ON COLUMN research.candidate.artifact_ref IS 'Pantheon artifact 指標';
COMMENT ON COLUMN research.candidate.content_sha256 IS '實際候選內容 hash';
COMMENT ON COLUMN research.candidate.snapshot_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN research.candidate.source_occurred_at IS '來源真實發生時間';
COMMENT ON COLUMN research.candidate.received_at IS '本平台接收時間';
COMMENT ON COLUMN research.candidate.lineage IS 'ticket/plan/run/model/prompt/data/evidence 版本引用';

CREATE TABLE research.candidate_evidence (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  candidate_id uuid NOT NULL,
  evidence_ref text NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  event_time timestamptz NOT NULL,
  available_time timestamptz NOT NULL,
  license_scope text NOT NULL,
  snapshot_asset_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, candidate_id, evidence_ref)
);
COMMENT ON TABLE research.candidate_evidence IS '候選報告採用來源快照、時間與授權範圍。';
COMMENT ON COLUMN research.candidate_evidence.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN research.candidate_evidence.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN research.candidate_evidence.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN research.candidate_evidence.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN research.candidate_evidence.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN research.candidate_evidence.candidate_id IS '指向 research.candidate，必須同一 tenant。';
COMMENT ON COLUMN research.candidate_evidence.evidence_ref IS 'Pantheon evidence/bundle reference';
COMMENT ON COLUMN research.candidate_evidence.evidence_sha256 IS '可驗證內容摘要';
COMMENT ON COLUMN research.candidate_evidence.event_time IS '來源事件時間';
COMMENT ON COLUMN research.candidate_evidence.available_time IS '研究當時可取得時間';
COMMENT ON COLUMN research.candidate_evidence.license_scope IS '授權或傳閱限制';
COMMENT ON COLUMN research.candidate_evidence.snapshot_asset_id IS '指向 core.object_asset，必須同一 tenant。';

CREATE TABLE review.case_file (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'queued',
  candidate_id uuid NOT NULL,
  analyst_id uuid,
  current_revision_no integer NOT NULL DEFAULT 1,
  priority text NOT NULL,
  due_at timestamptz,
  stale boolean NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('queued','assigned','reviewing','changes_requested','ready_to_sign','rejected','closed')),
  CHECK (priority IN ('normal','urgent')),
  UNIQUE (tenant_id, candidate_id)
);
COMMENT ON TABLE review.case_file IS '分析師覆核工作項；非交易治理批准。';
COMMENT ON COLUMN review.case_file.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN review.case_file.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN review.case_file.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN review.case_file.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN review.case_file.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN review.case_file.status IS '狀態：queued / assigned / reviewing / changes_requested / ready_to_sign / rejected / closed';
COMMENT ON COLUMN review.case_file.candidate_id IS '指向 research.candidate，必須同一 tenant。';
COMMENT ON COLUMN review.case_file.analyst_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN review.case_file.current_revision_no IS '目前採用修訂';
COMMENT ON COLUMN review.case_file.priority IS 'normal/urgent';
COMMENT ON COLUMN review.case_file.due_at IS '工作期限';
COMMENT ON COLUMN review.case_file.stale IS '候選新版本到達或證據變更';

CREATE TABLE review.conversation (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  provider text NOT NULL,
  external_conversation_id text NOT NULL,
  scoped_context_ref text NOT NULL,
  created_by uuid NOT NULL,
  last_synced_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, provider, external_conversation_id)
);
COMMENT ON TABLE review.conversation IS 'Agora Servant 對話參照；不複製所有 Pantheon 知識。';
COMMENT ON COLUMN review.conversation.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN review.conversation.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN review.conversation.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN review.conversation.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN review.conversation.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN review.conversation.case_id IS '指向 review.case_file，必須同一 tenant。';
COMMENT ON COLUMN review.conversation.provider IS 'agora';
COMMENT ON COLUMN review.conversation.external_conversation_id IS '外部 conversation';
COMMENT ON COLUMN review.conversation.scoped_context_ref IS '最小上下文引用';
COMMENT ON COLUMN review.conversation.created_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN review.conversation.last_synced_at IS '最後同步時間';

CREATE TABLE review.revision_request (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'requested',
  case_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  requested_by uuid NOT NULL,
  base_revision_no integer NOT NULL,
  instruction_asset_id uuid NOT NULL,
  external_job_ref text,
  requested_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('requested','accepted','running','completed','failed','cancelled','stale'))
);
COMMENT ON TABLE review.revision_request IS '分析師提出修訂並綁精確輸入版本。';
COMMENT ON COLUMN review.revision_request.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN review.revision_request.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN review.revision_request.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN review.revision_request.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN review.revision_request.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN review.revision_request.status IS '狀態：requested / accepted / running / completed / failed / cancelled / stale';
COMMENT ON COLUMN review.revision_request.case_id IS '指向 review.case_file，必須同一 tenant。';
COMMENT ON COLUMN review.revision_request.conversation_id IS '指向 review.conversation，必須同一 tenant。';
COMMENT ON COLUMN review.revision_request.requested_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN review.revision_request.base_revision_no IS '被修改版本';
COMMENT ON COLUMN review.revision_request.instruction_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN review.revision_request.external_job_ref IS 'Servant job';
COMMENT ON COLUMN review.revision_request.requested_at IS '請求時間';

CREATE TABLE review.revision_result (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  request_id uuid NOT NULL,
  revision_no integer NOT NULL,
  artifact_ref text NOT NULL,
  content_sha256 char(64) NOT NULL,
  asset_id uuid NOT NULL,
  produced_at timestamptz NOT NULL,
  evidence_refs jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, request_id, revision_no)
);
COMMENT ON TABLE review.revision_result IS '修訂產物不可變，新稿不覆寫已審稿。';
COMMENT ON COLUMN review.revision_result.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN review.revision_result.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN review.revision_result.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN review.revision_result.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN review.revision_result.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN review.revision_result.request_id IS '指向 review.revision_request，必須同一 tenant。';
COMMENT ON COLUMN review.revision_result.revision_no IS '新修訂編號';
COMMENT ON COLUMN review.revision_result.artifact_ref IS '外部產物';
COMMENT ON COLUMN review.revision_result.content_sha256 IS '新產物內容摘要';
COMMENT ON COLUMN review.revision_result.asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN review.revision_result.produced_at IS '產生時間';
COMMENT ON COLUMN review.revision_result.evidence_refs IS '本次引用來源；可對回 research evidence';

CREATE TABLE report.report (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  review_case_id uuid NOT NULL,
  report_number text NOT NULL,
  title text NOT NULL,
  instrument_refs jsonb NOT NULL,
  report_type text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (report_type IN ('initial','update','exit','supplement')),
  UNIQUE (tenant_id, report_number)
);
COMMENT ON TABLE report.report IS '正式投顧研究報告的穩定識別。';
COMMENT ON COLUMN report.report.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN report.report.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN report.report.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN report.report.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN report.report.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN report.report.review_case_id IS '指向 review.case_file，必須同一 tenant。';
COMMENT ON COLUMN report.report.report_number IS '正式報告編號';
COMMENT ON COLUMN report.report.title IS '標題，不含客戶個資';
COMMENT ON COLUMN report.report.instrument_refs IS '研究標的 ID／市場；並不等同可交易指令。';
COMMENT ON COLUMN report.report.report_type IS 'initial/update/exit/supplement';

CREATE TABLE report.report_version (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'draft',
  report_id uuid NOT NULL,
  report_version_no integer NOT NULL,
  candidate_id uuid NOT NULL,
  revision_result_id uuid,
  content_asset_id uuid NOT NULL,
  content_sha256 char(64) NOT NULL,
  bundle_sha256 char(64) NOT NULL,
  bundle_manifest_asset_id uuid NOT NULL,
  bundle_manifest jsonb NOT NULL,
  review_policy_version text NOT NULL,
  requires_compliance_review boolean NOT NULL DEFAULT true,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz NOT NULL,
  frozen_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('draft','in_review','changes_requested','ready_to_sign','signature_pending','signed','superseded','withdrawn')),
  CHECK (valid_until > valid_from),
  CHECK (status NOT IN ('ready_to_sign','signature_pending','signed') OR frozen_at IS NOT NULL),
  UNIQUE (tenant_id, report_id, report_version_no)
);
COMMENT ON TABLE report.report_version IS '不可變報告正文版本及簽前核定 bundle；狀態投影可前進。';
COMMENT ON COLUMN report.report_version.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN report.report_version.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN report.report_version.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN report.report_version.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN report.report_version.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN report.report_version.status IS '狀態：draft / in_review / changes_requested / ready_to_sign / signature_pending / signed / superseded / withdrawn';
COMMENT ON COLUMN report.report_version.report_id IS '指向 report.report，必須同一 tenant。';
COMMENT ON COLUMN report.report_version.report_version_no IS '報告版號';
COMMENT ON COLUMN report.report_version.candidate_id IS '指向 research.candidate，必須同一 tenant。';
COMMENT ON COLUMN report.report_version.revision_result_id IS '指向 review.revision_result，必須同一 tenant。';
COMMENT ON COLUMN report.report_version.content_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN report.report_version.content_sha256 IS '核准報告原 bytes hash';
COMMENT ON COLUMN report.report_version.bundle_sha256 IS '包含 channel 文案與 audience 規則版本的 canonical bundle hash';
COMMENT ON COLUMN report.report_version.bundle_manifest_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN report.report_version.bundle_manifest IS '確定的訊息版本、正文、受眾規則、風險揭露引用';
COMMENT ON COLUMN report.report_version.review_policy_version IS '需要哪些 professional/compliance review 的政策版本';
COMMENT ON COLUMN report.report_version.requires_compliance_review IS '是否須法遵先行覆核';
COMMENT ON COLUMN report.report_version.valid_from IS '研究建議開始有效時間';
COMMENT ON COLUMN report.report_version.valid_until IS '到期不可再發送';
COMMENT ON COLUMN report.report_version.frozen_at IS '簽前凍結時間';

CREATE TABLE report.review_decision (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  report_version_id uuid NOT NULL,
  review_type text NOT NULL,
  decision text NOT NULL,
  reviewer_id uuid NOT NULL,
  content_sha256 char(64) NOT NULL,
  bundle_sha256 char(64) NOT NULL,
  reviewed_at timestamptz NOT NULL,
  evidence_asset_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (review_type IN ('professional','compliance')),
  CHECK (decision IN ('approved','changes_requested','rejected'))
);
COMMENT ON TABLE report.review_decision IS '逐版本覆核，綁相同內容與 bundle；修訂後不能沿用。';
COMMENT ON COLUMN report.review_decision.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN report.review_decision.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN report.review_decision.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN report.review_decision.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN report.review_decision.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN report.review_decision.report_version_id IS '指向 report.report_version，必須同一 tenant。';
COMMENT ON COLUMN report.review_decision.review_type IS 'professional/compliance';
COMMENT ON COLUMN report.review_decision.decision IS 'approved/changes_requested/rejected';
COMMENT ON COLUMN report.review_decision.reviewer_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN report.review_decision.content_sha256 IS '被審查正文 hash';
COMMENT ON COLUMN report.review_decision.bundle_sha256 IS '被審查 bundle hash';
COMMENT ON COLUMN report.review_decision.reviewed_at IS '真實覆核時間';
COMMENT ON COLUMN report.review_decision.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';

CREATE TABLE report.signature_request (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'pending',
  report_version_id uuid NOT NULL,
  signer_id uuid NOT NULL,
  credential_id uuid NOT NULL,
  bundle_sha256 char(64) NOT NULL,
  provider text NOT NULL,
  provider_transaction_id text,
  requested_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('pending','submitted','verified','failed','expired')),
  UNIQUE (tenant_id, provider, provider_transaction_id)
);
COMMENT ON TABLE report.signature_request IS '簽章 provider 呼叫狀態；逾時需查詢，不可盲目重簽。';
COMMENT ON COLUMN report.signature_request.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN report.signature_request.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN report.signature_request.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN report.signature_request.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN report.signature_request.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN report.signature_request.status IS '狀態：pending / submitted / verified / failed / expired';
COMMENT ON COLUMN report.signature_request.report_version_id IS '指向 report.report_version，必須同一 tenant。';
COMMENT ON COLUMN report.signature_request.signer_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN report.signature_request.credential_id IS '指向 core.staff_credential，必須同一 tenant。';
COMMENT ON COLUMN report.signature_request.bundle_sha256 IS '待簽 bundle hash';
COMMENT ON COLUMN report.signature_request.provider IS '簽章供應商';
COMMENT ON COLUMN report.signature_request.provider_transaction_id IS 'provider transaction';
COMMENT ON COLUMN report.signature_request.requested_at IS '要求時間';
COMMENT ON COLUMN report.signature_request.expires_at IS '簽署挑戰到期';

CREATE TABLE report.signature (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  request_id uuid NOT NULL,
  report_version_id uuid NOT NULL,
  signer_id uuid NOT NULL,
  credential_id uuid NOT NULL,
  content_sha256 char(64) NOT NULL,
  bundle_sha256 char(64) NOT NULL,
  signed_file_sha256 char(64) NOT NULL,
  signed_asset_id uuid NOT NULL,
  verification_asset_id uuid NOT NULL,
  signed_at timestamptz NOT NULL,
  verified_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (verified_at >= signed_at),
  UNIQUE (tenant_id, request_id),
  UNIQUE (tenant_id, report_version_id)
);
COMMENT ON TABLE report.signature IS '已驗證分析師簽章證據；永不改內容。撤銷用 revocation。';
COMMENT ON COLUMN report.signature.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN report.signature.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN report.signature.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN report.signature.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN report.signature.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN report.signature.request_id IS '指向 report.signature_request，必須同一 tenant。';
COMMENT ON COLUMN report.signature.report_version_id IS '指向 report.report_version，必須同一 tenant。';
COMMENT ON COLUMN report.signature.signer_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN report.signature.credential_id IS '指向 core.staff_credential，必須同一 tenant。';
COMMENT ON COLUMN report.signature.content_sha256 IS '被簽報告正文 hash';
COMMENT ON COLUMN report.signature.bundle_sha256 IS '被簽完整服務訊息 bundle hash';
COMMENT ON COLUMN report.signature.signed_file_sha256 IS '簽後 PDF/容器 hash';
COMMENT ON COLUMN report.signature.signed_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN report.signature.verification_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN report.signature.signed_at IS '實際簽章時間';
COMMENT ON COLUMN report.signature.verified_at IS '驗簽成功時間';

CREATE TABLE report.revocation (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  signature_id uuid NOT NULL,
  actor_id uuid NOT NULL,
  reason_code text NOT NULL,
  revoked_at timestamptz NOT NULL,
  evidence_asset_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE report.revocation IS '撤銷或重驗失敗保留原簽章證據。';
COMMENT ON COLUMN report.revocation.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN report.revocation.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN report.revocation.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN report.revocation.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN report.revocation.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN report.revocation.signature_id IS '指向 report.signature，必須同一 tenant。';
COMMENT ON COLUMN report.revocation.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN report.revocation.reason_code IS '撤銷理由';
COMMENT ON COLUMN report.revocation.revoked_at IS '真實撤銷時間';
COMMENT ON COLUMN report.revocation.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';

CREATE TABLE publication.recommendation_series (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  series_code text NOT NULL,
  instrument_refs jsonb NOT NULL,
  strategy_ref text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, series_code)
);
COMMENT ON TABLE publication.recommendation_series IS '帶進、追蹤、帶出的建議序列，與 Persona 交易及客戶交易分離。';
COMMENT ON COLUMN publication.recommendation_series.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN publication.recommendation_series.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN publication.recommendation_series.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN publication.recommendation_series.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN publication.recommendation_series.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN publication.recommendation_series.series_code IS '建議序列碼';
COMMENT ON COLUMN publication.recommendation_series.instrument_refs IS '標的引用';
COMMENT ON COLUMN publication.recommendation_series.strategy_ref IS '可選外部策略參考';

CREATE TABLE publication.recommendation_version (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  series_id uuid NOT NULL,
  recommendation_version_no integer NOT NULL,
  action_type text NOT NULL,
  report_version_id uuid NOT NULL,
  channel_content jsonb NOT NULL,
  content_sha256 char(64) NOT NULL,
  audience_rule_version text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz NOT NULL,
  corrects_version_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (action_type IN ('entry','update','exit','correction')),
  CHECK (valid_until > valid_from),
  UNIQUE (tenant_id, series_id, recommendation_version_no)
);
COMMENT ON TABLE publication.recommendation_version IS '正式訊息與同時有效報告連結；重大判斷改變需新簽證。';
COMMENT ON COLUMN publication.recommendation_version.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN publication.recommendation_version.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN publication.recommendation_version.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN publication.recommendation_version.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN publication.recommendation_version.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN publication.recommendation_version.series_id IS '指向 publication.recommendation_series，必須同一 tenant。';
COMMENT ON COLUMN publication.recommendation_version.recommendation_version_no IS '序列版號';
COMMENT ON COLUMN publication.recommendation_version.action_type IS 'entry/update/exit/correction';
COMMENT ON COLUMN publication.recommendation_version.report_version_id IS '指向 report.report_version，必須同一 tenant。';
COMMENT ON COLUMN publication.recommendation_version.channel_content IS '已覆核 channel 文案或物件引用；簽章後禁止 LLM 改寫投資判斷。';
COMMENT ON COLUMN publication.recommendation_version.content_sha256 IS 'channel canonical content hash';
COMMENT ON COLUMN publication.recommendation_version.audience_rule_version IS '固定受眾規則版本';
COMMENT ON COLUMN publication.recommendation_version.valid_from IS '開始可用';
COMMENT ON COLUMN publication.recommendation_version.valid_until IS '到期不再發送';
COMMENT ON COLUMN publication.recommendation_version.corrects_version_id IS '指向 publication.recommendation_version，必須同一 tenant。';

CREATE TABLE publication.publication (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'queued',
  recommendation_version_id uuid NOT NULL,
  signature_id uuid NOT NULL,
  bundle_sha256 char(64) NOT NULL,
  queued_at timestamptz NOT NULL,
  published_at timestamptz,
  expires_at timestamptz NOT NULL,
  correction_of_id uuid,
  withdrawal_reason_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('queued','publishing','published','partially_failed','expired','corrected','withdrawn')),
  UNIQUE (tenant_id, recommendation_version_id)
);
COMMENT ON TABLE publication.publication IS '驗簽完成即建立發布，不加第二個 Publish 人工作業。';
COMMENT ON COLUMN publication.publication.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN publication.publication.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN publication.publication.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN publication.publication.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN publication.publication.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN publication.publication.status IS '狀態：queued / publishing / published / partially_failed / expired / corrected / withdrawn';
COMMENT ON COLUMN publication.publication.recommendation_version_id IS '指向 publication.recommendation_version，必須同一 tenant。';
COMMENT ON COLUMN publication.publication.signature_id IS '指向 report.signature，必須同一 tenant。';
COMMENT ON COLUMN publication.publication.bundle_sha256 IS '必須等於簽章 bundle';
COMMENT ON COLUMN publication.publication.queued_at IS '自動排入發布時間';
COMMENT ON COLUMN publication.publication.published_at IS '平台 feed 成功發布時間';
COMMENT ON COLUMN publication.publication.expires_at IS '期限；不得超過報告與訊息有效期';
COMMENT ON COLUMN publication.publication.correction_of_id IS '指向 publication.publication，必須同一 tenant。';
COMMENT ON COLUMN publication.publication.withdrawal_reason_code IS '撤回理由';

CREATE TABLE publication.audience_snapshot (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  publication_id uuid NOT NULL,
  rule_version text NOT NULL,
  evaluated_at timestamptz NOT NULL,
  snapshot_sha256 char(64) NOT NULL,
  recipient_count integer NOT NULL,
  snapshot_asset_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (recipient_count >= 0),
  UNIQUE (tenant_id, publication_id)
);
COMMENT ON TABLE publication.audience_snapshot IS '發布當下受眾規則及快照；不取代送出時再次查權限。';
COMMENT ON COLUMN publication.audience_snapshot.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN publication.audience_snapshot.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN publication.audience_snapshot.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN publication.audience_snapshot.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN publication.audience_snapshot.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN publication.audience_snapshot.publication_id IS '指向 publication.publication，必須同一 tenant。';
COMMENT ON COLUMN publication.audience_snapshot.rule_version IS '受眾規則版本';
COMMENT ON COLUMN publication.audience_snapshot.evaluated_at IS '評估時間';
COMMENT ON COLUMN publication.audience_snapshot.snapshot_sha256 IS '收件人與權益版本快照 hash';
COMMENT ON COLUMN publication.audience_snapshot.recipient_count IS '人數';
COMMENT ON COLUMN publication.audience_snapshot.snapshot_asset_id IS '指向 core.object_asset，必須同一 tenant。';

CREATE TABLE publication.recipient (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  snapshot_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  entitlement_id uuid NOT NULL,
  entitlement_version integer NOT NULL,
  eligibility_reason text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, snapshot_id, customer_id, entitlement_id)
);
COMMENT ON TABLE publication.recipient IS '逐收件人權益證據；不存姓名或手機。';
COMMENT ON COLUMN publication.recipient.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN publication.recipient.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN publication.recipient.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN publication.recipient.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN publication.recipient.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN publication.recipient.snapshot_id IS '指向 publication.audience_snapshot，必須同一 tenant。';
COMMENT ON COLUMN publication.recipient.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN publication.recipient.entitlement_id IS '指向 contract.entitlement，必須同一 tenant。';
COMMENT ON COLUMN publication.recipient.entitlement_version IS '發布當下權益版本';
COMMENT ON COLUMN publication.recipient.eligibility_reason IS '安全的符合理由';

CREATE TABLE delivery.message (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'queued',
  publication_id uuid NOT NULL,
  recipient_id uuid NOT NULL,
  channel text NOT NULL,
  channel_binding_id uuid,
  rendered_sha256 char(64) NOT NULL,
  rendered_asset_id uuid NOT NULL,
  provider_idempotency_key text NOT NULL,
  expires_at timestamptz NOT NULL,
  last_authorized_at timestamptz,
  checked_entitlement_version integer,
  read_at timestamptz,
  suppression_reason text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('queued','accepted','sent','delivered','failed','expired','suppressed','unknown')),
  CHECK (channel IN ('legendflow','line','sms','email')),
  CHECK (channel='legendflow' OR channel_binding_id IS NOT NULL),
  UNIQUE (tenant_id, publication_id, recipient_id, channel),
  UNIQUE (tenant_id, provider_idempotency_key)
);
COMMENT ON TABLE delivery.message IS '每 recipient/channel 一個邏輯消息；供應商重試另建 attempt。';
COMMENT ON COLUMN delivery.message.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN delivery.message.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN delivery.message.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN delivery.message.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN delivery.message.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN delivery.message.status IS '狀態：queued / accepted / sent / delivered / failed / expired / suppressed / unknown';
COMMENT ON COLUMN delivery.message.publication_id IS '指向 publication.publication，必須同一 tenant。';
COMMENT ON COLUMN delivery.message.recipient_id IS '指向 publication.recipient，必須同一 tenant。';
COMMENT ON COLUMN delivery.message.channel IS 'legendflow/line/sms/email';
COMMENT ON COLUMN delivery.message.channel_binding_id IS '指向 delivery.channel_binding，必須同一 tenant。';
COMMENT ON COLUMN delivery.message.rendered_sha256 IS '已核准文字 bytes hash';
COMMENT ON COLUMN delivery.message.rendered_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN delivery.message.provider_idempotency_key IS '同一邏輯消息穩定 key';
COMMENT ON COLUMN delivery.message.expires_at IS '建議有效期限';
COMMENT ON COLUMN delivery.message.last_authorized_at IS '送出前最後權限查核時間';
COMMENT ON COLUMN delivery.message.checked_entitlement_version IS '送出前權益版本';
COMMENT ON COLUMN delivery.message.read_at IS '僅 provider 可證實閱讀才填；沒有回執時 null。';
COMMENT ON COLUMN delivery.message.suppression_reason IS '退訂/到期/撤回等安全原因';

CREATE TABLE delivery.channel_binding (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'verified',
  customer_id uuid NOT NULL,
  channel text NOT NULL,
  provider text NOT NULL,
  destination_ciphertext bytea NOT NULL,
  key_ref text NOT NULL,
  destination_hmac char(64) NOT NULL,
  proof_asset_id uuid NOT NULL,
  external_identity_id uuid,
  verified_at timestamptz NOT NULL,
  service_delivery_enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('verified','unsubscribed','blocked','revoked')),
  CHECK (channel IN ('web','line','sms','email')),
  UNIQUE (tenant_id, provider, channel, destination_hmac)
);
COMMENT ON TABLE delivery.channel_binding IS '經驗證會員/LINE/手機/郵件收件身分；路由不可使用模型產生的新地址。';
COMMENT ON COLUMN delivery.channel_binding.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN delivery.channel_binding.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN delivery.channel_binding.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN delivery.channel_binding.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN delivery.channel_binding.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN delivery.channel_binding.status IS '狀態：verified / unsubscribed / blocked / revoked';
COMMENT ON COLUMN delivery.channel_binding.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN delivery.channel_binding.channel IS 'web/line/sms/email';
COMMENT ON COLUMN delivery.channel_binding.provider IS '通路provider';
COMMENT ON COLUMN delivery.channel_binding.destination_ciphertext IS '實際收件address/subject加密';
COMMENT ON COLUMN delivery.channel_binding.key_ref IS 'KMS reference';
COMMENT ON COLUMN delivery.channel_binding.destination_hmac IS '正規化收件subject的keyed HMAC';
COMMENT ON COLUMN delivery.channel_binding.proof_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN delivery.channel_binding.external_identity_id IS '指向 customer.external_identity，必須同一 tenant。';
COMMENT ON COLUMN delivery.channel_binding.verified_at IS '身份與channel同一性核驗時間';
COMMENT ON COLUMN delivery.channel_binding.service_delivery_enabled IS '客戶服務通知通路授權';

CREATE TABLE delivery.attempt (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  message_id uuid NOT NULL,
  attempt_no integer NOT NULL,
  provider text NOT NULL,
  provider_message_id text,
  outcome text NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  error_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (outcome IN ('accepted','rejected','timeout','unknown')),
  UNIQUE (tenant_id, message_id, attempt_no)
);
COMMENT ON TABLE delivery.attempt IS '各次對 provider 實際傳送嘗試。';
COMMENT ON COLUMN delivery.attempt.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN delivery.attempt.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN delivery.attempt.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN delivery.attempt.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN delivery.attempt.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN delivery.attempt.message_id IS '指向 delivery.message，必須同一 tenant。';
COMMENT ON COLUMN delivery.attempt.attempt_no IS '遞增嘗試編號';
COMMENT ON COLUMN delivery.attempt.provider IS '供應商';
COMMENT ON COLUMN delivery.attempt.provider_message_id IS 'provider ID';
COMMENT ON COLUMN delivery.attempt.outcome IS 'accepted/rejected/timeout/unknown';
COMMENT ON COLUMN delivery.attempt.started_at IS '開始呼叫';
COMMENT ON COLUMN delivery.attempt.finished_at IS '呼叫結束';
COMMENT ON COLUMN delivery.attempt.error_code IS '安全錯誤碼';

CREATE TABLE delivery.receipt (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  message_id uuid NOT NULL,
  provider text NOT NULL,
  provider_event_id text NOT NULL,
  receipt_type text NOT NULL,
  provider_occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  payload_sha256 char(64) NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (receipt_type IN ('accepted','sent','delivered','read','failed')),
  UNIQUE (tenant_id, provider, provider_event_id)
);
COMMENT ON TABLE delivery.receipt IS '回呼事件不可變，亂序回呼不可把 delivered 降級為 sent。';
COMMENT ON COLUMN delivery.receipt.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN delivery.receipt.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN delivery.receipt.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN delivery.receipt.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN delivery.receipt.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN delivery.receipt.message_id IS '指向 delivery.message，必須同一 tenant。';
COMMENT ON COLUMN delivery.receipt.provider IS '供應商';
COMMENT ON COLUMN delivery.receipt.provider_event_id IS '回呼唯一 ID';
COMMENT ON COLUMN delivery.receipt.receipt_type IS 'accepted/sent/delivered/read/failed';
COMMENT ON COLUMN delivery.receipt.provider_occurred_at IS 'provider 事件時間';
COMMENT ON COLUMN delivery.receipt.received_at IS '平台收到時間';
COMMENT ON COLUMN delivery.receipt.payload_sha256 IS '驗證後回呼摘要';

CREATE TABLE crm.marketing_attribution (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  source_type text NOT NULL,
  campaign_ref text,
  channel_ref text,
  acquired_at timestamptz NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  evidence_asset_id uuid,
  supersedes_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE crm.marketing_attribution IS '客戶最初來源及後續可稽核 attribution；不因換業務清除。';
COMMENT ON COLUMN crm.marketing_attribution.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN crm.marketing_attribution.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN crm.marketing_attribution.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN crm.marketing_attribution.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN crm.marketing_attribution.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN crm.marketing_attribution.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN crm.marketing_attribution.source_type IS 'web/campaign/referral/manual/unknown';
COMMENT ON COLUMN crm.marketing_attribution.campaign_ref IS 'campaign reference';
COMMENT ON COLUMN crm.marketing_attribution.channel_ref IS '流量來源';
COMMENT ON COLUMN crm.marketing_attribution.acquired_at IS '取得潛客時間';
COMMENT ON COLUMN crm.marketing_attribution.evidence_sha256 IS 'source evidence hash';
COMMENT ON COLUMN crm.marketing_attribution.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN crm.marketing_attribution.supersedes_id IS '指向 crm.marketing_attribution，必須同一 tenant。';

CREATE TABLE crm.opportunity (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'open',
  customer_id uuid NOT NULL,
  kind text NOT NULL,
  owner_actor_id uuid,
  contract_service_id uuid,
  target_service_code text NOT NULL,
  due_at timestamptz,
  loss_reason_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('open','qualifying','proposed','awaiting_customer','converted','lost','cancelled')),
  CHECK (kind IN ('new','renewal','upgrade','reactivation'))
);
COMMENT ON TABLE crm.opportunity IS '首次申辦、續訂、升級或重新開發機會。';
COMMENT ON COLUMN crm.opportunity.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN crm.opportunity.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN crm.opportunity.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN crm.opportunity.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN crm.opportunity.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN crm.opportunity.status IS '狀態：open / qualifying / proposed / awaiting_customer / converted / lost / cancelled';
COMMENT ON COLUMN crm.opportunity.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN crm.opportunity.kind IS 'new/renewal/upgrade/reactivation';
COMMENT ON COLUMN crm.opportunity.owner_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN crm.opportunity.contract_service_id IS '指向 contract.contract_service，必須同一 tenant。';
COMMENT ON COLUMN crm.opportunity.target_service_code IS '建議服務';
COMMENT ON COLUMN crm.opportunity.due_at IS '預定跟進時間';
COMMENT ON COLUMN crm.opportunity.loss_reason_code IS '流失原因';

CREATE TABLE crm.activity (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  opportunity_id uuid,
  actor_id uuid NOT NULL,
  activity_type text NOT NULL,
  occurred_at timestamptz NOT NULL,
  two_way boolean NOT NULL,
  evidence_asset_id uuid,
  outcome_code text NOT NULL,
  qualifies_for_claim boolean NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE crm.activity IS '真實業務行為及客戶回應證據；AI 建議、群發或分配名字不能單獨計獎。';
COMMENT ON COLUMN crm.activity.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN crm.activity.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN crm.activity.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN crm.activity.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN crm.activity.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN crm.activity.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN crm.activity.opportunity_id IS '指向 crm.opportunity，必須同一 tenant。';
COMMENT ON COLUMN crm.activity.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN crm.activity.activity_type IS 'call/meeting/conversation/customer_request/automated_campaign';
COMMENT ON COLUMN crm.activity.occurred_at IS '實際接觸時間';
COMMENT ON COLUMN crm.activity.two_way IS '有客戶回應';
COMMENT ON COLUMN crm.activity.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN crm.activity.outcome_code IS '結果代碼';
COMMENT ON COLUMN crm.activity.qualifies_for_claim IS '僅候選證據，最終仍須 conversion 核定';

CREATE TABLE crm.conversion (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  opportunity_id uuid NOT NULL,
  contract_service_id uuid NOT NULL,
  sales_actor_id uuid,
  qualifying_activity_id uuid,
  conversion_type text NOT NULL,
  decision_status text NOT NULL,
  decided_by uuid,
  converted_at timestamptz NOT NULL,
  attribution_id uuid,
  policy_version text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (conversion_type IN ('self_service','sales_assisted')),
  CHECK (decision_status IN ('proposed','approved','rejected','disputed')),
  CHECK (conversion_type <> 'sales_assisted' OR (sales_actor_id IS NOT NULL AND qualifying_activity_id IS NOT NULL)),
  UNIQUE (tenant_id, contract_service_id)
);
COMMENT ON TABLE crm.conversion IS '單筆交易銷售歸因；不是單純維護人欄位。';
COMMENT ON COLUMN crm.conversion.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN crm.conversion.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN crm.conversion.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN crm.conversion.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN crm.conversion.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN crm.conversion.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN crm.conversion.opportunity_id IS '指向 crm.opportunity，必須同一 tenant。';
COMMENT ON COLUMN crm.conversion.contract_service_id IS '指向 contract.contract_service，必須同一 tenant。';
COMMENT ON COLUMN crm.conversion.sales_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN crm.conversion.qualifying_activity_id IS '指向 crm.activity，必須同一 tenant。';
COMMENT ON COLUMN crm.conversion.conversion_type IS 'self_service/sales_assisted';
COMMENT ON COLUMN crm.conversion.decision_status IS 'proposed/approved/rejected/disputed';
COMMENT ON COLUMN crm.conversion.decided_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN crm.conversion.converted_at IS '完成續訂/升級時間';
COMMENT ON COLUMN crm.conversion.attribution_id IS '指向 crm.marketing_attribution，必須同一 tenant。';
COMMENT ON COLUMN crm.conversion.policy_version IS '歸因規則版本';

CREATE TABLE crm.work_item (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'open',
  customer_id uuid NOT NULL,
  opportunity_id uuid,
  owner_actor_id uuid,
  reason_code text NOT NULL,
  next_action text NOT NULL,
  due_at timestamptz NOT NULL,
  dedupe_key text NOT NULL,
  explanation jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('open','assigned','in_progress','done','snoozed','cancelled')),
  UNIQUE (tenant_id, dedupe_key)
);
COMMENT ON TABLE crm.work_item IS '業務要執行的具體名單工作；不只顯示分數。';
COMMENT ON COLUMN crm.work_item.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN crm.work_item.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN crm.work_item.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN crm.work_item.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN crm.work_item.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN crm.work_item.status IS '狀態：open / assigned / in_progress / done / snoozed / cancelled';
COMMENT ON COLUMN crm.work_item.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN crm.work_item.opportunity_id IS '指向 crm.opportunity，必須同一 tenant。';
COMMENT ON COLUMN crm.work_item.owner_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN crm.work_item.reason_code IS 'incomplete_application/renewal/upgrade/recovery/service_risk 等';
COMMENT ON COLUMN crm.work_item.next_action IS '具體下一步';
COMMENT ON COLUMN crm.work_item.due_at IS '到期時間';
COMMENT ON COLUMN crm.work_item.dedupe_key IS '避免同一客戶同類工作重複';
COMMENT ON COLUMN crm.work_item.explanation IS '規則來源與可解釋證據 IDs；非 AML 明細。';

CREATE TABLE crm.suppression (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  scope text NOT NULL,
  reason_code text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz,
  actor_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (valid_until IS NULL OR valid_until > valid_from)
);
COMMENT ON TABLE crm.suppression IS '限制招攬但不停止必要服務通知。';
COMMENT ON COLUMN crm.suppression.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN crm.suppression.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN crm.suppression.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN crm.suppression.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN crm.suppression.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN crm.suppression.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN crm.suppression.scope IS 'marketing/all_sales/channel_code';
COMMENT ON COLUMN crm.suppression.reason_code IS 'opt_out/complaint/hold/temporary';
COMMENT ON COLUMN crm.suppression.valid_from IS '開始';
COMMENT ON COLUMN crm.suppression.valid_until IS '截止，null 為直到另行撤銷';
COMMENT ON COLUMN crm.suppression.actor_id IS '指向 core.actor，必須同一 tenant。';

CREATE TABLE ownership.policy_version (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  policy_code text NOT NULL,
  policy_version integer NOT NULL,
  approval_status text NOT NULL,
  proposed_by uuid NOT NULL,
  lapse_basis text NOT NULL,
  lapse_days integer NOT NULL DEFAULT 7,
  departure_immediate boolean NOT NULL DEFAULT true,
  approved_by uuid,
  approved_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (approval_status IN ('draft','approved','retired')),
  CHECK (lapse_days > 0),
  CHECK (approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by)),
  UNIQUE (tenant_id, policy_code, policy_version)
);
COMMENT ON TABLE ownership.policy_version IS 'Pool 判定政策；七天未聯絡不是既定規則。';
COMMENT ON COLUMN ownership.policy_version.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN ownership.policy_version.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN ownership.policy_version.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN ownership.policy_version.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN ownership.policy_version.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN ownership.policy_version.policy_code IS 'pool policy';
COMMENT ON COLUMN ownership.policy_version.policy_version IS '版本';
COMMENT ON COLUMN ownership.policy_version.approval_status IS 'draft/approved/retired';
COMMENT ON COLUMN ownership.policy_version.proposed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN ownership.policy_version.lapse_basis IS 'proposed_all_paid_services_ended；需依決策登錄核定';
COMMENT ON COLUMN ownership.policy_version.lapse_days IS '連續中斷天數';
COMMENT ON COLUMN ownership.policy_version.departure_immediate IS '離職即歸公司';
COMMENT ON COLUMN ownership.policy_version.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN ownership.policy_version.approved_at IS '核准時間';

CREATE TABLE ownership.assignment (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  owner_actor_id uuid,
  owner_type text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz,
  reason_code text NOT NULL,
  policy_version_id uuid NOT NULL,
  assigned_by uuid NOT NULL,
  predecessor_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (owner_type IN ('company_pool','sales')),
  CHECK ((owner_type = 'sales') = (owner_actor_id IS NOT NULL)),
  CHECK (valid_until IS NULL OR valid_until > valid_from)
);
COMMENT ON TABLE ownership.assignment IS '維護關係的有效時間；不直接決定所有交易佣金。';
COMMENT ON COLUMN ownership.assignment.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN ownership.assignment.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN ownership.assignment.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN ownership.assignment.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN ownership.assignment.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN ownership.assignment.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN ownership.assignment.owner_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN ownership.assignment.owner_type IS 'company_pool/sales';
COMMENT ON COLUMN ownership.assignment.valid_from IS '歸屬生效';
COMMENT ON COLUMN ownership.assignment.valid_until IS '歸屬終止，null 仍有效';
COMMENT ON COLUMN ownership.assignment.reason_code IS 'assignment/departure/service_lapse/redevelopment';
COMMENT ON COLUMN ownership.assignment.policy_version_id IS '指向 ownership.policy_version，必須同一 tenant。';
COMMENT ON COLUMN ownership.assignment.assigned_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN ownership.assignment.predecessor_id IS '指向 ownership.assignment，必須同一 tenant。';

CREATE TABLE ownership.pool_entry (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  assignment_id uuid NOT NULL,
  reason_code text NOT NULL,
  all_paid_services_ended_at timestamptz,
  entered_at timestamptz NOT NULL,
  claimed_by uuid,
  claimed_at timestamptz,
  claim_activity_id uuid,
  evaluation_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (reason_code IN ('staff_departure','all_paid_services_lapsed')),
  CHECK (reason_code <> 'all_paid_services_lapsed' OR all_paid_services_ended_at IS NOT NULL)
);
COMMENT ON TABLE ownership.pool_entry IS '客戶回公司 Pool 的證據與重新開發狀態。';
COMMENT ON COLUMN ownership.pool_entry.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN ownership.pool_entry.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN ownership.pool_entry.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN ownership.pool_entry.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN ownership.pool_entry.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN ownership.pool_entry.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN ownership.pool_entry.assignment_id IS '指向 ownership.assignment，必須同一 tenant。';
COMMENT ON COLUMN ownership.pool_entry.reason_code IS 'staff_departure/all_paid_services_lapsed';
COMMENT ON COLUMN ownership.pool_entry.all_paid_services_ended_at IS '全部付費服務中斷起點；離職時可 null';
COMMENT ON COLUMN ownership.pool_entry.entered_at IS '真正歸公司時間';
COMMENT ON COLUMN ownership.pool_entry.claimed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN ownership.pool_entry.claimed_at IS '新業務接手時間';
COMMENT ON COLUMN ownership.pool_entry.claim_activity_id IS '指向 crm.activity，必須同一 tenant。';
COMMENT ON COLUMN ownership.pool_entry.evaluation_snapshot IS '所查全部 service ids/status/period/policy；有年度 active 服務不可用月訂中斷判全客流失。';

CREATE TABLE support.case_file (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'new',
  customer_id uuid,
  case_number text NOT NULL,
  category text NOT NULL,
  priority text NOT NULL,
  owner_actor_id uuid,
  contract_id uuid,
  publication_id uuid,
  sla_policy_version text NOT NULL,
  first_response_due_at timestamptz NOT NULL,
  resolution_due_at timestamptz,
  resolved_at timestamptz,
  resolution_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('new','triaged','assigned','waiting_customer','waiting_internal','resolved','closed','reopened')),
  UNIQUE (tenant_id, case_number)
);
COMMENT ON TABLE support.case_file IS '線上線下統一客服案件；客服 owner 不等於業務歸屬。';
COMMENT ON COLUMN support.case_file.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.case_file.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.case_file.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.case_file.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.case_file.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.case_file.status IS '狀態：new / triaged / assigned / waiting_customer / waiting_internal / resolved / closed / reopened';
COMMENT ON COLUMN support.case_file.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN support.case_file.case_number IS '案件號';
COMMENT ON COLUMN support.case_file.category IS 'service/complaint/refund/research/access/security';
COMMENT ON COLUMN support.case_file.priority IS 'low/normal/high/critical';
COMMENT ON COLUMN support.case_file.owner_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN support.case_file.contract_id IS '指向 contract.contract，必須同一 tenant。';
COMMENT ON COLUMN support.case_file.publication_id IS '指向 publication.publication，必須同一 tenant。';
COMMENT ON COLUMN support.case_file.sla_policy_version IS 'SLA 規則版本';
COMMENT ON COLUMN support.case_file.first_response_due_at IS '首次回應期限';
COMMENT ON COLUMN support.case_file.resolution_due_at IS '解決期限';
COMMENT ON COLUMN support.case_file.resolved_at IS '解決時間';
COMMENT ON COLUMN support.case_file.resolution_code IS '結案分類';

CREATE TABLE support.interaction (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  channel text NOT NULL,
  direction text NOT NULL,
  actor_id uuid,
  external_message_id text,
  content_asset_id uuid NOT NULL,
  occurred_at timestamptz NOT NULL,
  recording_consent_ref text,
  ai_draft boolean NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (channel IN ('web','line','email','phone','onsite')),
  CHECK (direction IN ('inbound','outbound','internal')),
  UNIQUE (tenant_id, channel, external_message_id)
);
COMMENT ON TABLE support.interaction IS '官網、訊息、郵件、電話、現場之原始接觸與人工/AI摘要來源。';
COMMENT ON COLUMN support.interaction.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.interaction.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.interaction.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.interaction.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.interaction.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.interaction.case_id IS '指向 support.case_file，必須同一 tenant。';
COMMENT ON COLUMN support.interaction.channel IS 'web/line/email/phone/onsite';
COMMENT ON COLUMN support.interaction.direction IS 'inbound/outbound/internal';
COMMENT ON COLUMN support.interaction.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN support.interaction.external_message_id IS '來源訊息 ID';
COMMENT ON COLUMN support.interaction.content_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN support.interaction.occurred_at IS '接觸時間';
COMMENT ON COLUMN support.interaction.recording_consent_ref IS '錄音同意/告知參考';
COMMENT ON COLUMN support.interaction.ai_draft IS '是 AI 草稿則禁止當已送出';

CREATE TABLE support.sla_event (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  case_id uuid NOT NULL,
  action text NOT NULL,
  occurred_at timestamptz NOT NULL,
  actor_id uuid NOT NULL,
  reason_code text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0)
);
COMMENT ON TABLE support.sla_event IS 'SLA 暫停、恢復、升級的 append-only 事件。';
COMMENT ON COLUMN support.sla_event.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.sla_event.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.sla_event.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.sla_event.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.sla_event.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.sla_event.case_id IS '指向 support.case_file，必須同一 tenant。';
COMMENT ON COLUMN support.sla_event.action IS 'started/paused/resumed/escalated/met/breached';
COMMENT ON COLUMN support.sla_event.occurred_at IS '發生時間';
COMMENT ON COLUMN support.sla_event.actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN support.sla_event.reason_code IS '理由';

CREATE TABLE support.response_draft (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'queued',
  case_id uuid NOT NULL,
  created_by uuid NOT NULL,
  content_asset_id uuid,
  content_sha256 char(64),
  knowledge_version text NOT NULL,
  model_version text NOT NULL,
  prompt_version text NOT NULL,
  source_refs jsonb NOT NULL,
  contains_new_investment_judgment boolean NOT NULL DEFAULT false,
  supersedes_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('queued','drafted','requires_human','blocked','failed')),
  CHECK ((content_asset_id IS NULL) = (content_sha256 IS NULL)),
  CHECK (status NOT IN ('drafted','requires_human') OR content_asset_id IS NOT NULL)
);
COMMENT ON TABLE support.response_draft IS '客服回覆草稿與來源版本；草稿永遠不代表可直接對外發送。';
COMMENT ON COLUMN support.response_draft.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.response_draft.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.response_draft.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.response_draft.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.response_draft.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.response_draft.status IS '狀態：queued / drafted / requires_human / blocked / failed';
COMMENT ON COLUMN support.response_draft.case_id IS '指向 support.case_file，必須同一 tenant。';
COMMENT ON COLUMN support.response_draft.created_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN support.response_draft.content_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN support.response_draft.content_sha256 IS '草稿精確內容hash；產生後不得原地改寫';
COMMENT ON COLUMN support.response_draft.knowledge_version IS '核准知識庫版本';
COMMENT ON COLUMN support.response_draft.model_version IS '固定模型或manual';
COMMENT ON COLUMN support.response_draft.prompt_version IS 'prompt版本或manual';
COMMENT ON COLUMN support.response_draft.source_refs IS '核准來源UUID列表';
COMMENT ON COLUMN support.response_draft.contains_new_investment_judgment IS '偵測新投資判斷時阻擋客服發送';
COMMENT ON COLUMN support.response_draft.supersedes_id IS '指向 support.response_draft，必須同一 tenant。';

CREATE TABLE support.response_approval (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  draft_id uuid NOT NULL,
  draft_content_sha256 char(64) NOT NULL,
  decision text NOT NULL,
  approval_mode text NOT NULL,
  reviewer_id uuid NOT NULL,
  template_version text NOT NULL,
  policy_version text NOT NULL,
  evidence_asset_id uuid NOT NULL,
  approved_at timestamptz NOT NULL,
  valid_until timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (decision IN ('approve','reject')),
  CHECK (approval_mode IN ('human_reviewed','approved_low_risk_template')),
  CHECK (valid_until>approved_at)
);
COMMENT ON TABLE support.response_approval IS '精確草稿的核准或駁回證據；low-risk template由受控policy service記錄。';
COMMENT ON COLUMN support.response_approval.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.response_approval.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.response_approval.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.response_approval.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.response_approval.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.response_approval.draft_id IS '指向 support.response_draft，必須同一 tenant。';
COMMENT ON COLUMN support.response_approval.draft_content_sha256 IS '被決定之草稿hash';
COMMENT ON COLUMN support.response_approval.decision IS 'approve/reject';
COMMENT ON COLUMN support.response_approval.approval_mode IS 'human_reviewed/approved_low_risk_template';
COMMENT ON COLUMN support.response_approval.reviewer_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN support.response_approval.template_version IS '核准低風險模板版本；human-only可填manual';
COMMENT ON COLUMN support.response_approval.policy_version IS '自動回覆或人工覆核政策';
COMMENT ON COLUMN support.response_approval.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN support.response_approval.approved_at IS '決策真實時點';
COMMENT ON COLUMN support.response_approval.valid_until IS '核定的發送期限';

CREATE TABLE support.response_dispatch (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'queued',
  case_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  draft_id uuid NOT NULL,
  content_sha256 char(64) NOT NULL,
  channel_binding_id uuid NOT NULL,
  channel text NOT NULL,
  approval_id uuid NOT NULL,
  approval_mode text NOT NULL,
  idempotency_key text NOT NULL,
  requested_by uuid NOT NULL,
  publication_reference_ids jsonb NOT NULL,
  provider_message_ref text,
  accepted_at timestamptz,
  sent_at timestamptz,
  delivered_at timestamptz,
  read_at timestamptz,
  valid_until timestamptz NOT NULL,
  outbound_interaction_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('queued','accepted','sent','delivered','failed','unknown','cancelled','expired')),
  CHECK (channel IN ('web','line','sms','email')),
  CHECK (approval_mode IN ('human_reviewed','approved_low_risk_template')),
  UNIQUE (tenant_id, idempotency_key)
);
COMMENT ON TABLE support.response_dispatch IS '已核准客服回覆的邏輯發送，與人工記錄interaction分開。';
COMMENT ON COLUMN support.response_dispatch.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.response_dispatch.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.response_dispatch.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.response_dispatch.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.response_dispatch.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.response_dispatch.status IS '狀態：queued / accepted / sent / delivered / failed / unknown / cancelled / expired';
COMMENT ON COLUMN support.response_dispatch.case_id IS '指向 support.case_file，必須同一 tenant。';
COMMENT ON COLUMN support.response_dispatch.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN support.response_dispatch.draft_id IS '指向 support.response_draft，必須同一 tenant。';
COMMENT ON COLUMN support.response_dispatch.content_sha256 IS '核准精確內容hash';
COMMENT ON COLUMN support.response_dispatch.channel_binding_id IS '指向 delivery.channel_binding，必須同一 tenant。';
COMMENT ON COLUMN support.response_dispatch.channel IS 'web/line/sms/email';
COMMENT ON COLUMN support.response_dispatch.approval_id IS '指向 support.response_approval，必須同一 tenant。';
COMMENT ON COLUMN support.response_dispatch.approval_mode IS 'human_reviewed/approved_low_risk_template';
COMMENT ON COLUMN support.response_dispatch.idempotency_key IS '邏輯回覆穩定key';
COMMENT ON COLUMN support.response_dispatch.requested_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN support.response_dispatch.publication_reference_ids IS '只連結已發布且客戶有權限之內容；不可加入新投資判斷。';
COMMENT ON COLUMN support.response_dispatch.provider_message_ref IS '外部消息識別';
COMMENT ON COLUMN support.response_dispatch.accepted_at IS 'provider接受時間';
COMMENT ON COLUMN support.response_dispatch.sent_at IS 'provider確認送出';
COMMENT ON COLUMN support.response_dispatch.delivered_at IS 'provider確認送達';
COMMENT ON COLUMN support.response_dispatch.read_at IS '僅可證實閱讀才填';
COMMENT ON COLUMN support.response_dispatch.valid_until IS '回覆不得再送出的期限';
COMMENT ON COLUMN support.response_dispatch.outbound_interaction_id IS '指向 support.interaction，必須同一 tenant。';

CREATE TABLE support.response_attempt (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  response_id uuid NOT NULL,
  attempt_no integer NOT NULL,
  provider text NOT NULL,
  provider_request_id text NOT NULL,
  outcome text NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  error_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (outcome IN ('accepted','rejected','timeout','unknown')),
  UNIQUE (tenant_id, response_id, attempt_no)
);
COMMENT ON TABLE support.response_attempt IS '客服外送provider各次呼叫結果；未知狀態先查詢再重試。';
COMMENT ON COLUMN support.response_attempt.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.response_attempt.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.response_attempt.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.response_attempt.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.response_attempt.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.response_attempt.response_id IS '指向 support.response_dispatch，必須同一 tenant。';
COMMENT ON COLUMN support.response_attempt.attempt_no IS '嘗試序號';
COMMENT ON COLUMN support.response_attempt.provider IS '通路provider';
COMMENT ON COLUMN support.response_attempt.provider_request_id IS '供應商請求/去重key';
COMMENT ON COLUMN support.response_attempt.outcome IS 'accepted/rejected/timeout/unknown';
COMMENT ON COLUMN support.response_attempt.started_at IS '呼叫開始';
COMMENT ON COLUMN support.response_attempt.finished_at IS '呼叫結束';
COMMENT ON COLUMN support.response_attempt.error_code IS '安全錯誤代碼';

CREATE TABLE support.response_receipt (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  response_id uuid NOT NULL,
  provider text NOT NULL,
  event_id text NOT NULL,
  provider_message_ref text NOT NULL,
  provider_status text NOT NULL,
  provider_occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  provider_receipt_asset_id uuid NOT NULL,
  payload_sha256 char(64) NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (provider_status IN ('accepted','sent','delivered','failed','read')),
  UNIQUE (tenant_id, provider, event_id)
);
COMMENT ON TABLE support.response_receipt IS '客服發送回條不可變；accepted/sent/delivered/read 分開。';
COMMENT ON COLUMN support.response_receipt.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN support.response_receipt.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN support.response_receipt.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN support.response_receipt.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN support.response_receipt.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN support.response_receipt.response_id IS '指向 support.response_dispatch，必須同一 tenant。';
COMMENT ON COLUMN support.response_receipt.provider IS '通路provider';
COMMENT ON COLUMN support.response_receipt.event_id IS '來源回條唯一ID';
COMMENT ON COLUMN support.response_receipt.provider_message_ref IS 'provider消息ID';
COMMENT ON COLUMN support.response_receipt.provider_status IS 'accepted/sent/delivered/failed/read';
COMMENT ON COLUMN support.response_receipt.provider_occurred_at IS 'provider真實時間';
COMMENT ON COLUMN support.response_receipt.received_at IS '平台收到時間';
COMMENT ON COLUMN support.response_receipt.provider_receipt_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN support.response_receipt.payload_sha256 IS '驗證過的回條hash';

CREATE TABLE finance.invoice (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'draft',
  customer_id uuid NOT NULL,
  contract_id uuid NOT NULL,
  invoice_number text NOT NULL,
  currency text NOT NULL,
  untaxed_amount numeric(20,6) NOT NULL,
  tax_amount numeric(20,6) NOT NULL,
  gross_amount numeric(20,6) NOT NULL,
  tax_profile_ref text NOT NULL,
  issued_at timestamptz,
  due_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('draft','issued','paid','voided','credited')),
  CHECK (gross_amount = untaxed_amount + tax_amount),
  CHECK (untaxed_amount >= 0 AND tax_amount >= 0),
  UNIQUE (tenant_id, invoice_number)
);
COMMENT ON TABLE finance.invoice IS '應收/發票文件與稅別；不得預設所有服務都是5%營業稅。';
COMMENT ON COLUMN finance.invoice.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.invoice.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.invoice.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.invoice.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.invoice.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.invoice.status IS '狀態：draft / issued / paid / voided / credited';
COMMENT ON COLUMN finance.invoice.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN finance.invoice.contract_id IS '指向 contract.contract，必須同一 tenant。';
COMMENT ON COLUMN finance.invoice.invoice_number IS '內部或電子發票識別碼';
COMMENT ON COLUMN finance.invoice.currency IS 'ISO currency';
COMMENT ON COLUMN finance.invoice.untaxed_amount IS '經核准稅類之未稅金額';
COMMENT ON COLUMN finance.invoice.tax_amount IS '稅額';
COMMENT ON COLUMN finance.invoice.gross_amount IS '應付總額';
COMMENT ON COLUMN finance.invoice.tax_profile_ref IS '核准稅務映射版本';
COMMENT ON COLUMN finance.invoice.issued_at IS '開立時間';
COMMENT ON COLUMN finance.invoice.due_at IS '到期時間';

CREATE TABLE finance.invoice_line (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  invoice_id uuid NOT NULL,
  contract_service_id uuid NOT NULL,
  line_no integer NOT NULL,
  untaxed_amount numeric(20,6) NOT NULL,
  tax_amount numeric(20,6) NOT NULL,
  description_code text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (untaxed_amount >= 0 AND tax_amount >= 0),
  UNIQUE (tenant_id, invoice_id, line_no)
);
COMMENT ON TABLE finance.invoice_line IS '逐服務帳款明細；升級增額不重複算原方案。';
COMMENT ON COLUMN finance.invoice_line.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.invoice_line.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.invoice_line.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.invoice_line.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.invoice_line.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.invoice_line.invoice_id IS '指向 finance.invoice，必須同一 tenant。';
COMMENT ON COLUMN finance.invoice_line.contract_service_id IS '指向 contract.contract_service，必須同一 tenant。';
COMMENT ON COLUMN finance.invoice_line.line_no IS '項次';
COMMENT ON COLUMN finance.invoice_line.untaxed_amount IS '未稅服務額';
COMMENT ON COLUMN finance.invoice_line.tax_amount IS '稅額';
COMMENT ON COLUMN finance.invoice_line.description_code IS '受控服務描述';

CREATE TABLE finance.payment (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'pending',
  customer_id uuid NOT NULL,
  invoice_id uuid,
  application_id uuid,
  provider text NOT NULL,
  provider_transaction_id text NOT NULL,
  amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  paid_at timestamptz,
  payment_method_ref text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('pending','authorized','paid','failed','refund_pending','refunded','disputed')),
  CHECK (amount >= 0),
  CHECK (invoice_id IS NOT NULL OR application_id IS NOT NULL),
  UNIQUE (tenant_id, provider, provider_transaction_id)
);
COMMENT ON TABLE finance.payment IS '收款與授權；收現不直接算營收，未接受可為暫收款。';
COMMENT ON COLUMN finance.payment.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.payment.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.payment.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.payment.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.payment.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.payment.status IS '狀態：pending / authorized / paid / failed / refund_pending / refunded / disputed';
COMMENT ON COLUMN finance.payment.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN finance.payment.invoice_id IS '指向 finance.invoice，必須同一 tenant。';
COMMENT ON COLUMN finance.payment.application_id IS '指向 customer.application，必須同一 tenant。';
COMMENT ON COLUMN finance.payment.provider IS '支付供應商';
COMMENT ON COLUMN finance.payment.provider_transaction_id IS '供應商交易號';
COMMENT ON COLUMN finance.payment.amount IS '實際收款或授權金額';
COMMENT ON COLUMN finance.payment.currency IS '幣別';
COMMENT ON COLUMN finance.payment.paid_at IS '收款時間';
COMMENT ON COLUMN finance.payment.payment_method_ref IS 'tokenized method reference，禁止 PAN/CVV';

CREATE TABLE finance.settlement (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  payment_id uuid NOT NULL,
  provider_settlement_id text NOT NULL,
  gross_amount numeric(20,6) NOT NULL,
  fee_amount numeric(20,6) NOT NULL,
  net_amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  settled_at timestamptz NOT NULL,
  bank_line_ref text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (net_amount = gross_amount - fee_amount),
  CHECK (fee_amount >= 0),
  UNIQUE (tenant_id, provider_settlement_id)
);
COMMENT ON TABLE finance.settlement IS '支付商入帳／銀行結算，手續費分列。';
COMMENT ON COLUMN finance.settlement.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.settlement.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.settlement.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.settlement.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.settlement.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.settlement.payment_id IS '指向 finance.payment，必須同一 tenant。';
COMMENT ON COLUMN finance.settlement.provider_settlement_id IS '結算唯一 ID';
COMMENT ON COLUMN finance.settlement.gross_amount IS '原收款總額';
COMMENT ON COLUMN finance.settlement.fee_amount IS '支付費用';
COMMENT ON COLUMN finance.settlement.net_amount IS '銀行淨入帳';
COMMENT ON COLUMN finance.settlement.currency IS '幣別';
COMMENT ON COLUMN finance.settlement.settled_at IS '銀行入帳時間';
COMMENT ON COLUMN finance.settlement.bank_line_ref IS '銀行明細引用';

CREATE TABLE finance.reconciliation_item (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'unmatched',
  source_type text NOT NULL,
  source_ref text NOT NULL,
  payment_id uuid,
  difference_amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  reason_code text,
  resolved_by uuid,
  resolved_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('unmatched','matched','exception','resolved')),
  UNIQUE (tenant_id, source_type, source_ref)
);
COMMENT ON TABLE finance.reconciliation_item IS '金流、發票、銀行與總帳差異處理。';
COMMENT ON COLUMN finance.reconciliation_item.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.reconciliation_item.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.reconciliation_item.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.reconciliation_item.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.reconciliation_item.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.reconciliation_item.status IS '狀態：unmatched / matched / exception / resolved';
COMMENT ON COLUMN finance.reconciliation_item.source_type IS 'payment/settlement/bank/invoice/gl';
COMMENT ON COLUMN finance.reconciliation_item.source_ref IS '來源識別';
COMMENT ON COLUMN finance.reconciliation_item.payment_id IS '指向 finance.payment，必須同一 tenant。';
COMMENT ON COLUMN finance.reconciliation_item.difference_amount IS '差異金額';
COMMENT ON COLUMN finance.reconciliation_item.currency IS '幣別';
COMMENT ON COLUMN finance.reconciliation_item.reason_code IS '差異代碼';
COMMENT ON COLUMN finance.reconciliation_item.resolved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.reconciliation_item.resolved_at IS '解決時間';

CREATE TABLE finance.service_schedule (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  contract_service_id uuid NOT NULL,
  schedule_version integer NOT NULL,
  period_start timestamptz NOT NULL,
  period_end timestamptz NOT NULL,
  untaxed_amount numeric(20,6) NOT NULL,
  recognition_policy_version text NOT NULL,
  approved_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (period_end > period_start),
  CHECK (untaxed_amount >= 0),
  UNIQUE (tenant_id, contract_service_id, schedule_version, period_start)
);
COMMENT ON TABLE finance.service_schedule IS '已接受契約的服務收入認列分期表，與收現分離。';
COMMENT ON COLUMN finance.service_schedule.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.service_schedule.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.service_schedule.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.service_schedule.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.service_schedule.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.service_schedule.contract_service_id IS '指向 contract.contract_service，必須同一 tenant。';
COMMENT ON COLUMN finance.service_schedule.schedule_version IS '分期表版本';
COMMENT ON COLUMN finance.service_schedule.period_start IS '本次履約區間開始';
COMMENT ON COLUMN finance.service_schedule.period_end IS '區間截止';
COMMENT ON COLUMN finance.service_schedule.untaxed_amount IS '本區間應認列未稅額';
COMMENT ON COLUMN finance.service_schedule.recognition_policy_version IS '財會核准之認列政策';
COMMENT ON COLUMN finance.service_schedule.approved_by IS '指向 core.actor，必須同一 tenant。';

CREATE TABLE finance.revenue_run (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'queued',
  period_start date NOT NULL,
  period_end date NOT NULL,
  cutoff_at timestamptz NOT NULL,
  recognition_policy_version text NOT NULL,
  dry_run boolean NOT NULL,
  calculation_manifest_sha256 char(64) NOT NULL,
  currency text NOT NULL,
  recognized_untaxed_total numeric(20,6) NOT NULL,
  deferred_remaining numeric(20,6) NOT NULL,
  entry_count integer NOT NULL DEFAULT 0,
  exception_count integer NOT NULL DEFAULT 0,
  prepared_by uuid NOT NULL,
  approved_by uuid,
  approved_at timestamptz,
  posted_at timestamptz,
  run_key text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('queued','calculated','review_pending','approved','posted','failed')),
  CHECK (period_end > period_start),
  CHECK (approved_by IS NULL OR approved_by<>prepared_by),
  CHECK (entry_count>=0 AND exception_count>=0),
  CHECK (status NOT IN ('approved','posted') OR (NOT dry_run AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND exception_count=0)),
  UNIQUE (tenant_id, run_key)
);
COMMENT ON TABLE finance.revenue_run IS '收入認列批次之計算、覆核及正式過帳；dry-run不可進入付款/計獎。';
COMMENT ON COLUMN finance.revenue_run.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.revenue_run.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.revenue_run.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.revenue_run.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.revenue_run.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.revenue_run.status IS '狀態：queued / calculated / review_pending / approved / posted / failed';
COMMENT ON COLUMN finance.revenue_run.period_start IS '會計期間開始';
COMMENT ON COLUMN finance.revenue_run.period_end IS '會計期間結束，exclusive';
COMMENT ON COLUMN finance.revenue_run.cutoff_at IS '本批次資料截止時間';
COMMENT ON COLUMN finance.revenue_run.recognition_policy_version IS '財會核准認列政策版本';
COMMENT ON COLUMN finance.revenue_run.dry_run IS '僅試算則不能正式過帳';
COMMENT ON COLUMN finance.revenue_run.calculation_manifest_sha256 IS '輸入資料與計算manifest hash';
COMMENT ON COLUMN finance.revenue_run.currency IS '幣別';
COMMENT ON COLUMN finance.revenue_run.recognized_untaxed_total IS '本批計算收入';
COMMENT ON COLUMN finance.revenue_run.deferred_remaining IS '剩餘預收負債';
COMMENT ON COLUMN finance.revenue_run.entry_count IS '本批明細數';
COMMENT ON COLUMN finance.revenue_run.exception_count IS '未解例外數';
COMMENT ON COLUMN finance.revenue_run.prepared_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.revenue_run.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.revenue_run.approved_at IS '覆核時間';
COMMENT ON COLUMN finance.revenue_run.posted_at IS '過帳時間';
COMMENT ON COLUMN finance.revenue_run.run_key IS '輸入與政策的去重key';

CREATE TABLE finance.revenue_recognition (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  run_id uuid NOT NULL,
  schedule_id uuid NOT NULL,
  recognition_key text NOT NULL,
  recognized_at timestamptz NOT NULL,
  untaxed_amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  confirmed_by uuid NOT NULL,
  commission_eligible boolean NOT NULL,
  reverses_id uuid,
  policy_version text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  UNIQUE (tenant_id, recognition_key)
);
COMMENT ON TABLE finance.revenue_recognition IS '財會確認的收入／更正；獎金只消費 eligible 收入。';
COMMENT ON COLUMN finance.revenue_recognition.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.revenue_recognition.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.revenue_recognition.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.revenue_recognition.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.revenue_recognition.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.revenue_recognition.run_id IS '指向 finance.revenue_run，必須同一 tenant。';
COMMENT ON COLUMN finance.revenue_recognition.schedule_id IS '指向 finance.service_schedule，必須同一 tenant。';
COMMENT ON COLUMN finance.revenue_recognition.recognition_key IS '認列去重 key';
COMMENT ON COLUMN finance.revenue_recognition.recognized_at IS '收入入帳時間';
COMMENT ON COLUMN finance.revenue_recognition.untaxed_amount IS '實際已履約未稅收入；更正以新列負數';
COMMENT ON COLUMN finance.revenue_recognition.currency IS '幣別';
COMMENT ON COLUMN finance.revenue_recognition.confirmed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.revenue_recognition.commission_eligible IS '可計獎標記';
COMMENT ON COLUMN finance.revenue_recognition.reverses_id IS '指向 finance.revenue_recognition，必須同一 tenant。';
COMMENT ON COLUMN finance.revenue_recognition.policy_version IS '認列政策';

CREATE TABLE finance.refund (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'requested',
  payment_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  case_id uuid,
  amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  prepared_by uuid NOT NULL,
  approved_by uuid,
  provider_refund_id text,
  confirmed_at timestamptz,
  reason_code text NOT NULL,
  idempotency_key text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('requested','checking','approval_pending','approved','submitted','confirmed','failed','reconciliation_required','closed','rejected')),
  CHECK (amount > 0),
  CHECK (approved_by IS NULL OR approved_by <> prepared_by),
  CHECK (status NOT IN ('approved','submitted','confirmed','closed') OR approved_by IS NOT NULL),
  UNIQUE (tenant_id, idempotency_key)
);
COMMENT ON TABLE finance.refund IS '退款簽核與 provider 結果；失敗及未知不可當成功。';
COMMENT ON COLUMN finance.refund.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.refund.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.refund.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.refund.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.refund.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.refund.status IS '狀態：requested / checking / approval_pending / approved / submitted / confirmed / failed / reconciliation_required / closed / rejected';
COMMENT ON COLUMN finance.refund.payment_id IS '指向 finance.payment，必須同一 tenant。';
COMMENT ON COLUMN finance.refund.customer_id IS '指向 customer.customer，必須同一 tenant。';
COMMENT ON COLUMN finance.refund.case_id IS '指向 support.case_file，必須同一 tenant。';
COMMENT ON COLUMN finance.refund.amount IS '退款實際金額';
COMMENT ON COLUMN finance.refund.currency IS '幣別';
COMMENT ON COLUMN finance.refund.prepared_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.refund.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.refund.provider_refund_id IS 'provider 退款交易';
COMMENT ON COLUMN finance.refund.confirmed_at IS 'provider 已證實退款時間';
COMMENT ON COLUMN finance.refund.reason_code IS '退款理由';
COMMENT ON COLUMN finance.refund.idempotency_key IS '固定退款命令 key';

CREATE TABLE finance.refund_allocation (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  refund_id uuid NOT NULL,
  recognition_id uuid,
  allocation_type text NOT NULL,
  amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (allocation_type IN ('earned_revenue','unearned_prepayment','tax','fee')),
  CHECK (amount >= 0),
  CHECK ((allocation_type = 'earned_revenue') = (recognition_id IS NOT NULL))
);
COMMENT ON TABLE finance.refund_allocation IS '退款拆分已認列收入沖減與未履約負債返還，避免獎金重扣。';
COMMENT ON COLUMN finance.refund_allocation.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.refund_allocation.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.refund_allocation.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.refund_allocation.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.refund_allocation.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.refund_allocation.refund_id IS '指向 finance.refund，必須同一 tenant。';
COMMENT ON COLUMN finance.refund_allocation.recognition_id IS '指向 finance.revenue_recognition，必須同一 tenant。';
COMMENT ON COLUMN finance.refund_allocation.allocation_type IS 'earned_revenue/unearned_prepayment/tax/fee';
COMMENT ON COLUMN finance.refund_allocation.amount IS '歸屬金額，非負';
COMMENT ON COLUMN finance.refund_allocation.currency IS '幣別';

CREATE TABLE finance.journal (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'draft',
  journal_number text NOT NULL,
  source_type text NOT NULL,
  source_id uuid NOT NULL,
  currency text NOT NULL,
  accounting_date date NOT NULL,
  prepared_by uuid NOT NULL,
  approved_by uuid,
  posted_at timestamptz,
  reverses_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('draft','approved','posted','reversed')),
  CHECK (approved_by IS NULL OR approved_by <> prepared_by),
  CHECK (status NOT IN ('approved','posted') OR approved_by IS NOT NULL),
  UNIQUE (tenant_id, journal_number),
  UNIQUE (tenant_id, source_type, source_id)
);
COMMENT ON TABLE finance.journal IS '會計分錄憑證；posted 必須平衡且 maker/checker。';
COMMENT ON COLUMN finance.journal.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.journal.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.journal.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.journal.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.journal.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.journal.status IS '狀態：draft / approved / posted / reversed';
COMMENT ON COLUMN finance.journal.journal_number IS '分錄編號';
COMMENT ON COLUMN finance.journal.source_type IS 'payment/revenue/refund/commission/adjustment';
COMMENT ON COLUMN finance.journal.source_id IS '來源 ID';
COMMENT ON COLUMN finance.journal.currency IS '幣別';
COMMENT ON COLUMN finance.journal.accounting_date IS '會計日，以 Asia/Taipei 期間判斷';
COMMENT ON COLUMN finance.journal.prepared_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.journal.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.journal.posted_at IS '過帳時間';
COMMENT ON COLUMN finance.journal.reverses_id IS '指向 finance.journal，必須同一 tenant。';

CREATE TABLE finance.journal_line (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  journal_id uuid NOT NULL,
  line_no integer NOT NULL,
  account_code text NOT NULL,
  debit numeric(20,6) NOT NULL,
  credit numeric(20,6) NOT NULL,
  cost_center text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0)),
  UNIQUE (tenant_id, journal_id, line_no)
);
COMMENT ON TABLE finance.journal_line IS '雙式簿記借貸明細；全憑證延遲檢查平衡。';
COMMENT ON COLUMN finance.journal_line.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.journal_line.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.journal_line.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.journal_line.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.journal_line.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.journal_line.journal_id IS '指向 finance.journal，必須同一 tenant。';
COMMENT ON COLUMN finance.journal_line.line_no IS '項次';
COMMENT ON COLUMN finance.journal_line.account_code IS '財會核准科目';
COMMENT ON COLUMN finance.journal_line.debit IS '借方';
COMMENT ON COLUMN finance.journal_line.credit IS '貸方';
COMMENT ON COLUMN finance.journal_line.cost_center IS '部門歸屬';

CREATE TABLE finance.accounting_export (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'prepared',
  period_key text NOT NULL,
  destination text NOT NULL,
  asset_id uuid NOT NULL,
  export_sha256 char(64) NOT NULL,
  external_batch_id text,
  prepared_by uuid NOT NULL,
  approved_by uuid,
  journal_ids jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('prepared','submitted','acknowledged','rejected','reconciled')),
  CHECK (approved_by IS NULL OR approved_by <> prepared_by),
  UNIQUE (tenant_id, destination, export_sha256)
);
COMMENT ON TABLE finance.accounting_export IS '匯出會計系統的批次檔與對帳回條。';
COMMENT ON COLUMN finance.accounting_export.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.accounting_export.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.accounting_export.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.accounting_export.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.accounting_export.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.accounting_export.status IS '狀態：prepared / submitted / acknowledged / rejected / reconciled';
COMMENT ON COLUMN finance.accounting_export.period_key IS '會計期間 YYYY-MM';
COMMENT ON COLUMN finance.accounting_export.destination IS '會計 ERP';
COMMENT ON COLUMN finance.accounting_export.asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN finance.accounting_export.export_sha256 IS '匯出 hash';
COMMENT ON COLUMN finance.accounting_export.external_batch_id IS '外部批號';
COMMENT ON COLUMN finance.accounting_export.prepared_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.accounting_export.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.accounting_export.journal_ids IS '本批次 journal refs';

CREATE TABLE finance.control_exception (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'open',
  control_code text NOT NULL,
  resource_type text NOT NULL,
  resource_id uuid NOT NULL,
  severity text NOT NULL,
  owner_actor_id uuid,
  reviewer_id uuid,
  due_at timestamptz NOT NULL,
  evidence_asset_id uuid,
  resolution_code text,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('open','assigned','remediating','awaiting_review','closed','waived')),
  CHECK (owner_actor_id IS NULL OR reviewer_id IS NULL OR owner_actor_id <> reviewer_id)
);
COMMENT ON TABLE finance.control_exception IS '內控例外、補正、覆核與稽核證據索引；避免散落試算表。';
COMMENT ON COLUMN finance.control_exception.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN finance.control_exception.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN finance.control_exception.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN finance.control_exception.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN finance.control_exception.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN finance.control_exception.status IS '狀態：open / assigned / remediating / awaiting_review / closed / waived';
COMMENT ON COLUMN finance.control_exception.control_code IS '控制項代碼';
COMMENT ON COLUMN finance.control_exception.resource_type IS '資源類型';
COMMENT ON COLUMN finance.control_exception.resource_id IS '資源 ID';
COMMENT ON COLUMN finance.control_exception.severity IS 'low/medium/high/critical';
COMMENT ON COLUMN finance.control_exception.owner_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.control_exception.reviewer_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN finance.control_exception.due_at IS '補正期限';
COMMENT ON COLUMN finance.control_exception.evidence_asset_id IS '指向 core.object_asset，必須同一 tenant。';
COMMENT ON COLUMN finance.control_exception.resolution_code IS '關閉或限期例外理由';

CREATE TABLE commission.policy_version (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  policy_code text NOT NULL,
  policy_version integer NOT NULL,
  approval_status text NOT NULL,
  proposed_by uuid NOT NULL,
  base_pool_rate numeric(9,6),
  marketing_year1 numeric(9,6) NOT NULL DEFAULT 1.0,
  marketing_year2 numeric(9,6) NOT NULL DEFAULT 0.5,
  marketing_auto_tail numeric(9,6) NOT NULL DEFAULT 0.25,
  assisted_marketing_share numeric(9,6) NOT NULL DEFAULT 0.3,
  assisted_sales_share numeric(9,6) NOT NULL DEFAULT 0.7,
  decay_interaction text NOT NULL,
  tail_lineage_rule text NOT NULL,
  effective_from timestamptz NOT NULL,
  approved_by uuid,
  approved_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (approval_status IN ('draft','approved','retired')),
  CHECK (base_pool_rate IS NULL OR base_pool_rate BETWEEN 0 AND 1),
  CHECK (assisted_marketing_share + assisted_sales_share = 1),
  CHECK (marketing_year1 BETWEEN 0 AND 1 AND marketing_year2 BETWEEN 0 AND 1 AND marketing_auto_tail BETWEEN 0 AND 1),
  CHECK (approval_status <> 'approved' OR (base_pool_rate IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by AND decay_interaction <> 'unresolved' AND tail_lineage_rule <> 'unresolved')),
  UNIQUE (tenant_id, policy_code, policy_version)
);
COMMENT ON TABLE commission.policy_version IS '獎金演算法、來源年齡係數及未核准參數的版本化政策。';
COMMENT ON COLUMN commission.policy_version.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN commission.policy_version.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN commission.policy_version.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN commission.policy_version.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN commission.policy_version.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN commission.policy_version.policy_code IS '政策碼';
COMMENT ON COLUMN commission.policy_version.policy_version IS '版本';
COMMENT ON COLUMN commission.policy_version.approval_status IS 'draft/approved/retired';
COMMENT ON COLUMN commission.policy_version.proposed_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.policy_version.base_pool_rate IS '獎金池率；未核准為 null，不能偷偷預設3%。';
COMMENT ON COLUMN commission.policy_version.marketing_year1 IS '用戶已定第一年係數';
COMMENT ON COLUMN commission.policy_version.marketing_year2 IS '第二年係數';
COMMENT ON COLUMN commission.policy_version.marketing_auto_tail IS '第三年起合格連續自動續訂係數';
COMMENT ON COLUMN commission.policy_version.assisted_marketing_share IS '介入續約行銷池比例';
COMMENT ON COLUMN commission.policy_version.assisted_sales_share IS '介入續約業務池比例';
COMMENT ON COLUMN commission.policy_version.decay_interaction IS 'unresolved/marketing_only/whole_pool';
COMMENT ON COLUMN commission.policy_version.tail_lineage_rule IS 'unresolved / 另行核准的服務鏈規則';
COMMENT ON COLUMN commission.policy_version.effective_from IS '政策計算開始';
COMMENT ON COLUMN commission.policy_version.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.policy_version.approved_at IS '政策核准時間';

CREATE TABLE commission.calculation_run (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'queued',
  policy_version_id uuid NOT NULL,
  mode text NOT NULL,
  period_key text NOT NULL,
  run_key text NOT NULL,
  period_start date NOT NULL,
  period_end date NOT NULL,
  revenue_cutoff_at timestamptz NOT NULL,
  input_sha256 char(64) NOT NULL,
  prepared_by uuid NOT NULL,
  approved_by uuid,
  approved_at timestamptz,
  started_by uuid NOT NULL,
  started_at timestamptz NOT NULL,
  completed_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('queued','calculating','calculated','review_pending','approved','rejected','exported','failed')),
  CHECK (mode IN ('simulation','live')),
  CHECK (period_end>period_start),
  CHECK (approved_by IS NULL OR approved_by<>prepared_by),
  CHECK (status NOT IN ('approved','exported') OR (mode='live' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  UNIQUE (tenant_id, run_key)
);
COMMENT ON TABLE commission.calculation_run IS '可重現的試算或正式計獎批次；未核准政策只可 simulation。';
COMMENT ON COLUMN commission.calculation_run.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN commission.calculation_run.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN commission.calculation_run.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN commission.calculation_run.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN commission.calculation_run.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN commission.calculation_run.status IS '狀態：queued / calculating / calculated / review_pending / approved / rejected / exported / failed';
COMMENT ON COLUMN commission.calculation_run.policy_version_id IS '指向 commission.policy_version，必須同一 tenant。';
COMMENT ON COLUMN commission.calculation_run.mode IS 'simulation/live';
COMMENT ON COLUMN commission.calculation_run.period_key IS 'YYYY-MM';
COMMENT ON COLUMN commission.calculation_run.run_key IS '唯一計算資料與政策快照 key';
COMMENT ON COLUMN commission.calculation_run.period_start IS '計獎區間開始';
COMMENT ON COLUMN commission.calculation_run.period_end IS '計獎區間結束，exclusive';
COMMENT ON COLUMN commission.calculation_run.revenue_cutoff_at IS '收入/退款資料截止';
COMMENT ON COLUMN commission.calculation_run.input_sha256 IS '輸入收入、退款、歸因、年齡快照 hash';
COMMENT ON COLUMN commission.calculation_run.prepared_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.calculation_run.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.calculation_run.approved_at IS '覆核時間';
COMMENT ON COLUMN commission.calculation_run.started_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.calculation_run.started_at IS '開始';
COMMENT ON COLUMN commission.calculation_run.completed_at IS '計算完成';

CREATE TABLE commission.entry (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  run_id uuid NOT NULL,
  recognition_id uuid NOT NULL,
  conversion_id uuid,
  assignment_id uuid,
  attribution_id uuid,
  beneficiary_type text NOT NULL,
  beneficiary_actor_id uuid,
  eligible_untaxed_amount numeric(20,6) NOT NULL,
  base_pool_amount numeric(20,6) NOT NULL,
  award_amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  customer_year integer NOT NULL,
  age_coefficient numeric(9,6) NOT NULL,
  calculation_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (beneficiary_type IN ('marketing','sales','company')),
  CHECK (beneficiary_type <> 'sales' OR beneficiary_actor_id IS NOT NULL),
  CHECK (award_amount >= 0 AND base_pool_amount >= 0),
  CHECK (customer_year >= 1),
  UNIQUE (tenant_id, run_id, recognition_id, beneficiary_type)
);
COMMENT ON TABLE commission.entry IS '逐筆收入切分行銷、業務、公司留存之計算明細，原始列不可改。';
COMMENT ON COLUMN commission.entry.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN commission.entry.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN commission.entry.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN commission.entry.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN commission.entry.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN commission.entry.run_id IS '指向 commission.calculation_run，必須同一 tenant。';
COMMENT ON COLUMN commission.entry.recognition_id IS '指向 finance.revenue_recognition，必須同一 tenant。';
COMMENT ON COLUMN commission.entry.conversion_id IS '指向 crm.conversion，必須同一 tenant。';
COMMENT ON COLUMN commission.entry.assignment_id IS '指向 ownership.assignment，必須同一 tenant。';
COMMENT ON COLUMN commission.entry.attribution_id IS '指向 crm.marketing_attribution，必須同一 tenant。';
COMMENT ON COLUMN commission.entry.beneficiary_type IS 'marketing/sales/company';
COMMENT ON COLUMN commission.entry.beneficiary_actor_id IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.entry.eligible_untaxed_amount IS '本筆可計獎未稅淨服務收入';
COMMENT ON COLUMN commission.entry.base_pool_amount IS '全筆獎金池';
COMMENT ON COLUMN commission.entry.award_amount IS '該受益者分配額';
COMMENT ON COLUMN commission.entry.currency IS '幣別';
COMMENT ON COLUMN commission.entry.customer_year IS '以核准周年時鐘計算的來源年';
COMMENT ON COLUMN commission.entry.age_coefficient IS '實際年齡係數';
COMMENT ON COLUMN commission.entry.calculation_snapshot IS '政策版本、原始收入、退款、歸因證據、四捨五入與留存計算';

CREATE TABLE commission.adjustment (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  original_entry_id uuid NOT NULL,
  refund_allocation_id uuid,
  adjustment_amount numeric(20,6) NOT NULL,
  currency text NOT NULL,
  reason_code text NOT NULL,
  prepared_by uuid NOT NULL,
  approved_by uuid NOT NULL,
  approved_at timestamptz NOT NULL,
  recovery_method text NOT NULL,
  dedupe_key text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (prepared_by <> approved_by),
  UNIQUE (tenant_id, dedupe_key)
);
COMMENT ON TABLE commission.adjustment IS '退款/歸因修正以原受領人與原政策沖回；不直接扣薪。';
COMMENT ON COLUMN commission.adjustment.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN commission.adjustment.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN commission.adjustment.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN commission.adjustment.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN commission.adjustment.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN commission.adjustment.original_entry_id IS '指向 commission.entry，必須同一 tenant。';
COMMENT ON COLUMN commission.adjustment.refund_allocation_id IS '指向 finance.refund_allocation，必須同一 tenant。';
COMMENT ON COLUMN commission.adjustment.adjustment_amount IS '有正負號之調整金額';
COMMENT ON COLUMN commission.adjustment.currency IS '幣別';
COMMENT ON COLUMN commission.adjustment.reason_code IS 'refund/attribution_correction/calculation_correction';
COMMENT ON COLUMN commission.adjustment.prepared_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.adjustment.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.adjustment.approved_at IS '覆核時間';
COMMENT ON COLUMN commission.adjustment.recovery_method IS 'ledger_offset/manual_review，不代表可自動從薪資扣除';
COMMENT ON COLUMN commission.adjustment.dedupe_key IS '調整唯一鍵';

CREATE TABLE commission.payout_batch (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL DEFAULT 'draft',
  period_key text NOT NULL,
  prepared_by uuid NOT NULL,
  approved_by uuid,
  currency text NOT NULL,
  total_amount numeric(20,6) NOT NULL,
  payroll_export_ref text,
  paid_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (status IN ('draft','approval_pending','approved','exported','paid','reversed')),
  CHECK (total_amount >= 0),
  CHECK (approved_by IS NULL OR approved_by <> prepared_by),
  CHECK (status NOT IN ('approved','exported','paid') OR approved_by IS NOT NULL)
);
COMMENT ON TABLE commission.payout_batch IS '正式獎金支出批次；試算結果不可付款。';
COMMENT ON COLUMN commission.payout_batch.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN commission.payout_batch.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN commission.payout_batch.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN commission.payout_batch.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN commission.payout_batch.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN commission.payout_batch.status IS '狀態：draft / approval_pending / approved / exported / paid / reversed';
COMMENT ON COLUMN commission.payout_batch.period_key IS '期間';
COMMENT ON COLUMN commission.payout_batch.prepared_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.payout_batch.approved_by IS '指向 core.actor，必須同一 tenant。';
COMMENT ON COLUMN commission.payout_batch.currency IS '幣別';
COMMENT ON COLUMN commission.payout_batch.total_amount IS '付款批次合計';
COMMENT ON COLUMN commission.payout_batch.payroll_export_ref IS '薪資/應付匯出引用';
COMMENT ON COLUMN commission.payout_batch.paid_at IS '實際付款時間';

CREATE TABLE commission.payout_line (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  batch_id uuid NOT NULL,
  entry_id uuid NOT NULL,
  amount numeric(20,6) NOT NULL,
  adjustment_ids jsonb NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id),
  CHECK (version > 0),
  CHECK (amount >= 0),
  UNIQUE (tenant_id, entry_id)
);
COMMENT ON TABLE commission.payout_line IS '獎金列與付款批次關係，避免一筆獎金重複支付。';
COMMENT ON COLUMN commission.payout_line.tenant_id IS '組織隔離鍵；由可信任服務在交易開始設定。';
COMMENT ON COLUMN commission.payout_line.id IS '資源識別碼；在同一租戶內唯一。';
COMMENT ON COLUMN commission.payout_line.version IS 'CAS 樂觀鎖版本；更新時由資料庫遞增。';
COMMENT ON COLUMN commission.payout_line.created_at IS '建立的 UTC 絕對時間。';
COMMENT ON COLUMN commission.payout_line.updated_at IS '最近更新時間；歷史證據列不可更新。';
COMMENT ON COLUMN commission.payout_line.batch_id IS '指向 commission.payout_batch，必須同一 tenant。';
COMMENT ON COLUMN commission.payout_line.entry_id IS '指向 commission.entry，必須同一 tenant。';
COMMENT ON COLUMN commission.payout_line.amount IS '本批次支付數額';
COMMENT ON COLUMN commission.payout_line.adjustment_ids IS '本次採用的已核准 adjustment IDs';
ALTER TABLE core.staff_credential ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.credential_revocation ADD FOREIGN KEY (tenant_id, credential_id) REFERENCES core.staff_credential (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.credential_revocation ADD FOREIGN KEY (tenant_id, revoked_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.retention_policy ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.object_asset ADD FOREIGN KEY (tenant_id, retention_policy_id) REFERENCES core.retention_policy (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.legal_hold ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.legal_hold ADD FOREIGN KEY (tenant_id, prior_hold_id) REFERENCES core.legal_hold (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.audit_event ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core.idempotency_record ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.customer ADD FOREIGN KEY (tenant_id, merged_into_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.external_identity ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.external_identity ADD FOREIGN KEY (tenant_id, proof_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.application ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.application ADD FOREIGN KEY (tenant_id, package_version_id) REFERENCES contract.package_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.application ADD FOREIGN KEY (tenant_id, preceding_contract_id) REFERENCES contract.contract (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.consent ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.consent ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.merge_case ADD FOREIGN KEY (tenant_id, source_customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.merge_case ADD FOREIGN KEY (tenant_id, target_customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.merge_case ADD FOREIGN KEY (tenant_id, proposed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.merge_case ADD FOREIGN KEY (tenant_id, reviewed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE customer.merge_case ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.package_version ADD FOREIGN KEY (tenant_id, asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.package_version ADD FOREIGN KEY (tenant_id, proposed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.package_version ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.customer_signature ADD FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.customer_signature ADD FOREIGN KEY (tenant_id, package_version_id) REFERENCES contract.package_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.customer_signature ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.customer_signature ADD FOREIGN KEY (tenant_id, signed_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.customer_signature ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.acceptance_decision ADD FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.acceptance_decision ADD FOREIGN KEY (tenant_id, customer_signature_id) REFERENCES contract.customer_signature (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.acceptance_decision ADD FOREIGN KEY (tenant_id, decided_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, decision_id) REFERENCES contract.acceptance_decision (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, customer_signature_id) REFERENCES contract.customer_signature (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, kyc_submission_id) REFERENCES kyc.submission (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, suitability_assessment_id) REFERENCES kyc.suitability_assessment (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, aml_clearance_id) REFERENCES aml.business_clearance (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, accepted_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.company_acceptance ADD FOREIGN KEY (tenant_id, company_signed_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.contract ADD FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.contract ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.contract ADD FOREIGN KEY (tenant_id, customer_signature_id) REFERENCES contract.customer_signature (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.contract ADD FOREIGN KEY (tenant_id, company_acceptance_id) REFERENCES contract.company_acceptance (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.contract_service ADD FOREIGN KEY (tenant_id, contract_id) REFERENCES contract.contract (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.contract_service ADD FOREIGN KEY (tenant_id, predecessor_service_id) REFERENCES contract.contract_service (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.entitlement ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.entitlement ADD FOREIGN KEY (tenant_id, contract_service_id) REFERENCES contract.contract_service (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.entitlement_decision ADD FOREIGN KEY (tenant_id, entitlement_id) REFERENCES contract.entitlement (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE contract.entitlement_decision ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.submission ADD FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.submission ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.submission ADD FOREIGN KEY (tenant_id, reviewed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.submission_document ADD FOREIGN KEY (tenant_id, submission_id) REFERENCES kyc.submission (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.submission_document ADD FOREIGN KEY (tenant_id, asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.submission_document ADD FOREIGN KEY (tenant_id, verified_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.suitability_assessment ADD FOREIGN KEY (tenant_id, submission_id) REFERENCES kyc.submission (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.suitability_assessment ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE kyc.suitability_assessment ADD FOREIGN KEY (tenant_id, assessed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.screening ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.screening ADD FOREIGN KEY (tenant_id, submission_id) REFERENCES kyc.submission (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.screening ADD FOREIGN KEY (tenant_id, restricted_result_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.case_file ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.case_file ADD FOREIGN KEY (tenant_id, screening_id) REFERENCES aml.screening (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.case_file ADD FOREIGN KEY (tenant_id, owner_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.case_action ADD FOREIGN KEY (tenant_id, case_id) REFERENCES aml.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.case_action ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.case_action ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.business_clearance ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.business_clearance ADD FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.business_clearance ADD FOREIGN KEY (tenant_id, case_id) REFERENCES aml.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE aml.business_clearance ADD FOREIGN KEY (tenant_id, decided_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE research.candidate ADD FOREIGN KEY (tenant_id, snapshot_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE research.candidate_evidence ADD FOREIGN KEY (tenant_id, candidate_id) REFERENCES research.candidate (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE research.candidate_evidence ADD FOREIGN KEY (tenant_id, snapshot_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.case_file ADD FOREIGN KEY (tenant_id, candidate_id) REFERENCES research.candidate (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.case_file ADD FOREIGN KEY (tenant_id, analyst_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.conversation ADD FOREIGN KEY (tenant_id, case_id) REFERENCES review.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.conversation ADD FOREIGN KEY (tenant_id, created_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.revision_request ADD FOREIGN KEY (tenant_id, case_id) REFERENCES review.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.revision_request ADD FOREIGN KEY (tenant_id, conversation_id) REFERENCES review.conversation (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.revision_request ADD FOREIGN KEY (tenant_id, requested_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.revision_request ADD FOREIGN KEY (tenant_id, instruction_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.revision_result ADD FOREIGN KEY (tenant_id, request_id) REFERENCES review.revision_request (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE review.revision_result ADD FOREIGN KEY (tenant_id, asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.report ADD FOREIGN KEY (tenant_id, review_case_id) REFERENCES review.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.report_version ADD FOREIGN KEY (tenant_id, report_id) REFERENCES report.report (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.report_version ADD FOREIGN KEY (tenant_id, candidate_id) REFERENCES research.candidate (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.report_version ADD FOREIGN KEY (tenant_id, revision_result_id) REFERENCES review.revision_result (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.report_version ADD FOREIGN KEY (tenant_id, content_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.report_version ADD FOREIGN KEY (tenant_id, bundle_manifest_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.review_decision ADD FOREIGN KEY (tenant_id, report_version_id) REFERENCES report.report_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.review_decision ADD FOREIGN KEY (tenant_id, reviewer_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.review_decision ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature_request ADD FOREIGN KEY (tenant_id, report_version_id) REFERENCES report.report_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature_request ADD FOREIGN KEY (tenant_id, signer_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature_request ADD FOREIGN KEY (tenant_id, credential_id) REFERENCES core.staff_credential (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature ADD FOREIGN KEY (tenant_id, request_id) REFERENCES report.signature_request (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature ADD FOREIGN KEY (tenant_id, report_version_id) REFERENCES report.report_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature ADD FOREIGN KEY (tenant_id, signer_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature ADD FOREIGN KEY (tenant_id, credential_id) REFERENCES core.staff_credential (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature ADD FOREIGN KEY (tenant_id, signed_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.signature ADD FOREIGN KEY (tenant_id, verification_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.revocation ADD FOREIGN KEY (tenant_id, signature_id) REFERENCES report.signature (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.revocation ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE report.revocation ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.recommendation_version ADD FOREIGN KEY (tenant_id, series_id) REFERENCES publication.recommendation_series (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.recommendation_version ADD FOREIGN KEY (tenant_id, report_version_id) REFERENCES report.report_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.recommendation_version ADD FOREIGN KEY (tenant_id, corrects_version_id) REFERENCES publication.recommendation_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.publication ADD FOREIGN KEY (tenant_id, recommendation_version_id) REFERENCES publication.recommendation_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.publication ADD FOREIGN KEY (tenant_id, signature_id) REFERENCES report.signature (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.publication ADD FOREIGN KEY (tenant_id, correction_of_id) REFERENCES publication.publication (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.audience_snapshot ADD FOREIGN KEY (tenant_id, publication_id) REFERENCES publication.publication (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.audience_snapshot ADD FOREIGN KEY (tenant_id, snapshot_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.recipient ADD FOREIGN KEY (tenant_id, snapshot_id) REFERENCES publication.audience_snapshot (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.recipient ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE publication.recipient ADD FOREIGN KEY (tenant_id, entitlement_id) REFERENCES contract.entitlement (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.message ADD FOREIGN KEY (tenant_id, publication_id) REFERENCES publication.publication (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.message ADD FOREIGN KEY (tenant_id, recipient_id) REFERENCES publication.recipient (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.message ADD FOREIGN KEY (tenant_id, channel_binding_id) REFERENCES delivery.channel_binding (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.message ADD FOREIGN KEY (tenant_id, rendered_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.channel_binding ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.channel_binding ADD FOREIGN KEY (tenant_id, proof_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.channel_binding ADD FOREIGN KEY (tenant_id, external_identity_id) REFERENCES customer.external_identity (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.attempt ADD FOREIGN KEY (tenant_id, message_id) REFERENCES delivery.message (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE delivery.receipt ADD FOREIGN KEY (tenant_id, message_id) REFERENCES delivery.message (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.marketing_attribution ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.marketing_attribution ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.marketing_attribution ADD FOREIGN KEY (tenant_id, supersedes_id) REFERENCES crm.marketing_attribution (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.opportunity ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.opportunity ADD FOREIGN KEY (tenant_id, owner_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.opportunity ADD FOREIGN KEY (tenant_id, contract_service_id) REFERENCES contract.contract_service (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.activity ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.activity ADD FOREIGN KEY (tenant_id, opportunity_id) REFERENCES crm.opportunity (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.activity ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.activity ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.conversion ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.conversion ADD FOREIGN KEY (tenant_id, opportunity_id) REFERENCES crm.opportunity (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.conversion ADD FOREIGN KEY (tenant_id, contract_service_id) REFERENCES contract.contract_service (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.conversion ADD FOREIGN KEY (tenant_id, sales_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.conversion ADD FOREIGN KEY (tenant_id, qualifying_activity_id) REFERENCES crm.activity (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.conversion ADD FOREIGN KEY (tenant_id, decided_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.conversion ADD FOREIGN KEY (tenant_id, attribution_id) REFERENCES crm.marketing_attribution (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.work_item ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.work_item ADD FOREIGN KEY (tenant_id, opportunity_id) REFERENCES crm.opportunity (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.work_item ADD FOREIGN KEY (tenant_id, owner_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.suppression ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE crm.suppression ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.policy_version ADD FOREIGN KEY (tenant_id, proposed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.policy_version ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.assignment ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.assignment ADD FOREIGN KEY (tenant_id, owner_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.assignment ADD FOREIGN KEY (tenant_id, policy_version_id) REFERENCES ownership.policy_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.assignment ADD FOREIGN KEY (tenant_id, assigned_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.assignment ADD FOREIGN KEY (tenant_id, predecessor_id) REFERENCES ownership.assignment (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.pool_entry ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.pool_entry ADD FOREIGN KEY (tenant_id, assignment_id) REFERENCES ownership.assignment (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.pool_entry ADD FOREIGN KEY (tenant_id, claimed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE ownership.pool_entry ADD FOREIGN KEY (tenant_id, claim_activity_id) REFERENCES crm.activity (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.case_file ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.case_file ADD FOREIGN KEY (tenant_id, owner_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.case_file ADD FOREIGN KEY (tenant_id, contract_id) REFERENCES contract.contract (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.case_file ADD FOREIGN KEY (tenant_id, publication_id) REFERENCES publication.publication (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.interaction ADD FOREIGN KEY (tenant_id, case_id) REFERENCES support.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.interaction ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.interaction ADD FOREIGN KEY (tenant_id, content_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.sla_event ADD FOREIGN KEY (tenant_id, case_id) REFERENCES support.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.sla_event ADD FOREIGN KEY (tenant_id, actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_draft ADD FOREIGN KEY (tenant_id, case_id) REFERENCES support.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_draft ADD FOREIGN KEY (tenant_id, created_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_draft ADD FOREIGN KEY (tenant_id, content_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_draft ADD FOREIGN KEY (tenant_id, supersedes_id) REFERENCES support.response_draft (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_approval ADD FOREIGN KEY (tenant_id, draft_id) REFERENCES support.response_draft (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_approval ADD FOREIGN KEY (tenant_id, reviewer_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_approval ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_dispatch ADD FOREIGN KEY (tenant_id, case_id) REFERENCES support.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_dispatch ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_dispatch ADD FOREIGN KEY (tenant_id, draft_id) REFERENCES support.response_draft (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_dispatch ADD FOREIGN KEY (tenant_id, channel_binding_id) REFERENCES delivery.channel_binding (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_dispatch ADD FOREIGN KEY (tenant_id, approval_id) REFERENCES support.response_approval (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_dispatch ADD FOREIGN KEY (tenant_id, requested_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_dispatch ADD FOREIGN KEY (tenant_id, outbound_interaction_id) REFERENCES support.interaction (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_attempt ADD FOREIGN KEY (tenant_id, response_id) REFERENCES support.response_dispatch (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_receipt ADD FOREIGN KEY (tenant_id, response_id) REFERENCES support.response_dispatch (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE support.response_receipt ADD FOREIGN KEY (tenant_id, provider_receipt_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.invoice ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.invoice ADD FOREIGN KEY (tenant_id, contract_id) REFERENCES contract.contract (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.invoice_line ADD FOREIGN KEY (tenant_id, invoice_id) REFERENCES finance.invoice (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.invoice_line ADD FOREIGN KEY (tenant_id, contract_service_id) REFERENCES contract.contract_service (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.payment ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.payment ADD FOREIGN KEY (tenant_id, invoice_id) REFERENCES finance.invoice (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.payment ADD FOREIGN KEY (tenant_id, application_id) REFERENCES customer.application (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.settlement ADD FOREIGN KEY (tenant_id, payment_id) REFERENCES finance.payment (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.reconciliation_item ADD FOREIGN KEY (tenant_id, payment_id) REFERENCES finance.payment (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.reconciliation_item ADD FOREIGN KEY (tenant_id, resolved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.service_schedule ADD FOREIGN KEY (tenant_id, contract_service_id) REFERENCES contract.contract_service (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.service_schedule ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.revenue_run ADD FOREIGN KEY (tenant_id, prepared_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.revenue_run ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.revenue_recognition ADD FOREIGN KEY (tenant_id, run_id) REFERENCES finance.revenue_run (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.revenue_recognition ADD FOREIGN KEY (tenant_id, schedule_id) REFERENCES finance.service_schedule (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.revenue_recognition ADD FOREIGN KEY (tenant_id, confirmed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.revenue_recognition ADD FOREIGN KEY (tenant_id, reverses_id) REFERENCES finance.revenue_recognition (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.refund ADD FOREIGN KEY (tenant_id, payment_id) REFERENCES finance.payment (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.refund ADD FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.refund ADD FOREIGN KEY (tenant_id, case_id) REFERENCES support.case_file (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.refund ADD FOREIGN KEY (tenant_id, prepared_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.refund ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.refund_allocation ADD FOREIGN KEY (tenant_id, refund_id) REFERENCES finance.refund (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.refund_allocation ADD FOREIGN KEY (tenant_id, recognition_id) REFERENCES finance.revenue_recognition (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.journal ADD FOREIGN KEY (tenant_id, prepared_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.journal ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.journal ADD FOREIGN KEY (tenant_id, reverses_id) REFERENCES finance.journal (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.journal_line ADD FOREIGN KEY (tenant_id, journal_id) REFERENCES finance.journal (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.accounting_export ADD FOREIGN KEY (tenant_id, asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.accounting_export ADD FOREIGN KEY (tenant_id, prepared_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.accounting_export ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.control_exception ADD FOREIGN KEY (tenant_id, owner_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.control_exception ADD FOREIGN KEY (tenant_id, reviewer_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE finance.control_exception ADD FOREIGN KEY (tenant_id, evidence_asset_id) REFERENCES core.object_asset (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.policy_version ADD FOREIGN KEY (tenant_id, proposed_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.policy_version ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.calculation_run ADD FOREIGN KEY (tenant_id, policy_version_id) REFERENCES commission.policy_version (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.calculation_run ADD FOREIGN KEY (tenant_id, prepared_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.calculation_run ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.calculation_run ADD FOREIGN KEY (tenant_id, started_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.entry ADD FOREIGN KEY (tenant_id, run_id) REFERENCES commission.calculation_run (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.entry ADD FOREIGN KEY (tenant_id, recognition_id) REFERENCES finance.revenue_recognition (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.entry ADD FOREIGN KEY (tenant_id, conversion_id) REFERENCES crm.conversion (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.entry ADD FOREIGN KEY (tenant_id, assignment_id) REFERENCES ownership.assignment (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.entry ADD FOREIGN KEY (tenant_id, attribution_id) REFERENCES crm.marketing_attribution (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.entry ADD FOREIGN KEY (tenant_id, beneficiary_actor_id) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.adjustment ADD FOREIGN KEY (tenant_id, original_entry_id) REFERENCES commission.entry (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.adjustment ADD FOREIGN KEY (tenant_id, refund_allocation_id) REFERENCES finance.refund_allocation (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.adjustment ADD FOREIGN KEY (tenant_id, prepared_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.adjustment ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.payout_batch ADD FOREIGN KEY (tenant_id, prepared_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.payout_batch ADD FOREIGN KEY (tenant_id, approved_by) REFERENCES core.actor (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.payout_line ADD FOREIGN KEY (tenant_id, batch_id) REFERENCES commission.payout_batch (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE commission.payout_line ADD FOREIGN KEY (tenant_id, entry_id) REFERENCES commission.entry (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;

COMMIT;
