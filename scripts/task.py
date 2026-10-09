#!/usr/bin/env python3
"""task.py — local tasks for the desk (lane TASK). Stored in ~/orbit-scratch/desk/tasks.json, never anywhere else.

  task.py add "<title>" [--step "<step>"]... [--body "<context>"] [--mins N] [--impact P1|P2|P3]
  task.py list            open tasks, one line each
  task.py show <id>       title, steps and context of one task (id with or without the task: prefix)
  task.py done <id> [note]   mark it done (desk-close.py does this for a task: card that gets a verb)
  task.py drop <id>       delete it

Tobias 2026-10-09: work that has no Plain thread, no PR and no Notion ticket (a help-center sweep, a cleanup) still
belongs on the desk. A task is one card, `task:<slug>`, until it gets a verb."""
import json,sys,os,re,datetime
P=os.path.expanduser('~/orbit-scratch/desk/tasks.json')
def load():
    try: return json.load(open(P))
    except Exception: return {'tasks':[]}
def save(d):
    tmp=P+'.tmp'; json.dump(d,open(tmp,'w'),indent=1,ensure_ascii=False); os.replace(tmp,P)
def slug(title,taken):
    s=re.sub(r'[^a-z0-9]+','-',title.lower()).strip('-')[:40].strip('-') or 'task'
    base=s; n=2
    while s in taken: s=f'{base}-{n}'; n+=1
    return s
def find(d,tid):
    tid=tid[5:] if tid.startswith('task:') else tid
    return next((t for t in d['tasks'] if t['id']==tid),None)
def opt(name,default=None):
    if name in sys.argv:
        i=sys.argv.index(name); v=sys.argv[i+1]; del sys.argv[i:i+2]; return v
    return default
def mark_done(tid,note=''):
    """Called by desk-close.py too; returns False when the id is unknown."""
    d=load(); t=find(d,tid)
    if not t: return False
    t['done']=datetime.date.today().isoformat()
    if note: t['doneNote']=note
    save(d); return True
if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'list'
    d=load()
    if cmd=='add':
        steps=[]
        while '--step' in sys.argv: steps.append(opt('--step'))
        body=opt('--body',''); mins=int(opt('--mins','10')); impact=opt('--impact','P2')
        title=' '.join(sys.argv[2:]).strip()
        if not title: sys.exit('task.py add "<title>" [--step "<step>"]...')
        t={'id':slug(title,{x['id'] for x in d['tasks']}),'title':title,'steps':steps,'body':body,'mins':mins,'impact':impact,
           'since':datetime.date.today().isoformat()}
        d['tasks'].append(t); save(d); print(f"task:{t['id']} added ({len(steps)} steps)")
    elif cmd=='list':
        for t in d['tasks']:
            if not t.get('done'): print(f"task:{t['id']} · {t['impact']} · since {t['since']} · {t['title']} ({len(t.get('steps') or [])} steps)")
    elif cmd=='show':
        t=find(d,sys.argv[2]) or sys.exit(f'no task {sys.argv[2]}')
        print(f"# {t['title']}\ntask:{t['id']} · {t['impact']} · {t['mins']} min · since {t['since']}"+(f" · done {t['done']}" if t.get('done') else ''))
        for i,s in enumerate(t.get('steps') or [],1): print(f'{i}. {s}')
        if t.get('body'): print('\n'+t['body'])
    elif cmd=='done':
        print('done' if mark_done(sys.argv[2],' '.join(sys.argv[3:])) else f'no task {sys.argv[2]}')
    elif cmd=='drop':
        t=find(d,sys.argv[2]) or sys.exit(f'no task {sys.argv[2]}')
        d['tasks'].remove(t); save(d); print(f"task:{t['id']} dropped")
    else: sys.exit(__doc__)
