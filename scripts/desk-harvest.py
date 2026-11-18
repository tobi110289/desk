#!/usr/bin/env python3
"""desk-harvest.py — the queue of facts the desk learned about tenants and requesters, asked in one go.

2026-11-10: the queue is worked one line at a time, oldest first, like held runs behind a lock. The batch question never
ran: 49 lines waited from 26 Oct on, because one question holds four options at most and nothing but the `m` key
started it. `next` hands out the oldest line with its id; `done <id> stored|skipped` settles exactly that line, so a
walk that stops half way keeps the rest.

2026-10-23: a card used to end in its own MEMORY question. Now every card (a pane pick that ran in the background,
a card the session worked) drops what it learned here, and the person answers one question for all of it when they
press `m` on the pane or run `/desk harvest`. The session then runs `memory.py propose` for each picked line and
marks the batch asked.

  desk-harvest.py add <card id> --ref <ref> [--tenant <id>] [--customer c_…] "<line>" ["<line>" …]
  desk-harvest.py list [--json]      the lines not yet asked, grouped by tenant
  desk-harvest.py mark [--all | <card id> …]   mark the lines asked (after the question, picked or not)
  desk-harvest.py count              the number of lines not yet asked (the pane reads it)
  desk-harvest.py next               the oldest line not yet asked, as JSON with its id and how many wait; {} when empty
  desk-harvest.py done <id> stored|skipped   settle that one line

A line is one plain fact with its source and confidence in words, e.g. "Dana is the dispatch lead (she says so in the
thread; high)". File: harvest.json in the desk folder (deskconfig.py; DESK_DIR for tests).
"""
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402
DESK = deskconfig.DESK
PATH = f'{DESK}/harvest.json'


def load():
    try:
        return json.load(open(PATH))
    except Exception:
        return {'items': []}


def save(d):
    tmp = f'{PATH}.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def _norm(line):
    return ' '.join((line or '').lower().split())


def add(card_id, lines, ref='', tenant='', customer=''):
    d = load()
    # 2026-11-11: a line is known by its text, whichever card found it and whether it was asked or not, so a fact
    # that two cards learn, or one the person already skipped, is not asked again.
    have = {_norm(i['line']) for i in d['items']}
    n = 0
    for line in lines:
        line = (line or '').strip()
        if not line or _norm(line) in have:
            continue
        have.add(_norm(line))
        d['items'].append({'cardId': card_id, 'ref': ref, 'tenant': tenant, 'customer': customer, 'line': line,
                           'addedAt': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'asked': False})
        n += 1
    if n:
        save(d)
    return n


def pending():
    return [i for i in load()['items'] if not i.get('asked')]


def id_of(item):
    return hashlib.sha1(f"{item['cardId']}|{item['line']}".encode()).hexdigest()[:8]


def settle(item_id, outcome):
    d = load()
    for i in d['items']:
        if not i.get('asked') and id_of(i) == item_id:
            i['asked'] = True; i['outcome'] = outcome
            i['askedAt'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            save(d)
            return True
    return False


def mark(card_ids=None):
    d = load()
    n = 0
    for i in d['items']:
        if not i.get('asked') and (card_ids is None or i['cardId'] in card_ids):
            i['asked'] = True; i['askedAt'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'); n += 1
    save(d)
    return n


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__); return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == 'count':
        print(len(pending())); return 0
    if cmd == 'list':
        items = pending()
        if '--json' in rest:
            print(json.dumps(items, ensure_ascii=False, indent=1)); return 0
        if not items:
            print('nothing to harvest'); return 0
        by = {}
        for i in items:
            by.setdefault(i.get('tenant') or '(no tenant)', []).append(i)
        for t, rows in by.items():
            print(f'## {t}')
            for i in rows:
                print(f"- [{i['cardId']} / {i.get('ref') or '-'}{' / ' + i['customer'] if i.get('customer') else ''}] {i['line']}")
        return 0
    if cmd == 'next':
        items = sorted(pending(), key=lambda i: i.get('addedAt') or '')
        if not items:
            print('{}'); return 0
        first = items[0]
        print(json.dumps({'id': id_of(first), 'waiting': len(items), **{k: first.get(k) for k in ('cardId', 'ref', 'tenant', 'customer', 'line', 'addedAt')}},
                         ensure_ascii=False, indent=1)); return 0
    if cmd == 'done':
        if len(rest) < 2 or rest[1] not in ('stored', 'skipped'):
            print('usage: desk-harvest.py done <id> stored|skipped'); return 2
        if not settle(rest[0], rest[1]):
            print(f'no waiting line with id {rest[0]}'); return 1
        print(f'{rest[1]}; {len(pending())} lines wait'); return 0
    if cmd == 'mark':
        ids = None if '--all' in rest or not rest else [a for a in rest if not a.startswith('--')]
        print(f'marked {mark(ids)} lines asked'); return 0
    if cmd == 'add':
        def opt(name):
            return rest[rest.index(name) + 1] if name in rest else ''
        card_id = rest[0]
        skip = set()
        for name in ('--ref', '--tenant', '--customer'):
            if name in rest:
                k = rest.index(name); skip.update({k, k + 1})
        lines = [a for k, a in enumerate(rest) if k > 0 and k not in skip]
        print(f"added {add(card_id, lines, opt('--ref'), opt('--tenant'), opt('--customer'))} lines"); return 0
    print(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]) or 0)
