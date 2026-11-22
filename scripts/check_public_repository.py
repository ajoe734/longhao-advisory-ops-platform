#!/usr/bin/env python3
"""Targeted public-repo hygiene checks, not a substitute for full security review."""
from pathlib import Path
import re,subprocess
ROOT=Path(__file__).resolve().parents[1]
patterns={
 'private_key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'github_token':re.compile(rb'gh[pousr]_[A-Za-z0-9]{36,255}'),
 'openai_key':re.compile(rb'sk-(?:proj-)?[A-Za-z0-9_-]{40,}'),
}
raw=subprocess.check_output(['git','-C',str(ROOT),'ls-files','--cached','--others','--exclude-standard','-z'])
paths=[ROOT/p.decode() for p in raw.split(b'\0') if p]
errors=[]
for path in paths:
 if not path.is_file():continue
 rel=path.relative_to(ROOT)
 if path.suffix.lower() in {'.pdf','.docx','.xlsx','.p12','.pfx','.pem','.key'}:errors.append(f'{rel}: unreviewed sensitive/binary artifact type')
 if path.name=='.env':errors.append(f'{rel}: runtime environment file')
 data=path.read_bytes()
 for name,pattern in patterns.items():
  if pattern.search(data):errors.append(f'{rel}: potential {name}')
if errors:raise SystemExit('\n'.join(errors))
print(f'PASS targeted public-repository checks: {len(paths)} files; no claim of comprehensive secret/PII scanning')
