#!/usr/bin/env python3
"""Deterministic PostgreSQL integrity scenarios; use only disposable DB."""
from pathlib import Path
import json,uuid
ROOT=Path(__file__).resolve().parents[1]
ids=json.loads((ROOT/'seeds/ids.json').read_text())
NS=uuid.UUID('95bf168e-efba-4a09-bb38-b7cd1c0b36a3')
def u(k): return ids.get(k,str(uuid.uuid5(NS,'test:'+k)))
def q(x): return "'"+str(x).replace("'","''")+"'"
T=q(u('tenant'))
sql=["""-- Synthetic database invariants. Entire test suite rolls back test-only changes.
BEGIN ISOLATION LEVEL SERIALIZABLE;
CREATE FUNCTION pg_temp.assert_true(label text, ok boolean) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF ok IS DISTINCT FROM true THEN RAISE EXCEPTION 'ASSERT-FAIL: %',label; END IF;
 RAISE NOTICE 'ASSERT-PASS: %',label;
END $$;
CREATE FUNCTION pg_temp.expect_error(label text, command text, expected_state text, message_part text DEFAULT '')
RETURNS void LANGUAGE plpgsql AS $$
DECLARE got_state text; got_message text;
BEGIN
 BEGIN
  EXECUTE command;
  SET CONSTRAINTS ALL IMMEDIATE;
 EXCEPTION WHEN OTHERS THEN
  GET STACKED DIAGNOSTICS got_state=RETURNED_SQLSTATE,got_message=MESSAGE_TEXT;
  IF got_state<>expected_state OR strpos(got_message,message_part)=0 THEN
   RAISE EXCEPTION 'ASSERT-FAIL: %, expected % / %, got % / %',label,expected_state,message_part,got_state,got_message;
  END IF;
  RAISE NOTICE 'ASSERT-PASS: %',label;
  RETURN;
 END;
 RAISE EXCEPTION 'ASSERT-FAIL: %, expected rejection did not occur',label;
END $$;
""",f"SELECT set_config('app.tenant_id',{T},true);\n"]
def a(label,cond):sql.append(f"SELECT pg_temp.assert_true({q(label)},({cond}));\n")
def bad(label,cmd,state='23514',message=''):
    sql.append(f"SELECT pg_temp.expect_error({q(label)},$test${cmd}$test$,{q(state)},{q(message)});\n")
def clone(table,source,**changes):
    patch={'id':u('clone:'+source),**changes}
    return f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table},to_jsonb(x)||{q(json.dumps(patch))}::jsonb)).* FROM {table} x WHERE tenant_id={T} AND id={q(u(source))}"

a('DB-01 customer signs before KYC without premature acceptance',f"SELECT cs.customer_signed_at < k.submitted_at AND k.reviewed_at <= ca.accepted_at FROM contract.customer_signature cs JOIN kyc.submission k ON cs.tenant_id=k.tenant_id AND cs.application_id=k.application_id JOIN contract.company_acceptance ca ON ca.tenant_id=cs.tenant_id AND ca.application_id=cs.application_id WHERE cs.id={q(u('customer_signature'))}")
a('DB-02 paid cash is distinct from earned service income',"SELECT (SELECT sum(amount) FROM finance.payment) > (SELECT sum(untaxed_amount) FROM finance.revenue_recognition)")
a('DB-03 signature has automatic publication outbox',f"SELECT EXISTS(SELECT 1 FROM core.outbox WHERE event_type='report.signature_verified' AND aggregate_id={q(u('report_version'))})")
a('DB-04 delivery read remains unknown',f"SELECT read_at IS NULL FROM delivery.message WHERE id={q(u('message'))}")
a('DB-05 live pool rate remains unset',f"SELECT base_pool_rate IS NULL AND approval_status='draft' FROM commission.policy_version WHERE id={q(u('commission_draft'))}")

bad('DB-06 cross-tenant FK rejected',clone('customer.external_identity','identity',tenant_id=u('tenant2'),subject_hmac='1'*64),'23503')
bad('DB-07 duplicate external identity rejected',clone('customer.external_identity','identity'),'23505')
bad('DB-08 immutable customer signature',f"UPDATE contract.customer_signature SET content_sha256={'0'*64!r} WHERE tenant_id={T} AND id={q(u('customer_signature'))}",message='append-only')
bad('DB-09 wrong customer evidence cannot grant acceptance',clone('contract.company_acceptance','acceptance',accepted_at='2026-10-01T00:00:00Z'),message='acceptance cannot precede')
sql.append(f"UPDATE kyc.submission SET status='rejected' WHERE tenant_id={T} AND id={q(u('kyc'))};\n")
bad('DB-10 rejected KYC blocks acceptance',clone('contract.company_acceptance','acceptance'),message='gates not satisfied')
sql.append(f"UPDATE kyc.submission SET status='approved' WHERE tenant_id={T} AND id={q(u('kyc'))};\n")
bad('DB-11 effective contract cannot backdate acceptance',f"UPDATE contract.contract SET effective_at='2026-10-31T00:00:00Z' WHERE tenant_id={T} AND id={q(u('contract'))}",message='prior company acceptance')
bad('DB-12 entitlement cannot extend past paid service',f"UPDATE contract.entitlement SET valid_until='2030-01-01T00:00:00Z' WHERE tenant_id={T} AND id={q(u('entitlement'))}",message='service interval')
bad('DB-13 entitlement cannot target other customer',f"UPDATE contract.entitlement SET customer_id={q(u('other_customer'))} WHERE tenant_id={T} AND id={q(u('entitlement'))}",message='accepted contract')
bad('DB-14 frozen report bytes immutable',f"UPDATE report.report_version SET content_sha256={'0'*64!r} WHERE tenant_id={T} AND id={q(u('report_version'))}",message='frozen report')
bad('DB-15 signed report cannot become editable',f"UPDATE report.report_version SET status='draft' WHERE tenant_id={T} AND id={q(u('report_version'))}",message='cannot return')
bad('DB-16 verified signature evidence immutable',f"DELETE FROM report.signature WHERE tenant_id={T} AND id={q(u('signature'))}",message='append-only')
bad('DB-17 publication cannot precede verification',clone('publication.publication','publication',queued_at='2026-11-02T01:00:00Z'),message='valid signed matching')
bad('DB-18 publication cannot exceed report validity',clone('publication.publication','publication',expires_at='2027-01-01T00:00:00Z'),message='valid signed matching')
bad('DB-19 publication hash must match signature',clone('publication.publication','publication',bundle_sha256='0'*64),message='valid signed matching')
bad('DB-20 duplicate per-recipient channel message rejected',clone('delivery.message','message',provider_idempotency_key='another-key'),'23505')
bad('DB-21 overlapping maintenance assignment rejected',clone('ownership.assignment','assignment',valid_from='2026-11-03T00:00:00Z'),message='overlapping ownership')
bad('DB-22 sales conversion without activity rejected',f"INSERT INTO crm.conversion(tenant_id,id,customer_id,opportunity_id,contract_service_id,sales_actor_id,conversion_type,decision_status,converted_at,policy_version) VALUES({T},{q(u('invalid_conversion'))},{q(u('customer'))},{q(u('opportunity'))},{q(u('service'))},{q(u('sales'))},'sales_assisted','approved','2026-11-02T03:00:00Z','fixture')")
bad('DB-23 draft policy cannot calculate live awards',clone('commission.calculation_run','simulation',mode='live',run_key='invalid-live'),message='ratified complete policy')
bad('DB-24 approved policy cannot omit base rate',clone('commission.policy_version','commission_draft',approval_status='approved',policy_version=2,approved_by=u('finance_checker'),approved_at='2026-11-01T00:00:00Z'))
bad('DB-25 financial totals must reconcile',f"UPDATE finance.invoice SET gross_amount=11999 WHERE tenant_id={T} AND id={q(u('invoice'))}")
bad('DB-26 negative settlement fee rejected',clone('finance.settlement','settlement',provider_settlement_id='invalid-fee',fee_amount=-1,net_amount=12001))
bad('DB-27 income records immutable',f"UPDATE finance.revenue_recognition SET untaxed_amount=99999 WHERE tenant_id={T} AND id={q(u('revenue'))}",message='append-only')

# Maker/checker and refund basis checks use transaction-local rows only.
refund=f"INSERT INTO finance.refund(tenant_id,id,payment_id,customer_id,amount,currency,prepared_by,approved_by,status,reason_code,idempotency_key) VALUES({T},{q(u('refund'))},{q(u('payment'))},{q(u('customer'))},100,'TWD',{q(u('finance_maker'))},{q(u('finance_checker'))},'approved','fixture','fixture-refund')"
sql.append(refund+';\n')
bad('DB-28 same person cannot approve own refund',f"UPDATE finance.refund SET approved_by=prepared_by WHERE tenant_id={T} AND id={q(u('refund'))}")
bad('DB-29 unearned refund cannot reduce earned-income commission',f"INSERT INTO finance.refund_allocation(tenant_id,id,refund_id,recognition_id,allocation_type,amount,currency) VALUES({T},{q(u('refund_alloc_bad'))},{q(u('refund'))},{q(u('revenue'))},'unearned_prepayment',100,'TWD')")
sql.append(f"INSERT INTO finance.refund_allocation(tenant_id,id,refund_id,allocation_type,amount,currency) VALUES({T},{q(u('refund_alloc'))},{q(u('refund'))},'unearned_prepayment',100,'TWD');\n")
a('DB-30 unearned refund has no eligible revenue link',f"SELECT recognition_id IS NULL FROM finance.refund_allocation WHERE id={q(u('refund_alloc'))}")

# Journal deferrable gate is exercised at SET CONSTRAINTS, just like COMMIT.
sql.append(f"INSERT INTO finance.journal(tenant_id,id,journal_number,source_type,source_id,currency,accounting_date,prepared_by,approved_by) VALUES({T},{q(u('journal'))},'TEST-J-1','test',{q(u('test_journal_source'))},'TWD','2026-11-22',{q(u('finance_maker'))},{q(u('finance_checker'))});\n")
sql.append(f"INSERT INTO finance.journal_line(tenant_id,id,journal_id,line_no,account_code,debit,credit) VALUES({T},{q(u('journal_debit'))},{q(u('journal'))},1,'cash',100,0),({T},{q(u('journal_credit'))},{q(u('journal'))},2,'prepayment',0,100);\n")
bad('DB-31 journal maker checker enforced',f"UPDATE finance.journal SET approved_by=prepared_by WHERE tenant_id={T} AND id={q(u('journal'))}")
bad('DB-32 unbalanced approved journal rejected',f"UPDATE finance.journal_line SET credit=99 WHERE tenant_id={T} AND id={q(u('journal_credit'))}; UPDATE finance.journal SET status='approved' WHERE tenant_id={T} AND id={q(u('journal'))}",message='balanced lines')
sql.append(f"UPDATE finance.journal SET status='posted',posted_at='2026-11-22T00:00:00Z' WHERE tenant_id={T} AND id={q(u('journal'))}; SET CONSTRAINTS ALL IMMEDIATE;\n")
a('DB-33 balanced journal posts',f"SELECT status='posted' FROM finance.journal WHERE id={q(u('journal'))}")
bad('DB-34 posted journal cannot be rewritten',f"UPDATE finance.journal_line SET debit=101 WHERE tenant_id={T} AND id={q(u('journal_debit'))}",message='posted journal lines immutable')

# Complete-service lapse is independent from seven-day contact frequency.
sql.append(clone('ownership.policy_version','pool_policy',id=u('approved_pool_policy'),policy_code='SYNTHETIC_TEST_APPROVED',approval_status='approved',approved_by=u('admin'),approved_at='2026-10-01T00:00:00Z')+';\n')
sql.append(clone('ownership.assignment','assignment',id=u('historical_pool_assignment'),owner_type='company_pool',owner_actor_id=None,valid_from='2026-10-01T00:00:00Z',valid_until='2026-11-02T02:00:00Z',policy_version_id=u('approved_pool_policy'))+';\n')
pool_insert=f"INSERT INTO ownership.pool_entry(tenant_id,id,customer_id,assignment_id,reason_code,all_paid_services_ended_at,entered_at,evaluation_snapshot) VALUES({T},{q(u('pool_bad'))},{q(u('customer'))},{q(u('historical_pool_assignment'))},'all_paid_services_lapsed','2026-10-25T00:00:00Z','2026-11-01T12:00:00Z','{{\"fixture_only\":true}}')"
bad('DB-43 active annual service prevents whole-customer lapse',pool_insert,message='any paid service')
bad('DB-44 seven complete lapse days are required',pool_insert.replace('2026-10-25T00:00:00Z','2026-10-30T00:00:00Z'),message='elapsed complete interval')
a('DB-45 customer lack of contact is not Pool basis',f"SELECT lapse_basis='proposed_all_paid_services_ended' AND lapse_days=7 FROM ownership.policy_version WHERE id={q(u('pool_policy'))}")
bad('DB-46 outbox payload cannot mutate on retry',f"UPDATE core.outbox SET payload='{{}}'::jsonb WHERE tenant_id={T} AND id={q(u('sign_outbox'))}",message='immutable payload')
bad('DB-47 published message cannot swap report signature',f"UPDATE publication.publication SET bundle_sha256={'0'*64!r} WHERE tenant_id={T} AND id={q(u('publication'))}",message='immutable payload')
bad('DB-48 delivery retry cannot rewrite signed wording',f"UPDATE delivery.message SET rendered_sha256={'0'*64!r} WHERE tenant_id={T} AND id={q(u('message'))}",message='immutable payload')
bad('DB-49 commission rerun cannot swap input fingerprint',f"UPDATE commission.calculation_run SET input_sha256={'0'*64!r} WHERE tenant_id={T} AND id={q(u('simulation'))}",message='immutable payload')
bad('DB-50 commission policy terms require new version',f"UPDATE commission.policy_version SET base_pool_rate=0.03 WHERE tenant_id={T} AND id={q(u('commission_draft'))}",message='immutable payload')
bad('DB-51 approved contract template cannot rewrite approver',f"UPDATE contract.package_version SET approved_by={q(u('admin'))} WHERE tenant_id={T} AND id={q(u('package_version'))}",message='approval evidence immutable')
bad('DB-57 service response approval must bind exact draft',clone('support.response_approval','response_approval',draft_content_sha256='0'*64),message='exact draft hash')
bad('DB-58 service reply cannot target another customer',clone('support.response_dispatch','response',customer_id=u('other_customer'),idempotency_key='invalid-recipient'),message='verified customer channel')
bad('DB-59 completed service draft cannot be overwritten',f"UPDATE support.response_draft SET content_sha256={'0'*64!r} WHERE tenant_id={T} AND id={q(u('response_draft'))}",message='new version')
bad('DB-60 service response retry cannot rewrite content',f"UPDATE support.response_dispatch SET content_sha256={'0'*64!r} WHERE tenant_id={T} AND id={q(u('response'))}",message='immutable payload')
sql.append(clone('support.response_draft','response_draft',id=u('unsafe_draft'),contains_new_investment_judgment=True)+';\n')
bad('DB-61 support cannot approve new investment judgment',clone('support.response_approval','response_approval',draft_id=u('unsafe_draft')),message='unapproved investment judgment')
bad('DB-62 duplicate support provider receipt rejected',clone('support.response_receipt','response_delivery_receipt'),'23505')
bad('DB-63 service actor cannot impersonate human reviewer',clone('support.response_approval','response_approval',reviewer_id=u('worker')),message='enabled human actor')
a('DB-64 support delivered state is separate from unknown read',f"SELECT status='delivered' AND delivered_at IS NOT NULL AND read_at IS NULL FROM support.response_dispatch WHERE id={q(u('response'))}")
a('DB-42 all business tables have forced tenant RLS',"SELECT bool_and(c.relrowsecurity AND c.relforcerowsecurity) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname IN ('core','customer','contract','kyc','aml','research','review','report','publication','delivery','crm','ownership','commission','support','finance')")
rls_start=len(sql)
# RLS must run under normal postmaster: PostgreSQL standalone is always privileged.
for classification,key in [('internal','audit_internal'),('restricted_aml','audit_aml')]:
    sql.append(f"INSERT INTO core.audit_event(tenant_id,id,actor_id,action,resource_type,resource_id,correlation_id,outcome,classification,occurred_at,event_hash) VALUES({T},{q(u(key))},{q(u('admin'))},'synthetic',{'aml.case' if classification=='restricted_aml' else 'customer.customer'!r},{q(u('customer'))},{q(u('audit_trace'))},'success',{q(classification)},'2026-11-22T00:00:00Z',{'a'*64!r});\n")
sql.append(clone('core.object_asset','kyc_document',id=u('aml_asset'),classification='restricted_aml',storage_key='synthetic/restricted-aml')+';\n')
sql.append('SET LOCAL ROLE longhao_m01;\n')
a('DB-35 assigned tenant can see own customer',f"SELECT count(*)=2 FROM customer.customer")
sql.append(f"SELECT set_config('app.tenant_id',{q(u('tenant2'))},true);\n")
a('DB-36 other tenant cannot see customer',"SELECT count(*)=0 FROM customer.customer")
bad('DB-37 tenant spoofed insert rejected',f"INSERT INTO customer.customer(tenant_id,id) VALUES({T},{q(u('rls_bad'))})",'42501','row-level security')
sql.append(f"SELECT set_config('app.tenant_id',{T},true);\n")
bad('DB-38 customer module cannot access restricted AML case',"SELECT * FROM aml.case_file",'42501','permission denied')
bad('DB-39 customer module cannot mutate contract',f"UPDATE contract.contract SET status='terminated' WHERE tenant_id={T}",'42501','permission denied')
sql.append('RESET ROLE;\nSET LOCAL ROLE longhao_m02;\n')
a('DB-40 contract module may read safe AML clearance',f"SELECT count(*)=1 FROM aml.business_clearance")
bad('DB-41 contract module cannot read AML notes',"SELECT * FROM aml.case_file",'42501','permission denied')
sql.append('RESET ROLE;\nSET LOCAL ROLE longhao_m10;\n')
bad('DB-52 CRM cannot read unrestricted shared audit log',"SELECT * FROM core.audit_event",'42501','permission denied')
a('DB-53 CRM cannot see AML asset metadata',f"SELECT count(*)=0 FROM core.object_asset WHERE id={q(u('aml_asset'))}")
sql.append('RESET ROLE;\nSET LOCAL ROLE longhao_audit;\n')
a('DB-54 regular auditor sees only non-AML audit records',"SELECT count(*)=1 AND bool_and(classification<>'restricted_aml') FROM core.audit_event")
sql.append('RESET ROLE;\nSET LOCAL ROLE longhao_aml_audit;\n')
a('DB-55 AML auditor can see AML audit records',"SELECT count(*)=1 AND bool_and(classification='restricted_aml') FROM core.audit_event")
a('DB-56 AML auditor can see authorized restricted asset metadata',f"SELECT count(*)=1 FROM core.object_asset WHERE id={q(u('aml_asset'))}")
sql.append('RESET ROLE;\n')
(ROOT/'tests/001_invariants.sql').write_text(''.join(sql[:rls_start])+"ROLLBACK;\n")
(ROOT/'tests/002_rls_roles.sql').write_text(sql[0]+sql[1]+''.join(sql[rls_start:])+"ROLLBACK;\n")
print('Generated 52 invariant assertions and 12 normal-server RLS/role assertions')
