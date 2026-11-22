-- Run with a controlled migration principal that has CREATEROLE.
-- These are NOLOGIN capability roles. Deployment binds separate login principals
-- and connection pools; no password, token or production principal is in this repo.
BEGIN;
DO $$
DECLARE role_name text; i integer;
BEGIN
  FOREACH role_name IN ARRAY ARRAY['longhao_tenant_access','longhao_audit','longhao_aml_audit'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS',role_name);
    END IF;
  END LOOP;
  FOR i IN 1..14 LOOP
    role_name := 'longhao_m'||lpad(i::text,2,'0');
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS INHERIT',role_name);
    END IF;
    EXECUTE format('GRANT longhao_tenant_access TO %I',role_name);
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA core TO longhao_tenant_access,longhao_audit,longhao_aml_audit;
GRANT EXECUTE ON FUNCTION core.current_tenant_id(),core.require_enabled_human(uuid,uuid) TO longhao_tenant_access,longhao_audit,longhao_aml_audit;
GRANT SELECT ON core.tenant,core.actor,core.staff_credential,core.credential_revocation,core.retention_policy,core.object_asset TO longhao_tenant_access;
GRANT SELECT,INSERT ON core.object_asset TO longhao_tenant_access;
GRANT INSERT ON core.audit_event TO longhao_tenant_access;
GRANT SELECT,INSERT,UPDATE ON core.outbox,core.inbox,core.idempotency_record,core.job_lease TO longhao_tenant_access;
-- Retention/hold decisions are privileged workflows, never a generic worker privilege.
GRANT SELECT ON core.legal_hold TO longhao_tenant_access;
GRANT SELECT,INSERT ON core.retention_policy,core.legal_hold,core.staff_credential,core.credential_revocation TO longhao_m14;
GRANT SELECT,INSERT,UPDATE ON core.actor TO longhao_m01;

DO $$
DECLARE domain_name text; role_name text; i integer:=0;
BEGIN
  FOREACH domain_name IN ARRAY ARRAY['customer','contract','kyc','aml','research','review','report','publication','delivery','crm','ownership','commission','support','finance'] LOOP
    i:=i+1; role_name:='longhao_m'||lpad(i::text,2,'0');
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I',domain_name,role_name);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA %I TO %I',domain_name,role_name);
    -- Deliberately no DELETE on business/evidence tables. Retention deletion uses a
    -- separately approved archival procedure and object-store legal-hold checks.
    EXECUTE format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO %I',domain_name,role_name);
    IF domain_name<>'aml' THEN
      EXECUTE format('GRANT USAGE ON SCHEMA %I TO longhao_audit',domain_name);
      EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO longhao_audit',domain_name);
    END IF;
  END LOOP;
END $$;
GRANT SELECT ON core.audit_event,core.legal_hold,core.object_asset,core.retention_policy,core.actor,core.staff_credential,core.credential_revocation TO longhao_audit;
GRANT USAGE ON SCHEMA aml TO longhao_aml_audit;
GRANT SELECT ON ALL TABLES IN SCHEMA aml TO longhao_aml_audit;
GRANT SELECT ON core.audit_event,core.object_asset TO longhao_aml_audit;
CREATE POLICY audit_regular_classification ON core.audit_event AS RESTRICTIVE FOR SELECT
TO longhao_audit USING (classification<>'restricted_aml');
CREATE POLICY audit_aml_classification ON core.audit_event AS RESTRICTIVE FOR SELECT
TO longhao_aml_audit USING (classification='restricted_aml');
CREATE POLICY object_aml_classification ON core.object_asset AS RESTRICTIVE FOR SELECT
USING (classification<>'restricted_aml' OR pg_has_role(current_user,'longhao_m04','member') OR pg_has_role(current_user,'longhao_aml_audit','member'));

-- Explicit read dependencies; a module cannot mutate another module's tables.
GRANT USAGE ON SCHEMA customer,kyc,aml,finance TO longhao_m02;
GRANT SELECT ON customer.application,customer.customer,kyc.submission,kyc.suitability_assessment,aml.business_clearance,
  finance.payment TO longhao_m02;
GRANT USAGE ON SCHEMA customer TO longhao_m03,longhao_m04,longhao_m10,longhao_m11,longhao_m12,longhao_m13,longhao_m14;
GRANT SELECT ON customer.customer,customer.application TO longhao_m03,longhao_m04,longhao_m10,longhao_m11,longhao_m12,longhao_m13,longhao_m14;
GRANT USAGE ON SCHEMA kyc TO longhao_m04;
GRANT SELECT ON kyc.submission,kyc.submission_document TO longhao_m04;
GRANT USAGE ON SCHEMA research TO longhao_m06,longhao_m07;
GRANT SELECT ON ALL TABLES IN SCHEMA research TO longhao_m06,longhao_m07;
GRANT USAGE ON SCHEMA review TO longhao_m07;
GRANT SELECT ON ALL TABLES IN SCHEMA review TO longhao_m07;
GRANT USAGE ON SCHEMA report TO longhao_m08,longhao_m09;
GRANT SELECT ON report.report,report.report_version,report.signature,report.revocation TO longhao_m08,longhao_m09;
GRANT USAGE ON SCHEMA contract TO longhao_m08,longhao_m09,longhao_m10,longhao_m11,longhao_m12,longhao_m13,longhao_m14;
GRANT SELECT ON contract.contract,contract.contract_service,contract.entitlement TO longhao_m08,longhao_m09,longhao_m10,longhao_m11,longhao_m12,longhao_m13,longhao_m14;
GRANT USAGE ON SCHEMA publication TO longhao_m09,longhao_m13;
GRANT SELECT ON ALL TABLES IN SCHEMA publication TO longhao_m09,longhao_m13;
GRANT USAGE ON SCHEMA delivery TO longhao_m13;
GRANT SELECT ON delivery.channel_binding TO longhao_m13;
GRANT USAGE ON SCHEMA crm TO longhao_m11,longhao_m12;
GRANT SELECT ON crm.activity,crm.conversion,crm.marketing_attribution,crm.suppression TO longhao_m11,longhao_m12;
GRANT USAGE ON SCHEMA ownership,finance TO longhao_m12;
GRANT SELECT ON ownership.assignment,ownership.pool_entry,finance.revenue_recognition,finance.refund_allocation,
  finance.service_schedule,finance.refund TO longhao_m12;
GRANT USAGE ON SCHEMA support TO longhao_m14;
GRANT SELECT ON support.case_file TO longhao_m14;
GRANT USAGE ON SCHEMA commission TO longhao_m14;
GRANT SELECT ON commission.payout_batch,commission.payout_line,commission.entry,commission.adjustment TO longhao_m14;
COMMIT;
