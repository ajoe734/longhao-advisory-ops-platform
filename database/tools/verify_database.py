#!/usr/bin/env python3
"""Apply all schema files and synthetic SQL assertions to an EMPTY disposable DB.

--dsn-env: normal server (CI), includes RLS/role tests.
--ephemeral: optional pgserver normal local instance, includes RLS/role tests.
--standalone: bundled PostgreSQL single-user engine without sockets; explicitly
              SKIPS runtime RLS/privilege tests because that mode is privileged.

This is a test runner, not a production migration orchestrator.
"""
import argparse,hashlib,json,os,re,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
WIRE_SQL="""SELECT jsonb_build_object(
 'event_id',event_id,'event_type',event_type,'schema_version',schema_version,
 'tenant_id',tenant_id,'producer',producer,'aggregate_type',aggregate_type,
 'aggregate_id',aggregate_id,'aggregate_version',aggregate_version,'sequence_no',sequence_no,
 'occurred_at',to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
 'emitted_at',to_char(emitted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
 'correlation_id',correlation_id,'causation_id',causation_id,'idempotency_key',idempotency_key,
 'classification',classification,'payload_sha256',trim(payload_sha256),'payload',payload
) AS envelope FROM core.outbox WHERE event_type='report.signature_verified'"""

def validate_persisted_event(envelope,result):
    from jsonschema import Draft202012Validator,FormatChecker
    schema=json.loads((ROOT.parent/'events/schemas/report.signature_verified.v1.schema.json').read_text())
    Draft202012Validator(schema,format_checker=FormatChecker()).validate(envelope)
    canonical=json.dumps(envelope['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(canonical).hexdigest()!=envelope['payload_sha256']:
        raise RuntimeError('persisted outbox payload SHA-256 mismatch')
    if envelope['aggregate_id']!=envelope['payload']['report_version_id']:
        raise RuntimeError('persisted signature event aggregate/report mismatch')
    result['persisted_outbox_wire_schema']='passed'
    result['persisted_outbox_payload_sha256']='passed'
    result['persisted_outbox_rows_checked']=1
    print('PASS persisted outbox row -> canonical event schema + payload hash')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dsn-env',metavar='ENV_NAME')
    mode.add_argument('--ephemeral',action='store_true')
    mode.add_argument('--standalone',action='store_true')
    p.add_argument('--report',type=Path,help='Optional machine-readable verification report')
    a=p.parse_args()
    files=sorted((ROOT/'migrations').glob('*.sql'))+[ROOT/'seeds/001_synthetic.sql',ROOT/'tests/001_invariants.sql']
    if not a.standalone:files.append(ROOT/'tests/002_rls_roles.sql')
    result={'verified_at':datetime.now(timezone.utc).isoformat(),'mode':'standalone' if a.standalone else 'normal-server',
            'files':[],'rls_execution':'not_run_standalone_is_privileged' if a.standalone else 'required',
            'concurrency_execution':'not_run_requires_application_integration_tests','status':'running'}
    temp=tempfile.TemporaryDirectory(prefix='longhao-db-test-')
    try:
        if a.standalone:
            import pgserver
            bindir=Path(pgserver.__file__).parent/'pginstall/bin'
            data=Path(temp.name)/'pgdata'
            init=subprocess.run([str(bindir/'initdb'),'-D',str(data),'-A','trust','-U','postgres','--no-locale','--encoding=UTF8'],capture_output=True,text=True)
            if init.returncode:raise RuntimeError('disposable initdb failed: '+init.stderr[-2000:])
            def execute(text):
                # Known checked-in SQL uses full-line comments. Flattening allows
                # standalone's newline-command input without splitting PL/pgSQL.
                if any('--' in line for line in text.splitlines() if not line.lstrip().startswith('--')):
                    raise RuntimeError('standalone adapter disallows inline SQL comments; use normal server')
                flattened=' '.join(line for line in text.splitlines() if not line.lstrip().startswith('--'))+'\n'
                cmd=[str(bindir/'postgres'),'--single','-D',str(data),'-c','shared_memory_type=mmap','-c','dynamic_shared_memory_type=mmap','-c','log_min_messages=notice','postgres']
                r=subprocess.run(cmd,input=flattened,text=True,capture_output=True)
                errors=[line for line in r.stderr.splitlines() if re.search(r'\b(ERROR|FATAL|PANIC):',line)]
                if r.returncode or errors:raise RuntimeError('\n'.join(errors) or r.stderr[-2000:])
                return r.stdout+'\n'+r.stderr
            v=execute('SELECT version();')
            match=re.search(r'PostgreSQL \d[^"\n]+',v)
            result['database_version']=match.group(0) if match else 'unknown'
            for file in files:
                text=file.read_text();out=execute(text)
                expected=len(re.findall(r'SELECT pg_temp\.(assert_true|expect_error)\(',text))
                passed=len(re.findall(r'NOTICE:\s+ASSERT-PASS:',out))
                if expected and passed!=expected:raise RuntimeError(f'{file.name}: expected {expected} assertions, observed {passed}')
                result['files'].append({'path':str(file.relative_to(ROOT.parent)),'sha256':hashlib.sha256(text.encode()).hexdigest(),'assertions':passed,'status':'passed'})
                print(f'PASS {file.relative_to(ROOT.parent)} ({passed} assertions)')
            wire_out=execute("SELECT encode(convert_to(envelope::text,'UTF8'),'hex') AS wire_hex FROM ("+WIRE_SQL+") q;")
            wire_match=re.search(r'wire_hex = "([0-9a-f]+)"',wire_out)
            if not wire_match:raise RuntimeError('persisted outbox wire row missing')
            validate_persisted_event(json.loads(bytes.fromhex(wire_match.group(1))),result)
        else:
            import psycopg
            if a.ephemeral:
                import pgserver
                server=pgserver.get_server(Path(temp.name)/'pgdata',cleanup_mode='delete')
                dsn=server.get_uri()
            else:
                dsn=os.environ.get(a.dsn_env,'')
                if not dsn:raise RuntimeError('specified test database environment variable is empty')
            with psycopg.connect(dsn,autocommit=True) as conn:
                result['database_version']=conn.execute('SELECT version()').fetchone()[0]
                if conn.execute("SELECT to_regclass('core.tenant') IS NOT NULL").fetchone()[0]:
                    raise RuntimeError('Refusing nonempty target: core.tenant already exists; use EMPTY disposable database')
                notices=[]
                conn.add_notice_handler(lambda n:notices.append(n.message_primary))
                for file in files:
                    notices.clear();text=file.read_text();conn.execute(text)
                    expected=len(re.findall(r'SELECT pg_temp\.(assert_true|expect_error)\(',text))
                    passed=sum(n.startswith('ASSERT-PASS:') for n in notices)
                    if expected and passed!=expected:raise RuntimeError(f'{file.name}: expected {expected} assertions, observed {passed}')
                    result['files'].append({'path':str(file.relative_to(ROOT.parent)),'sha256':hashlib.sha256(text.encode()).hexdigest(),'assertions':passed,'status':'passed'})
                    print(f'PASS {file.relative_to(ROOT.parent)} ({passed} assertions)')
                stored=conn.execute(WIRE_SQL).fetchone()
                if not stored:raise RuntimeError('persisted outbox row missing')
                validate_persisted_event(stored[0],result)
            result['rls_execution']='passed'
        result['status']='passed'
        result['assertions_passed']=sum(f['assertions'] for f in result['files'])
        if a.report:
            a.report.parent.mkdir(parents=True,exist_ok=True)
            a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='files'},ensure_ascii=False,indent=2))
    finally:
        temp.cleanup()

if __name__=='__main__':main()
