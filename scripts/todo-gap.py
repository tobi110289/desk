#!/usr/bin/env python3
"""Plain TODO vs loop board: which TODO threads the board does not show, and why.
Reads the loop's sweep-cache.json + state.json. Writes todo-gap.md and todo-gap.html
next to this file (dated copy on --snapshot). `--counts` prints one line for the board."""
import json,re,sys,html,os
from datetime import datetime
from zoneinfo import ZoneInfo
from deskconfig import PLAIN_THREAD
LOOP=os.path.expanduser('~/orbit-scratch/agent-loop')
# Data files live beside the symlink in the desk data dir, never beside the script in the repo (a run through the real
# path once left human-call.json, with thread ids and titles, inside the public repo folder).
HERE=os.path.expanduser('~/orbit-scratch/desk')
c=json.load(open(f'{LOOP}/sweep-cache.json'))['threads']; s=json.load(open(f'{LOOP}/state.json'))['threads']
try: s={**json.load(open(f'{LOOP}/state-archive.json'))['threads'],**s}   # done entries older than 7 days live there
except Exception: pass
# The sweep cache is a fingerprint cache: it keeps threads after they were marked DONE or SNOOZED in Plain, so the
# page must filter on Plain's live status. One paginated query lists every thread that is TODO right now.
# Without PLAIN_API_KEY (or on a failed request) fall back to the cached fingerprint status and say so on the page.
def live_todo():
    key=os.environ.get('PLAIN_API_KEY')
    if not key: return None,'PLAIN_API_KEY missing'
    sys.path.insert(0,os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import plain   # the one Plain reader
    q='query($after:String){ threads(first:100, after:$after, filters:{statuses:[TODO]}) { pageInfo { hasNextPage endCursor } edges { node { id ref title status } } } }'
    out={}; after=None
    for _ in range(20):
        try: d=plain.gql(q,{'after':after})['threads']
        except Exception as e: return None,f'Plain query failed: {e}'
        for e in d['edges']: out[e['node']['id']]=e['node']
        if not d['pageInfo']['hasNextPage']: return out,None
        after=d['pageInfo']['endCursor']
    return out,None
todo,warn=live_todo()
if todo is None:
    todo={tid:{'ref':v.get('ref'),'title':v.get('title')} for tid,v in c.items() if (v.get('fingerprint') or {}).get('status','TODO')=='TODO'}
dropped={tid:v for tid,v in c.items() if tid not in todo}
unswept={tid:n for tid,n in todo.items() if tid not in c}
c={tid:v for tid,v in c.items() if tid in todo}
for tid,n in unswept.items(): c[tid]={'ref':n.get('ref'),'title':n.get('title'),'state':'NOT-SWEPT','fingerprint':{}}
live={r for r,e in s.items() if e.get('phase')!='done'}; done={r for r,e in s.items() if e.get('phase')=='done'}
def ref_of(v):
    r=v.get('ref')
    if r: return r
    m=re.search(r'\[(T-\d+)\]',v.get('row') or ''); return m.group(1) if m else '?'
def title_of(v):
    t=v.get('title')
    if t: return t
    m=re.search(r'\)\s*(.*?)\s*\|',v.get('row') or ''); return (m.group(1) if m else '').strip()
def impact_of(v):
    i=v.get('impact')
    if i: return i
    m=re.search(r'impact_level=([^;]*)',(v.get('fingerprint') or {}).get('fields') or ''); return m.group(1) if m else '-'
BUCKETS=[
 ('A','on the board (live loop entry)','the loop owns it'),
 ('B','loop done, Plain thread still TODO','fix merged or shipped, customer not told, thread not closed'),
 ('C','no Hermes grade','no readiness field and no brief; Hermes never graded them'),
 ('D','ready-for-human','product or human call, no code task; nobody lists them'),
 ('E','in the loop queue','to validate, validated without worker, stale validation'),
 ('F','needs-info','waiting on the customer'),
 ('G','feature request',''),
 ('H','wontfix','should be closed in Plain'),
 ('I','duplicate','should be linked and closed'),
 ('J','TODO in Plain, not in the sweep cache','the sweeper never saw it; check probe-sweep.sh'),
]
def bucket(ref,st,readiness=''):
    if st=='NOT-SWEPT': return 'J'
    if ref in live: return 'A'
    if ref in done or 'IN-FLIGHT done' in st: return 'B'
    if st=='NOT-READY' and readiness:
        # the sweeper writes a bare NOT-READY for threads Hermes graded on the merits; the field says which grade
        return {'ready-for-human':'D','wontfix':'H','needs-info':'F','needs-triage':'E'}.get(readiness,'C')   # the housekeeper drops done entries; the sweeper keeps 'IN-FLIGHT done' in the state
    if 'already-shipped' in st: return 'B'   # 2026-09-24: sweeper suffix for a merged remedy; the customer note is the gap
    if 'needs-evidence' in st: return 'E'    # 2026-09-24: internal evidence tasks first, then re-validate
    if 'wontfix' in st: return 'H'
    if 'duplicate' in st: return 'I'
    if 'needs-info' in st: return 'F'
    if 'ready-for-human' in st or 'rfh' in st or st.startswith('HUMAN-OWNED'): return 'D'
    if st.startswith('NOT-READY(feature'): return 'G'
    if st.startswith('NOT-READY'): return 'C'
    return 'E'
rows={k:[] for k,_,_ in BUCKETS}
for tid,v in c.items():
    ref=ref_of(v); st=v.get('state') or '?'; title=title_of(v); lv=todo.get(tid) or {}
    if ref=="?" and lv.get("ref"): ref=lv["ref"]          # the sweep cache has no ref/title for some threads; Plain does
    if not title and lv.get("title"): title=lv["title"]
    rd=re.search(r'agent_readiness=([^;]*)',(v.get('fingerprint') or {}).get('fields') or ''); rd=rd.group(1) if rd else ''
    rows[bucket(ref,st,rd)].append((ref,impact_of(v),title,(v.get('latestPassAt') or '')[:10],st if st!='NOT-READY' or not rd else f'NOT-READY({rd})',tid))
now=datetime.now(ZoneInfo('Europe/Berlin')).strftime('%Y-%m-%d %H:%M')
# Bucket D on the board (Tobias, 2026-09-10): ready-for-human threads have no code task and nobody lists them.
# The board reads human-call.json; the caption is the state string without its NOT-READY(...) wrapper.
ORDER={'P0':0,'RBP':1,'RBS':2,'P1':3,'P2':4}
def call_of(st):
    m=re.match(r'^[A-Z-]+\((.*)\)$',st); c=m.group(1) if m else st
    return re.sub(r'^(hermes|hand-graded|hermes delta [^ ]+ [^ ]+)\s*:?\s*','',c).strip(' ;:')
# Decision briefs (Tobias, 2026-09-14: "enough context to make a decision and a recommended next move"): human-call-briefs.json,
# written by human-call-briefs.py merge from the codex brief jobs. A brief is stale when the thread's newest timeline entry
# (sweep-cache fingerprint) is newer than the entry the brief was read from; the board says so instead of hiding it.
BR=(json.load(open(f'{HERE}/human-call-briefs.json')) if os.path.exists(f'{HERE}/human-call-briefs.json') else {}).get('briefs',{})
def with_brief(r):
    b=BR.get(r[5]) or {}; newest=((c.get(r[5]) or {}).get('fingerprint') or {}).get('newestEntryId')
    row={'ref':r[0],'impact':r[1],'title':r[2],'lastPass':r[3],'call':call_of(r[4])[:120],'thread':r[5]}
    if b: row.update({k:b.get(k) for k in ('question','context','owner','options','recommend','askedOn','lastCustomerAt','lastOrbitAt','confidence','asOfAt')}); row['stale']=bool(newest and b.get('asOfEntryId')!=newest); row['decision']=(b.get('decision') or {}).get('text')
    return row
json.dump({'generatedAt':now,'rows':[with_brief(r) for r in sorted(rows['D'],key=lambda x:(ORDER.get(x[1],9),x[0]))]},open(f'{HERE}/human-call.json','w'),indent=1,ensure_ascii=False)
if '--counts' in sys.argv:
    print(' · '.join(f'{k} {len(rows[k])}' for k,_,_ in BUCKETS if k!='A'), f'({len(c)} TODO threads, {len(c)-len(rows["A"])} not on the board)'); sys.exit()
src=(f'Plain live status ({len(dropped)} cached threads no longer TODO dropped)' if warn is None else f'cached fingerprint status only: {warn}')
md=[f'# Plain TODO vs board · {now} Berlin', f'\n{len(c)} threads TODO in Plain · {len(rows["A"])} on the board · {len(c)-len(rows["A"])} not shown · source: {src}\n',
    '| bucket | count | why |','|---|---|---|']+[f'| {k}. {n} | {len(rows[k])} | {w} |' for k,n,w in BUCKETS]
for k,n,w in BUCKETS:
    md.append(f'\n## {k}. {n} ({len(rows[k])})\n')
    for r in sorted(rows[k],key=lambda x:(x[1],x[0])):
        md.append(f'- [{r[0]}]({PLAIN_THREAD}{r[5]}) {r[1]} · {r[2][:70]} · {r[3]} · `{r[4][:90]}`')
open(f'{HERE}/todo-gap.md','w').write('\n'.join(md))
if '--snapshot' in sys.argv: open(f'{HERE}/todo-gap-{now[:10]}.md','w').write('\n'.join(md))
esc=html.escape
h=[f'<title>Plain TODO Gap</title><style>body{{font:15px/1.5 -apple-system,system-ui,sans-serif;background:#F3F5F8;color:#141A22;margin:0;padding:32px 20px}}main{{max-width:980px;margin:0 auto}}h1{{font-size:20px}}h2{{font-size:14px;letter-spacing:.06em;text-transform:uppercase;color:#5C6774;margin:28px 0 8px}}table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #D9DEE6;border-radius:6px}}td,th{{padding:6px 10px;border-top:1px solid #D9DEE6;text-align:left;vertical-align:top;font-size:14px}}th{{font-size:12px;color:#5C6774}}code{{font:12px ui-monospace,monospace;color:#5C6774}}a{{color:#1D5FCC;text-decoration:none}}.id{{font:500 13px ui-monospace,monospace;white-space:nowrap}}@media(prefers-color-scheme:dark){{body{{background:#0F1319;color:#E7ECF2}}table{{background:#171C24;border-color:#2A323D}}td,th{{border-color:#2A323D}}a{{color:#6EA1FF}}}}</style><main>',
   f'<h1>Plain TODO vs board <small style="color:#5C6774;font-weight:400">{now} Berlin</small></h1>',
   f'<p>{len(c)} threads TODO in Plain · {len(rows["A"])} on the board · <b>{len(c)-len(rows["A"])} not shown</b>. Status: {esc(src)}. Source: <code>~/orbit-scratch/desk/todo-gap.py</code>; the loop board links here.</p>',
   '<table><tr><th>bucket</th><th>count</th><th>why</th></tr>']
for k,n,w in BUCKETS: h.append(f'<tr><td><a href="#{k}">{k}. {esc(n)}</a></td><td>{len(rows[k])}</td><td>{esc(w)}</td></tr>')
h.append('</table>')
for k,n,w in BUCKETS:
    h.append(f'<h2 id="{k}">{k}. {esc(n)} ({len(rows[k])})</h2><table><tr><th>ref</th><th>impact</th><th>title</th><th>last pass</th><th>loop state</th></tr>')
    for r in sorted(rows[k],key=lambda x:(x[1],x[0])):
        h.append(f'<tr><td class="id"><a href="{PLAIN_THREAD}{r[5]}">{esc(r[0])}</a></td><td>{esc(r[1])}</td><td>{esc(r[2][:80])}</td><td>{esc(r[3])}</td><td><code>{esc(r[4][:100])}</code></td></tr>')
    h.append('</table>')
h.append('</main>')
open(f'{HERE}/todo-gap.html','w').write('\n'.join(h))
print(f'wrote todo-gap.md + todo-gap.html ({len(c)} threads, {len(c)-len(rows["A"])} not on the board)')
