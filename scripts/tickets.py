#!/usr/bin/env python3
"""tickets.py: the Notion follow-up tickets we created, linked to Plain threads and loop entries.

The cache ~/orbit-scratch/desk/notion-tickets.json is written by the desk mod (~/.claude/mods/desk) through the
Notion MCP, because no Notion API key exists locally. This module only reads it and sorts the tickets into the
buckets the desk and the pane show. `python3 tickets.py` prints the buckets."""
import datetime, json, os, re, sys

HOME = os.path.expanduser('~')
DESK = f'{HOME}/orbit-scratch/desk'
LOOP = f'{HOME}/orbit-scratch/agent-loop'
CACHE = f'{DESK}/notion-tickets.json'
OPEN = ('Ready to Execute', 'Ready to Estimate', 'Triage', 'Not started', 'Blocked')
RUNNING = ('In progress', 'In review')
SHIPPED = ('Done', 'Staging Released', 'Production Released')
IDLE_DAYS = 14
NOW = datetime.datetime.now(datetime.timezone.utc)


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _age_days(iso):
    try:
        t = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00').replace(' ', 'T'))
    except Exception:
        return 0
    return max(0, (NOW - t).days)


def _page_id(url):
    m = re.search(r'([0-9a-f]{32})', (url or '').replace('-', ''))
    return m.group(1) if m else ''


def cache_age_min():
    try:
        return round((NOW.timestamp() - os.stat(CACHE).st_mtime) / 60)
    except OSError:
        return None


def classify():
    """Buckets over the cached tickets. `threads` maps ticket id -> [{ref, status, threadId}] from the sweep cache
    (notion_ticket field) and the loop state (ORB-<id> in a note or the notion field)."""
    cache = _load(CACHE, {})
    tickets = cache.get('tickets') or []
    sweep = _load(f'{LOOP}/sweep-cache.json', {}).get('threads', {})
    state = _load(f'{LOOP}/state.json', {}).get('threads', {})
    archive = _load(f'{LOOP}/state-archive.json', {}).get('threads', {})
    by_page = {_page_id(t.get('url')): t for t in tickets if _page_id(t.get('url'))}
    by_id = {int(t['id']): t for t in tickets if t.get('id') is not None}
    links = {}

    def link(ticket, ref, status, thread_id=''):
        rows = links.setdefault(ticket['id'], [])
        if not any(r['ref'] == ref for r in rows):
            rows.append({'ref': ref, 'status': status, 'threadId': thread_id})

    for tid, row in sweep.items():
        fp = row.get('fingerprint') or {}
        fields = dict(x.split('=', 1) for x in (fp.get('fields') or '').split(';') if '=' in x)
        page = _page_id(fields.get('notion_ticket', ''))
        ref = (re.search(r'\[(T-\d+)\]', row.get('row') or '') or [None, ''])[1]
        if page in by_page and ref:
            link(by_page[page], ref, fp.get('status') or '', tid)
    for ref, entry in list(state.items()) + list(archive.items()):
        text = ' '.join(str(entry.get(k) or '') for k in ('note', 'status', 'notion'))
        for n in {int(x) for x in re.findall(r'ORB-(\d{3,4})', text, re.I)}:
            if n in by_id:
                thread = next((tid for tid, row in sweep.items() if f'[{ref}]' in (row.get('row') or '')), '')
                st = (sweep.get(thread) or {}).get('fingerprint', {}).get('status', '') if thread else ''
                link(by_id[n], ref, st, thread)

    idle, shipped_untold, running, open_, shipped = [], [], [], [], []
    for t in tickets:
        t = dict(t)
        t['age'] = _age_days(t.get('createdAt', ''))
        t['threads'] = links.get(t['id'], [])
        status = t.get('status') or ''
        if status in RUNNING:
            running.append(t)
        elif status in SHIPPED:
            shipped.append(t)
            # A linked thread still open on Plain: the customer waits for the news.
            if any(r['status'] in ('TODO', 'SNOOZED') for r in t['threads']):
                shipped_untold.append(t)
        elif status in OPEN:
            open_.append(t)
            # Nobody moves it: unassigned, no PR, and either old or not even in a sprint.
            if not t.get('assignee') and not t.get('prs') and (t['age'] >= IDLE_DAYS or not t.get('sprint')):
                idle.append(t)
    idle.sort(key=lambda t: -t['age'])
    return {
        'generatedAt': cache.get('generatedAt', ''), 'cacheAgeMin': cache_age_min(),
        'total': len(tickets), 'open': len(open_), 'running': len(running), 'shipped': len(shipped),
        'idle': idle, 'shippedUntold': shipped_untold, 'runningList': running,
        'withThread': sum(1 for t in tickets if links.get(t['id'])),
    }


if __name__ == '__main__':
    c = classify()
    print(f"{c['total']} tickets · {c['open']} open · {c['running']} running · {c['shipped']} shipped · "
          f"{len(c['idle'])} idle · {len(c['shippedUntold'])} shipped, customer not told · cache {c['cacheAgeMin']} min")
    for t in c['idle']:
        print(f"  idle   ORB-{t['id']} {t['age']:3d}d {t['status']:17} {t['title'][:70]}")
    for t in c['shippedUntold']:
        print(f"  untold ORB-{t['id']} {t['status']:17} {t['title'][:60]} ← {', '.join(r['ref'] for r in t['threads'])}")
