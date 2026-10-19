#!/usr/bin/env python3
"""desk-close.py <card-id> <verb> [note]  — verbs: do decide delegate defer drop caption.  For defer: desk-close.py <id> defer "<trigger>" "<act>".  caption: store a status caption for a non-loop card (orbot / run cards) without closing it."""
import re, json,sys,os,datetime
P=os.path.expanduser('~/orbit-scratch/desk/desk.json')
import fcntl
_lock=open(os.path.join(os.path.dirname(P),'.desk.lock'),'w'); fcntl.flock(_lock,fcntl.LOCK_EX)   # desk.py checks desk.json under the same lock before it writes
d=json.load(open(P)); cid,verb=sys.argv[1],sys.argv[2]
today=datetime.date.today().isoformat(); c=d['cards'].setdefault(cid,{})
if verb=='caption':
    c['caption']=' '.join(sys.argv[3:]); c.pop('closed',None)
elif verb=='defer':
    # status: the caption this wait was written for; a new caption voids it. Bind to the LIVE loop caption, not the one
    # cards.json saw at the last desk.py run: a sitting that rewrites the caption and then defers otherwise voids itself
    # (T-118 and T-113, 2026-09-14).
    status=c.get('status')
    try:
        sys.path.insert(0,os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import loopstate; thr=loopstate.threads(archived=True)
        if cid.startswith('loop:') and cid[5:] in thr: status=(thr[cid[5:]].get('status') or '').strip()
        elif cid.startswith('gap:') and cid[4:] in thr: status='loop done, thread TODO: '+(thr[cid[4:]].get('status') or '').strip()
    except Exception: pass
    _trig=re.sub(r'^(?:until\s+)+','',sys.argv[3].strip()); _act=sys.argv[4] if len(sys.argv)>4 else c.get('act','')
    if ' -> ' in _trig and not (len(sys.argv)>4 and sys.argv[4]): _trig,_act=[x.strip() for x in _trig.split(' -> ',1)]
    c['defer']={'trigger':_trig.strip(),'act':_act,'since':today,'status':status}
    # 2026-10-16: a batch's status line carries every member's status and age, so it changed daily and on the desk's own
    # Notion writes, and the deferral voided itself within the hour (batch:TICKET:all). A batch deferral binds to its
    # member set instead: desk.py voids it only when a member appears that was not in it.
    if cid.startswith('batch:'):
        _refs=set()
        try:
            for card in json.load(open(os.path.expanduser('~/orbit-scratch/desk/cards.json')))['cards']:
                if card['id']==cid: _refs={m.get('ref') for m in card.get('members') or [] if m.get('ref')}
        except Exception: pass
        c['defer']['refs']=sorted(_refs)
    c.pop('closed',None)
else:
    members=[]; status=''
    try:
        for card in json.load(open(os.path.expanduser('~/orbit-scratch/desk/cards.json')))['cards']:
            if card['id']==cid: members=[f"{m.get('ref')}|{m.get('task') or ''}|{m.get('status') or ''}" for m in card.get('members') or []]; status=card.get('status') or ''
    except Exception: pass
    # status = the caption the card was closed with; a later caption reopens a single card (desk.py add())
    # 2026-09-23: --caption "<text>" writes the loop caption here and binds the closure to it. Closing first and rewriting
    # the caption afterwards reopened every loop card the same sitting (T-121, ORB-201-KIT came back within minutes).
    if '--caption' in sys.argv:
        i=sys.argv.index('--caption'); cap=sys.argv[i+1]; sys.argv=sys.argv[:i]+sys.argv[i+2:]
        ref=cid.split(':',1)[1] if ':' in cid else cid
        sp=os.path.expanduser('~/orbit-scratch/agent-loop/state.json')
        try:
            sys.path.insert(0,os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import loopstate; loopstate.revive(ref)   # an archived entry comes back
            st=json.load(open(sp)); thr=st['threads']
            if ref in thr:
                thr[ref]['status']=cap
                # 2026-10-13: a caption that says done ends the entry. one entry sat 18 days as a STALE card: validated as
                # 'blocked' (already shipped), the desk captioned it 'done; …' twice, but the reaper only reads running
                # entries and the archive only moves phase done, so nothing ever flipped the phase.
                if re.match(r'(done|closed)\b',cap.strip(),re.I) and thr[ref].get('phase') in ('blocked','pr-open'): thr[ref]['phase']='done'
                thr[ref]['note']=((thr[ref].get('note') or '')+f" | desk {today}: "+' '.join(sys.argv[3:])).strip(' |')
                json.dump(st,open(sp,'w'),indent=2,ensure_ascii=False)
                status=cap if cid.startswith('loop:') else ('loop done, thread TODO: '+cap if cid.startswith('gap:') else status)
                # 2026-10-19: a caption the desk writes while closing one card of a thread must not void a hold on a
                # sibling card of the same thread (a gap card deferred until the customer replies came back on every
                # close of the thread's today card). Rebind sibling deferrals to the caption just written.
                for sid,sc in d.get('cards',{}).items():
                    if sid==cid or not sc.get('defer') or sid.split(':',1)[-1]!=ref: continue
                    if sid.startswith('loop:'): sc['defer']['status']=cap
                    elif sid.startswith('gap:'): sc['defer']['status']='loop done, thread TODO: '+cap
        except Exception as e: print('caption not written:',e)
    c['closed']={'verb':verb,'date':today,'note':' '.join(sys.argv[3:]),'members':members,'status':status}
    if cid.startswith('fast:'): d['pins']=[p for p in d.get('pins',[]) if p['ref']!=cid[5:]]   # a verb unpins a FAST card
    if cid.startswith('task:'):   # a verb ends a local task (tasks.json); a defer keeps it open
        try:
            sys.path.insert(0,os.path.dirname(os.path.realpath(__file__))); import task; task.mark_done(cid,f"{verb}: "+' '.join(sys.argv[3:]))
        except Exception as e: print('task not marked done:',e)
d['log'].append({'date':today,'id':cid,'verb':verb,'note':' '.join(sys.argv[3:]) if verb!='defer' else f"until {sys.argv[3]}"})
json.dump(d,open(P,'w'),indent=1,ensure_ascii=False); print(f"{cid} → {verb}")
