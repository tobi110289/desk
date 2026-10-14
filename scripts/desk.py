#!/usr/bin/env python3
"""desk.py — gather everything waiting on Tobias, rank it, print the ranked six, keep desk.json.

Usage:
  desk.py                 gather + rank + print (writes desk.json, cards.json)
  desk.py --all           also print every lane in full
  desk.py --preflight     print only the PRE-FLIGHT block (what is due) as JSON
  desk.py --wins          print what was closed today
  desk.py --tenant <id>   scope the sitting to one tenant: only cards whose Plain thread carries that
                          tenant_id (sweep-cache), plus PR cards those threads' loop entries name
  desk.py --today         scope the sitting to the Plain threads created today (Europe/Berlin): every open
                          thread from today is a card, nothing older is (--since YYYY-MM-DD for another day)
  desk.py --event <json>  apply one live event from the adapter's feed to cards.json and stop (deskevent.py; no
                          gather, no pre-flight, no board files; desk-events.py feeds it)
  desk.py T-120 ORB-203 pin one or more Plain threads / Notion tickets: each becomes a FAST card above the
                          ranking (score 1000+) and stays there in every sitting until it gets a verb
                          (`desk-close.py fast:T-120 <verb>` unpins it; `--unpin <ref>` drops one by hand)
Sources: agent-loop state.json + sweep-cache.json, gh (own open PRs on orbit AND orbot), harvested orbot
issues, the Hermes feedback LOG.md,
desk.json (deferrals, seen counts, captions of non-loop cards, done log).
Read-only towards every source except desk.json / cards.json.
"""
import json, re, sys, os, subprocess, datetime, glob
HOME=os.path.expanduser('~')
LOOP=f'{HOME}/orbit-scratch/agent-loop'
DESK=f'{HOME}/orbit-scratch/desk'
DESKFILE=f'{DESK}/desk.json'
HERMES_LOG=f'{HOME}/orbit-scratch/hermes-feedback/LOG.md'
import deskconfig
PLAIN=deskconfig.PLAIN_THREAD
ORBIT=deskconfig.REPO; ORBOT=deskconfig.BOT_REPO
GH=f'https://github.com/{ORBIT}/pull/'; GHO=f'https://github.com/{ORBOT}/'
LOOP_TICK_DUE_HOURS=4          # loop is "due" when nothing wrote the loop state for this long
HARVEST_DUE_OPEN=3             # harvest is "due" at this many open LOG entries …
HARVEST_DUE_AGE_DAYS=7         # … or when the oldest open entry is older than this
SWEEPS=f'{HOME}/orbit-scratch/sentry-sweeps'   # the weekly Sentry sweep's state + reports (desk owns its follow-through since 2026-09-11)
now=datetime.datetime.now(datetime.timezone.utc)
today=now.strftime('%Y-%m-%d')

def load(p,default):
    try: return json.load(open(p))
    except Exception: return default
def parse_iso(iso):
    try: return datetime.datetime.fromisoformat(iso.replace('Z','+00:00'))
    except Exception: return None
def age_days(iso):
    d=parse_iso(iso or ''); return max(0,(now-d).days) if d else 0
def hours_since(dt): return (now-dt).total_seconds()/3600 if dt else None
def gh(args,timeout=40):
    try:
        out=subprocess.run(['gh',*args],capture_output=True,text=True,timeout=timeout).stdout
        return json.loads(out) if out.strip() else []
    except Exception: return None
_deploy_cache={}
def latest_deploy(env):
    """Newest successful GitHub Deployment for env (staging|production): {sha, at}. Cached per run."""
    if env in _deploy_cache: return _deploy_cache[env]
    res=None
    for d in (gh(['api',f'repos/{ORBIT}/deployments?environment={env}&per_page=6'],30) or []):
        sts=gh(['api',f"repos/{ORBIT}/deployments/{d['id']}/statuses?per_page=1"],20) or []
        if sts and sts[0].get('state')=='success': res={'sha':d['sha'],'at':d['created_at']}; break
    _deploy_cache[env]=res; return res
_pr_cache={}
def pr_merge(pr):
    if pr in _pr_cache: return _pr_cache[pr]
    d=gh(['pr','view',str(pr),'--repo',ORBIT,'--json','state,mergeCommit,mergedAt,title'],30) or {}
    _pr_cache[pr]=d; return d
def release_state(env,pr):
    """Is PR #pr contained in the newest successful deployment of env? {landed, deployAt, deploySha, merged}."""
    d=pr_merge(pr); sha=(d.get('mergeCommit') or {}).get('oid')
    dep=latest_deploy(env)
    if not sha or not dep: return {'landed':False,'merged':bool(sha),'deployAt':(dep or {}).get('at'),'deploySha':(dep or {}).get('sha'),'title':d.get('title')}
    # --jq '{s:.status}' keeps the answer valid JSON for gh(); a bare '.status' prints `ahead`,
    # json.loads throws, gh() returns None and every deploy trigger stays dead (found 2026-09-15).
    cmp=(gh(['api',f'repos/{ORBIT}/compare/{sha}...{dep["sha"]}','--jq','{s:.status}'],30) or {}).get('s')
    return {'landed':cmp in ('ahead','identical'),'merged':True,'deployAt':dep['at'],'deploySha':dep['sha'],'title':d.get('title')}
release_watch=[]   # rows for the board: every card parked on a deployed:<env>:<pr> trigger, with its live state

# 2026-10-14: the live feed. `--event` adds or refreshes one card from the event fields alone and exits before any
# source below is read (the gather takes ~45 s, an event must land in under one).
import deskevent
if '--event' in sys.argv:
    _i=sys.argv.index('--event')
    sys.exit(deskevent.main(sys.argv[_i+1] if _i+1<len(sys.argv) else ''))

def a2a_feed():
    """GET /inbox/feed on the A2A hub with this machine's own token. None when the hub or the
    token is unavailable (old hub without the endpoint, no config, network down)."""
    cfg={}
    try:
        for line in open(f'{HOME}/.orbit-a2a/config.env'):
            m=re.match(r'\s*([A-Z0-9_]+)=(.*)$',line.strip())
            if m: cfg[m.group(1)]=m.group(2).strip().strip('"').strip("'").replace('\\ ',' ').replace('\\(','(').replace('\\)',')')
    except Exception: return None
    tok=cfg.get('A2A_TOKEN'); hub=(cfg.get('A2A_HUB_URL') or deskconfig.A2A_HUB).rstrip('/')
    if not tok: return None
    try:
        import urllib.request
        # Cloudflare in front of the hub rejects the bare Python-urllib agent with 403; curl's name passes.
        req=urllib.request.Request(f'{hub}/inbox/feed',headers={'authorization':f'Bearer {tok}','user-agent':'curl/8.0 desk.py'})
        with urllib.request.urlopen(req,timeout=10) as r: return json.load(r)
    except Exception: return None
feed=a2a_feed()
def a2a_cfg():
    cfg={}
    try:
        for line in open(f'{HOME}/.orbit-a2a/config.env'):
            m=re.match(r'\s*([A-Z0-9_]+)=(.*)$',line.strip())
            if m: cfg[m.group(1)]=m.group(2).strip().strip('"').strip("'")
    except Exception: pass
    return cfg
_a2a_humans=None
def a2a_task(tid,agent=None):
    """GetTask (JSON-RPC, A2A-Version 1.0) on the hub for a task I sent. Returns {state,timestamp,from,agent,reply,
    choices} or None when the hub, the token or the task is unreachable. Without an agent name every human agent of
    the registry is tried (the hub answers TASK_NOT_FOUND for the wrong one). 2026-10-13: replaces the feed check."""
    global _a2a_humans
    cfg=a2a_cfg(); tok=cfg.get('A2A_TOKEN'); hub=(cfg.get('A2A_HUB_URL') or deskconfig.A2A_HUB).rstrip('/')
    if not tok: return None
    import urllib.request
    H={'authorization':f'Bearer {tok}','content-type':'application/json','a2a-version':'1.0','user-agent':'curl/8.0 desk.py'}
    if agent: names=[agent]
    else:
        if _a2a_humans is None:
            try:
                with urllib.request.urlopen(urllib.request.Request(f'{hub}/registry.json',headers={'user-agent':'curl/8.0 desk.py'}),timeout=10) as r:
                    _a2a_humans=[a['name'] for a in json.load(r).get('agents',[]) if a.get('name') and a['name']!='orbot' and not a['name'].startswith('tobias')]
            except Exception: return None
        names=_a2a_humans
    for name in names:
        body=json.dumps({'jsonrpc':'2.0','id':1,'method':'GetTask','params':{'id':tid,'tenant':'','historyLength':0}}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(f'{hub}/agents/{name}',data=body,headers=H),timeout=10) as r: res=json.load(r)
        except Exception: return None
        if 'result' not in res: continue   # TASK_NOT_FOUND on this agent: try the next
        t=res['result']; st=(t.get('status') or {}); out={'state':st.get('state'),'timestamp':st.get('timestamp'),'agent':name,'from':None,'reply':None,'choices':None}
        for a in t.get('artifacts') or []:
            md=a.get('metadata') or {}
            txt=' '.join(p.get('text','') for p in a.get('parts') or [] if p.get('text'))
            if md.get('author')=='human' or (a.get('name')=='Reply' and txt):
                out['reply']=txt; out['choices']=md.get('choices'); out['from']=md.get('from') or name; break
        return out
    return None
def a2a_head(t,n=70):
    txt=re.sub(r'\s+',' ',(t.get('text') or '')).strip()
    return txt[:n]+('…' if len(txt)>n else '')
def a2a_age_h(t):
    d=parse_iso((t.get('createdAt') or '').replace(' ','T')+('Z' if 'Z' not in (t.get('createdAt') or '') else ''))
    h=hours_since(d); return h if h is not None else 0

# 2026-10-09: the desk pane reruns this script in the background, so a close can land while it runs. The stamp of
# desk.json at load is checked again under the lock before the write; a changed file means the run is stale.
def _desk_stamp():
    try: return os.stat(DESKFILE).st_mtime_ns
    except OSError: return 0
DESK_STAMP=_desk_stamp()
desk=load(DESKFILE,{'cards':{},'log':[]})
desk.setdefault('cards',{}); desk.setdefault('log',[]); desk.pop('runs',None); desk.pop('preflight',None)   # 2026-09-29: never read
loopstate=load(f'{LOOP}/state.json',{'threads':{}})
# live entries plus the archive (done entries older than 7 days, jev/loopstate.py): the desk still sees old done captions
state={**load(f'{LOOP}/state-archive.json',{'threads':{}}).get('threads',{}),**loopstate.get('threads',{})}
cache=load(f'{LOOP}/sweep-cache.json',{'threads':{}})['threads']
def _ref(v):
    if v.get('ref'): return v['ref']
    m=re.search(r'\[(T-\d+)\]',v.get('row') or ''); return m.group(1) if m else None
byref={_ref(v):dict(v,id=k) for k,v in cache.items() if _ref(v)}

# ---------------------------------------------------------------- support memory (orbot, read locally)
# Tobias 2026-09-24: every customer draft must be informed by the orbot support memory (tenant profile + requester file),
# and what a sitting learns must flow back. Reads come from the local ~/orbot checkout (memory.py pulls main here, so
# a sitting is at most one pull stale); writes queue in memory-proposals.jsonl and go up as one orbot PR (memory:pr card).
sys.path.insert(0,DESK)
try:
    import memory
    MEMORY=memory.pull() if os.environ.get('DESK_MEMORY','1')!='0' else {'ok':False,'head':memory.head(),'reason':'DESK_MEMORY=0'}
    try: MEMORY['synced']=memory.sync()
    except Exception: MEMORY['synced']=[]
except Exception as _e:
    memory=None; MEMORY={'ok':False,'head':'?','reason':str(_e)[:80]}

IMPACT=deskevent.IMPACT
def impact_of(ref):
    r=byref.get(ref)
    if not r: return 'P2',2
    m=re.search(r'impact_level=([A-Za-z0-9-]+)',(r.get('fingerprint') or {}).get('fields',''))
    lvl=m.group(1) if m else 'P2'
    return lvl,IMPACT.get(lvl,1)

# ---------------------------------------------------------------- pre-flight: is an executor due?
def loop_last_write():
    # `updated` is written by the loop only (the desk never touches it, see SKILL.md)
    stamps=[parse_iso(t.get('updated') or '') for t in state.values() if 'desk:' not in (t.get('note') or '')]
    stamps=[s for s in stamps if s]; return max(stamps) if stamps else None
def hermes_open():
    try: text=open(HERMES_LOG).read()
    except Exception: return []
    out=[]
    for m in re.finditer(r'^## (\d{4}-\d{2}-\d{2}) (T-\d+).*· open\s*$',text,re.M):
        out.append({'date':m.group(1),'ref':m.group(2)})
    return out
preflight={'loop':{'due':False},'harvest':{'due':False}}
llw=loop_last_write(); h=hours_since(llw)
respawn=[r for r,t in state.items() if re.search(r'respawn',t.get('status') or '',re.I)]
if h is None or h>=LOOP_TICK_DUE_HOURS or respawn:
    preflight['loop']={'due':True,'reason':(f'last loop write {h:.0f}h ago' if h is not None else 'no loop state')+(f'; respawn wanted on {", ".join(respawn)}' if respawn else '')}
else: preflight['loop']={'due':False,'reason':f'last loop write {h:.1f}h ago'}
ho=hermes_open(); oldest=min((age_days(e['date']+'T00:00:00Z') for e in ho),default=0)
if len(ho)>=HARVEST_DUE_OPEN or (ho and oldest>=HARVEST_DUE_AGE_DAYS):
    preflight['harvest']={'due':True,'reason':f'{len(ho)} open entries ({", ".join(e["ref"] for e in ho)}), oldest {oldest}d','open':ho}
else: preflight['harvest']={'due':False,'reason':f'{len(ho)} open entries, oldest {oldest}d'}
# ---------------------------------------------------------------- sentry sweep state (known-issues.md "## Runs" + the run's report)
def sweep_runs():
    try: text=open(f'{SWEEPS}/known-issues.md').read()
    except Exception: return []
    m=re.search(r'^## Runs\s*\n(.*?)(?=^## |\Z)',text,re.M|re.S)
    if not m: return []
    out=[]
    for line in m.group(1).splitlines():
        if not line.startswith('|') or set(line.replace('|','').strip())<=set('- '): continue
        cells=[c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells)<12 or cells[0].lower().startswith('run id'): continue
        rid=cells[0].strip('`'); rep=re.sub(r'^`|`$','',cells[-2]).replace('~',HOME)
        out.append({'runId':rid,'date':cells[1],'window':cells[2],'scope':cells[3],'status':cells[4].lower(),'report':rep,'page':cells[-1]})
    return out
def sweep_decisions(report):
    """open decisions from `## Decisions needed`, minus the ids already under `## Answered since`."""
    try: text=open(report).read()
    except Exception: return []
    def section(name):
        m=re.search(r'^## '+re.escape(name)+r'\s*\n(.*?)(?=^## |\Z)',text,re.M|re.S); return m.group(1) if m else ''
    answered=set(re.findall(r'\b([BD]\d+)\b',section('Answered since')))
    out=[]
    for m in re.finditer(r'^\d+\.\s+\*\*(.+?)\*\*\s*(.*)$',section('Decisions needed'),re.M):
        bold,rest=m.group(1),m.group(2)
        idm=re.search(r'\b([BD]\d+)\b',bold); did=idm.group(1) if idm else re.sub(r'\W+','-',bold)[:12]
        if did in answered: continue
        owners=re.findall(r'@(\w+)',bold+' '+rest)
        title=re.sub(r'^[BD]\d+\s*[—:-]\s*','',bold).rstrip('.,:')
        out.append({'id':did,'title':title,'text':re.sub(r'\s*@\w+','',rest).strip(),'owners':owners})
    return out
_runs=sweep_runs(); _last=max((r['date'] for r in _runs),default=None); _paused=[r for r in _runs if r['status']=='paused']
_sw_age=age_days(_last+'T00:00:00Z') if _last else None
# 2026-09-29 (Tobias): the Superset cron `weekly-sentry-sweep` is the one trigger; the desk never starts a sweep.
preflight['sweep']={'due':False,'reason':(f'last sweep {_sw_age}d ago' if _sw_age is not None else 'no sweep state')+'; the Superset cron starts it'+(f'; {len(_paused)} paused at the fix pass' if _paused else '')}
if '--preflight' in sys.argv:
    print(json.dumps(preflight,indent=1,ensure_ascii=False)); sys.exit(0)

# ---------------------------------------------------------------- lanes (detectors on the caption contract)
LANES=[
 # name, regex, minutes, who waits, imperative template
 # 2026-09-16 (Tobias: everything waiting on me lands on the desk): the reaper writes `"<line>" staged (yours)`, `worker
 # stalled: Fable limit`, `own: …`, `finished without a PR`, `… postdates validation`, `agent-ready …`; each is one act.
 ('SEND',   r'press Enter|unsent|say "apply the plan"|sign-off|staged',       1, 'agent', 'Send or drop the line you staged in `{task}`'),
 ('REVIEW', r'human-review|changes requested|CONFLICTING|merge on',          15,'agent', 'Review {pr}: {status}'),
 ('REPLY',  r'(?<!sent )\breply\b(?! sent)|answer drafted|Rückmeldung',      3, 'customer','Send the drafted reply on {ref}: {status}'),
 ('RUN',    r'run script|rollout|downward sync|deploy check|create .*ticket|notification ticket|worker stalled|Fable limit|^own:|finished without a PR|postdates validation|agent-ready|respawn', 2, 'nobody','Say go and the agent runs: {status}'),
 ]
# 2026-09-29 (Tobias): questions to teammates go out on ONE channel, OrBot's Ask-Orbit on Slack. The desk ASK lane (A2A
# bundle) is gone; a caption that waits on a teammate's decision is no desk card.
# TODO: route via Ask-Orbit (see orbot adapter orbitInputRequest).
ASK_RE=deskconfig.ASK_PATTERN   # teammate names and the words that mark a wait on a person (config.json askPattern)
def person_of(s):
    for p in deskconfig.PEOPLE:
        if p in s: return p
    return 'unknown'

def _cmp(cid,s):
    """Status as compared for closures and deferral binding. A tenant:<ref> card's status is a fact list that carries
    "last entry <date> by <actor>": every reply changes it, so a closure or deferral written this morning stopped
    matching the moment Tobias answered (T-122/T-123 came back on 2026-09-15). Compare tenant cards without that fact."""
    if cid.startswith('tenant:'): return re.sub(r';\s*last entry [^;]*','',s or '')
    # 2026-09-23: a promise: card's status carries the age in days ("ours last 2026-09-17 (5d, …"); the count ticks over
    # during the day and reopened T-122/T-123 within the hour. Compare without it.
    if cid.startswith('promise:'): return re.sub(r'\(\d+d,','(Nd,',s or '')
    return s or ''
def member_key(m): return f"{m.get('ref')}|{m.get('task') or ''}|{m.get('status') or ''}"   # 2026-09-14: the caption is part of the key, so a member whose staged line changed (T-105 r3 -> r4) reopens the batch
cards=[]
# 2026-10-06 (Tobias): threads listed in ignore.json never become cards (a teammate's sprint threads: he owns them end to
# end, the desk kept surfacing them as FAST/gap cards). An explicit pin (fast:<ref>) still wins.
try: IGNORE=json.load(open(f'{DESK}/ignore.json'))
except Exception: IGNORE={}
def ignored(card):
    if card['id'].startswith('fast:'): return False
    refs={card.get('ref') or ''}|{m.get('ref') or '' for m in (card.get('members') or [])}
    if refs & set(IGNORE.get('refs') or []): return True
    title=(card.get('title') or card.get('status') or '')
    return any(re.search(p,title,re.I) for p in (IGNORE.get('titles') or []))
def add(card):
    if ignored(card): return
    cid=card['id']
    prev=desk['cards'].get(cid,{})
    if prev.get('closed'):
        # a batch stays closed only today and only while its member set has not grown; new members reopen it.
        # a single card stays closed only while its caption is the one it was closed with; a new caption reopens it.
        # Tobias 2026-09-10: a SEND batch closed on 09-09 hid T-107's staged line for a day, so batch closures expire at midnight.
        if not card.get('members'):
            was=prev['closed'].get('status')
            # 2026-09-14: a closure written before captions were recorded (status None) hid T-110 for six days; such a
            # closure counts for its own day only.
            if was is None:
                if prev['closed'].get('date')==today: return
            elif _cmp(cid,card.get('status') or '')==_cmp(cid,was): return
        elif prev['closed'].get('date')==today:
            done=set(prev['closed'].get('members') or [])
            if {member_key(m) for m in card['members']} <= done: return
    if prev.get('caption') and not card.get('status'): card['status']=prev['caption']
    d=prev.get('defer')
    if card.get('screen'):
        # 2026-09-24: a card built from the worker's screen (picker, staged line, sign-off) is a terminal wait, not the
        # thread wait a deferral parks; it neither borrows nor voids one. The hold on the thread stays as written.
        card['seen']=prev.get('seen',0)+(0 if prev.get('lastSeen')==today else 1); cards.append(card); return
    # 2026-09-14: loop:<ref>, gap:<ref> and bare <ref> are one thread and one act; a deferral on any of them parks all
    # (T-119 sat parked as gap:T-119 and came back as loop:T-119 the moment its caption said "reply").
    # 2026-09-17: a pinned card that carries a decision never borrows a sibling's wait; the decision ended it
    if not d and not card.get('decided'):
        _ref=card.get('ref') or ''
        for _sib in (f'loop:{_ref}',f'gap:{_ref}',_ref):
            _sd=desk['cards'].get(_sib,{}).get('defer') if _sib!=cid else None
            if _sd:
                _st=_sd.get('status'); _cur=card.get('status') or ''
                if _st is not None:
                    _st=re.sub(r'^loop done, thread TODO: ','',_st); _cur=re.sub(r'^loop done, thread TODO: ','',_cur)
                    if _st!=_cur: continue
                d={**_sd,'status':card.get('status'),'borrowed':_sib}; break
    # a deferral belongs to the caption it was written for: when the loop writes a new caption the wait is over
    # (2026-09-10: T-108 parked on deployed:production:2955 for the "ist live" reply hid a staged line of Tobias's).
    if d and d.get('status') is not None and _cmp(cid,card.get('status') or '')!=_cmp(cid,d['status']) and not card.get('rebuilt'):
        import os as _os
        if _os.environ.get('DESK_DEBUG'): print(f"VOID {cid}: card status={card.get('status')!r} bound={d['status']!r}",file=sys.stderr)
        desk['cards'].setdefault(cid,{}).pop('defer',None); prev=desk['cards'][cid]
        card['note']=f"deferral voided (caption changed since {d.get('since')}, was until {d.get('trigger')}): "+(card.get('note') or ''); d=None
    if d:
        trig=d.get('trigger','')
        # 2026-10-12: older closes stored 'until <trigger> -> <act>' in the trigger field, so appstore:/deployed:/manual:
        # never matched (four parked cards sat past their release). Normalise here and on write.
        trig=re.sub(r'^(?:until\s+)+','',trig.strip())
        if ' -> ' in trig:
            trig,_act=trig.split(' -> ',1)
            if not d.get('act'): d['act']=_act.strip()
        trig=trig.strip()
        fired=False
        if re.match(r'\d{4}-\d{2}-\d{2}$',trig): fired = today>=trig
        elif re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$',trig): fired = now.strftime('%Y-%m-%dT%H:%MZ')>=trig
        elif trig.startswith('pr-merged:'):
            n=trig.split(':')[1]; repo=ORBOT if 'orbot' in trig else ORBIT
            st=gh(['pr','view',n,'--repo',repo,'--json','state'],20) or {}
            fired = st.get('state')=='MERGED'
        elif trig.startswith('appstore:'):
            # appstore:<bundleId>>=<version> fires once the App Store lists that version or newer (iTunes lookup, DE storefront)
            try:
                import urllib.request
                bid,ver=trig[len('appstore:'):].split('>=',1)
                with urllib.request.urlopen(f'https://itunes.apple.com/lookup?bundleId={bid}&country=de',timeout=15) as r:
                    res=json.load(r).get('results') or []
                live=(res[0].get('version') if res else '0') or '0'
                fired=[int(x) for x in live.split('.')]>=[int(x) for x in ver.split('.')]
                if fired:
                    # 2026-09-16: an App Store release landed: same treatment as a deploy, the card comes back on top with the deferral's act
                    card['status']=f"Cockpit {live} in the App Store (>= {ver}): customer not told"
                    card['act']=d.get('act') or f"Cockpit {live} is in the stores: tell the customer, then close the thread"
                    card['lane']='NOTIFY' if card['lane'] in ('NOTIFY','STALE','REPLY','RUN') else card['lane']
                    card['score']=900; card['released']=True
            except Exception: fired=False
        elif trig.startswith('deployed:'):
            # deployed:<env>:<pr> fires once the PR's merge commit is inside the newest successful Deployment of env
            try: _,env,prn=trig.split(':',2); rs=release_state(env,int(prn))
            except Exception: rs={'landed':False}
            fired=rs.get('landed',False)
            release_watch.append({'id':cid,'ref':card.get('ref'),'thread':card.get('thread'),'pr':int(prn) if prn.isdigit() else None,'env':env,
                                  'landed':fired,'merged':rs.get('merged'),'deployAt':rs.get('deployAt'),'since':d.get('since') or prev.get('lastSeen'),'act':d.get('act','')})
            if fired:
                # a release landed: the card comes back on top, whatever its lane score was
                card['status']=f"landed on {env} {(rs.get('deployAt') or '')[:16]}Z (PR #{prn}): customer not told"
                card['act']=d.get('act') or f"Release landed on {env} (PR #{prn}): tell the customer, then close the thread"
                card['lane']='NOTIFY' if card['lane'] in ('NOTIFY','STALE','REPLY','RUN') else card['lane']
                card['score']=900; card['released']=True
        elif trig.startswith('manual:') and (card.get('thread') or state.get(card.get('ref') or '',{}).get('threadId')):
            # 2026-09-17 (T-116): a hold "until Sam answers" never fired when Sam answered, because a customer reply
            # rewrites no caption and the desk read no Plain for parked threads. A manual hold on a thread now fires by itself
            # when a customer-visible customer message is newer than the hold.
            try:
                _tid=card.get('thread') or state.get(card.get('ref'),{}).get('threadId')
                _q='query($id:ID!){ thread(threadId:$id){ status timelineEntries(first:8){ edges{ node{ timestamp{ iso8601 } actor{ __typename } entry{ __typename ... on ChatEntry{ chatText: text } ... on EmailEntry{ textContent } ... on SlackMessageEntry{ slackText: text } ... on SlackReplyEntry{ replyText: text } } } } } } }'
                _d=plain_gql(_q,{'id':_tid}) or {}
                _since=(d.get('since') or '0000-00-00')
                for _ed in (_d.get('thread') or {}).get('timelineEntries',{}).get('edges',[]):
                    _x=_ed['node']; _e=_x['entry']
                    if _x['actor']['__typename']=='CustomerActor' and _e['__typename'] in MSG_ENTRIES and _x['timestamp']['iso8601'][:10]>_since:
                        _txt=re.sub(r'\s+',' ',(_e.get('chatText') or _e.get('textContent') or _e.get('slackText') or _e.get('replyText') or '').strip())[:90]
                        card['status']=f"customer answered {_x['timestamp']['iso8601'][:16]}Z: {_txt}"
                        card['act']=(d.get('act') or card.get('act') or '')+f" (hold was: {trig[7:][:60]})"
                        card['lane']='REPLY' if card['lane'] in ('STALE','NOTIFY','RUN','PROMISE') else card['lane']
                        card['score']=900; card['released']=True; fired=True; break
            except Exception: fired=False
        elif trig.startswith('a2a:'):
            # 2026-10-13 (Tobias: "make sure the desk picks up a2a answers automatically"): the desk reads the answer
            # itself. The hub feed's waitingOnOthers was empty for an answered task, so the old check only dropped the
            # hold and the card fell back to its ordinary score with its old act; a teammate's answered decision sat under
            # a TASK card at score 20 until Tobias asked. GetTask on the hub is the authority: a completed task with a
            # human reply fires and the card carries the reply (status, act from the deferral, score 900 like a deploy);
            # a cancelled task fires too, since the wait is void; an open task or an unreadable hub keeps the card parked.
            # Trigger forms: a2a:<taskId> (every registry human is tried) or a2a:<agent>:<taskId>.
            _parts=trig.split(':'); tid=_parts[-1]; _agent=_parts[1] if len(_parts)==3 else None
            _t=a2a_task(tid,_agent)
            if _t and _t.get('state') in ('TASK_STATE_COMPLETED','TASK_STATE_CANCELED'):
                fired=True
                _who=_t.get('from') or _t.get('agent') or 'teammate'; _ts=(_t.get('timestamp') or '')[:16]
                if _t.get('reply'):
                    _head=re.sub(r'\s+',' ',_t['reply']).strip()[:120]
                    _ch=', '.join(f"{k}={v}" for k,v in (_t.get('choices') or {}).items())
                    card['status']=f"{_who} answered {_ts}Z"+(f" ({_ch})" if _ch else '')+f": {_head}"
                else:
                    card['status']=f"a2a task {tid[:8]} {'cancelled' if _t['state'].endswith('CANCELED') else 'closed without a reply'}: decide without {_who}"
                card['act']=(d.get('act') or card.get('act') or '')+f" (a2a {tid[:8]} answered; full reply in cards.json a2a_reply)"
                card['a2a_reply']=_t.get('reply'); card['a2a_choices']=_t.get('choices'); card['a2a_task']=tid
                card['lane']='REPLY' if card['lane'] in ('STALE','NOTIFY','RUN','PROMISE') else card['lane']
                card['score']=900; card['released']=True
            elif _t is None:
                card['note']=f"a2a task {tid[:8]} unreadable on the hub (still parked). "+(card.get('note') or '')
        if not fired:
            card['deferred']=d; card['score']=-1
    card['seen']=prev.get('seen',0)+(0 if prev.get('lastSeen')==today else 1)
    cards.append(card)

# ---------------------------------------------------------------- source 1: agent-loop state
AGENT_OWNED=r'agent polling|agent working|agent on HORST|\(no action\)|worker \d+%|in HORST queue|^sent[ :"]|; agent wo'
STALE_AGENT_HOURS=6
_prh_doc=None
def _my_prs():
    """Own open orbit PRs. 2026-09-29: read from desk/pr-health.json (jev/pr_health.py, the one PR cache the tick, the
    board and the desk share), rebuilt when older than 15 min; the plain gh list below is the fallback when it fails."""
    global _prh_doc
    try:
        import sys as _s, os as _o; _s.path.insert(0,_o.path.expanduser('~/orbit-scratch/agent-loop/jev')); import pr_health as _p
        _prh_doc=_p.load(900) or _p.build()
        return {int(n):{'ci':'red' if r.get('ci_red') else 'ok','labels':r.get('labels') or [],'review':'' if r.get('review') in (None,'-') else r['review'],'draft':bool(r.get('draft'))} for n,r in _prh_doc['prs'].items()}
    except Exception as _e:
        print(json.dumps({'desk':'pr_health_failed','error':str(_e)[:160]}),file=sys.stderr)
    out={}
    for p in gh(['pr','list','--repo',ORBIT,'--state','open','--author','@me','--limit','50','--json','number,labels,statusCheckRollup,reviewDecision,isDraft']) or []:
        checks=p.get('statusCheckRollup') or []
        red=any((c.get('conclusion') or c.get('state')) in ('FAILURE','ERROR') for c in checks)
        out[p['number']]={'ci':'red' if red else 'ok','labels':[l['name'] for l in p['labels']],'review':p.get('reviewDecision') or '','draft':bool(p.get('isDraft'))}
    return out
# 2026-09-23: Jev lane hints, promote-only (~/orbit-scratch/agent-loop/jev/desk_lanes.py; JEV_DESK=0 disables). The regex
# detectors keep the first word; Jev only lifts a row the regex left unmatched into a real lane at confidence >= 0.85
# (T-105 "awaits human review" -> REVIEW, T-109 "waiting on Kim" -> ASK, T-125 "Robin: verify" -> ASK). It never hides a card.
try:
    import sys as _sys, os as _os; _sys.path.insert(0,_os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import desk_lanes as _jev
    _jev.prefetch([(r,t) for r,t in state.items() if t.get('phase') in ('handoff-running','pr-open','blocked')])
except Exception:
    class _jev:
        @staticmethod
        def lane(ref): return None
        @staticmethod
        def person(ref): return None
my_prs=_my_prs()
# ---------------------------------------------------------------- source 1a: the worker terminal is the truth (2026-09-24)
# Tobias, 2026-09-24: all 16 "with agents / HORST" rows were idle at a picker or a typed, unsent line, three of their PRs
# had merged, and the desk showed none of it: the caption rules trusted "agent working" for 6 h from the loop's own
# `updated` stamp and skipped pr-open / handoff-running rows altogether. Now every in-flight entry with a terminal is
# read through superset before ranking (jev/desk_screens.py, ~5 s for 24 rows; DESK_SCREENS=0 skips it) and the screen
# decides the card, whatever the caption says: a picker or a staged line is a SEND member, an idle worker whose PR
# waits on HORST's changes gets a prefilled SEND line, a merged PR retires, a dead worker is a RUN card, a working one hides.
_screens={}
if os.environ.get('DESK_SCREENS','1')!='0':
    try:
        import sys as _sys3, os as _os3; _sys3.path.insert(0,_os3.path.expanduser('~/orbit-scratch/agent-loop/jev')); import desk_screens as _ds
        _screens=_ds.cached(900) or _ds.screen(state)   # the tick's screen cache when fresh
    except Exception as _e:
        print(json.dumps({'desk':'screens_failed','error':str(_e)[:160]}),file=sys.stderr)
def _pr_facts(pr):
    """{'merged':bool,'review':str,'labels':[...],'draft':bool} for a PR of a live entry; merged PRs are not in my_prs."""
    if not pr: return None
    if pr in my_prs: return {'merged':False,**my_prs[pr]}
    tr=((_prh_doc or {}).get('tracked') or {}).get(str(pr))
    if tr: return {'merged':tr.get('state')=='MERGED','review':'','labels':[],'draft':False,'mergedAt':tr.get('mergedAt')}
    d=pr_merge(pr); return {'merged':d.get('state')=='MERGED','review':'','labels':[],'draft':False,'mergedAt':d.get('mergedAt')}
def screen_card(ref,t,x):
    """One card from a classified screen, or 'hide' (agent working / waiting on HORST), or None (fall back to the caption rules)."""
    st=x.get('state'); pr=t.get('pr'); task=t.get('task') or ref; lvl,iw=impact_of(ref)
    base={'id':f'loop:{ref}','ref':ref,'impact':lvl,'age':age_days(t.get('updated') or ''),'pr':pr,'task':task,'workspace':t.get('workspace'),'host':t.get('host'),'terminal':t.get('terminal'),
          'thread':(byref.get(ref) or {}).get('id') or t.get('threadId'),'note':(t.get('note') or '')[:400],'screen':st,'screenAt':x.get('capturedAt')}
    if st=='mid_turn_working': return 'hide'
    pf=_pr_facts(pr)
    if st=='staged_line_typed':
        line=x.get('staged') or ''
        return {**base,'lane':'SEND','mins':1,'waits':'agent','status':f'"{line[:60]}" staged (yours)','staged':line,
                'act':f'Send or drop the line staged in `{task}`: "{line[:120]}"','score':50+iw}
    if st=='picker_or_question_waiting':
        opts=x.get('options') or []; q=x.get('question') or ''
        return {**base,'lane':'SEND','mins':1,'waits':'agent','status':f'sign-off: picker open: {(opts[0] if opts else q)[:50]}','picker':opts,'question':q,
                'act':f'Answer the picker in `{task}`: {q[:90]} → '+' / '.join(f'{i+1}. {o[:40]}' for i,o in enumerate(opts[:4])),'score':50+iw}
    if st=='sign_off_waiting':
        q=x.get('question') or ''
        return {**base,'lane':'SEND','mins':1,'waits':'agent','status':f'sign-off: {q[:60]}','question':q,
                'act':f'Answer `{task}`, it stopped for you: {q[:140]}','score':50+iw}
    if st=='gone' and x.get('sentinel') and not pr:
        return {**base,'lane':'RUN','mins':2,'waits':'nobody','status':'finished without a PR','act':f'Worker of {ref} finished without a PR: read its summary, then respawn or retire the entry','score':30+iw}
    if st in ('quota_limit','onboarding_or_trust_prompt','shell_prompt_no_agent','gone'):
        why={'quota_limit':'model limit reached','onboarding_or_trust_prompt':'onboarding prompt','shell_prompt_no_agent':'bare shell, no Claude UI','gone':'terminal unreadable'}[st]
        return {**base,'lane':'RUN','mins':2,'waits':'nobody','status':f'worker stalled: {why}','act':f'Worker of {ref} {f"#{pr}" if pr else ""} stalled ({why}): respawn it or retire the entry','score':30+iw}
    if st in ('idle_empty_prompt','finished_summary'):
        if pf and pf.get('merged'):
            return {**base,'lane':'RUN','mins':1,'waits':'nobody','status':f'PR #{pr} merged {(pf.get("mergedAt") or "")[:10]}; entry not retired',
                    'act':f'#{pr} merged: write the caption "merged …" so the loop retires {ref}, then answer the worker\'s last ask or drop it','score':30+iw}
        if pf and not pf['draft'] and pf.get('review')=='CHANGES_REQUESTED' and 'auto-review' not in pf['labels'] and 'needs-human-review' not in pf['labels'] and 'needs-uat' not in pf['labels']:   # 2026-09-25: needs-uat = HORST's UAT queue, not a request for a person
            line=f"Address HORST's latest review on PR #{pr}, then run the /babysit skill for it. Report back here when you stop."
            return {**base,'lane':'SEND','mins':1,'waits':'agent','status':f'idle; HORST changes requested on #{pr}, no auto-review','staged':line,'prefilled':True,
                    'act':f'Worker of {ref} idles while #{pr} waits on HORST\'s changes: send "{line[:70]}…"','score':40+iw}
        if pf and 'needs-human-review' in pf['labels']: return None   # the prh:<pr> REVIEW card owns it
        if pf and pf.get('ci')=='red':
            line=f"CI is red on PR #{pr}: read the failing job, fix or rerun, then run the /babysit skill for it. Report back here when you stop."
            return {**base,'lane':'SEND','mins':1,'waits':'agent','status':f'idle; CI red on #{pr}','staged':line,'prefilled':True,'act':f'Worker of {ref} idles while #{pr} is red: send the fix line','score':40+iw}
        return 'hide'   # idle with the PR in HORST's queue or approved: nothing for a person to do
    return None
for ref,t in state.items():
    ph=t.get('phase'); st=(t.get('status') or '').strip(); note=(t.get('note') or '')
    _x=_screens.get(ref)
    if _x and _x.get('state')=='gone' and ph in ('handoff-running','pr-open','blocked'):
        # 2026-09-29: an unreadable terminal is settled by the cache's box facts, not by a hand read (CARDS.md RUN)
        if _x.get('sentinel') or _x.get('tmuxAlive') is False: _x={**_x,'conf':1.0}
        elif _x.get('tmuxAlive') is True: continue   # detached terminal, agent alive: the loop re-attaches
    if _x and (_x.get('conf') or 0)>=0.85 and ph in ('handoff-running','pr-open','blocked'):
        _c=screen_card(ref,t,_x)
        if _c=='hide': continue
        if _c: add(_c); continue
    if ph=='pr-open' and t.get('pr') and not t.get('terminal'):
        # 2026-09-17: a pr-open entry with no terminal has no agent; it reaches the desk when CI is red or nothing
        # queues it for HORST (#3130 sat red for 19h, #3162 finished with no auto-review label).
        pi=my_prs.get(t['pr'])
        if pi and (pi['ci']=='red' or 'auto-review' not in pi['labels']):
            why='CI red' if pi['ci']=='red' else 'no auto-review label'
            add({'id':f'loop:{ref}','ref':ref,'lane':'RUN','mins':2,'impact':impact_of(ref)[0],'age':age_days(t.get('updated') or ''),'waits':'nobody',
                 'pr':t['pr'],'task':t.get('task'),'workspace':None,'host':None,'terminal':None,'thread':(byref.get(ref) or {}).get('id') or t.get('threadId'),
                 'status':st,'note':note[:400],'act':f"#{t['pr']} has no agent and {why}: own it (fix CI / add auto-review) or spawn a babysit",'score':round((impact_of(ref)[1]+2)/2*10,1)})
        continue
    if ph not in ('blocked','done'): continue
    if ph=='done' and not re.search(r'reply|open|pending|release|sync|rollout|deploy',st): continue
    if ph=='done' and re.search(r'(^|[;:] )done\s*$',st): continue   # 2026-09-16: a done entry whose caption ends in 'done' waits on nobody (T-111 came back daily as STALE)
    if re.search(r'reply sent',st) and not re.search(r'\+ ',st): continue
    _dc=(desk.get('captions') or {}).get(ref) or {}
    _stamps=[parse_iso(t.get('updated') or '')]+([parse_iso(_dc.get('at') or '')] if _dc.get('status')==st else [])
    silent=hours_since(max(x for x in _stamps if x)) if any(_stamps) else 0   # desk-caption.py stamps its own rewrites
    if re.search(AGENT_OWNED,st):
        # 2026-09-17 (Tobias: 20 open PRs, none on the desk): a caption that says an agent owns the work is only trusted
        # for STALE_AGENT_HOURS. Nine "agent working" rows were 23h old; the terminals held unsent lines, a Fable limit
        # and two dead sessions. Older than that, the caption is a claim to verify, not a reason to skip.
        if silent<STALE_AGENT_HOURS: continue
        lane,mins,waits,tpl='RUN',2,'nobody','Read the terminal of {ref} {pr}: caption "{status}" is '+f'{silent:.0f}h old'+'; rewrite it (staged / stalled / working)'
    else:
        lane,mins,waits,tpl='STALE',2,'nobody','Re-read {ref} and decide: keep (with a status) or drop'
        for name,pat,m,w,tp in LANES:
            if re.search(pat,st):
                lane,mins,waits,tpl=name,m,w,tp; break
        else:
            if re.search(ASK_RE,st): continue   # waits on a teammate (ASK_RE above)
            _jl=_jev.lane(ref)   # regex matched nothing: Jev may promote the row into a real lane (never into NONE)
            if _jl=='ASK': continue
            if _jl: lane,mins,waits,tpl=next((n,m,w,tp) for n,_,m,w,tp in LANES if n==_jl)
    lvl,iw=impact_of(ref)
    age=age_days(t.get('updated') or '')
    waitw={'customer':3,'person':2,'agent':2,'nobody':0}[waits]
    cod=iw+waitw+min(age/3,2)
    pr=t.get('pr')
    card={'id':f'loop:{ref}','ref':ref,'lane':lane,'mins':mins,'impact':lvl,'age':age,'waits':waits,
          'pr':pr,'task':t.get('task'),'workspace':t.get('workspace'),'host':t.get('host'),'terminal':t.get('terminal'),
          'thread':(byref.get(ref) or {}).get('id') or t.get('threadId'),
          'status':st,'note':note[:400],
          'act':tpl.format(task=t.get('task') or ref,pr=f'#{pr}' if pr else '',status=st or '(no caption)',ref=ref,person=(person_of(st+note) if person_of(st+note)!='unknown' else (_jev.person(ref) or 'unknown'))),
          'score':round(cod/mins*10,1)}
    add(card)

# ---------------------------------------------------------------- source 2: own open PRs (orbit + orbot)
def _cached_prs(repo):
    """Open PRs by us from the PR cache (orbit: .prs, orbot: .orbot) in gh's list shape, or None without a cache."""
    if not _prh_doc: return None
    if repo==ORBOT: return _prh_doc.get('orbot')
    return [{'number':r['pr'],'title':r['title'],'labels':[{'name':l} for l in r.get('labels') or []],'reviewDecision':None if r.get('review') in (None,'-') else r['review'],
             'mergeable':r.get('mergeable'),'updatedAt':r.get('updatedAt') or r.get('created'),'isDraft':r.get('draft')} for r in _prh_doc.get('prs',{}).values()]
def pr_cards(repo,prefix,url):
    prs=_cached_prs(repo)
    if prs is None: prs=gh(['pr','list','--repo',repo,'--state','open','--author','@me','--json','number,title,labels,reviewDecision,mergeable,updatedAt,isDraft']) or []
    # 2026-09-14: every loop entry's PR counts as covered, parked ones included, or a deferred loop card resurfaces as a bare REVIEW card (#2948)
    covered=({c['pr'] for c in cards if c.get('pr')} | {t.get('pr') for t in state.values() if t.get('pr')}) if repo==ORBIT else set()
    for p in prs:
        n=p['number']
        if n in covered or p.get('isDraft'): continue
        labels=[l['name'] for l in p['labels']]
        needs = p.get('reviewDecision')=='CHANGES_REQUESTED' or p.get('mergeable')=='CONFLICTING' or 'needs-human-review' in labels
        if not needs: continue
        st=', '.join(x for x in [p.get('reviewDecision') if p.get('reviewDecision')!='UNKNOWN' else None,p.get('mergeable') if p.get('mergeable')=='CONFLICTING' else None,'needs-human-review' if 'needs-human-review' in labels else None] if x)
        age=age_days(p['updatedAt']); ref=f'{prefix}#{n}'
        add({'id':f'{"gh" if repo==ORBIT else "orbot"}:{n}','ref':ref,'lane':'REVIEW','mins':15,'impact':'-','age':age,'waits':'agent','pr':n,'thread':None,'url':f'{url}{n}','repo':repo,
             'status':st,'note':p['title'],'act':f'Decide the fate of {ref} ({st}): rework, hand to an agent, or close','score':round((2+2+min(age/3,2))/15*10,1)})
pr_cards(ORBIT,'',GH)
pr_cards(ORBOT,'orbot',f'{GHO}pull/')

# ---------------------------------------------------------------- source 2b: PR health (facts a caption cannot hide)
# 2026-09-23 (Tobias: "5 waiting for me, 22 open PRs, something is wrong"): eleven PRs had 8-14 HORST rounds and nine
# had no commit for five days while their loop captions said "agent working", so the 6 h trust rule hid them. Facts
# trigger (rounds >= 5, age >= 7 d, no commit >= 5 d, human review asked); Jev judges the round trajectory and
# recommends the move. One batched STUCK card, one REVIEW card per PR that asks for a human. ~/orbit-scratch/agent-loop/jev/pr_health.py.
try:
    import sys as _sys2, os as _os2; _sys2.path.insert(0,_os2.path.expanduser('~/orbit-scratch/agent-loop/jev')); import pr_health as _prh
    for _c in _prh.cards(_prh_doc or _prh.build()):
        if _c.get('pr') and any(k.get('pr')==_c['pr'] and k.get('lane')=='REVIEW' for k in cards): continue   # the loop card already asks for the review
        add(_c)
except Exception as _e:
    print(json.dumps({'desk':'pr_health_failed','error':str(_e)[:160]}), file=sys.stderr)

# source 3 (harvested orbot issues, ORBOT lane) removed 2026-09-29: its act fed a run workspace, and runs[] is gone.

# ---------------------------------------------------------------- source 5: A2A inbox feed (decisions and messages others put to me)
if feed:
    lookups_waiting=[]
    for t in feed.get('waitingOnMe',[]):
        kind=t.get('kind') or 'lookup'; h=a2a_age_h(t)
        if kind=='message':
            add({'id':f"a2a:{t['taskId']}",'ref':f"@{t.get('from')}",'lane':'INBOX','mins':1,'impact':'P2','age':int(h//24),'waits':'agent','pr':None,'thread':None,'url':None,
                 'a2a':{'taskId':t['taskId'],'from':t.get('from'),'kind':kind,'acked':bool(t.get('acked')),'points':0,'decision':None},
                 'status':f"{'acked' if t.get('acked') else 'unseen'} · message · {h:.0f}h",'note':(t.get('text') or '')[:400],
                 'act':f"Read {t.get('from')}'s message ({h:.0f}h old): {a2a_head(t)}",'score':round((2+2+min(h/72,2))*10,1)})
            continue
        if kind!='decision':
            if h>=1: lookups_waiting.append(t)
            continue
        pts=(t.get('decision') or {}).get('points') if isinstance(t.get('decision'),dict) else None
        n=len(pts) if pts else max(1,len(re.findall(r'(?m)^\s*\*?\d+[a-z]?[.)]',t.get('text') or '')))
        add({'id':f"a2a:{t['taskId']}",'ref':f"@{t.get('from')}",'lane':'INBOX','mins':2*n,'impact':'P1','age':int(h//24),'waits':'agent','pr':None,'thread':None,'url':None,
             'a2a':{'taskId':t['taskId'],'from':t.get('from'),'kind':kind,'acked':bool(t.get('acked')),'points':n,'decision':t.get('decision')},
             'status':f"{'acked' if t.get('acked') else 'unseen'} · {n} point{'s' if n!=1 else ''} · {h:.0f}h",'note':(t.get('text') or '')[:400],
             'act':f"Answer {t.get('from')}'s decision ({n} point{'s' if n!=1 else ''}, {h:.0f}h old): {a2a_head(t)}",'score':round((4+2+min(h/72,2))/(2*n)*10,1)})
    if lookups_waiting:
        add({'id':'a2a:poller','ref':'a2a poller','lane':'RUN','mins':2,'impact':'P1','age':0,'waits':'agent','pr':None,'thread':None,'url':None,
             'status':f'{len(lookups_waiting)} lookup(s) waiting over an hour; the poller is not answering','note':'\n'.join(a2a_head(t) for t in lookups_waiting[:5]),
             'act':'Check the inbox poller (launchctl list | grep a2a-inbox, ~/.orbit-a2a/poll.log), then run /a2a-inbox','score':30.0})

# ---------------------------------------------------------------- source 6: Plain threads whose fix is live but the customer was not told
# The orbit CI (.github/scripts/plain-fix-lifecycle.sh) posts a FIX_DEPLOYED note on every linked thread when a stage
# deploy succeeds and wakes the thread up. A thread that still carries no customer-visible message from us after that
# note is a NOTIFY card: draft the reply, Tobias says send, the send marks the thread done.
sys.path.insert(0,os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import plain as _plain
MSG_ENTRIES={'EmailEntry','ChatEntry','SlackMessageEntry','SlackReplyEntry','MSTeamsMessageEntry','DiscordMessageEntry'}
FIX_DEPLOYED=re.compile(r'<!-- FIX_DEPLOYED env=(\w+) sha=(\S+) at=(\S+) prs=(\S*) run=(\S*) -->')
def plain_gql(q,variables):
    if not os.environ.get('PLAIN_API_KEY'): return None
    return _plain.try_gql(q,variables)   # the one Plain reader: ~/orbit-scratch/agent-loop/jev/plain.py (sets a User-Agent Cloudflare accepts)
def notify_cards():
    """Refs that got a NOTIFY card. Two phases: page every TODO thread with its fields (cheap), then read the newest
    12 timeline entries of the ones with a github_pr field. Newest entry first: the first FIX_DEPLOYED marker wins,
    and any message from us seen before it means the customer was already told."""
    q1='query($after:String){ threads(first:100, after:$after, filters:{statuses:[TODO,SNOOZED]}) { pageInfo{ hasNextPage endCursor } edges{ node{ id ref title threadFields{ key stringValue } } } } }'
    q2='query($id:ID!){ thread(threadId:$id){ timelineEntries(first:12){ edges{ node{ timestamp{ iso8601 } actor{ __typename } entry{ __typename ... on NoteEntry{ markdown } } } } } } }'
    after=None; cands=[]
    while True:
        d=plain_gql(q1,{'after':after})
        if not d: return set()
        for e in d['threads']['edges']:
            n=e['node']; fields={f['key']:f.get('stringValue') for f in n['threadFields']}
            if fields.get('github_pr'): cands.append((n,fields))
        if not d['threads']['pageInfo']['hasNextPage']: break
        after=d['threads']['pageInfo']['endCursor']
    refs=set()
    for n,fields in cands:
        d=plain_gql(q2,{'id':n['id']})
        if not d or not d.get('thread'): continue
        told=False; mark=None
        for ed in d['thread']['timelineEntries']['edges']:
            x=ed['node']; typ=x['entry']['__typename']
            m=FIX_DEPLOYED.search(x['entry'].get('markdown') or '') if typ=='NoteEntry' else None
            if m:
                if m.group(1)=='production' or fields.get('stage')=='staging':
                    mark={'env':m.group(1),'at':m.group(3),'prs':m.group(4)}; break
                continue
            if x['actor']['__typename'] in ('UserActor','MachineUserActor') and typ in MSG_ENTRIES: told=True
        if not mark or told: continue
        ref=n['ref']; lvl=fields.get('impact_level') if fields.get('impact_level') in IMPACT else 'P2'
        age=age_days(mark['at']); prs=mark['prs'] or '?'
        add({'id':f'notify:{ref}','ref':ref,'lane':'NOTIFY','mins':3,'impact':lvl,'age':age,'waits':'customer',
             'pr':(mark['prs'].split(',')[0] or None),'task':None,'thread':n['id'],'url':PLAIN+n['id'],
             'status':f"live on {mark['env']} since {mark['at'][:16]}Z; PR #{prs}; customer not told",'note':(n.get('title') or '')[:200],
             'act':f"Tell {ref} the fix is live on {mark['env']} (PR #{prs}): draft the reply, then send",
             'score':round((IMPACT.get(lvl,1)+3+min(age/3,2))/3*10,1)})
        refs.add(ref)
    return refs
notify_refs=notify_cards()
# a loop REPLY card on the same thread is the older fact; the NOTIFY card carries the deploy
cards[:]=[c for c in cards if not (c['lane']=='REPLY' and c['ref'] in notify_refs)]

# ---------------------------------------------------------------- source 7: TODO-gap bucket B (loop done, Plain thread still TODO)
# todo-gap.py sorts every Plain TODO thread into buckets; B = the loop's entry is done (fix merged or shipped) but the
# thread is still TODO: the customer was never told or the thread never closed. One NOTIFY card per thread, id gap:<ref>,
# so a parked loop:<ref> deferral does not hide it. Asked for by Tobias 2026-09-10.
try: subprocess.run(['python3',f'{DESK}/todo-gap.py'],capture_output=True,timeout=60)
except Exception: pass
try:
    gap_md=open(f'{DESK}/todo-gap.md').read()
    sec=re.search(r'^## B\..*?$(.*?)(?=^## |\Z)',gap_md,re.S|re.M)
    for m in re.finditer(r'- \[(T-\d+)\]\((https://[^)]+/thread/(th_\w+))\) (\S+) · (.*?) · (\S*) · `([^`]*)`',sec.group(1) if sec else ''):
        ref,url,tid,lvl,title,_date,st=m.groups()
        if ref in notify_refs: continue
        lvl=lvl if lvl in IMPACT else 'P2'; t=state.get(ref,{}); age=age_days(t.get('updated') or '')
        add({'id':f'gap:{ref}','ref':ref,'lane':'NOTIFY','mins':3,'impact':lvl,'age':age,'waits':'customer',
             'pr':t.get('pr'),'task':None,'thread':tid,'url':url,
             'status':f"loop done, thread TODO: {(t.get('status') or st)[:60]}",'note':title[:200],
             'act':f"Close the loop on {ref} ({title[:60]}): tell the customer where the fix stands, or close the thread",
             'score':round((IMPACT.get(lvl,1)+3+min(age/3,2))/3*10,1)})
except Exception: pass


# ---------------------------------------------------------------- source 8: snoozed threads that end with OUR message (PROMISE)
# Tobias 2026-09-17 (T-117): "checke ich" auto-snoozes a thread as "waiting for customer"; the loop tracks TODO only and
# the board never shows it, so a promise of ours sat for 10 days (T-104: 169 days). A scan found 17 of 30 snoozed threads
# ending with our own message. Detector: SNOOZED thread whose newest customer-visible message is ours (or the customer's,
# unanswered) and whose loop entry is absent or done. One card per thread, id promise:<ref>; the status carries the last
# visible entry's timestamp, so a verdict (close / drop / defer) sticks until a new message arrives. At most PROMISE_CAP
# per sitting, oldest first, so they do not crowd out the live work.
PROMISE_CAP=3
def promise_cards():
    q1='query($after:String){ threads(first:100, after:$after, filters:{statuses:[SNOOZED]}) { pageInfo{ hasNextPage endCursor } edges{ node{ id ref title customer{ fullName email{ email } } threadFields{ key stringValue } statusDetail{ __typename } } } } }'
    q2='query($id:ID!){ thread(threadId:$id){ timelineEntries(first:15){ edges{ node{ id timestamp{ iso8601 } actor{ __typename ... on UserActor{ user{ fullName } } ... on MachineUserActor{ machineUser{ fullName } } } entry{ __typename ... on ChatEntry{ chatText: text } ... on EmailEntry{ textContent } ... on SlackMessageEntry{ slackText: text } ... on SlackReplyEntry{ replyText: text } ... on MSTeamsMessageEntry{ teamsText: text } } } } } } }'
    after=None; nodes=[]
    while True:
        d=plain_gql(q1,{'after':after})
        if not d: return []
        nodes+= [e['node'] for e in d['threads']['edges']]
        if not d['threads']['pageInfo']['hasNextPage']: break
        after=d['threads']['pageInfo']['endCursor']
    out=[]
    for n in nodes:
        ref=n['ref']; t=state.get(ref,{})
        if t and t.get('phase')!='done': continue          # the loop owns it; its deploy note ends in a NOTIFY card
        d=plain_gql(q2,{'id':n['id']})
        if not d or not d.get('thread'): continue
        last=None
        for ed in d['thread']['timelineEntries']['edges']:
            x=ed['node']; e=x['entry']
            if e['__typename'] in MSG_ENTRIES:
                txt=(e.get('chatText') or e.get('textContent') or e.get('slackText') or e.get('replyText') or e.get('teamsText') or '').strip()
                last={'id':x['id'],'at':x['timestamp']['iso8601'],'actor':x['actor']['__typename'],
                      'who':((x['actor'].get('user') or x['actor'].get('machineUser') or {}).get('fullName') or ('customer' if x['actor']['__typename']=='CustomerActor' else 'us')),
                      'text':re.sub(r'\s+',' ',txt)}
                break
        if not last: continue
        ours=last['actor'] in ('UserActor','MachineUserActor')
        fields={f['key']:f.get('stringValue') for f in n['threadFields']}
        if (n.get('statusDetail') or {}).get('__typename','').endswith('WaitingIndefinitely'): continue   # "Close the loop" was set by hand: a deliberate park (T-102 -> ORB-202), not a forgotten promise
        lvl=fields.get('impact_level') if fields.get('impact_level') in IMPACT else 'P2'
        age=age_days(last['at']); detail=(n.get('statusDetail') or {}).get('__typename','').replace('ThreadStatusDetail','')
        cust=(n.get('customer') or {}).get('fullName') or ''
        quote=last['text'][:90]
        who='you' if ours else cust or 'the customer'
        out.append({'id':f'promise:{ref}','ref':ref,'lane':'PROMISE','mins':3,'impact':lvl,'age':age,'waits':'customer',
             'pr':(fields.get('github_pr') or '').rsplit('/',1)[-1] or None,'task':None,'thread':n['id'],'url':PLAIN+n['id'],
             'status':f"{'ours' if ours else 'customer'} last {last['at'][:10]} ({age}d, {detail}): {quote}",
             'note':f"{(n.get('title') or '')[:80]} · {cust} · {'we wrote last' if ours else 'customer wrote last, unanswered'}",
             'act':(f"{ref} ({(n.get('title') or '')[:50]}): {who} wrote last, {age} days ago, thread snoozed as {detail}. Tell {cust or 'the customer'} where it stands, close the loop, or hand it on"
                    if ours else f"{ref} ({(n.get('title') or '')[:50]}): {cust or 'the customer'} wrote last, {age} days ago, thread still snoozed. Answer or close"),
             'score':round((IMPACT.get(lvl,1)+3+min(age/3,2))/3*10,1),'_age':age})
    out.sort(key=lambda c:-(c['_age'] or 0))
    return out
try:
    _pc=promise_cards(); _shown=0
    for c in _pc:
        c.pop('_age',None)
        if c['ref'] in notify_refs or any(x['ref']==c['ref'] for x in cards): continue
        prev=desk['cards'].get(c['id'],{})
        # a verdict binds to the last visible message: same status -> stays closed (add() checks); the cap counts only cards that will show
        if prev.get('closed') and _cmp(c['id'],c['status'])==_cmp(c['id'],prev['closed'].get('status') or ''): add(c); continue
        if prev.get('defer'): add(c); continue
        if _shown>=PROMISE_CAP: continue
        add(c); _shown+=1
except Exception as _e:
    if os.environ.get('DESK_DEBUG'): print(f"promise_cards failed: {_e}",file=sys.stderr)

# ---------------------------------------------------------------- scope (--tenant <id>)
TENANT=None
if '--tenant' in sys.argv:
    i=sys.argv.index('--tenant')
    TENANT=sys.argv[i+1] if i+1<len(sys.argv) else None
# --today / --since <date>: the day's threads (Tobias, 2026-09-30: "a desk session but only with plain threads from today").
# Days are Europe/Berlin days, the day Tobias works in; Plain stamps createdAt in UTC.
import zoneinfo
BERLIN=zoneinfo.ZoneInfo('Europe/Berlin')
SINCE=None
if '--today' in sys.argv: SINCE=now.astimezone(BERLIN).strftime('%Y-%m-%d')
if '--since' in sys.argv:
    i=sys.argv.index('--since'); SINCE=sys.argv[i+1] if i+1<len(sys.argv) else SINCE
SCOPE=(f'tenant {TENANT}' if TENANT else None) or (f'threads since {SINCE}' if SINCE else None)
def _tenant_key(s): return re.sub(r'[^a-z0-9]','',(s or '').lower())
WAIT_SCAN_CAP=80   # timeline entries read at most per thread to find its newest customer-visible message
Q_TIMELINE_PAGE='query($id:ID!,$after:String,$n:Int!){ thread(threadId:$id){ timelineEntries(first:$n, after:$after){ pageInfo{ hasNextPage endCursor } edges{ node{ timestamp{ iso8601 } actor{ __typename } entry{ __typename } } } } } }'
def scan_wait(nodes):
    """Newest-first timeline nodes -> (since, decided). Notes, field writes, labels and status changes are skipped. since is
    the oldest customer message after our newest reply; decided turns True at the first customer-visible message."""
    since=''
    for xn in nodes:
        if (xn.get('entry') or {}).get('__typename') not in MSG_ENTRIES: continue
        if (xn.get('actor') or {}).get('__typename')!='CustomerActor': return since,True   # a reply of ours (Luna or a person)
        since=(xn.get('timestamp') or {}).get('iso8601') or since
    return since,bool(since)
def wait_since_of(tid,ref,nodes,page,is_today):
    """2026-10-14: a thread can carry a dozen notes after the customer's message (four per Hermes pass, plus desk
    notes), so the first page often holds no message at all. For a thread of today the walk pages on with the Plain
    cursor, up to WAIT_SCAN_CAP entries; a thread with no customer-visible message in that window counts as answered."""
    since,decided=scan_wait(nodes)
    if decided or not is_today or not page.get('hasNextPage'): return since
    read=len(nodes); after=page.get('endCursor')
    while read<WAIT_SCAN_CAP:
        d=plain_gql(Q_TIMELINE_PAGE,{'id':tid,'after':after,'n':min(20,WAIT_SCAN_CAP-read)})
        if not d or not d.get('thread'): return ''
        tl=d['thread']['timelineEntries']; more=[e['node'] for e in tl['edges']]; read+=len(more)
        since,decided=scan_wait(more)
        if decided: return since
        if not tl['pageInfo']['hasNextPage'] or not more: return ''
        after=tl['pageInfo']['endCursor']
    print(f"desk: {ref} shows no customer-visible message in its newest {WAIT_SCAN_CAP} timeline entries; counted as answered",file=sys.stderr)
    return ''
def _plain_threads(statuses, keep):
    """Every Plain thread in <statuses> that <keep>(node) accepts, paginated 100 at a time; None when Plain is unreachable."""
    q='query($after:String,$st:[ThreadStatus!]){ threads(first:100, after:$after, filters:{statuses:$st}) { pageInfo{ hasNextPage endCursor } edges{ node{ id ref title status createdAt{ iso8601 } customer{ email{ email } company{ name domainName } } threadFields{ key stringValue } timelineEntries(first:10){ pageInfo{ hasNextPage endCursor } edges{ node{ timestamp{ iso8601 } actor{ __typename } entry{ __typename } } } } } } } }'
    after=None; out=[]
    while True:
        d=plain_gql(q,{'after':after,'st':statuses})
        if not d: return None   # Plain unreachable: the caller falls back to the cache
        for e in d['threads']['edges']:
            n=e['node']; f={x['key']:x.get('stringValue') for x in n.get('threadFields') or []}
            c=n.get('customer') or {}; dom=((c.get('company') or {}).get('domainName') or '')
            em=((c.get('email') or {}).get('email') or ''); edom=em.split('@',1)[1] if '@' in em else ''
            if not keep(n,f,dom,edom): continue
            ne=(n.get('timelineEntries') or {}).get('edges') or []
            newest=ne[0]['node'] if ne else {}
            cust=next((x['node'] for x in ne if (x['node'].get('actor') or {}).get('__typename')=='CustomerActor'),None)
            _created=(n.get('createdAt') or {}).get('iso8601') or ''
            wait_since=wait_since_of(n['id'],n['ref'],[x['node'] for x in ne],(n.get('timelineEntries') or {}).get('pageInfo') or {},
                                     berlin_day(_created)==TODAY_BERLIN or berlin_day(((cust or {}).get('timestamp') or {}).get('iso8601'))==TODAY_BERLIN)
            if wait_since and not cust: cust={'timestamp':{'iso8601':wait_since}}   # the customer's message sat beyond the first page
            out.append({'id':n['id'],'ref':n['ref'],'title':n.get('title') or '','status':n.get('status') or '','fields':f,'email':em,'domain':dom or edom,
                        'createdAt':(n.get('createdAt') or {}).get('iso8601') or '',
                        'customerAt':((cust or {}).get('timestamp') or {}).get('iso8601') or '',
                        'newestAt':(newest.get('timestamp') or {}).get('iso8601') or '','newestActor':(newest.get('actor') or {}).get('__typename') or '',
                        'newestType':(newest.get('entry') or {}).get('__typename') or '','waitSince':wait_since})
        if not d['threads']['pageInfo']['hasNextPage']: break
        after=d['threads']['pageInfo']['endCursor']
    return out
def plain_threads_since(date):
    """Every TODO or SNOOZED thread created on or after <date> (a Europe/Berlin calendar day), or with a customer
    message on or after it (T-121, 2026-09-30: Sam answered an older thread by mail and the first version of this
    scope, createdAt only, missed it). Done threads wait on nobody."""
    def day(iso):
        t=parse_iso(iso or ''); return t.astimezone(BERLIN).strftime('%Y-%m-%d') if t else ''
    def keep(n,f,dom,edom):
        if day((n.get('createdAt') or {}).get('iso8601'))>=date: return True
        for e in (n.get('timelineEntries') or {}).get('edges') or []:
            x=e['node']
            if (x.get('actor') or {}).get('__typename')=='CustomerActor' and day((x.get('timestamp') or {}).get('iso8601'))>=date: return True
        return False
    return _plain_threads(['TODO','SNOOZED'],keep)
def plain_tenant_threads(tenant):
    """Every TODO thread on Plain that belongs to <tenant>. The tenant_id thread field exists only on Hermes/Sentry
    auto-flags; a customer thread carries its tenant through the customer's company domain or email domain
    (dana@acme.example -> acme). Match: tenant_id field == tenant, or the tenant id (letters and digits only) is inside
    the domain with punctuation stripped (north-wind.example -> northwind). ~/orbit-scratch/desk/tenants.json may map
    {tenant: [domains]} for the cases the heuristic misses. Found 2026-09-15: /desk acme showed none of acme's
    five open threads because none of them carries a tenant_id field."""
    try: extra=json.load(open(f'{DESK}/tenants.json')).get(tenant,[])
    except Exception: extra=[]
    tk=_tenant_key(tenant)
    def keep(n,f,dom,edom):
        return (f.get('tenant_id')==tenant) or any(x and (x in extra or tk in _tenant_key(x)) for x in (dom,edom))
    return _plain_threads(['TODO'],keep)
tenant_threads=None
def tenant_scope(tenant):
    """Refs and PR numbers that belong to one tenant: every TODO thread of the tenant on Plain (tenant_id field or
    customer domain), every sweep-cache thread with tenant_id=<tenant>, every loop entry whose note names the tenant,
    and the PRs those loop entries carry (`pr` field or #n in the note)."""
    global tenant_threads
    refs=set(); prs=set()
    tenant_threads=plain_tenant_threads(tenant)
    for th in tenant_threads or []: refs.add(th['ref'])
    for ref,r in byref.items():
        if f'tenant_id={tenant}' in (r.get('fingerprint') or {}).get('fields',''): refs.add(ref)
    for ref,t in state.items():
        if ref in refs or tenant.lower() in (t.get('note') or '').lower() or tenant.lower() in (t.get('status') or '').lower():
            refs.add(ref)
            if t.get('pr'): prs.add(int(t['pr']))
            for m in re.finditer(r'#(\d{3,5})',t.get('note') or ''): prs.add(int(m.group(1)))
    return refs,prs
def since_scope(date):
    """Refs and PR numbers of the threads created since <date>, plus the PRs their loop entries carry."""
    global tenant_threads
    refs=set(); prs=set()
    tenant_threads=plain_threads_since(date)
    for th in tenant_threads or []: refs.add(th['ref'])
    for ref in refs:
        t=state.get(ref,{})
        if t.get('pr'): prs.add(int(t['pr']))
        for m in re.finditer(r'#(\d{3,5})',t.get('note') or ''): prs.add(int(m.group(1)))
    return refs,prs
TODAY_BERLIN=now.astimezone(BERLIN).strftime('%Y-%m-%d')
def berlin_day(iso):
    t=parse_iso(iso or ''); return t.astimezone(BERLIN).strftime('%Y-%m-%d') if t else ''
READINESS_ACT=deskevent.READINESS_ACT   # one table for the gather and the live event path
def fast_thread_cards(threads,prefix):
    """One FAST card `<prefix>:<ref>` per Plain thread that no other source put on the desk (no loop entry, no deploy
    note, no gap row), the same recipe as a pinned ref: read it live, claim, validate, draft the reply or hand it to a
    worker. Tobias, 2026-09-15: "/desk acme" must show T-122, T-123, T-124, T-115 and T-120, none of which the
    loop owns. Since 2026-10-09 the unscoped run calls it with today's threads too."""
    on_desk={c.get('ref') for c in cards}
    for th in threads or []:
        ref=th['ref']
        if ref in on_desk or ref in state: continue   # a loop entry means an agent, HORST or a release owns it (or a gap/loop card already exists)
        f=th['fields']; rd=f.get('agent_readiness') or 'needs-triage'; lvl=f.get('impact_level') if f.get('impact_level') in IMPACT else 'P2'
        t=state.get(ref,{}); r=byref.get(ref) or {}
        age=age_days(th['newestAt']) if th['newestAt'] else 0
        who='customer' if th['newestActor']=='CustomerActor' else ('nobody' if rd=='needs-info' else 'customer')
        cst=(r.get('state') or '').lower()
        if 'wontfix' in cst or 'duplicate' in cst: rd='wontfix' if 'wontfix' in cst else 'duplicate'
        if rd in ('wontfix','duplicate'): who='nobody'   # nobody waits on a close; the readiness field or the sweep grade says so
        # 2026-09-30: the caption is the deferral's anchor (add() voids a hold when it changes), so it carries only what the
        # customer or Plain's status can change: the thread status and the newest CUSTOMER entry. Our own notes, field
        # writes and readiness flips live in the act and the note, never in the caption (T-126/T-127 came back one
        # rerun after their defer because the validation note moved "last entry").
        last=f"customer wrote {th['customerAt'][:10]}" if th.get('customerAt') else (f"last entry {th['newestAt'][:10]} by {th['newestActor'].replace('Actor','').lower() or '?'}" if th['newestAt'] else 'no timeline')
        facts=[f"Plain {th.get('status') or 'TODO'}", last]
        if th.get('createdAt'): facts.append(f"opened {parse_iso(th['createdAt']).astimezone(BERLIN).strftime('%Y-%m-%d %H:%M')} Berlin")
        note_bits=[f"{rd}; owner {f.get('triage_owner') or 'nobody'}"]
        if r.get('state'): note_bits.append(f"cache: {r['state'][:50]}")
        if f.get('github_pr'): note_bits.append(f"PR {f['github_pr']}")
        if f.get('notion_ticket'): note_bits.append(f"notion {f['notion_ticket']}")
        add({'id':f"{prefix}:{ref}",'ref':ref,'lane':'FAST','mins':10,'impact':lvl,'age':age,'waits':who,
             'pr':t.get('pr'),'task':None,'thread':th['id'],'url':PLAIN+th['id'],
             'status':'; '.join(facts)[:300],'note':(th['title'][:120]+' · '+'; '.join(note_bits))[:300],
             'act':f"{ref} ({th['title'][:60]}, {th['email']}): {READINESS_ACT.get(rd,READINESS_ACT['needs-triage'])}",
             'score':deskevent.fast_score(lvl,who,age)})
def unanswered_cards(threads):
    """2026-10-14: a thread of today whose newest customer-visible message is the customer's gets a today:<ref> card
    even when the loop owns it. A thread opened in the morning got a validation note and a worker within 30 minutes,
    the worker's PR was open, and the customer had no reply for five hours while the desk showed nothing: the loop
    entry hid the row. The card goes away by itself on the first run after a reply of ours (Luna or a person)."""
    have={c['id'] for c in cards}
    for th in threads or []:
        ref=th['ref']; since=th.get('waitSince')
        if not since or f'today:{ref}' in have: continue
        if TODAY_BERLIN not in (berlin_day(th['createdAt']),berlin_day(th['customerAt'])): continue
        f=th['fields']; lvl=f.get('impact_level') if f.get('impact_level') in IMPACT else 'P2'
        hm=parse_iso(since).astimezone(datetime.timezone.utc).strftime('%H:%MZ')
        facts=[f"Plain {th.get('status') or 'TODO'}",f"customer wrote {th['customerAt'][:10]}"]
        if th.get('createdAt'): facts.append(f"opened {parse_iso(th['createdAt']).astimezone(BERLIN).strftime('%Y-%m-%d %H:%M')} Berlin")
        t=state.get(ref,{})
        add({'id':f'today:{ref}','ref':ref,'lane':'FAST','mins':10,'impact':lvl,'age':0,'waits':'customer',
             'pr':t.get('pr'),'task':None,'thread':th['id'],'url':PLAIN+th['id'],'unanswered':since,
             'status':'; '.join(facts)[:300],
             'note':(th['title'][:120]+' · '+(f"loop: {t.get('phase')}, {(t.get('status') or '')[:60]}" if t else 'no loop entry'))[:300],
             'act':f"{ref} ({th['title'][:60]}, {th['email']}): customer waits for a first answer since {hm}: draft the reply",
             'score':deskevent.fast_score(lvl,'customer',0)})
        have.add(f'today:{ref}')
if SCOPE:
    srefs,sprs=tenant_scope(TENANT) if TENANT else since_scope(SINCE)
    def in_scope(c):
        if c.get('ref') in srefs: return True
        if c['id'].startswith('gh:') and c.get('pr') in sprs: return True
        return False
    cards[:]=[c for c in cards if in_scope(c)]
    # ---------------------------------------------------------------- source 7a: the scope's open threads
    # A tenant sitting is the whole desk for that customer, a today sitting the whole desk for the day's threads.
    if tenant_threads is None:
        print(f"WARNING: Plain unreachable, {SCOPE} scoped from the cache only (its open threads are missing)",file=sys.stderr)
    fast_thread_cards(tenant_threads,'tenant' if TENANT else 'today')
    day_threads=tenant_threads
else:
    # ---------------------------------------------------------------- source 7a: today's threads (every unscoped run)
    # Tobias, 2026-10-09: "desk should ALWAYS prioritize new plain threads of the day, second new answers of today, then
    # everything else, without me needing to type /desk --today". One more Plain read per run (TODO + SNOOZED, the same
    # page plain-stats.py reads); a thread of today with no card becomes today:<ref>, the lift below ranks the rest.
    day_threads=plain_threads_since(TODAY_BERLIN)
    if day_threads is None: print("WARNING: Plain unreachable, today's threads are missing from the desk",file=sys.stderr)
    fast_thread_cards(day_threads,'today')
    unanswered_cards(day_threads)
# A thread opened today ranks above everything but desk.json "top"; a thread with a customer message today ranks right
# under those. Pins (1000+) sit below both unless the pinned thread is itself from today. Only live cards move: a
# parked card keeps its hold (a customer message changes the caption, and add() voids the hold on its own).
TODAY_NEW={th['ref'] for th in day_threads or [] if berlin_day(th['createdAt'])==TODAY_BERLIN}
TODAY_ANSWERED={th['ref'] for th in day_threads or [] if th['ref'] not in TODAY_NEW and berlin_day(th['customerAt'])==TODAY_BERLIN}

# ---------------------------------------------------------------- source 7b: release deferrals without a live card
# A card parked on deployed:<env>:<pr> must outlive its loop entry (the housekeeper drops done entries): when no
# source produced the card this run, rebuild it from the deferral so the release check still runs and the board
# release section still lists it. Tobias, 2026-09-10: bucket B lands on the board by default and resurfaces on release.
seen_ids={c['id'] for c in cards}
for cid,pc in list(desk['cards'].items()):
    d=pc.get('defer') or {}
    if not d.get('trigger','').startswith('deployed:') or cid in seen_ids or pc.get('closed'): continue
    ref=pc.get('ref') or cid.split(':',1)[-1]; r=byref.get(ref) or {}; t=state.get(ref,{})
    try: prn=int(d['trigger'].split(':')[2])
    except Exception: prn=None
    if SCOPE and not in_scope({'id':cid,'ref':ref,'pr':prn}): continue   # a rebuilt card honours --tenant / --today like every other
    lvl,_=impact_of(ref)
    add({'id':cid,'ref':ref,'lane':'NOTIFY','mins':3,'impact':lvl,'age':age_days((d.get('since') or today)+'T00:00:00Z'),'waits':'customer',
         'pr':prn,'task':None,'thread':r.get('id') or t.get('threadId'),'url':None,
         'status':f"release pending: {d['trigger']}",'note':(r.get('title') or '')[:200],'rebuilt':True,   # synthetic caption: never voids the bound deferral (T-119, 2026-09-15)
         'act':d.get('act') or f"Release landed (PR #{prn}): tell {ref}, then close the thread",
         'score':round((IMPACT.get(lvl,1)+3)/3*10,1)})

# ---------------------------------------------------------------- source 7c: TODO threads nobody owns (OWE, 2026-10-12)
# Tobias 2026-10-12: nineteen TODO threads sat off the desk because no source made a card for them: graded
# ready-for-human / needs-info / in the loop queue / never swept, no loop entry, not opened today, not pinned.
# todo-gap.py already lists them (buckets D, E, F, J); every such thread without another card becomes one FAST card
# owe:<ref> (the FAST recipe: claim + validate, draft the reply, hand to a worker). D/E/J rank like a P2 pin minus a
# notch; F (waiting on the customer) only after seven days, low. A parked deferral still hides it via add().
try:
    _carded={c.get('ref') for c in cards}|{x['ref'] for x in desk.get('pins',[])}
    # a thread whose card (today:/fast:/owe:/gap:) got its verb today stays off until tomorrow; today's threads have their own cards
    _carded|={cid.split(':')[-1] for cid,pc in desk['cards'].items() if (pc.get('closed') or {}).get('date')==today}
    _carded|=set(TODAY_NEW)|set(TODAY_ANSWERED)
    for bucket,minage,base in (('D',0,60),('E',0,70),('J',0,55),('F',7,25)):
        sec=re.search(r'^## '+bucket+r'\..*?$(.*?)(?=^## |\Z)',gap_md,re.S|re.M)
        for m in re.finditer(r'- \[(T-\d+)\]\((https://[^)]+/thread/(th_\w+))\) (\S+) · (.*?) · (\S*) · `([^`]*)`',sec.group(1) if sec else ''):
            ref,url,tid,lvl,title,_date,st=m.groups()
            if ref in _carded or ref in notify_refs: continue
            if state.get(ref,{}).get('phase') in ('handoff-running','pr-open','blocked'): continue
            if SCOPE and not in_scope({'id':f'owe:{ref}','ref':ref,'thread':tid}): continue
            age=age_days((_date or today)+'T00:00:00Z') if re.match(r'\d{4}-\d{2}-\d{2}$',_date or '') else 0
            if age<minage: continue
            lvl=lvl if lvl in IMPACT else 'P2'
            # E = bot stalled (Tobias 2026-10-12): the triage pass ended needs-triage, so nobody holds the thread; the desk claims it for local triage
            why={'D':'ready-for-human, nobody listed it','E':'bot stalled (needs-triage), claim it here','J':'never swept','F':'needs-info, customer silent'}[bucket]
            add({'id':f'owe:{ref}','ref':ref,'lane':'FAST','mins':10,'impact':lvl,'age':age,'waits':'customer',
                 'pr':None,'task':None,'thread':tid,'url':url,
                 'status':f"{why}: {st[:50]}",'note':title[:200],
                 'act':f"{ref} ({title[:60]}): {why}. Claim + validate, draft the reply, or hand it to a worker",
                 'score':base+IMPACT.get(lvl,1)+min(age,10)})
            _carded.add(ref)
except Exception as _e:
    if os.environ.get('DESK_DEBUG'): print(f"owe source failed: {_e}",file=sys.stderr)


# ---------------------------------------------------------------- source 8: pinned refs (FAST lane)
# `desk.py T-120 ORB-203` pins refs; they live in desk.json "pins" and come back every sitting until closed.
# A T-ref is looked up in the sweep cache, then live on Plain (threadByRef) when the cache does not have it.
# An ORB-ref cannot be read here (no Notion token in the shell): the card carries the id and the FAST recipe
# in CARDS.md fetches the ticket in the session. Tobias asked for this on 2026-09-10 (fast-track a customer).
PIN_RE=re.compile(r'^(T|ORB)-(\d+)$',re.I)
desk.setdefault('pins',[])
for a in sys.argv[1:]:
    m=PIN_RE.match(a)
    if m and not any(x['ref']==f'{m.group(1).upper()}-{m.group(2)}' for x in desk['pins']):
        desk['pins'].append({'ref':f'{m.group(1).upper()}-{m.group(2)}','since':today})
    # 2026-10-06: a ref given again after its card got a verb is a re-pin (T-128 was dropped, then pinned once more
    # the same minute); clear the closure so the pin source below does not skip it.
    if m: desk['cards'].get(f"fast:{m.group(1).upper()}-{m.group(2)}",{}).pop('closed',None)
if '--unpin' in sys.argv:
    i=sys.argv.index('--unpin'); u=(sys.argv[i+1] if i+1<len(sys.argv) else '').upper()
    desk['pins']=[x for x in desk['pins'] if x['ref']!=u]
def plain_thread_by_ref(ref):
    if not os.environ.get('PLAIN_API_KEY'): return None
    return (_plain.try_gql(_plain.Q_BY_REF,{'r':ref},20) or {}).get('threadByRef')
keep=[]
for i,pin in enumerate(desk['pins']):
    ref=pin['ref']; cid=f'fast:{ref}'
    if desk['cards'].get(cid,{}).get('closed'): continue   # got its verb: unpinned
    keep.append(pin)
    t=state.get(ref,{}); r=byref.get(ref); tid=(r or {}).get('id') or t.get('threadId'); title=(r or {}).get('title') or ''
    lvl='P2'; facts=[]
    if ref.startswith('T-'):
        if r: lvl,_=impact_of(ref); facts.append(f"cache: {(r.get('state') or '?')[:50]}")
        th=plain_thread_by_ref(ref)
        if th:
            tid=tid or th['id']; title=title or th.get('title') or ''
            f={x['key']:x.get('stringValue') for x in th.get('threadFields') or []}
            lvl=f.get('impact_level') or lvl
            facts.append(f"Plain {th['status']}" + (f", tenant {th['tenant']['name']}" if th.get('tenant') else ''))
            facts.append(f"readiness {f.get('agent_readiness') or '-'}; owner {f.get('triage_owner') or 'nobody'}")
            if f.get('github_pr'): facts.append(f"PR {f['github_pr']}")
            if f.get('notion_ticket'): facts.append(f"notion {f['notion_ticket']}")
        elif not r: facts.append('not in the sweep cache and Plain lookup failed')
    else:
        # ORB ticket: find a Plain thread that names it (notion_ticket field or the loop note)
        for tref,rr in byref.items():
            if ref in (rr.get('fingerprint') or {}).get('fields',''): facts.append(f'Plain thread {tref}'); tid=tid or rr['id']; break
        for tref,tt in state.items():
            if ref in (tt.get('note') or '')+(tt.get('status') or ''): facts.append(f'loop entry {tref}: {(tt.get("status") or "")[:40]}'); break
        facts.append('read the ticket with notion-fetch in the session')
    if t: facts.append(f"loop {t.get('phase')}: {(t.get('status') or '')[:50]}")
    add({'id':cid,'ref':ref,'lane':'FAST','decided':bool(pin.get('decision')),'mins':10,'impact':lvl,'age':age_days(pin.get('since','')+'T00:00:00Z'),'waits':'customer',
         'pr':t.get('pr'),'task':None,'thread':tid,'url':None if tid else (f'https://www.notion.so/search?q={ref}' if ref.startswith('ORB-') else None),
         'status':'; '.join(facts)[:300],'note':title[:200],
         'act':(f"Decided {pin.get('since','')}: {pin['decision']}" if pin.get('decision') else f"Fast-track {ref}{(': '+title[:60]) if title else ''}: claim it, validate, draft the reply or hand it to a worker"),
         'score':1000-i})
desk['pins']=keep

# ---------------------------------------------------------------- source 9: the weekly Sentry sweep (Tobias, 2026-09-11: the desk owns its follow-through)
# a `paused` run row = a fix pass waiting for Tobias's picks (one SWEEP card); every open line under the report's
# `## Decisions needed` = one card: owned by Tobias (or nobody) → SWEEP lane (decide here, recorded in the report),
# owned by someone else → no card (TODO: route via Ask-Orbit, see orbot adapter orbitInputRequest).
# 2026-09-16 (Tobias): the 09-11 run finished its own fix pass (status `complete`), so `for r in _paused` skipped its six
# open decisions and the deep dives behind them; the sweep got lost. Decisions now come from the NEWEST run whatever its
# status; the fix-pass card stays tied to `paused`.
for r in (_runs[-1:] if _runs else []):
    decs=sweep_decisions(r['report']); age=age_days(r['date']+'T00:00:00Z')
    try: nbugs=len(re.findall(r'^\| B\d+ ',open(r['report']).read(),re.M))
    except Exception: nbugs=0
    if r['status']=='paused':
        add({'id':f"sweep:{r['runId']}",'ref':f"sweep {r['date']}",'lane':'SWEEP','mins':10,'impact':'P1','age':age,'waits':'person','pr':None,'thread':None,'url':r['page'] or None,
             'status':f"paused at the fix pass since {r['date']}; {nbugs} fixable bugs, {len(decs)} open decisions",'note':r['report'],
             'act':f"Pick the fixes from the {r['date']} sweep (bucket 2 rows, resolve list, SHOP-77 reopen) and resume its fix pass in this session",
             'score':round((IMPACT['P1']+2+min(age,6))/10*10,1)})
    for d in decs:
        others=[o for o in d['owners'] if o.lower()!='tobias']
        mine=('Tobias' in d['owners']) or not d['owners']
        if mine:
            add({'id':f"sweep-decision:{r['runId']}:{d['id']}",'ref':f"{d['id']} ({r['date']})",'lane':'SWEEP','mins':2,'impact':'P1','age':age,'waits':'person','pr':None,'thread':None,'url':r['page'] or None,
                 'status':f"{d['id']}: {d['title'][:60]}",'note':d['text'][:300]+(f" (also @{', @'.join(others)})" if others else ''),
                 'act':f"Decide {d['id']} from the {r['date']} sweep: {d['title'][:80]}",'score':round((IMPACT['P1']+2+min(age,6))/2*10,1)})
# 2026-09-16 (Tobias): the sweep's ticket files are the "bugs to be fixed" that got lost. Every ticket of the newest run
# under ~/orbit-scratch/sentry-sweep-<date>/issues/NN-*.md with `Status: needs-triage|ready-for-agent` that no decision
# line covers (`Owner: … decision N`) and that names no PR of its own is one RUN card: hand it to a worker, ticket it, or drop it.
for r in (_runs[-1:] if _runs else []):
    tdir=f"{HOME}/orbit-scratch/sentry-sweep-{r['date']}/issues"
    for f in sorted(glob.glob(f'{tdir}/*.md')):
        try: head=open(f).read(3000)
        except Exception: continue
        st=(re.search(r'^Status:\s*(\S+)',head,re.M) or [None,''])[1].lower()
        if st not in ('needs-triage','ready-for-agent'): continue
        owner=(re.search(r'^Owner:\s*(.+)$',head,re.M) or [None,''])[1]
        if re.search(r'decision \d',owner,re.I): continue        # the decision card carries it
        if re.search(r'^(PR|Fix):\s*#\d+',head,re.M): continue   # a PR exists; the loop or HORST owns it
        if re.search(r'^Worker:\s*\S',head,re.M): continue       # 2026-09-28: a spawned worker owns it (its loop entry carries the PR later); the card came back the day after every handoff
        nn=os.path.basename(f)[:2]; title=(re.search(r'^#\s*(.+)$',head,re.M) or [None,os.path.basename(f)])[1]
        age=age_days(r['date']+'T00:00:00Z')
        add({'id':f"sweep-ticket:{r['date']}:{nn}",'ref':f"sweep ticket {nn} ({r['date']})",'lane':'RUN','mins':3,'impact':'P2','age':age,'waits':'nobody','pr':None,'thread':None,'url':None,
             'status':f"sweep ticket {nn}: {title[:60]} (owner: {owner or 'unassigned'})",'note':f,
             'act':f"Sweep ticket {nn} from {r['date']} has no owner and no PR: hand it to a worker, ticket it in Notion, or drop it",
             'score':round((IMPACT['P2']+1+min(age,6))/2*10,1)})

# ---------------------------------------------------------------- source 9c: local ticket files (2026-10-12)
# The to-tickets / triage skills file tickets as ~/orbit-scratch/<feature>/issues/NN-<slug>.md with a `Status:` line.
# Only the newest sweep's folder was read (above); about 150 open files in 60 other folders sat where no card saw them.
# Every open file with no PR, no worker and no unfinished `Depends on:` / `Blocked by:` ticket in its folder is one TICKET
# member, in two batches that read differently: `batch:TICKET:local-agent` (ready-for-agent: a brief waits for a worker)
# and `batch:TICKET:local-call` (needs-triage: a person has to look at the file; this is the skill's sense of the word,
# NOT the Plain-thread sense "bot stalled" that source 7c uses). The member status carries no age, so a closed batch
# only comes back when a file's Status line changes or a new file appears.
LOCAL_DONE=re.compile(r'^(done|closed|merged|wontfix|resolved|fixed|filed|folded|implemented|in review|in-progress)',re.I)
def local_ticket_head(f):
    try: head=open(f).read(3000)
    except Exception: return '',''
    m=re.search(r'^Status:\s*(.+)$',head,re.M); return (m.group(1).strip() if m else ''),head
if not SCOPE:
    _newest_sweep=f"sentry-sweep-{_runs[-1]['date']}" if _runs else None
    for tdir in sorted(glob.glob(f'{HOME}/orbit-scratch/*/issues')):
        feat=os.path.basename(os.path.dirname(tdir))
        if feat==_newest_sweep: continue
        files=sorted(glob.glob(f'{tdir}/*.md'))
        heads={os.path.basename(f)[:2]:local_ticket_head(f) for f in files}
        for f in files:
            nn=os.path.basename(f)[:2]; st,head=heads[nn]
            kind=st.split()[0].lower() if st else ''
            if kind not in ('needs-triage','ready-for-agent','ready-for-human'): continue
            human=kind=='ready-for-human'
            # ready-for-human with a PR is the normal state (a person merges or checks it), so the PR is a hint on
            # that card, not a reason to skip; for the other two kinds a PR means the loop or HORST owns the file.
            prs=sorted({int(n) for n in re.findall(r'#(\d+)',st+' '+' '.join(re.findall(r'^(?:PR|Fix):\s*(.+)$',head,re.M)))})
            if not human and (re.search(r'^(PR|Fix):\s*#\d+',head,re.M) or re.search(r'^Worker:\s*\S',head,re.M)): continue
            owner=(re.search(r'^Owner:\s*(.+)$',head,re.M) or [None,''])[1].strip()
            if re.search(r'decision \d',owner,re.I): continue
            deps=[t for v in re.findall(r'^(?:Depends on|Blocked by):\s*(.+)$',head,re.M) for t in re.split(r'[\s,]+',v) if re.match(r'^\d{2}$',t)]
            if any(d!=nn and d in heads and not LOCAL_DONE.match(heads[d][0] or '') for d in deps): continue   # a blocking ticket is still open
            title=re.sub(r'^\d{2}\s*[—–-]?\s*','',(re.search(r'^#\s*(.+)$',head,re.M) or [None,os.path.basename(f)])[1])
            created=(re.search(r'^Created:\s*(\d{4}-\d{2}-\d{2})',head,re.M) or [None,None])[1]
            if not created:
                try: created=datetime.datetime.fromtimestamp(os.stat(f).st_birthtime,datetime.timezone.utc).strftime('%Y-%m-%d')
                except Exception: created=today
            age=age_days(created+'T00:00:00Z')
            lvl=(re.search(r'^(?:Impact|Severity|Priority):\s*(P[0-2])',head,re.M) or [None,'P2'])[1]
            _tm=re.match(r'^t-(\d+)',feat)
            tref=('T-'+_tm.group(1)) if _tm else (re.search(r'^Plain:\s*(T-\d+)',head,re.M) or [None,None])[1]
            if tref and state.get(tref,{}).get('phase') in ('handoff-running','pr-open'): continue   # a worker is on the thread already
            call=kind=='needs-triage'
            kind_key='local-call' if call else ('local-human' if human else 'local-agent')
            head_word={'local-call':'your call','local-human':'needs a person','local-agent':'brief ready'}[kind_key]
            if call: act=f"{feat} ticket {nn} ({title[:60]}): your call. Read the file and set its Status (ready-for-agent, ready-for-human, wontfix), or defer it"
            elif human and prs: act=f"{feat} ticket {nn} ({title[:60]}) names PR {', '.join('#'+str(p) for p in prs)}: check the PR landed and what the file still asks of a person, then set Status: done (or say what stays open)"
            elif human: act=f"{feat} ticket {nn} ({title[:60]}) waits for a person{' (owner: '+owner+')' if owner else ''}: do it, defer it with a trigger, or hand it to a worker if a brief would do"
            else: act=f"{feat} ticket {nn} ({title[:60]}) has a brief and no worker: hand it to a worker, ticket it in Notion, or drop it"
            add({'id':f"local:{feat}:{nn}",'ref':f"{feat}/{nn}",'lane':'TICKET','mins':1 if kind_key=='local-agent' else 2,'impact':lvl,'age':age,'waits':'nobody' if kind_key=='local-agent' else 'person',
                 'pr':(prs[-1] if human and prs else None),'task':None,'thread':None,'url':None,'kind':kind_key,
                 'status':f"{head_word} · {st[:60]}"+(f" · from {tref}" if tref else '')+(f" · owner: {owner[:30]}" if human and owner else ''),
                 'note':title[:200],
                 'act':act+f" · {f.replace(HOME,'~')}",
                 'score':round((IMPACT.get(lvl,1)+1+min(age/14,3))/3*10,1)})

# ---------------------------------------------------------------- source 9b: Notion follow-up tickets we created (Tobias, 2026-10-06)
# tickets.py reads ~/orbit-scratch/desk/notion-tickets.json (the desk mod writes it through the Notion MCP) and links
# each ticket to Plain threads (notion_ticket field, ORB-<id> in loop notes). Two cards: one TICKET batch of the idle
# tickets (open, unassigned, no PR, >= 14 days old or in no sprint), and one NOTIFY card per shipped ticket whose linked
# Plain thread is still open (the customer never heard). The NOTIFY card steps aside when gap:/loop: already carries the ref.
NOTION='https://app.notion.com/'
if not SCOPE:
    try:
        sys.path.insert(0,DESK); import tickets as _tk
        _tc=_tk.classify()
        for t in _tc['idle']:
            add({'id':f"ticket:{t['id']}",'ref':f"ORB-{t['id']}",'lane':'TICKET','mins':1,'impact':t.get('impact') or 'P2','age':t['age'],'waits':'nobody',
                 'pr':None,'task':None,'thread':(t['threads'][0]['threadId'] if t['threads'] else None),'url':t.get('url'),
                 'status':f"{t['status']} · {t['age']}d · {'no sprint' if not t.get('sprint') else 'Kanban'} · unassigned"+(f" · from {', '.join(r['ref'] for r in t['threads'])}" if t['threads'] else ''),
                 'note':t['title'][:200],
                 'act':f"ORB-{t['id']} ({t['title'][:60]}) idles {t['age']}d: push it (ask {deskconfig.TICKET_OWNER} / assign), hand it to an agent, or archive it",
                 'score':round((IMPACT.get(t.get('impact') or '',1)+1+min(t['age']/14,3))/3*10,1)})
        for t in _tc['shippedUntold']:
            refs=[r['ref'] for r in t['threads'] if r['status'] in ('TODO','SNOOZED')]
            # The loop, a gap card or a parked release trigger already owns the thread: no second card for it.
            if any(x['ref'] in refs for x in cards): continue
            ref=refs[0]; thr=next((r for r in t['threads'] if r['ref']==ref),{})
            add({'id':f"ticket-done:{t['id']}",'ref':ref,'lane':'NOTIFY','mins':3,'impact':t.get('impact') or 'P2','age':age_days(t.get('completedAt') or t.get('createdAt') or ''),'waits':'customer',
                 'pr':None,'task':None,'thread':thr.get('threadId'),'url':t.get('url'),
                 'status':f"ORB-{t['id']} {t['status']}, thread {thr.get('status','')}: customer not told",'note':t['title'][:200],
                 'act':f"ORB-{t['id']} is {t['status']} ({t['title'][:50]}): tell the {ref} customer where it stands"+(", then close the thread" if t['status']!='Staging Released' else " (staging now, production with the next release)"),
                 'score':round((IMPACT.get(t.get('impact') or '',1)+3+1)/3*10,1)})
    except Exception as _e:
        if os.environ.get('DESK_DEBUG'): print(f"tickets failed: {_e}",file=sys.stderr)

# ---------------------------------------------------------------- source 1b: a PR's departures go back onto the Notion ticket (2026-10-07)
# From the /deliver comparison (Leo's skill records a departure in the ticket and in a comment on it). Our launchpad workers
# have no Notion, so they write "## Departures from the ticket" (worker-prompt.sh: "from the brief") and "## Verified on a
# stage" into the PR body, and this card carries the section onto the ticket from the desk session (CARDS.md, RUN notion:).
# The PR body is read once a day per PR and cached in notion-sync.json; the card's status is the PR plus the section's hash,
# so a closed card comes back only when the section changes.
_NSYNC=os.path.expanduser('~/orbit-scratch/desk/notion-sync.json')
def _departures(pr):
    try: cache=json.load(open(_NSYNC))
    except Exception: cache={}
    c=cache.get(str(pr)) or {}
    if (c.get('checkedAt') or '')[:10]==today: return c.get('section')
    body=(gh(['pr','view',str(pr),'--repo',ORBIT,'--json','body'],30) or {}).get('body') or ''
    sec=[]
    for head in ('Departures from the ticket','Departures from the brief','Verified on a stage','Follow-up ticket (to file)'):
        m=re.search(r'^##\s+'+re.escape(head)+r'\s*\n(.*?)(?=^##\s|\Z)',body,re.S|re.M)
        if not m: continue
        txt=m.group(1).strip()
        if head.startswith('Departures') and txt and txt.lower()!='none': sec.append(f'## {head}\n{txt}')
        if head.startswith('Verified') and re.search(r'not walked',txt,re.I): sec.append(f'## {head}\n{txt}')
        # 2026-10-08: a launchpad worker has no Notion (T-129 waited a day on a sign-in that cannot complete); it writes
        # the ticket it wants filed into the PR body and the desk files it from here (notion-create-pages, Tasks data source)
        if head.startswith('Follow-up ticket') and txt: sec.append(f'## {head}\n{txt}')
    section='\n\n'.join(sec) or None
    cache[str(pr)]={'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'),'section':section}
    try: json.dump(cache,open(_NSYNC,'w'),indent=1,ensure_ascii=False)
    except Exception: pass
    return section
if not SCOPE:
    for ref,t in state.items():
        if t.get('pr') not in my_prs: continue   # 2026-10-08: T-refs too (a Plain worker's follow-up ticket goes to Notion the same way)
        try: sec=_departures(t['pr'])
        except Exception as _e:
            if os.environ.get('DESK_DEBUG'): print(f"departures {ref}: {_e}",file=sys.stderr)
            continue
        if not sec: continue
        import hashlib as _hl
        h=_hl.sha1(sec.encode()).hexdigest()[:8]
        add({'id':f'notion:{ref}','ref':ref,'lane':'RUN','mins':2,'impact':impact_of(ref)[0],'age':age_days(t.get('updated') or ''),'waits':'person',
             'pr':t['pr'],'task':t.get('task'),'workspace':None,'host':None,'terminal':None,'thread':None,'url':f'https://github.com/{ORBIT}/pull/{t["pr"]}',
             'status':f"#{t['pr']} departures {h}",'note':sec[:400],'section':sec,
             'act':(f"File the follow-up ticket from PR #{t['pr']}'s body in Notion (notion-create-pages, Tasks data source, duplicate check first), then write the ORB id into the PR body" if 'Follow-up ticket' in sec else
                    f"Post PR #{t['pr']}'s departures onto the Notion ticket {ref} as a comment (notion-create-comment) and set its PR link"),
             'score':round((impact_of(ref)[1]+2)/2*10,1)})

# ---------------------------------------------------------------- source 10: queued support-memory proposals (memory.py queue)
# Facts Tobias okayed in sittings wait in memory-proposals.jsonl. Three or more, or one three days old, is one RUN card:
# `memory.py pr` applies them on a branch in ~/orbot, validates with the orbot parser, opens the PR. One open memory PR at a
# time (the next batch waits for the merge, so the files never conflict). Tobias merges orbot PRs himself; no HORST there.
MEMORY_PR_MIN=3; MEMORY_PR_AGE_DAYS=3
if memory and not SCOPE:
    try:
        _ms=memory.queue_status()
        if _ms['queued'] and not _ms['openPr'] and (_ms['queued']>=MEMORY_PR_MIN or _ms['age']>=MEMORY_PR_AGE_DAYS):
            add({'id':'memory:pr','ref':'memory','lane':'RUN','mins':2,'impact':'P2','age':_ms['age'],'waits':'nobody','pr':None,'task':None,'thread':None,'url':None,
                 'status':f"{_ms['queued']} queued for {', '.join(_ms['tenants'])}; oldest {_ms['oldest']}",'note':'',
                 'act':f"Open the orbot support-memory PR with the {_ms['queued']} facts you okayed ({', '.join(_ms['tenants'])}): python3 ~/orbit-scratch/desk/memory.py pr",
                 'score':round((2+0+min(_ms['age']/3,2))/2*10,1)})
    except Exception as _e:
        if os.environ.get('DESK_DEBUG'): print(f"memory queue failed: {_e}",file=sys.stderr)

# ---------------------------------------------------------------- 9d. local tasks (Tobias, 2026-10-09)
# Work with no Plain thread, no PR and no Notion ticket (a help-center sweep, a cleanup) still belongs on the desk.
# `task.py add "<title>" --step "…"` stores it in desk/tasks.json, local only; each open task is one TASK card
# `task:<slug>` until it gets a verb (desk-close.py marks it done there). The title is the caption, so a closure sticks.
# 2026-10-13: a queue of many small decisions of one kind feeds the desk one task at a time (task-drip.py beside task.py,
# local like tasks.json): the next item follows only after the open one has its verb and the queue's pace allows.
try:
    if os.path.exists(f'{DESK}/task-drip.py'): subprocess.run(['python3',f'{DESK}/task-drip.py'],capture_output=True,timeout=20)
except Exception: pass
if not SCOPE:
    for _t in load(os.path.join(os.path.dirname(DESKFILE),'tasks.json'),{'tasks':[]}).get('tasks',[]):
        if _t.get('done'): continue
        _age=age_days(_t.get('since','')+'T00:00:00Z'); _steps=_t.get('steps') or []
        add({'id':f"task:{_t['id']}",'ref':_t['id'][:24],'lane':'TASK','mins':_t.get('mins',10),'impact':_t.get('impact') or 'P2','age':_age,'waits':'nobody',
             'pr':None,'task':None,'thread':None,'url':None,'status':_t['title'][:300],'note':'; '.join(_steps)[:200],
             'act':f"{_t['title']}"+(f" ({len(_steps)} steps)" if _steps else '')+f": start it, hand it to an agent, or drop it (task.py show {_t['id']})",
             'score':round(20+min(_age,10),1)})

# ---------------------------------------------------------------- batches
def batch(lane,key=lambda c:'all',title=None,mins_each=1,waits='agent',act=None,pred=lambda c:True):
    grp={}
    for c in [x for x in cards if x['lane']==lane and x['score']>=0 and pred(x)]:
        grp.setdefault(key(c),[]).append(c)
    for k,cs in grp.items():
        for c in cs: cards.remove(c)
        mins=max(1,mins_each*len(cs)); one=cs[0] if len(cs)==1 else None
        card={'id':f'batch:{lane}:{k}','ref':one['ref'] if one else f'{lane}×{len(cs)}','lane':lane,'mins':mins,'impact':max(cs,key=lambda c:IMPACT.get(c["impact"],1))['impact'],
              'age':max(c['age'] for c in cs),'waits':waits,'pr':one['pr'] if one else None,'thread':one['thread'] if one else None,'url':(one or {}).get('url'),
              'members':[{k2:c.get(k2) for k2 in ('ref','pr','task','workspace','host','terminal','thread','status','act','url','staged','picker','question','prefilled','screen')} for c in cs],
              'status':'; '.join(f"{c['ref']}: {c['status'][:40]}" for c in cs)[:300],'note':'','act':(one['act'] if one else (act or title).format(n=len(cs),k=k)),'score':max(c['score'] for c in cs)}
        add(card)
# 2026-09-18 (Tobias): "put the decisions on the top of my desk". desk.json "top" is an ordered list of card ids that
# rank above everything, pins included (2000-i). It runs BEFORE the batches so a batch inherits the lift.
# An id that matches no live card does nothing, so an answered decision drops out by itself; parked cards stay parked.
for c in cards:
    if c['score']<0: continue
    if c.get('ref') in TODAY_NEW: c['score']=deskevent.lift_new(c['score'])
    elif c.get('ref') in TODAY_ANSWERED: c['score']=deskevent.lift_answered(c['score'])
for _i,_cid in enumerate(desk.get('top',[])):
    for c in cards:
        if c['id']==_cid and c['score']>=0: c['score']=2000-_i
batch('SEND',act='Go through the {n} lines you typed but never sent: send / edit / drop each',mins_each=1,waits='agent')
batch('TICKET',act='Work through the {n} follow-up tickets we filed that nobody moves: push, hand to an agent, or archive each',mins_each=1,waits='nobody',pred=lambda c:not c.get('kind'))
batch('TICKET',key=lambda c:'local-agent',act='Hand the {n} local tickets with a ready brief to workers, ticket them in Notion, or drop them',mins_each=1,waits='nobody',pred=lambda c:c.get('kind')=='local-agent')
batch('TICKET',key=lambda c:'local-call',act='Your call on {n} local tickets filed needs-triage: read each file and set its Status, or defer it',mins_each=2,waits='person',pred=lambda c:c.get('kind')=='local-call')
batch('TICKET',key=lambda c:'local-human',act='Clear the {n} local tickets that wait for a person: close the ones whose PR landed, do or defer the rest',mins_each=2,waits='person',pred=lambda c:c.get('kind')=='local-human')
batch('STALE',act='Sweep {n} loop entries with no caption: keep (write the caption) or drop each',mins_each=1,waits='nobody',pred=lambda c:not (c.get('status') or '').strip())   # 2026-09-16: captioned entries stay single cards; the batch hid 13 live rows behind one 'drop'
live=[c for c in cards if c['score']>=0]
live.sort(key=lambda c:(-c['score'],c['ref']))
parked=[c for c in cards if c['score']<0]

# every card that ends in customer text (the ranked six plus the pins) carries its support memory: who the requester is,
# du/Sie, greeting, role, and which files say so. The thread's customer identity is cached (memory-cache.json), so this
# costs one Plain read per new thread. The full block for drafting: `memory.py show <ref>`.
CUSTOMER_TEXT_LANES={'REPLY','NOTIFY','PROMISE','FAST'}
if memory:
    for c in [x for x in live[:6]+[x for x in live[6:] if x['id'].startswith('fast:')] if x['lane'] in CUSTOMER_TEXT_LANES and (x.get('thread') or re.match(r'^T-\d+$',x.get('ref') or ''))]:
        try:
            ident=memory.thread_identity(c.get('thread') or c['ref'])
            if not ident: c['memory']={'summary':'memory: thread not readable','files':[]}; continue
            m=memory.lookup(ident['company'],ident['customer'],ident['tenant_field'],ident['domain'],ident['email'],ident['title'])
            c['memory']={'summary':m['summary'],'tenant':m['tenant'],'customer':ident['customer'],'name':ident['name'],'files':m['files'],
                         'knownIssues':[k['title'] for k in m['knownIssues']],'head':m['head']}
        except Exception as _e:
            c['memory']={'summary':f'memory: lookup failed ({str(_e)[:60]})','files':[]}

# ---------------------------------------------------------------- persist
for c in cards:
    desk['cards'][c['id']]={**desk['cards'].get(c['id'],{}),'seen':c['seen'],'lastSeen':today,'lane':c['lane'],'act':c['act'],'ref':c['ref'],'status':c.get('status')}
    if c.get('deferred'): desk['cards'][c['id']]['defer']=c['deferred']
_seen=set(); release_watch=[r for r in release_watch if not ((r['ref'],r['pr'],r['env']) in _seen or _seen.add((r['ref'],r['pr'],r['env'])))]
if not SCOPE: json.dump({'generatedAt':now.isoformat(),'rows':release_watch},open(f'{DESK}/release-watch.json','w'),indent=1,ensure_ascii=False)   # a tenant sitting sees a subset; the board files stay whole
import fcntl
_lock=open(f'{DESK}/.desk.lock','w'); fcntl.flock(_lock,fcntl.LOCK_EX)   # desk-close.py holds the same lock for its read-modify-write
if _desk_stamp()!=DESK_STAMP:
    print("desk.json changed while this run gathered (a card was closed); nothing written, run again",file=sys.stderr); sys.exit(75)
json.dump(desk,open(DESKFILE,'w'),indent=1,ensure_ascii=False)
# scopeArgs: what the pane's background run passes so a tenant or today desk stays scoped until a bare `desk.py` widens it.
_scope_args=(['--tenant',TENANT] if TENANT else [])+((['--today'] if '--today' in sys.argv and '--since' not in sys.argv else ['--since',SINCE]) if SINCE else [])
json.dump({'generatedAt':now.isoformat(),'scope':SCOPE or '','scopeArgs':_scope_args,'preflight':preflight,'cards':live,'parked':parked},open(f'{DESK}/cards.json','w'),indent=1,ensure_ascii=False)
# board-sync.json: what the board needs to group rows the way the desk does (2026-09-14, Tobias: board and desk must agree).
# parked: loop refs the desk holds on a trigger, with a readable "until"; live: loop refs the desk ranks as waiting on Tobias.
def _until(trig):
    trig=re.sub(r'^trigger:\s*','',trig).split(' -> act:')[0].strip()   # batch deferrals store "trigger: … -> act: …"
    if trig.startswith('deployed:'):
        _,env,prn=trig.split(':',2); return f"until #{prn} on {env}"
    if trig.startswith('pr-merged:'): return f"until {trig.split(':',1)[1].replace('orbot#','orbot #')} merges" if 'orbot' in trig else f"until #{trig.split(':',1)[1]} merges"
    if trig.startswith('a2a:'): return 'until the a2a decision is answered'
    if trig.startswith('appstore:'): return f"until Cockpit {trig.split('>=',1)[-1]} is in the App Store"
    if trig.startswith('manual:'): return 'until: '+trig[7:].split('->')[0].strip()[:40]
    if re.match(r'\d{4}-\d{2}-\d{2}$',trig): return f"until {trig}"
    if re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$',trig): return f"until {trig}"
    return 'until '+trig[:60]
def _refs(c):
    out=[]
    for m in (c.get('members') or [c]):
        r=m.get('ref') or ''
        if re.match(r'^T-\d+$',r) or r in state: out.append(r)   # 2026-09-16: ORB-/ORBIT-/SWEEP- loop entries count too, so board and desk group them alike
    return out
_parked={}
for c in parked:
    d=c.get('deferred') or {}
    for r in _refs(c): _parked.setdefault(r,{'id':c['id'],'trigger':d.get('trigger',''),'until':_until(d.get('trigger','')),'status':(d.get('status') if not c.get('members') else None)})   # status: the board drops the hold when the loop rewrites the caption, same rule as add()
# live: ref -> the loop caption the desk ranked; the board honours the entry only while that caption stands, so a
# caption the desk wrote on closing a card ("sent … agent working") moves the row off "waiting on you" at once.
_live={r:(state.get(r,{}).get('status') or '').strip() for c in live for r in _refs(c) if r not in _parked}
if not SCOPE: json.dump({'generatedAt':now.isoformat(),'parked':_parked,'live':_live},open(f'{DESK}/board-sync.json','w'),indent=1,ensure_ascii=False)   # same: never let a scoped run shrink the board

# ---------------------------------------------------------------- print
def link(c):
    if c.get('thread'): return f"[{c['ref']}]({PLAIN}{c['thread']})"
    if c.get('url'): return f"[{c['ref']}]({c['url']})"
    if c.get('pr') and c['ref'].startswith('#'): return f"[{c['ref']}]({GH}{c['pr']})"
    return c['ref']
def prlink(c): return f" [#{c['pr']}]({GH}{c['pr']})" if c.get('pr') and not c['ref'].startswith('#') and not c.get('url') else ''

wins=[l for l in desk['log'] if l.get('date')==today]
total=sum(c['mins'] for c in live)
if '--wins' in sys.argv:
    for w in wins: print(f"- {w['id']} → {w['verb']} {w.get('note','')}")
    print(f"{len(wins)} closed today"); sys.exit(0)
pins_live=[c['ref'] for c in live if c['id'].startswith('fast:')]   # tenant:<ref> cards share the FAST lane but are not pins
_today_live=[c['ref'] for c in live if c.get('ref') in TODAY_NEW]+[c['ref'] for c in live if c.get('ref') in TODAY_ANSWERED]
print(f"**Desk {now.strftime('%Y-%m-%dT%H:%MZ')}**{f' · scope: {SCOPE}' if SCOPE else ''}{(' · today: '+', '.join(_today_live)) if _today_live else ''}{(' · pinned: '+', '.join(pins_live)) if pins_live else ''} · {len({r for c in live for r in _refs(c)})} waiting on you ({len(live)} cards{(' incl. '+str(len([c for c in live if not _refs(c)]))+' outside the loop') if [c for c in live if not _refs(c)] else ''}, ~{total} min) · {len(parked)} parked · {len(wins)} closed today")
due=[f"{k} ({v['reason']})" for k,v in preflight.items() if v['due']]
quiet=[f"{k} ({v['reason']})" for k,v in preflight.items() if not v['due']]
print(f"PRE-FLIGHT: due → {'; '.join(due) if due else 'none'}" + (f" · quiet → {'; '.join(quiet)}" if quiet else '')
      + f" · memory {MEMORY.get('head')}" + ('' if MEMORY.get('ok') else f" (not pulled: {MEMORY.get('reason')})") + (f"; merged: PR {', '.join(MEMORY['synced'])}" if MEMORY.get('synced') else ''))
print()
print("TOP SIX (work from the top; the desk ranks again on every run)")
for i,c in enumerate(live[:6],1):
    exp=' · EXPIRING (seen 3+)' if c['seen']>=3 and c['lane']!='SEND' else ''
    print(f"{i}. {link(c)}{prlink(c)} · {c['lane']} · {c['mins']} min · {c['impact']} · {c['age']}d · score {c['score']}{exp} · `{c['id']}`\n   → {c['act']}")
    if c.get('memory'): print(f"   {c['memory']['summary']}" + (f" · known issue: {'; '.join(c['memory']['knownIssues'])}" if c['memory'].get('knownIssues') else ''))
if len(live)>6:
    print(f"\n… {len(live)-6} more behind these (`desk.py --all`).")
by={}
for c in live: by.setdefault(c['lane'],[]).append(c)
print("\nLANES: "+' · '.join(f"{k} {len(v)}" for k,v in sorted(by.items(),key=lambda kv:-len(kv[1]))))
if parked:
    print("\nPARKED: "+' · '.join(f"{c['ref']} until {c['deferred']['trigger']}" for c in parked))
if feed and feed.get('waitingOnOthers'):
    rows=[]
    for t in feed['waitingOnOthers']:
        h=a2a_age_h(t); seen='handed over' if t.get('acked') else 'not seen'
        rows.append(f"{t.get('owner')} · {t.get('kind') or 'lookup'} · {h:.0f}h · {seen} · {a2a_head(t,60)}")
    print("\nWAITING ON OTHERS (a2a): "+' | '.join(rows))
if '--all' in sys.argv:
    for lane,cs in by.items():
        print(f"\n## {lane} ({len(cs)})")
        for c in cs: print(f"  {link(c)}{prlink(c)} · {c['mins']} min · {c['impact']} · {c['age']}d · {c['score']} · `{c['id']}` · {c['status'][:80]}")
