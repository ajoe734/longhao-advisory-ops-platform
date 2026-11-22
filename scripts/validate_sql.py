#!/usr/bin/env python3
"""Parse SQL syntax; this does NOT assert migrations/RLS have executed."""
from pathlib import Path
from pglast import parse_sql
ROOT=Path(__file__).resolve().parents[1]
files=sorted((ROOT/'database').glob('migrations/*.sql'))+sorted((ROOT/'database').glob('seeds/*.sql'))+sorted((ROOT/'database').glob('tests/*.sql'))
if not files:raise SystemExit('No SQL files')
count=0
for path in files:
    count+=len(parse_sql(path.read_text()))
print(f'PASS SQL syntax: {len(files)} files, {count} top-level statements; runtime/RLS not implied')
