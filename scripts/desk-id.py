#!/usr/bin/env python3
"""desk-id.py <D-12 | 12 | card id | ref> ...  — which card a desk number names, and the number of a card.

The gather gives every card the desk's own short number once (desk.py, desk.json "numbers"). The number stays with the
card id after the card has left the desk, so "D-12" in an old message still resolves.

  desk-id.py D-12        -> D-12  loop:T-1  STALE  T-1  on the desk: <act>
  desk-id.py T-1         -> every numbered card of that ref
  desk-id.py --json D-12 -> the same as JSON lines

Reads desk.json and cards.json. Writes nothing.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402

DESK = deskconfig.DESK


def load(name, default):
    try:
        return json.load(open(f'{DESK}/{name}'))
    except Exception:
        return default


def rows(query, desk, cache):
    """The numbered cards a query names: a number, a card id or a ref."""
    ids = (desk.get('numbers') or {}).get('ids') or {}
    live = {c.get('id'): c for c in (cache.get('cards') or []) + (cache.get('parked') or [])}
    known = desk.get('cards') or {}
    m = re.fullmatch(r'(?:[Dd]-?)?(\d+)', query.strip())
    if m:
        hit = [cid for cid, no in ids.items() if no == int(m.group(1))]
    else:
        hit = [cid for cid in ids if cid == query or (live.get(cid) or known.get(cid) or {}).get('ref') == query]
    out = []
    for cid in sorted(hit, key=lambda c: ids[c]):
        card = live.get(cid) or known.get(cid) or {}
        out.append({'no': ids[cid], 'id': cid, 'lane': card.get('lane') or '', 'ref': card.get('ref') or '',
                    'onDesk': cid in live, 'act': card.get('act') or '', 'lastSeen': (known.get(cid) or {}).get('lastSeen') or ''})
    return out


def main(argv):
    as_json = '--json' in argv
    queries = [a for a in argv if not a.startswith('--')]
    if not queries:
        print(__doc__); return 2
    desk, cache = load('desk.json', {}), load('cards.json', {})
    found = 0
    for q in queries:
        for r in rows(q, desk, cache):
            found += 1
            if as_json:
                print(json.dumps(r, ensure_ascii=False))
            else:
                where = 'on the desk' if r['onDesk'] else f"left the desk, last seen {r['lastSeen'] or 'unknown'}"
                print(f"D-{r['no']}  {r['id']}  {r['lane']}  {r['ref']}  {where}: {r['act']}")
    if not found:
        print('no numbered card matches', file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
