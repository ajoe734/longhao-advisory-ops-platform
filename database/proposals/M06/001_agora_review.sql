-- M06 proposal only. The data steward assigns the shared migration number.
-- Baseline: dev@48ae302308de64159ee25ed506fb6f2e548cf886 (PostgreSQL 16).
SET TIME ZONE 'UTC';

-- OpenAPI ReviewCase already declares professional_reviewer_id and
-- compliance_review_required; no existing M06 command sets either field yet
-- (assign_review_case only carries assignee_id), so this starts nullable/false
-- and fails closed until a steward-approved policy adds the command/event.
ALTER TABLE review.case_file
  ADD COLUMN IF NOT EXISTS professional_reviewer_id uuid,
  ADD COLUMN IF NOT EXISTS compliance_review_required boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT m06_case_file_reviewer_fk
    FOREIGN KEY (tenant_id, professional_reviewer_id) REFERENCES core.actor (tenant_id, id) NOT VALID;

-- Conversation.audit_transcript_asset_id and scoped_artifact_ref are part of
-- the OpenAPI Conversation response; the existing scoped_context_ref text
-- column cannot hold a structured {system, external_id, kind} reference.
ALTER TABLE review.conversation
  ADD COLUMN IF NOT EXISTS audit_transcript_asset_id uuid,
  ADD COLUMN IF NOT EXISTS scoped_artifact_ref jsonb,
  ADD CONSTRAINT m06_conversation_transcript_fk
    FOREIGN KEY (tenant_id, audit_transcript_asset_id) REFERENCES core.object_asset (tenant_id, id) NOT VALID,
  ADD CONSTRAINT m06_conversation_scoped_ref_ck
    CHECK (
      scoped_artifact_ref IS NULL
      OR (scoped_artifact_ref ? 'system' AND scoped_artifact_ref ? 'external_id' AND scoped_artifact_ref ? 'kind')
    ) NOT VALID;
UPDATE review.conversation SET scoped_artifact_ref = jsonb_build_object('system', provider, 'external_id', scoped_context_ref, 'kind', 'legacy_scope')
  WHERE scoped_artifact_ref IS NULL AND scoped_context_ref IS NOT NULL;

-- RevisionRequest requires an exact candidate version binding
-- (base_artifact_ref + base_content_sha256), the caller's instructions scope
-- (required_evidence_refs) and requested_output; none of these exist yet on
-- review.revision_request. instruction_asset_id continues to hold the full
-- instructions text as an immutable object.
ALTER TABLE review.revision_request
  ADD COLUMN IF NOT EXISTS base_artifact_ref jsonb,
  ADD COLUMN IF NOT EXISTS base_content_sha256 char(64),
  ADD COLUMN IF NOT EXISTS required_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS requested_output text,
  ADD COLUMN IF NOT EXISTS retry_of_revision_id uuid,
  ADD CONSTRAINT m06_revision_request_base_sha_ck
    CHECK (base_content_sha256 IS NULL OR base_content_sha256 ~ '^[a-f0-9]{64}$') NOT VALID,
  ADD CONSTRAINT m06_revision_request_output_ck
    CHECK (requested_output IS NULL OR requested_output IN ('revised_report', 'additional_evidence', 'counter_analysis', 'message_draft')) NOT VALID,
  ADD CONSTRAINT m06_revision_request_retry_fk
    FOREIGN KEY (tenant_id, retry_of_revision_id) REFERENCES review.revision_request (tenant_id, id) NOT VALID;

-- UAT-M06-011 requires a visible, pending diff for significant changes. The
-- comparison itself runs through an ArtifactDiffPort today (no raw candidate
-- content is duplicated into this table); these two columns only persist the
-- resulting flag/summary so the version-compare screen survives a restart.
ALTER TABLE review.revision_result
  ADD COLUMN IF NOT EXISTS significant_diff boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS diff_summary jsonb NOT NULL DEFAULT '[]'::jsonb;
