-- M09 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@48ae302308de64159ee25ed506fb6f2e548cf886 (PostgreSQL 16).
SET TIME ZONE 'UTC';

-- OpenAPI `set_communication_preference` separates marketing opt-in from
-- required service notices per channel; no baseline table carries this yet.
CREATE TABLE IF NOT EXISTS delivery.communication_preference (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  customer_id uuid NOT NULL,
  channel text NOT NULL,
  purpose text NOT NULL,
  enabled boolean NOT NULL,
  consent_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customer.customer (tenant_id, id),
  FOREIGN KEY (tenant_id, consent_id) REFERENCES customer.consent (tenant_id, id),
  CHECK (version > 0),
  CHECK (channel IN ('legendflow', 'line', 'sms', 'email', 'web')),
  CHECK (purpose IN ('service', 'marketing')),
  UNIQUE (tenant_id, customer_id, channel, purpose)
);
COMMENT ON TABLE delivery.communication_preference IS 'M09 per-channel service/marketing opt-in; marketing suppression never silences a contractual service notice.';
COMMENT ON COLUMN delivery.communication_preference.channel IS 'legendflow/line/sms/email/web';
COMMENT ON COLUMN delivery.communication_preference.purpose IS 'service (required notice) / marketing (opt-in campaign)';
COMMENT ON COLUMN delivery.communication_preference.enabled IS 'true permits sends of this purpose over this channel';
COMMENT ON COLUMN delivery.communication_preference.consent_id IS '指向 customer.consent，必須同一 tenant；marketing enabled=true 要求 granted=true 的同意。';

-- `redrive_delivery_event` (`/dead-letters/{dead_letter_id}/redrive`) CAS's a
-- distinct resource from `delivery.message`: the worker's lease/retry queue
-- for one message's outbound attempts, mirroring the shared `core.job_lease`
-- pattern but scoped to this module so dead-letter redrive has its own
-- auditable identity and version.
CREATE TABLE IF NOT EXISTS delivery.dispatch_job (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  message_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempt_count integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 5,
  lease_owner text,
  lease_until timestamptz,
  next_attempt_at timestamptz,
  last_error_code text,
  redrive_reason text,
  redriven_by uuid,
  dead_letter_event_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, message_id) REFERENCES delivery.message (tenant_id, id),
  FOREIGN KEY (tenant_id, redriven_by) REFERENCES core.actor (tenant_id, id),
  CHECK (version > 0),
  CHECK (status IN ('pending', 'leased', 'sent', 'retry', 'dead_letter', 'cancelled')),
  CHECK (attempt_count >= 0 AND max_attempts > 0),
  CHECK (status <> 'dead_letter' OR dead_letter_event_id IS NOT NULL),
  UNIQUE (tenant_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_m09_dispatch_job_due
  ON delivery.dispatch_job (tenant_id, status, next_attempt_at)
  WHERE status IN ('pending', 'retry');
COMMENT ON TABLE delivery.dispatch_job IS 'M09 per-message outbound lease/retry/dead-letter queue; redrive_delivery_event CAS''s this row, not delivery.message.';
COMMENT ON COLUMN delivery.dispatch_job.status IS 'pending/leased/sent/retry/dead_letter/cancelled';
COMMENT ON COLUMN delivery.dispatch_job.dead_letter_event_id IS '進入 dead_letter 時產生的 expected_event_id，防止過期 redrive 請求誤配。';
COMMENT ON COLUMN delivery.dispatch_job.redrive_reason IS 'delivery_supervisor 核准重驅時填寫的非空原因。';

-- Correction messages must point at the original without rewriting it.
ALTER TABLE delivery.message
  ADD COLUMN IF NOT EXISTS replaces_delivery_id uuid,
  ADD CONSTRAINT m09_message_replaces_self_ck CHECK (replaces_delivery_id IS NULL OR replaces_delivery_id <> id) NOT VALID;
ALTER TABLE delivery.message
  ADD CONSTRAINT m09_message_replaces_fk FOREIGN KEY (tenant_id, replaces_delivery_id)
    REFERENCES delivery.message (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE NOT VALID;
COMMENT ON COLUMN delivery.message.replaces_delivery_id IS 'publication.corrected 的替代訊息指回原 delivery.message；原文 body/hash/attempt 不被覆寫。';

DO $m09_rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['communication_preference', 'dispatch_job']
  LOOP
    EXECUTE format('ALTER TABLE delivery.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE delivery.%I FORCE ROW LEVEL SECURITY', table_name);
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies WHERE schemaname = 'delivery' AND tablename = table_name AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON delivery.%I USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id())',
        table_name
      );
    END IF;
  END LOOP;
END
$m09_rls$;

DROP TRIGGER IF EXISTS guard_row ON delivery.communication_preference;
CREATE TRIGGER guard_row BEFORE UPDATE ON delivery.communication_preference
FOR EACH ROW EXECUTE FUNCTION core.touch_version();
DROP TRIGGER IF EXISTS guard_row ON delivery.dispatch_job;
CREATE TRIGGER guard_row BEFORE UPDATE ON delivery.dispatch_job
FOR EACH ROW EXECUTE FUNCTION core.touch_version();
