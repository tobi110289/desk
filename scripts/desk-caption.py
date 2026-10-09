#!/usr/bin/env python3
"""desk-caption.py <ref> "<status>" [note]: rewrite a loop entry's caption the desk way.
Writes `status` (+ a `desk:` note marker) on the loop entry, never `updated`, and stamps desk.json
captions[ref] = {status, at} so desk.py can age an agent-owned caption from the moment the desk wrote it
(2026-09-17: seven freshly sent lines came straight back as liveness cards because `updated` was 8 days old)."""
import json,os,sys,tempfile,datetime
ref,status=sys.argv[1],sys.argv[2]; note=sys.argv[3] if len(sys.argv)>3 else ''
now=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def save(p,obj):
    fd,tmp=tempfile.mkstemp(dir=os.path.dirname(p) or '.')
    with os.fdopen(fd,'w') as f: json.dump(obj,f,indent=2,ensure_ascii=False)
    os.replace(tmp,p)
sys.path.insert(0,os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import loopstate; loopstate.revive(ref)   # an archived entry comes back when the desk writes to it
S=os.path.expanduser('~/orbit-scratch/agent-loop/state.json'); s=json.load(open(S))
t=s['threads'].get(ref)
if t is None: sys.exit(f'{ref}: no loop entry')
t['status']=status
if note: t['note']=(t.get('note') or '')+f' desk: {now[:10]} {note}'
save(S,s)
D=os.path.expanduser('~/orbit-scratch/desk/desk.json'); d=json.load(open(D))
d.setdefault('captions',{})[ref]={'status':status,'at':now}
save(D,d); print(f'{ref} → "{status}" @ {now}')
