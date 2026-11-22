-- Critical cross-domain integrity checks. Application policy checks are still required.
-- All functions use invoker rights; there is no generic SECURITY DEFINER bypass.
BEGIN;

CREATE FUNCTION core.require_enabled_human(p_tenant uuid, p_actor uuid) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM core.actor WHERE tenant_id=p_tenant AND id=p_actor
                 AND actor_type='human' AND enabled AND departed_at IS NULL) THEN
    RAISE EXCEPTION 'enabled human actor required' USING ERRCODE='23514';
  END IF;
END $$;

CREATE FUNCTION core.guard_fixed_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mutable_columns text[];
BEGIN
  mutable_columns:=TG_ARGV[0]::text[] || ARRAY['version','updated_at'];
  IF (to_jsonb(NEW)-mutable_columns) IS DISTINCT FROM (to_jsonb(OLD)-mutable_columns) THEN
    RAISE EXCEPTION 'immutable payload or calculation identity: %',TG_TABLE_NAME USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER immutable_envelope BEFORE UPDATE ON core.outbox FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{status,available_at,lease_until,lease_owner,attempt_count,last_error_code}');
CREATE TRIGGER immutable_envelope BEFORE UPDATE ON core.inbox FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{status,lease_until,applied_at,result_code}');
CREATE TRIGGER immutable_publication BEFORE UPDATE ON publication.publication FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{status,published_at,withdrawal_reason_code}');
CREATE TRIGGER immutable_message BEFORE UPDATE ON delivery.message FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{status,last_authorized_at,checked_entitlement_version,read_at,suppression_reason}');
CREATE TRIGGER immutable_calculation BEFORE UPDATE ON commission.calculation_run FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{status,completed_at,approved_by,approved_at}');
CREATE TRIGGER immutable_package_terms BEFORE UPDATE ON contract.package_version FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{approval_status,approved_by,approved_at}');
CREATE TRIGGER immutable_commission_terms BEFORE UPDATE ON commission.policy_version FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{approval_status,approved_by,approved_at}');
CREATE TRIGGER immutable_pool_terms BEFORE UPDATE ON ownership.policy_version FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{approval_status,approved_by,approved_at}');
CREATE TRIGGER immutable_response BEFORE UPDATE ON support.response_dispatch FOR EACH ROW
EXECUTE FUNCTION core.guard_fixed_record('{status,provider_message_ref,accepted_at,sent_at,delivered_at,read_at,outbound_interaction_id}');

CREATE FUNCTION support.guard_completed_draft() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.content_sha256 IS NOT NULL AND
    (to_jsonb(NEW)-ARRAY['status','updated_at','version']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','updated_at','version']) THEN
    RAISE EXCEPTION 'completed response draft requires a new version' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER completed_draft_guard BEFORE UPDATE ON support.response_draft FOR EACH ROW EXECUTE FUNCTION support.guard_completed_draft();

CREATE FUNCTION support.guard_response_approval() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d support.response_draft%ROWTYPE;
BEGIN
  SELECT * INTO STRICT d FROM support.response_draft WHERE tenant_id=NEW.tenant_id AND id=NEW.draft_id;
  IF NEW.draft_content_sha256<>d.content_sha256 OR d.content_sha256 IS NULL THEN
    RAISE EXCEPTION 'response approval must bind exact draft hash' USING ERRCODE='23514';
  END IF;
  IF NEW.approval_mode='human_reviewed' THEN PERFORM core.require_enabled_human(NEW.tenant_id,NEW.reviewer_id); END IF;
  IF NEW.decision='approve' AND (d.status NOT IN ('drafted','requires_human') OR d.contains_new_investment_judgment) THEN
    RAISE EXCEPTION 'response cannot introduce unapproved investment judgment' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER response_approval_gate BEFORE INSERT ON support.response_approval FOR EACH ROW EXECUTE FUNCTION support.guard_response_approval();

CREATE FUNCTION support.guard_response_dispatch() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d support.response_draft%ROWTYPE; a support.response_approval%ROWTYPE;
        b delivery.channel_binding%ROWTYPE; c support.case_file%ROWTYPE;
BEGIN
  SELECT * INTO STRICT d FROM support.response_draft WHERE tenant_id=NEW.tenant_id AND id=NEW.draft_id;
  SELECT * INTO STRICT a FROM support.response_approval WHERE tenant_id=NEW.tenant_id AND id=NEW.approval_id;
  SELECT * INTO STRICT b FROM delivery.channel_binding WHERE tenant_id=NEW.tenant_id AND id=NEW.channel_binding_id;
  SELECT * INTO STRICT c FROM support.case_file WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id;
  IF d.case_id<>c.id OR c.customer_id IS NULL OR c.customer_id<>NEW.customer_id OR
     d.content_sha256<>NEW.content_sha256 OR a.draft_id<>d.id OR a.draft_content_sha256<>d.content_sha256 OR
     a.decision<>'approve' OR a.approval_mode<>NEW.approval_mode OR d.contains_new_investment_judgment OR
     b.customer_id<>NEW.customer_id OR b.channel<>NEW.channel OR b.status<>'verified' OR NOT b.service_delivery_enabled OR
     NEW.valid_until>a.valid_until OR NEW.valid_until<=a.approved_at THEN
    RAISE EXCEPTION 'response requires approved exact draft and verified customer channel' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER response_dispatch_gate BEFORE INSERT ON support.response_dispatch FOR EACH ROW EXECUTE FUNCTION support.guard_response_dispatch();
CREATE FUNCTION core.guard_policy_approval() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.approval_status IN ('approved','retired') AND
    (NEW.approval_status NOT IN ('approved','retired') OR NEW.approved_by IS DISTINCT FROM OLD.approved_by OR NEW.approved_at IS DISTINCT FROM OLD.approved_at) THEN
    RAISE EXCEPTION 'ratified policy approval evidence immutable' USING ERRCODE='23514';
  END IF;
  IF OLD.approval_status='retired' AND NEW.approval_status<>'retired' THEN
    RAISE EXCEPTION 'retired policy requires new version' USING ERRCODE='23514';
  END IF;
  IF NEW.approval_status='approved' THEN
    PERFORM core.require_enabled_human(NEW.tenant_id,NEW.approved_by);
    IF NEW.approved_by=NEW.proposed_by THEN
      RAISE EXCEPTION 'policy maker cannot approve own policy' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER policy_approval BEFORE UPDATE ON contract.package_version FOR EACH ROW EXECUTE FUNCTION core.guard_policy_approval();
CREATE TRIGGER policy_approval BEFORE UPDATE ON commission.policy_version FOR EACH ROW EXECUTE FUNCTION core.guard_policy_approval();
CREATE TRIGGER policy_approval BEFORE UPDATE ON ownership.policy_version FOR EACH ROW EXECUTE FUNCTION core.guard_policy_approval();

CREATE FUNCTION contract.guard_company_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a customer.application%ROWTYPE;
        k kyc.submission%ROWTYPE;
        s kyc.suitability_assessment%ROWTYPE;
        b aml.business_clearance%ROWTYPE;
        sig contract.customer_signature%ROWTYPE;
        d contract.acceptance_decision%ROWTYPE;
BEGIN
  SELECT * INTO STRICT a FROM customer.application WHERE tenant_id=NEW.tenant_id AND id=NEW.application_id;
  SELECT * INTO STRICT sig FROM contract.customer_signature WHERE tenant_id=NEW.tenant_id AND id=NEW.customer_signature_id;
  SELECT * INTO STRICT k FROM kyc.submission WHERE tenant_id=NEW.tenant_id AND id=NEW.kyc_submission_id;
  SELECT * INTO STRICT s FROM kyc.suitability_assessment WHERE tenant_id=NEW.tenant_id AND id=NEW.suitability_assessment_id;
  SELECT * INTO STRICT b FROM aml.business_clearance WHERE tenant_id=NEW.tenant_id AND id=NEW.aml_clearance_id;
  SELECT * INTO STRICT d FROM contract.acceptance_decision WHERE tenant_id=NEW.tenant_id AND id=NEW.decision_id;
  PERFORM core.require_enabled_human(NEW.tenant_id, NEW.accepted_by);
  IF sig.application_id<>a.id OR sig.customer_id<>a.customer_id OR
     k.application_id<>a.id OR k.customer_id<>a.customer_id OR
     s.submission_id<>k.id OR s.customer_id<>a.customer_id OR
     b.application_id<>a.id OR b.customer_id<>a.customer_id OR sig.package_version_id<>a.package_version_id OR
     d.application_id<>a.id OR d.customer_signature_id<>sig.id OR d.decision<>'accept' OR d.decided_by<>NEW.accepted_by THEN
    RAISE EXCEPTION 'acceptance evidence belongs to another application/customer' USING ERRCODE='23514';
  END IF;
  IF k.status<>'approved' OR s.status NOT IN ('suitable','conditional') OR b.decision<>'approved'
     OR (k.valid_until IS NOT NULL AND k.valid_until<=NEW.accepted_at)
     OR (s.valid_until IS NOT NULL AND s.valid_until<=NEW.accepted_at)
     OR b.valid_until<=NEW.accepted_at THEN
    RAISE EXCEPTION 'company acceptance gates not satisfied' USING ERRCODE='23514';
  END IF;
  IF NEW.accepted_at<sig.customer_signed_at OR NEW.accepted_at<k.reviewed_at
     OR NEW.accepted_at<s.assessed_at OR NEW.accepted_at<b.decided_at OR NEW.accepted_at<d.decided_at THEN
    RAISE EXCEPTION 'acceptance cannot precede its evidence' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER company_acceptance_gate BEFORE INSERT ON contract.company_acceptance
FOR EACH ROW EXECUTE FUNCTION contract.guard_company_acceptance();

CREATE FUNCTION contract.guard_contract_effective() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a contract.company_acceptance%ROWTYPE;
BEGIN
  IF NEW.status IN ('accepted','effective') THEN
    SELECT * INTO STRICT a FROM contract.company_acceptance WHERE tenant_id=NEW.tenant_id AND id=NEW.company_acceptance_id;
    IF a.application_id<>NEW.application_id OR a.customer_signature_id<>NEW.customer_signature_id THEN
      RAISE EXCEPTION 'contract acceptance mismatch' USING ERRCODE='23514';
    END IF;
    IF NEW.status='effective' AND (NEW.effective_at IS NULL OR NEW.effective_at<a.accepted_at) THEN
      RAISE EXCEPTION 'effective contract requires actual prior company acceptance' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER effective_contract_gate BEFORE INSERT OR UPDATE ON contract.contract
FOR EACH ROW EXECUTE FUNCTION contract.guard_contract_effective();

CREATE FUNCTION contract.guard_active_entitlement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s contract.contract_service%ROWTYPE; c contract.contract%ROWTYPE;
BEGIN
  IF NEW.status='active' THEN
    SELECT * INTO STRICT s FROM contract.contract_service WHERE tenant_id=NEW.tenant_id AND id=NEW.contract_service_id;
    SELECT * INTO STRICT c FROM contract.contract WHERE tenant_id=NEW.tenant_id AND id=s.contract_id;
    IF c.customer_id<>NEW.customer_id OR c.status<>'effective' OR c.company_acceptance_id IS NULL
       OR NEW.valid_from<s.starts_at OR NEW.valid_until>s.ends_at THEN
      RAISE EXCEPTION 'active entitlement requires effective accepted contract and service interval' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER active_entitlement_gate BEFORE INSERT OR UPDATE ON contract.entitlement
FOR EACH ROW EXECUTE FUNCTION contract.guard_active_entitlement();

CREATE FUNCTION report.guard_frozen_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.frozen_at IS NOT NULL AND (
    NEW.report_id IS DISTINCT FROM OLD.report_id OR NEW.report_version_no IS DISTINCT FROM OLD.report_version_no OR
    NEW.candidate_id IS DISTINCT FROM OLD.candidate_id OR NEW.revision_result_id IS DISTINCT FROM OLD.revision_result_id OR
    NEW.content_asset_id IS DISTINCT FROM OLD.content_asset_id OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
    NEW.bundle_sha256 IS DISTINCT FROM OLD.bundle_sha256 OR NEW.bundle_manifest_asset_id IS DISTINCT FROM OLD.bundle_manifest_asset_id OR NEW.bundle_manifest IS DISTINCT FROM OLD.bundle_manifest OR
    NEW.review_policy_version IS DISTINCT FROM OLD.review_policy_version OR NEW.requires_compliance_review IS DISTINCT FROM OLD.requires_compliance_review OR
    NEW.valid_from IS DISTINCT FROM OLD.valid_from OR NEW.valid_until IS DISTINCT FROM OLD.valid_until OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at
  ) THEN
    RAISE EXCEPTION 'frozen report content requires a new report version' USING ERRCODE='23514';
  END IF;
  IF OLD.status IN ('signed','superseded','withdrawn') AND NEW.status NOT IN ('signed','superseded','withdrawn') THEN
    RAISE EXCEPTION 'signed version cannot return to editable draft' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER frozen_report_guard BEFORE UPDATE ON report.report_version
FOR EACH ROW EXECUTE FUNCTION report.guard_frozen_version();

CREATE FUNCTION report.guard_signature() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r report.report_version%ROWTYPE; req report.signature_request%ROWTYPE;
        cred core.staff_credential%ROWTYPE; n integer;
BEGIN
  SELECT * INTO STRICT r FROM report.report_version WHERE tenant_id=NEW.tenant_id AND id=NEW.report_version_id;
  SELECT * INTO STRICT req FROM report.signature_request WHERE tenant_id=NEW.tenant_id AND id=NEW.request_id;
  SELECT * INTO STRICT cred FROM core.staff_credential WHERE tenant_id=NEW.tenant_id AND id=NEW.credential_id;
  PERFORM core.require_enabled_human(NEW.tenant_id,NEW.signer_id);
  IF r.frozen_at IS NULL OR r.status NOT IN ('ready_to_sign','signature_pending') OR
     r.content_sha256<>NEW.content_sha256 OR r.bundle_sha256<>NEW.bundle_sha256 OR
     req.report_version_id<>r.id OR req.signer_id<>NEW.signer_id OR req.credential_id<>cred.id OR req.bundle_sha256<>r.bundle_sha256 OR
     cred.actor_id<>NEW.signer_id OR cred.credential_type<>'analyst' OR
     cred.valid_from>NEW.signed_at OR (cred.valid_until IS NOT NULL AND cred.valid_until<=NEW.signed_at) OR
     (cred.revoked_at IS NOT NULL AND cred.revoked_at<=NEW.signed_at) OR
     EXISTS (SELECT 1 FROM core.credential_revocation cr WHERE cr.tenant_id=NEW.tenant_id AND cr.credential_id=cred.id AND cr.revoked_at<=NEW.signed_at) OR
     NEW.signed_at<r.frozen_at OR NEW.signed_at>=req.expires_at OR NEW.signed_at>=r.valid_until THEN
    RAISE EXCEPTION 'signature identity/version/time/credential gate failed' USING ERRCODE='23514';
  END IF;
  SELECT count(DISTINCT review_type) INTO n FROM report.review_decision
    WHERE tenant_id=NEW.tenant_id AND report_version_id=r.id AND decision='approved'
      AND content_sha256=r.content_sha256 AND bundle_sha256=r.bundle_sha256
      AND reviewed_at<=NEW.signed_at AND (review_type='professional' OR r.requires_compliance_review);
  IF n < (CASE WHEN r.requires_compliance_review THEN 2 ELSE 1 END) THEN
    RAISE EXCEPTION 'required reviews incomplete for exact signed bundle' USING ERRCODE='23514';
  END IF;
  IF EXISTS (SELECT 1 FROM report.review_decision d WHERE d.tenant_id=NEW.tenant_id AND d.report_version_id=r.id
    AND d.decision IN ('changes_requested','rejected') AND d.reviewed_at<=NEW.signed_at AND
    NOT EXISTS (SELECT 1 FROM report.review_decision later WHERE later.tenant_id=d.tenant_id AND later.report_version_id=d.report_version_id
      AND later.review_type=d.review_type AND later.decision='approved' AND later.content_sha256=r.content_sha256
      AND later.bundle_sha256=r.bundle_sha256 AND later.reviewed_at>d.reviewed_at AND later.reviewed_at<=NEW.signed_at)) THEN
    RAISE EXCEPTION 'unresolved review rejection' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER signature_gate BEFORE INSERT ON report.signature FOR EACH ROW EXECUTE FUNCTION report.guard_signature();

CREATE FUNCTION report.require_signature_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM core.outbox WHERE tenant_id=NEW.tenant_id AND aggregate_type='report_version'
       AND aggregate_id=NEW.report_version_id AND event_type='report.signature_verified' AND schema_version='1.0.0'
       AND producer='M07' AND classification='research_confidential'
       AND occurred_at=NEW.verified_at AND emitted_at>=NEW.verified_at
       AND aggregate_version=(SELECT version FROM report.report_version WHERE tenant_id=NEW.tenant_id AND id=NEW.report_version_id)
       AND payload->>'signature_id'=NEW.id::text
       AND payload->>'report_version_id'=NEW.report_version_id::text
       AND payload->>'report_content_sha256'=trim(NEW.content_sha256)
       AND payload->>'bundle_manifest_sha256'=trim(NEW.bundle_sha256)
       AND payload->>'signed_file_sha256'=trim(NEW.signed_file_sha256)
       AND (payload->>'verified_at')::timestamptz=NEW.verified_at
       AND (payload->>'valid_until')::timestamptz=(SELECT valid_until FROM report.report_version WHERE tenant_id=NEW.tenant_id AND id=NEW.report_version_id)) THEN
    RAISE EXCEPTION 'verified signature and automatic publication outbox must commit atomically' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER signature_requires_outbox AFTER INSERT ON report.signature
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION report.require_signature_outbox();

CREATE FUNCTION publication.guard_publication() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sig report.signature%ROWTYPE; r report.report_version%ROWTYPE;
        advice publication.recommendation_version%ROWTYPE;
BEGIN
  SELECT * INTO STRICT sig FROM report.signature WHERE tenant_id=NEW.tenant_id AND id=NEW.signature_id;
  SELECT * INTO STRICT advice FROM publication.recommendation_version WHERE tenant_id=NEW.tenant_id AND id=NEW.recommendation_version_id;
  SELECT * INTO STRICT r FROM report.report_version WHERE tenant_id=NEW.tenant_id AND id=sig.report_version_id;
  IF advice.report_version_id<>r.id OR sig.bundle_sha256<>NEW.bundle_sha256 OR
     NEW.queued_at<sig.verified_at OR NEW.expires_at>r.valid_until OR NEW.expires_at>advice.valid_until OR
     NEW.expires_at<=NEW.queued_at OR r.status NOT IN ('signed','superseded') OR
     EXISTS (SELECT 1 FROM report.revocation WHERE tenant_id=NEW.tenant_id AND signature_id=sig.id) THEN
    RAISE EXCEPTION 'publication requires valid signed matching report and advice' USING ERRCODE='23514';
  END IF;
  -- The manifest is frozen before signing. Every published advice id and hash is in it.
  IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.bundle_manifest->'recommendations') v
      WHERE v->>'id'=advice.id::text AND v->>'content_sha256'=trim(advice.content_sha256)) THEN
    RAISE EXCEPTION 'recommendation is not covered by signed bundle manifest' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER publication_gate BEFORE INSERT ON publication.publication
FOR EACH ROW EXECUTE FUNCTION publication.guard_publication();

CREATE UNIQUE INDEX one_current_assignment ON ownership.assignment (tenant_id, customer_id) WHERE valid_until IS NULL;
CREATE INDEX assignment_history ON ownership.assignment (tenant_id,customer_id,valid_from,valid_until);
CREATE INDEX active_service_interval ON contract.contract_service (tenant_id, starts_at, ends_at);
CREATE INDEX outbox_dispatch ON core.outbox (status,available_at,lease_until) WHERE status IN ('pending','retry','leased');
CREATE INDEX delivery_retry ON delivery.message (tenant_id,status,expires_at) WHERE status IN ('queued','failed','unknown');
CREATE INDEX crm_due_work ON crm.work_item (tenant_id,owner_actor_id,due_at) WHERE status IN ('open','assigned','in_progress');
CREATE INDEX support_sla_work ON support.case_file (tenant_id,status,first_response_due_at,resolution_due_at);

CREATE FUNCTION ownership.guard_assignment_interval() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Serializes concurrent assignment creation and service-lapse calculation for one customer.
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || ':' || NEW.customer_id::text, 0));
  IF EXISTS (SELECT 1 FROM ownership.assignment x WHERE x.tenant_id=NEW.tenant_id AND x.customer_id=NEW.customer_id AND x.id<>NEW.id
    AND tstzrange(x.valid_from,x.valid_until,'[)') && tstzrange(NEW.valid_from,NEW.valid_until,'[)')) THEN
    RAISE EXCEPTION 'overlapping ownership intervals' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ownership_interval_guard BEFORE INSERT OR UPDATE ON ownership.assignment
FOR EACH ROW EXECUTE FUNCTION ownership.guard_assignment_interval();

CREATE FUNCTION ownership.guard_pool_entry() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a ownership.assignment%ROWTYPE; p ownership.policy_version%ROWTYPE;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || ':' || NEW.customer_id::text, 0));
  SELECT * INTO STRICT a FROM ownership.assignment WHERE tenant_id=NEW.tenant_id AND id=NEW.assignment_id;
  SELECT * INTO STRICT p FROM ownership.policy_version WHERE tenant_id=NEW.tenant_id AND id=a.policy_version_id;
  IF a.customer_id<>NEW.customer_id OR a.owner_type<>'company_pool' THEN
    RAISE EXCEPTION 'pool entry must reference company assignment' USING ERRCODE='23514';
  END IF;
  IF NEW.reason_code='all_paid_services_lapsed' THEN
    IF p.approval_status<>'approved' OR NEW.entered_at<NEW.all_paid_services_ended_at + make_interval(days=>p.lapse_days) THEN
      RAISE EXCEPTION 'pool lapse requires approved policy and elapsed complete interval' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM customer.customer WHERE tenant_id=NEW.tenant_id AND id=NEW.customer_id AND status='restricted') OR
       EXISTS (SELECT 1 FROM contract.contract WHERE tenant_id=NEW.tenant_id AND customer_id=NEW.customer_id AND status='suspended') THEN
      RAISE EXCEPTION 'restricted or suspended relationship is not ordinary sales lapse' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM contract.contract_service s JOIN contract.contract c ON (c.tenant_id,c.id)=(s.tenant_id,s.contract_id)
      WHERE c.tenant_id=NEW.tenant_id AND c.customer_id=NEW.customer_id AND c.status='effective'
        AND tstzrange(s.starts_at,s.ends_at,'[)') && tstzrange(NEW.all_paid_services_ended_at,NEW.entered_at,'[]')) THEN
      RAISE EXCEPTION 'any paid service during lapse prevents whole-customer Pool release' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER pool_entry_gate BEFORE INSERT ON ownership.pool_entry FOR EACH ROW EXECUTE FUNCTION ownership.guard_pool_entry();

CREATE FUNCTION commission.guard_live_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p commission.policy_version%ROWTYPE;
BEGIN
  SELECT * INTO STRICT p FROM commission.policy_version WHERE tenant_id=NEW.tenant_id AND id=NEW.policy_version_id;
  IF NEW.mode='live' AND (p.approval_status<>'approved' OR p.base_pool_rate IS NULL OR
      p.decay_interaction='unresolved' OR p.tail_lineage_rule='unresolved') THEN
    RAISE EXCEPTION 'live commission requires ratified complete policy' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER live_commission_gate BEFORE INSERT OR UPDATE ON commission.calculation_run
FOR EACH ROW EXECUTE FUNCTION commission.guard_live_run();

CREATE FUNCTION commission.guard_payout_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e commission.entry%ROWTYPE; r commission.calculation_run%ROWTYPE;
BEGIN
  SELECT * INTO STRICT e FROM commission.entry WHERE tenant_id=NEW.tenant_id AND id=NEW.entry_id;
  SELECT * INTO STRICT r FROM commission.calculation_run WHERE tenant_id=NEW.tenant_id AND id=e.run_id;
  IF r.mode<>'live' OR r.status NOT IN ('approved','exported') OR e.beneficiary_type='company' THEN
    RAISE EXCEPTION 'only approved live marketing/sales entries can be paid' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER payout_line_gate BEFORE INSERT OR UPDATE ON commission.payout_line
FOR EACH ROW EXECUTE FUNCTION commission.guard_payout_line();

CREATE FUNCTION finance.guard_recognition_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r finance.revenue_run%ROWTYPE;
BEGIN
  SELECT * INTO STRICT r FROM finance.revenue_run WHERE tenant_id=NEW.tenant_id AND id=NEW.run_id;
  IF r.dry_run OR r.status NOT IN ('approved','posted') OR r.currency<>NEW.currency THEN
    RAISE EXCEPTION 'recognized income requires approved non-dry-run batch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER recognition_run_gate BEFORE INSERT ON finance.revenue_recognition FOR EACH ROW EXECUTE FUNCTION finance.guard_recognition_run();

CREATE FUNCTION finance.guard_revenue_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('approved','posted') AND
    (to_jsonb(NEW)-ARRAY['status','posted_at','version','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','posted_at','version','updated_at']) THEN
    RAISE EXCEPTION 'approved revenue calculation immutable' USING ERRCODE='23514';
  END IF;
  IF OLD.status='posted' AND NEW.status<>'posted' THEN
    RAISE EXCEPTION 'posted revenue run requires corrective batch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER revenue_run_guard BEFORE UPDATE ON finance.revenue_run FOR EACH ROW EXECUTE FUNCTION finance.guard_revenue_run();

CREATE FUNCTION finance.assert_journal_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t uuid; j uuid; s text; dr numeric; cr numeric; n bigint;
BEGIN
  t:=COALESCE(NEW.tenant_id,OLD.tenant_id);
  IF TG_TABLE_NAME='journal' THEN j:=COALESCE(NEW.id,OLD.id); ELSE j:=COALESCE(NEW.journal_id,OLD.journal_id); END IF;
  SELECT status INTO s FROM finance.journal WHERE tenant_id=t AND id=j;
  IF s IN ('approved','posted') THEN
    SELECT coalesce(sum(debit),0),coalesce(sum(credit),0),count(*) INTO dr,cr,n FROM finance.journal_line WHERE tenant_id=t AND journal_id=j;
    IF n<2 OR dr<>cr OR dr=0 THEN
      RAISE EXCEPTION 'approved/posted journal must have nonzero balanced lines' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER journal_balanced AFTER INSERT OR UPDATE ON finance.journal
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_journal_balance();
CREATE CONSTRAINT TRIGGER journal_lines_balanced AFTER INSERT OR UPDATE OR DELETE ON finance.journal_line
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_journal_balance();

CREATE FUNCTION finance.guard_posted_journal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s text;
BEGIN
  IF TG_TABLE_NAME='journal' THEN
    IF OLD.status IN ('posted','reversed') THEN
      RAISE EXCEPTION 'posted journal immutable; append reversal journal' USING ERRCODE='23514';
    END IF;
  ELSE
    SELECT status INTO s FROM finance.journal WHERE tenant_id=COALESCE(OLD.tenant_id,NEW.tenant_id) AND id=COALESCE(OLD.journal_id,NEW.journal_id);
    IF s IN ('posted','reversed') THEN
      RAISE EXCEPTION 'posted journal lines immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN COALESCE(NEW,OLD);
END $$;
CREATE TRIGGER posted_journal_guard BEFORE UPDATE OR DELETE ON finance.journal
FOR EACH ROW EXECUTE FUNCTION finance.guard_posted_journal();
CREATE TRIGGER posted_line_guard BEFORE INSERT OR UPDATE OR DELETE ON finance.journal_line
FOR EACH ROW EXECUTE FUNCTION finance.guard_posted_journal();

REVOKE ALL ON ALL TABLES IN SCHEMA aml FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core,contract,report,publication,ownership,commission,finance,support FROM PUBLIC;
COMMIT;
