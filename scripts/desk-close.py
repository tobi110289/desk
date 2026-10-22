#!/usr/bin/env python3
"""desk-close.py <card-id> <verb> [note]  — verbs: do decide delegate defer drop caption.  For defer: desk-close.py <id> defer "<trigger>" "<act>".  caption: store a status caption for a non-loop card (orbot / run cards) without closing it.

2026-10-22: the verb lands on the card's wait (deskhold: holds[key], bound to the wait's `since`), never on a display
string. The card's key and since come from cards.json, so this script holds no copy of the hold logic. It also writes
the verb into the card in cards.json, so the pane shows it at once without a logic copy of its own."""
import re, json, sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__))); import deskhold
P=os.path.expanduser('~/orbit-scratch/desk/desk.json'); CARDS=os.path.join(os.path.dirname(P),'cards.json')
import fcntl
_lock=open(os.path.join(os.path.dirname(P),'.desk.lock'),'w'); fcntl.flock(_lock,fcntl.LOCK_EX)   # desk.py checks desk.json under the same lock before it writes
d=json.load(open(P)); cid,verb=sys.argv[1],sys.argv[2]
deskhold.migrate(d)
# --pick "<label>": the option the user pressed on the pane (the mod passes it); scored against the prepared brief below
pick=None
if '--pick' in sys.argv:
    i=sys.argv.index('--pick'); pick=sys.argv[i+1]; sys.argv=sys.argv[:i]+sys.argv[i+2:]
today=deskhold.today_berlin()   # the desk's one clock
c=d['cards'].setdefault(cid,{})
try: cardsdoc=json.load(open(CARDS))
except Exception: cardsdoc={'cards':[],'parked':[]}
card=next((x for x in cardsdoc.get('cards',[])+cardsdoc.get('parked',[]) if x['id']==cid),None)
if card is None:
    # not on the desk this run: rebuild the wait from what desk.json remembers of the card (key, since, lane, ref)
    card={'id':cid,'ref':c.get('ref') or cid.split(':',1)[-1],'lane':c.get('lane') or '','status':c.get('status') or '','key':c.get('key'),'since':c.get('since')}
ref=cid.split(':',1)[1] if ':' in cid else cid
new_caption=None
if '--caption' in sys.argv:
    # 2026-09-23: --caption "<text>" writes the loop caption here and binds the closure to it. Closing first and rewriting
    # the caption afterwards reopened every loop card the same sitting (T-121, ORB-201-KIT came back within minutes).
    i=sys.argv.index('--caption'); new_caption=sys.argv[i+1]; sys.argv=sys.argv[:i]+sys.argv[i+2:]
bound=new_caption if new_caption is not None and cid.startswith(('loop:','gap:')) else None
if verb=='caption':
    c['caption']=' '.join(sys.argv[3:]); d.get('holds',{}).get(deskhold.key_of(card),{}).pop('closed',None)
elif verb=='defer':
    _trig=re.sub(r'^(?:until\s+)+','',sys.argv[3].strip()); _act=sys.argv[4] if len(sys.argv)>4 else card.get('act') or c.get('act','')
    if ' -> ' in _trig and not (len(sys.argv)>4 and sys.argv[4]): _trig,_act=[x.strip() for x in _trig.split(' -> ',1)]
    deskhold.defer(d,card,_trig.strip(),_act,today,since=bound)
else:
    deskhold.close(d,card,verb,today,' '.join(sys.argv[3:]),since=bound)
    if cid.startswith('fast:'): d['pins']=[p for p in d.get('pins',[]) if p['ref']!=cid[5:]]   # a verb unpins a FAST card
    if cid.startswith('task:'):   # a verb ends a local task (tasks.json); a defer keeps it open
        try:
            import task; task.mark_done(cid,f"{verb}: "+' '.join(sys.argv[3:]))
        except Exception as e: print('task not marked done:',e)
if new_caption is not None:
    sp=os.path.expanduser('~/orbit-scratch/agent-loop/state.json')
    try:
        sys.path.insert(0,os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import loopstate; loopstate.revive(ref)   # an archived entry comes back
        st=json.load(open(sp)); thr=st['threads']
        if ref in thr:
            old=(thr[ref].get('status') or '').strip()
            thr[ref]['status']=new_caption
            # 2026-10-13: a caption that says done ends the entry. one entry sat 18 days as a STALE card: validated as
            # 'blocked' (already shipped), the desk captioned it 'done; …' twice, but the reaper only reads running
            # entries and the archive only moves phase done, so nothing ever flipped the phase.
            if re.match(r'(done|closed)\b',new_caption.strip(),re.I) and thr[ref].get('phase') in ('blocked','pr-open'): thr[ref]['phase']='done'
            thr[ref]['note']=((thr[ref].get('note') or '')+f" | desk {today}: "+' '.join(sys.argv[3:])).strip(' |')
            json.dump(st,open(sp,'w'),indent=2,ensure_ascii=False)
            # 2026-10-19: a caption written while closing one card of a thread must not void a hold on a sibling wait of
            # the same thread: every hold of the entity bound to the old caption follows it.
            deskhold.rebind(d,ref,old,new_caption)
            d.setdefault('captions',{})[ref]={'status':new_caption,'at':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    except Exception as e: print('caption not written:',e)
d['log'].append({'date':today,'id':cid,'verb':verb,'note':' '.join(sys.argv[3:]) if verb!='defer' else f"until {sys.argv[3]}"})
json.dump(d,open(P,'w'),indent=1,ensure_ascii=False); print(f"{cid} → {verb}")
# the pane reads the verb from the card itself until the next gather removes it
if verb!='caption':
    try:
        for x in cardsdoc.get('cards',[]):
            if x['id']==cid: x['verb']=verb
        tmp=CARDS+'.close.tmp'; json.dump(cardsdoc,open(tmp,'w'),indent=1,ensure_ascii=False); os.replace(tmp,CARDS)
    except Exception as e: print('cards.json not stamped:',e)
# 2026-10-20: score the prepared brief against what the user did. The brief file goes away with the next prepare pass,
# so the guess is copied here, at the close: briefs/<id>.json recommended option and draft, the pick and the verb.
# desk-score.py reads the ledger. A caption is not a close and is not scored.
if verb!='caption':
    try:
        bp=os.path.join(os.path.dirname(P),'briefs',re.sub(r'[^A-Za-z0-9._-]','_',cid)+'.json'); b=json.load(open(bp))
        if not b.get('error'):
            opts=b.get('options') or []
            with open(os.path.join(os.path.dirname(P),'score.jsonl'),'a') as f:
                f.write(json.dumps({'date':today,'at':datetime.datetime.now().isoformat(timespec='seconds'),'id':cid,'ref':b.get('ref'),'lane':b.get('lane'),
                    'preparedAt':b.get('preparedAt'),'model':b.get('model'),
                    'guess':{'option':(opts[0].get('label') if opts else None),'verb':(opts[0].get('verb') if opts else None),'options':[o.get('label') for o in opts],
                             'draft':b.get('draft') or '','replyReady':bool(b.get('replyReady')),'resolved':bool(b.get('resolved')),'openChecks':len(b.get('openChecks') or [])},
                    'did':{'pick':pick,'verb':verb,'note':' '.join(sys.argv[3:])}},ensure_ascii=False)+'\n')
    except FileNotFoundError: pass
    except Exception as e: print('not scored:',e)
