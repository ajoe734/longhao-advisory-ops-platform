-- Synthetic database invariants. Entire test suite rolls back test-only changes.
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
SELECT set_config('app.tenant_id','a2d788c2-2287-5f87-a543-e0ad843ccea7',true);
INSERT INTO core.audit_event(tenant_id,id,actor_id,action,resource_type,resource_id,correlation_id,outcome,classification,occurred_at,event_hash) VALUES('a2d788c2-2287-5f87-a543-e0ad843ccea7','b33a4fd9-bdf8-55d6-9b7d-5d818c82bbb2','ceac1141-723b-5509-a1dd-6d0c4f62000c','synthetic','customer.customer','37c81365-3290-5667-8091-8e32df231885','4ed8a136-3dcc-52c4-8354-9f0bb90cb176','success','internal','2026-11-22T00:00:00Z','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
INSERT INTO core.audit_event(tenant_id,id,actor_id,action,resource_type,resource_id,correlation_id,outcome,classification,occurred_at,event_hash) VALUES('a2d788c2-2287-5f87-a543-e0ad843ccea7','b6179015-1656-54c7-b749-e036fe2d6567','ceac1141-723b-5509-a1dd-6d0c4f62000c','synthetic','aml.case','37c81365-3290-5667-8091-8e32df231885','4ed8a136-3dcc-52c4-8354-9f0bb90cb176','success','restricted_aml','2026-11-22T00:00:00Z','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
INSERT INTO core.object_asset SELECT (jsonb_populate_record(NULL::core.object_asset,to_jsonb(x)||'{"id": "34ef6c1a-9cf1-5e41-be3d-66a49fdaffa3", "classification": "restricted_aml", "storage_key": "synthetic/restricted-aml"}'::jsonb)).* FROM core.object_asset x WHERE tenant_id='a2d788c2-2287-5f87-a543-e0ad843ccea7' AND id='51b0c1ad-5443-50e5-a6c9-0c29c7406b99';
SET LOCAL ROLE longhao_m01;
SELECT pg_temp.assert_true('DB-35 assigned tenant can see own customer',(SELECT count(*)=2 FROM customer.customer));
SELECT set_config('app.tenant_id','b439fa49-31bd-5745-a6ae-1a867e5f1495',true);
SELECT pg_temp.assert_true('DB-36 other tenant cannot see customer',(SELECT count(*)=0 FROM customer.customer));
SELECT pg_temp.expect_error('DB-37 tenant spoofed insert rejected',$test$INSERT INTO customer.customer(tenant_id,id) VALUES('a2d788c2-2287-5f87-a543-e0ad843ccea7','831ef633-8ca6-5ae9-902e-a2d14973fcb0')$test$,'42501','row-level security');
SELECT set_config('app.tenant_id','a2d788c2-2287-5f87-a543-e0ad843ccea7',true);
SELECT pg_temp.expect_error('DB-38 customer module cannot access restricted AML case',$test$SELECT * FROM aml.case_file$test$,'42501','permission denied');
SELECT pg_temp.expect_error('DB-39 customer module cannot mutate contract',$test$UPDATE contract.contract SET status='terminated' WHERE tenant_id='a2d788c2-2287-5f87-a543-e0ad843ccea7'$test$,'42501','permission denied');
RESET ROLE;
SET LOCAL ROLE longhao_m02;
SELECT pg_temp.assert_true('DB-40 contract module may read safe AML clearance',(SELECT count(*)=1 FROM aml.business_clearance));
SELECT pg_temp.expect_error('DB-41 contract module cannot read AML notes',$test$SELECT * FROM aml.case_file$test$,'42501','permission denied');
RESET ROLE;
SET LOCAL ROLE longhao_m10;
SELECT pg_temp.expect_error('DB-52 CRM cannot read unrestricted shared audit log',$test$SELECT * FROM core.audit_event$test$,'42501','permission denied');
SELECT pg_temp.assert_true('DB-53 CRM cannot see AML asset metadata',(SELECT count(*)=0 FROM core.object_asset WHERE id='34ef6c1a-9cf1-5e41-be3d-66a49fdaffa3'));
RESET ROLE;
SET LOCAL ROLE longhao_audit;
SELECT pg_temp.assert_true('DB-54 regular auditor sees only non-AML audit records',(SELECT count(*)=1 AND bool_and(classification<>'restricted_aml') FROM core.audit_event));
RESET ROLE;
SET LOCAL ROLE longhao_aml_audit;
SELECT pg_temp.assert_true('DB-55 AML auditor can see AML audit records',(SELECT count(*)=1 AND bool_and(classification='restricted_aml') FROM core.audit_event));
SELECT pg_temp.assert_true('DB-56 AML auditor can see authorized restricted asset metadata',(SELECT count(*)=1 FROM core.object_asset WHERE id='34ef6c1a-9cf1-5e41-be3d-66a49fdaffa3'));
RESET ROLE;
ROLLBACK;
