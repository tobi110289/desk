#!/usr/bin/env python3
"""desk-caption.py <ref> "<status>" [note]: rewrite a loop entry's caption the desk way.
Writes `status` (+ a `desk:` note marker) on the loop entry, never `updated`, and stamps desk.json
captions[ref] = {status, at} so desk.py can age an agent-owned caption from the moment the desk wrote it
(2026-09-17: seven freshly sent lines came straight back as liveness cards because `updated` was 8 days old)."""
import json,os,sys,tempfile,datetime
import deskconfig
ref,status=sys.argv[1],sys.argv[2]; note=sys.argv[3] if len(sys.argv)>3 else ''
now=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def save(p,obj):
    fd,tmp=tempfile.mkstemp(dir=os.path.dirname(p) or '.')
    with os.fdopen(fd,'w') as f: json.dump(obj,f,indent=2,ensure_ascii=False)
    os.replace(tmp,p)
D=deskconfig.need(f'{deskconfig.DESK}/desk.json','desk file')   # checked before the loop state is written
sys.path.insert(0,deskconfig.need(deskconfig.JEV)); import loopstate; loopstate.revive(ref)   # an archived entry comes back when the desk writes to it
S=deskconfig.need(f'{deskconfig.LOOP}/state.json','loop state'); s=json.load(open(S))
t=s['threads'].get(ref)
if t is None: sys.exit(f'{ref}: no loop entry')
old_status=(t.get('status') or '').strip(); t['status']=status
if note: t['note']=(t.get('note') or '')+f' desk: {now[:10]} {note}'
save(S,s)
# 2026-11-09: a caption written before or after a close moved the loop card's since away from the one the close was
# bound to, and the card came back on the next gather (W45: one loop card closed six times, one close read "caption
# already reads ..."). Holds bound to the old caption follow it, under the lock desk-close.py takes.
import fcntl; _lock=open(os.path.join(os.path.dirname(D),'.desk.lock'),'w'); fcntl.flock(_lock,fcntl.LOCK_EX)
d=json.load(open(D))
d.setdefault('captions',{})[ref]={'status':status,'at':now}
try:
    sys.path.insert(0,os.path.dirname(os.path.realpath(__file__))); import deskhold; deskhold.rebind(d,ref,old_status,status)
except Exception as e: print('holds not rebound:',e)
save(D,d); print(f'{ref} → "{status}" @ {now}')
