#!/usr/bin/env python3
"""Check documentation links and trace all 14 modules to API/data/UAT contracts."""
import csv,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
errors=[]
def check(test,message):
 if not test:errors.append(message)
manifest=json.loads((ROOT/'project/module-manifest.json').read_text())
modules=manifest['modules'];ids={m['id'] for m in modules}
check(ids=={f'M{i:02}' for i in range(1,15)},'Expected exact M01..M14')
check(len(modules)==14 and manifest['development_mode']=='parallel','Full parallel scope missing')
check(manifest['production_release_requires_all_modules'],'Partial release must not redefine complete scope')
for m in modules:
 check(m['start_dependencies']==[],f'{m["id"]}: serialized start dependency')
 for folder in ['docs/modules','agents/task-packets']:
  p=ROOT/folder/(m['id']+'-'+m['slug']+'.md')
  check(p.is_file(),f'Missing {p.relative_to(ROOT)}')
 check((ROOT/f'project/issue-bodies/{m["issue"]:02}.md').is_file(),f'Missing issue body {m["id"]}')
for p in ROOT.rglob('*.md'):
 if any(s in p.parts for s in ['.git','.venv','node_modules']):continue
 for target in re.findall(r'(?<!!)\[[^\]\n]*\]\(([^)\n]+)\)',p.read_text()):
  target=target.split('#')[0]
  if not target or '://' in target or target.startswith(('mailto:','sandbox:','<')):continue
  if ' ' in target:target=target.split(' ')[0]
  check((p.parent/target).exists(),f'Broken link {p.relative_to(ROOT)} -> {target}')
ops={o['operation_id'] for o in json.loads((ROOT/'api/operation-catalog.json').read_text())}
tables={t['name'] for t in json.loads((ROOT/'database/catalog.json').read_text())['tables']}|{'core.tenant'}
tests=json.loads((ROOT/'qa/test-catalog.json').read_text())['tests']
check(len({t['id'] for t in tests})==len(tests),'Duplicate UAT IDs')
used=set()
for t in tests:
 for op in t.get('operation_ids',[]):check(op in ops,f'{t["id"]}: unknown operation {op}');used.add(op)
 for table in t.get('tables',[]):check(table in tables,f'{t["id"]}: unknown table {table}')
 check(t['status'] in ['NOT_RUN','PASS','FAIL','BLOCKED'],'Unknown UAT result status')
for mid in ids:check(sum(t.get('module')==mid for t in tests)>=8,f'{mid} lacks concrete cases')
check(ops<=used,'Operations without UAT: '+', '.join(sorted(ops-used)))
rows=list(csv.DictReader((ROOT/'qa/traceability.csv').open(encoding='utf-8-sig')))
for row in rows:
 for key in ['spec_path','task_packet_path']:check((ROOT/row[key]).is_file(),f'Missing traceability {key}')
policy=json.loads((ROOT/'config/commission-policy.example.json').read_text())
check(policy['base_pool_rate'] is None and not policy['production_enabled'],'Unapproved live commission rate')
check(not policy['pool_return']['production_enabled'],'Pool interpretation must remain draft until approved')
with (ROOT/'config/rbac-matrix.csv').open() as f:rbac=list(csv.DictReader(f))
check({x['operation_id'] for x in rbac}==ops,'RBAC matrix drift; regenerate from OpenAPI')
if errors:raise SystemExit('\n'.join(errors))
print(f'PASS documentation: 14 parallel modules, {len(ops)} API operations traced, {len(tests)} acceptance cases, {len(rows)} trace rows; implementation UAT status remains separately recorded')
