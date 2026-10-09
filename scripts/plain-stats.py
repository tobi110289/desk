#!/usr/bin/env python3
"""plain-stats.py: the Plain numbers the desk pane shows. Writes ~/orbit-scratch/desk/plain-stats.json.

Read-only. One paginated Plain read (TODO + SNOOZED threads, newest 10 timeline entries each) plus the local
loop state, sweep cache and cards cache, so the pane can say how Plain and the desk line up. `--print` also
prints the JSON. Exit 2 when Plain is unreachable (the previous file stays)."""
import datetime, json, os, re, sys, zoneinfo

HOME = os.path.expanduser('~')
DESK = f'{HOME}/orbit-scratch/desk'
LOOP = f'{HOME}/orbit-scratch/agent-loop'
OUT = f'{DESK}/plain-stats.json'
BERLIN = zoneinfo.ZoneInfo('Europe/Berlin')
sys.path.insert(0, f'{LOOP}/jev')
import plain  # noqa: E402
import deskconfig  # noqa: E402

Q = '''query($after:String,$st:[ThreadStatus!]){ threads(first:100, after:$after, filters:{statuses:$st}) {
  pageInfo{ hasNextPage endCursor }
  edges{ node{ id ref title status createdAt{ iso8601 } priority
    customer{ fullName company{ name } }
    threadFields{ key stringValue }
    timelineEntries(first:10){ edges{ node{ timestamp{ iso8601 } actor{ __typename } entry{ __typename } } } } } } } }'''

NOW = datetime.datetime.now(datetime.timezone.utc)
TODAY = NOW.astimezone(BERLIN).strftime('%Y-%m-%d')
WEEK_AGO = (NOW - datetime.timedelta(days=7)).isoformat()


def parse(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except Exception:
        return None


def day(iso):
    t = parse(iso or '')
    return t.astimezone(BERLIN).strftime('%Y-%m-%d') if t else ''


def age_h(iso):
    t = parse(iso or '')
    return round((NOW - t).total_seconds() / 3600, 1) if t else None


def file_age_min(path):
    try:
        return round((NOW.timestamp() - os.stat(path).st_mtime) / 60)
    except OSError:
        return None


def threads():
    after, out = None, []
    while True:
        d = plain.try_gql(Q, {'after': after, 'st': ['TODO', 'SNOOZED']})
        if not d:
            return None
        for e in d['threads']['edges']:
            n = e['node']
            fields = {x['key']: x.get('stringValue') for x in n.get('threadFields') or []}
            entries = [x['node'] for x in (n.get('timelineEntries') or {}).get('edges') or []]
            # Notes are internal; the customer only sees the rest.
            visible = [x for x in entries if (x.get('entry') or {}).get('__typename') != 'NoteEntry']
            newest = visible[0] if visible else (entries[0] if entries else {})
            actor = (newest.get('actor') or {}).get('__typename') or ''
            cust = next((x for x in entries if (x.get('actor') or {}).get('__typename') == 'CustomerActor'), None)
            c = n.get('customer') or {}
            out.append({
                'id': n['id'], 'ref': n['ref'], 'title': n.get('title') or '', 'status': n.get('status') or '',
                'priority': n.get('priority'), 'impact': fields.get('impact_level') or '',
                'readiness': fields.get('agent_readiness') or '',
                'customer': c.get('fullName') or '', 'company': (c.get('company') or {}).get('name') or '',
                'createdAt': (n.get('createdAt') or {}).get('iso8601') or '',
                'customerAt': ((cust or {}).get('timestamp') or {}).get('iso8601') or '',
                'newestAt': (newest.get('timestamp') or {}).get('iso8601') or '',
                'lastSpoke': {'CustomerActor': 'customer', 'UserActor': 'us', 'MachineUserActor': 'bot',
                              'SystemActor': 'system'}.get(actor, actor or 'nobody'),
            })
        if not d['threads']['pageInfo']['hasNextPage']:
            break
        after = d['threads']['pageInfo']['endCursor']
    return out


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def main():
    ts = threads()
    if ts is None:
        print('plain unreachable', file=sys.stderr)
        sys.exit(2)
    todo = [t for t in ts if t['status'] == 'TODO']
    snoozed = [t for t in ts if t['status'] == 'SNOOZED']
    waiting_on_us = [t for t in todo if t['lastSpoke'] == 'customer']
    waiting_on_customer = [t for t in ts if t['lastSpoke'] in ('us', 'bot')]
    new_today = [t for t in ts if day(t['createdAt']) == TODAY]
    new_week = [t for t in ts if t['createdAt'] >= WEEK_AGO]
    replied_today = [t for t in ts if day(t['customerAt']) == TODAY]
    oldest_unanswered = max((age_h(t['customerAt']) or 0 for t in waiting_on_us), default=0)
    oldest_open = max((age_h(t['createdAt']) or 0 for t in todo), default=0)
    readiness = {}
    for t in todo:
        k = t['readiness'] or 'ungraded'
        readiness[k] = readiness.get(k, 0) + 1

    # How the desk and the loop line up with Plain's TODO list.
    state = load(f'{LOOP}/state.json', {}).get('threads', {})
    todo_refs = {t['ref'] for t in todo}
    all_refs = {t['ref'] for t in ts}
    loop_live = {r for r, t in state.items() if t.get('phase') not in ('done', None) and r in todo_refs}
    loop_live_any = {r for r, t in state.items() if t.get('phase') not in ('done', None) and r in all_refs}
    loop_done_todo = {r for r, t in state.items() if t.get('phase') == 'done' and r in todo_refs}
    cards = load(f'{DESK}/cards.json', {})
    on_desk_any = set()
    for c in cards.get('cards', []):
        for m in c.get('members') or []:
            if m.get('ref') in all_refs:
                on_desk_any.add(m['ref'])
        if c.get('ref') in all_refs:
            on_desk_any.add(c['ref'])
    on_desk = on_desk_any & todo_refs
    parked_any = {c.get('ref') for c in cards.get('parked', []) if c.get('ref') in all_refs}
    parked = parked_any & todo_refs
    sweep = load(f'{LOOP}/sweep-cache.json', {}).get('threads', {})
    unswept = [t['ref'] for t in todo if t['id'] not in sweep]
    covered = loop_live | on_desk | parked
    uncovered = sorted(todo_refs - covered)
    # Owned at all (TODO or snoozed): what the NEWEST rows show.
    owned = loop_live_any | on_desk_any | parked_any

    try:
        sys.path.insert(0, DESK); import tickets as _tk
        _tc = _tk.classify()
        ticket_stats = {
            'total': _tc['total'], 'open': _tc['open'], 'running': _tc['running'], 'shipped': _tc['shipped'],
            'idle': len(_tc['idle']), 'shippedUntold': len(_tc['shippedUntold']), 'withThread': _tc['withThread'],
            'cacheAgeMin': _tc['cacheAgeMin'], 'oldestIdleDays': (_tc['idle'][0]['age'] if _tc['idle'] else 0),
            'untoldRefs': [f"ORB-{t['id']}←{','.join(r['ref'] for r in t['threads'])}" for t in _tc['shippedUntold']][:6],
        }
    except Exception:
        ticket_stats = None
    # Our open PRs, from the desk's PR cache (jev/pr_health.py rebuilds it every 15 min).
    prs = []
    try:
        ph = load(f'{DESK}/pr-health.json', {})
        for r in sorted((ph.get('prs') or {}).values(), key=lambda r: -r['pr']):
            labels = r.get('labels') or []
            header = (r.get('last_round') or {}).get('header') or ''
            if r.get('draft'): state = 'draft'
            elif r.get('ci_red'): state = 'ci red'
            elif r.get('mergeable') == 'CONFLICTING': state = 'conflict'
            elif r.get('needs_human'): state = 'human review'
            elif r.get('review') == 'CHANGES_REQUESTED': state = 'changes requested'
            elif 'Returned to author' in header: state = 'returned'
            elif 'auto-review' in labels: state = 'horst queued'
            elif r.get('review') == 'APPROVED': state = 'approved'
            else: state = 'no review asked'
            prs.append({'pr': r['pr'], 'repo': 'orbit', 'title': (r.get('title') or '')[:80], 'state': state, 'rounds': r.get('rounds') or 0,
                        'commitDays': r.get('days_since_commit'), 'ageDays': r.get('age_days'), 'loop': r.get('loop'), 'stuck': bool(r.get('stuck')),
                        'url': r.get('url') or f"https://github.com/{deskconfig.REPO}/pull/{r['pr']}"})
        for r in ph.get('orbot') or []:
            state = 'draft' if r.get('isDraft') else ('conflict' if r.get('mergeable') == 'CONFLICTING' else ('changes requested' if r.get('reviewDecision') == 'CHANGES_REQUESTED' else 'waiting on Tobias'))
            prs.append({'pr': r['number'], 'repo': 'orbot', 'title': (r.get('title') or '')[:80], 'state': state, 'rounds': 0,
                        'commitDays': age_h(r.get('updatedAt', '')) and round(age_h(r['updatedAt']) / 24), 'ageDays': None, 'loop': None, 'stuck': False,
                        'url': f"https://github.com/{deskconfig.BOT_REPO}/pull/{r['number']}"})
        pr_stats = {'generatedAt': ph.get('generatedAt', ''), 'cacheAgeMin': file_age_min(f'{DESK}/pr-health.json'), 'list': prs,
                    'byState': {}}
        for x in prs: pr_stats['byState'][x['state']] = pr_stats['byState'].get(x['state'], 0) + 1
    except Exception:
        pr_stats = None
    newest = sorted(ts, key=lambda t: t['newestAt'], reverse=True)[:8]
    out = {
        'generatedAt': NOW.isoformat(timespec='seconds'),
        'todo': len(todo), 'snoozed': len(snoozed),
        'newToday': len(new_today), 'newWeek': len(new_week), 'customerRepliesToday': len(replied_today),
        'waitingOnUs': len(waiting_on_us), 'oldestUnansweredH': oldest_unanswered,
        'waitingOnCustomer': len(waiting_on_customer), 'oldestOpenDays': round(oldest_open / 24),
        'readiness': readiness,
        'sync': {
            'loopLive': len(loop_live), 'loopDoneStillTodo': len(loop_done_todo), 'onDesk': len(on_desk),
            'parked': len(parked), 'uncovered': len(uncovered), 'uncoveredRefs': uncovered[:12],
            'unswept': len(unswept), 'loopStateAgeMin': file_age_min(f'{LOOP}/state.json'),
            'sweepCacheAgeMin': file_age_min(f'{LOOP}/sweep-cache.json'),
            'deskAgeMin': file_age_min(f'{DESK}/cards.json'),
        },
        'newest': [{
            'ref': t['ref'], 'title': t['title'][:70], 'status': t['status'], 'impact': t['impact'],
            'who': t['customer'] or t['company'], 'lastSpoke': t['lastSpoke'], 'ageH': age_h(t['newestAt']),
            'isNew': day(t['createdAt']) == TODAY, 'onDesk': t['ref'] in owned,
        } for t in newest],
        'titles': {t['ref']: t['title'][:90] for t in ts},
        'tickets': ticket_stats,
        'prs': pr_stats,
        'waitingOnUsRefs': sorted(((t['ref'], age_h(t['customerAt']) or 0) for t in waiting_on_us), key=lambda x: -x[1])[:8],
    }
    tmp = OUT + '.tmp'
    json.dump(out, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)
    if '--print' in sys.argv:
        print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
