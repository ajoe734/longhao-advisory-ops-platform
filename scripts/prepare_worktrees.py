#!/usr/bin/env python3
"""Plan or create all fourteen independent Git worktrees. Never resets existing work."""
import argparse,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def git(*args):
    return subprocess.check_output(['git','-C',str(ROOT),*args],text=True).strip()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',default='main')
    p.add_argument('--apply',action='store_true',help='Create absent worktrees; default only prints plan')
    a=p.parse_args()
    manifest=json.loads((ROOT/'project/module-manifest.json').read_text())
    modules=manifest['modules']
    if len(modules)!=14 or any(m['start_dependencies'] for m in modules):
        raise SystemExit('Expected 14 immediately startable workstreams')
    base_sha=git('rev-parse','--verify',a.base+'^{commit}')
    plan=[dict(id=m['id'],branch=m['branch'],path=str((ROOT/m['worktree']).resolve()),base_sha=base_sha) for m in modules]
    if not a.apply:
        print(json.dumps({'mode':'plan_only','module_count':14,'worktrees':plan},ensure_ascii=False,indent=2));return
    existing=git('worktree','list','--porcelain')
    registered={}
    current=None
    for line in existing.splitlines():
        if line.startswith('worktree '): current=line[9:]
        elif line.startswith('branch '): registered[current]=line[7:]
    branches=set(git('for-each-ref','--format=%(refname:short)','refs/heads').splitlines())
    # Preflight ALL paths before making any changes; never overwrite another checkout.
    for item in plan:
        path=Path(item['path']);expected='refs/heads/'+item['branch']
        if path.exists() and registered.get(str(path))!=expected:
            raise SystemExit(f'Existing unrelated path; refusing overwrite: {path}')
        if item['branch'] in branches and not path.exists():
            raise SystemExit(f'Branch already exists elsewhere; review manually: {item["branch"]}')
    for item in plan:
        path=Path(item['path'])
        if path.exists():print('KEEP',item['id'],path);continue
        path.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['git','-C',str(ROOT),'worktree','add','-b',item['branch'],str(path),base_sha],check=True)
        print('CREATED',item['id'],path)
if __name__=='__main__':main()
