-- M07 資料庫整合提案：對 database/migrations/0001_domains.sql 既有 report.* schema 的補充。
-- 基準：既有 report.report / report.report_version / report.review_decision /
-- report.signature_request / report.signature / report.revocation 已由 baseline migration 建立；
-- 本檔只新增本模組必要而 baseline 尚未表達的欄位，不重建既有表。

BEGIN;

-- 1. report.report：允許 OpenAPI ReportCreate.report_type 的 'correction'，
--    並記錄 preceding_report_id（更正／接續同系列報告的穩定連結，不等同 supersede）。
-- baseline CHECK 未命名，由 Postgres 自動產生名稱；用 catalog 查詢找出實際名稱再換掉，
-- 不猜測固定字串（steward 實跑時仍應先 \d report.report 確認只有一條符合的 CHECK）。
DO $$
DECLARE existing_check text;
BEGIN
  SELECT con.conname INTO existing_check
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'report' AND rel.relname = 'report' AND con.contype = 'c'
    AND pg_get_constraintdef(con.oid) LIKE '%report_type%';
  IF existing_check IS NOT NULL THEN
    EXECUTE format('ALTER TABLE report.report DROP CONSTRAINT %I', existing_check);
  END IF;
END $$;
ALTER TABLE report.report ADD CONSTRAINT report_report_report_type_check
  CHECK (report_type IN ('initial','update','exit','supplement','correction'));

-- 與其餘跨 aggregate 參考（review_case_id、candidate_id 等）一致：僅記錄同 tenant
-- 的 uuid 參考並以註解說明，不另建跨 aggregate 的 FOREIGN KEY；referential
-- 正確性由應用層與既有 report.guard_* trigger 負責，PK 仍是 (tenant_id, id) 複合鍵。
ALTER TABLE report.report ADD COLUMN preceding_report_id uuid NULL;
COMMENT ON COLUMN report.report.preceding_report_id IS '指向 report.report，必須同一 tenant；更正／接續報告的前一份報告，不是 report_version 的 supersede 鏈。';
CREATE INDEX ix_report_report_preceding_report_id ON report.report (tenant_id, preceding_report_id);

-- 2. report.report_version：記錄哪個版本 supersede 哪個（REQ-M07-005）
--    與凍結時綁定的 required_review_ids（REQ-M07-001/002，凍結後不可變）。
ALTER TABLE report.report_version ADD COLUMN supersedes_version_id uuid NULL;
COMMENT ON COLUMN report.report_version.supersedes_version_id IS '指向 report.report_version，必須同一 tenant；本版簽後修訂取代的前一個已簽版本。';
CREATE INDEX ix_report_report_version_supersedes_version_id ON report.report_version (tenant_id, supersedes_version_id);

ALTER TABLE report.report_version ADD COLUMN required_review_ids uuid[] NULL;
COMMENT ON COLUMN report.report_version.required_review_ids IS '凍結時綁定的 report.review_decision id 清單；frozen_at 設定後與其他凍結欄位一樣不可變（見 report.guard_frozen_version）。';

-- 3. report.review_decision：補 reason_code（OpenAPI ReportReviewRequest 必填，
--    baseline 尚未儲存）；checklist_result_asset_id（即既有 evidence_asset_id）
--    在本模組的每個 review 都是必填證據，baseline 允許 NULL 過寬，分階段收斂。
ALTER TABLE report.review_decision ADD COLUMN reason_code text NULL;
COMMENT ON COLUMN report.review_decision.reason_code IS '覆核理由代碼；OpenAPI ReportReviewRequest.reason_code 的持久化欄位。';

ALTER TABLE report.review_decision ADD CONSTRAINT review_decision_evidence_required
  CHECK (evidence_asset_id IS NOT NULL) NOT VALID;
-- 待下方整合步驟完成既有列回填後，執行：
--   ALTER TABLE report.review_decision VALIDATE CONSTRAINT review_decision_evidence_required;
--   ALTER TABLE report.review_decision ALTER COLUMN evidence_asset_id SET NOT NULL;

-- 4. report.signature_request：補 redirect_url（provider session 導向位址）與
--    last_receipt_id（供 provider 重送 callback 時回同一 receipt，UAT-M07-008）。
ALTER TABLE report.signature_request ADD COLUMN redirect_url text NULL;
COMMENT ON COLUMN report.signature_request.redirect_url IS 'provider 簽署會話導向位址；只接受 https。';
ALTER TABLE report.signature_request ADD CONSTRAINT signature_request_redirect_url_https_check
  CHECK (redirect_url IS NULL OR redirect_url ~ '^https://');
ALTER TABLE report.signature_request ADD COLUMN last_receipt_id uuid NULL;
COMMENT ON COLUMN report.signature_request.last_receipt_id IS '最近一次已處理 callback 的 CallbackReceipt id；provider 重送同一 request 時回同一 receipt，不重複發布。';

-- 5. report.revocation：補 publication_withdrawal_event_id，追溯撤銷觸發的
--    下游撤回事件（report.signature_revoked），供 SignatureRevocation.publication_withdrawal_event_id 讀取。
ALTER TABLE report.revocation ADD COLUMN publication_withdrawal_event_id uuid NULL;
COMMENT ON COLUMN report.revocation.publication_withdrawal_event_id IS '指向 core.outbox.event_id（同 tenant，core.outbox 有 UNIQUE(tenant_id, event_id)）；本次撤銷同交易發出的 report.signature_revoked 事件。';

-- 6. report.guard_frozen_version()：baseline（database/migrations/0003_business_guards.sql）
--    的凍結後不可變欄位清單是在本提案新增 supersedes_version_id／required_review_ids
--    之前寫的，兩欄仍不在其內，凍結後可被悄悄改掉。steward 套用本提案與 baseline 後，
--    以下 CREATE OR REPLACE 用相同簽名取代該 function，只多檢查這兩個新欄位；
--    其餘判斷逐行與 0003_business_guards.sql 的原定義一致，不改變既有行為。
CREATE OR REPLACE FUNCTION report.guard_frozen_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.frozen_at IS NOT NULL AND (
    NEW.report_id IS DISTINCT FROM OLD.report_id OR NEW.report_version_no IS DISTINCT FROM OLD.report_version_no OR
    NEW.candidate_id IS DISTINCT FROM OLD.candidate_id OR NEW.revision_result_id IS DISTINCT FROM OLD.revision_result_id OR
    NEW.content_asset_id IS DISTINCT FROM OLD.content_asset_id OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
    NEW.bundle_sha256 IS DISTINCT FROM OLD.bundle_sha256 OR NEW.bundle_manifest_asset_id IS DISTINCT FROM OLD.bundle_manifest_asset_id OR NEW.bundle_manifest IS DISTINCT FROM OLD.bundle_manifest OR
    NEW.review_policy_version IS DISTINCT FROM OLD.review_policy_version OR NEW.requires_compliance_review IS DISTINCT FROM OLD.requires_compliance_review OR
    NEW.valid_from IS DISTINCT FROM OLD.valid_from OR NEW.valid_until IS DISTINCT FROM OLD.valid_until OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at OR
    NEW.supersedes_version_id IS DISTINCT FROM OLD.supersedes_version_id OR NEW.required_review_ids IS DISTINCT FROM OLD.required_review_ids
  ) THEN
    RAISE EXCEPTION 'frozen report content requires a new report version' USING ERRCODE='23514';
  END IF;
  IF OLD.status IN ('signed','superseded','withdrawn') AND NEW.status NOT IN ('signed','superseded','withdrawn') THEN
    RAISE EXCEPTION 'signed version cannot return to editable draft' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

COMMIT;
