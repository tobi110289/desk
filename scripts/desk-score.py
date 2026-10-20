#!/usr/bin/env python3
"""desk-score.py — how well the prepared briefs guess what the user does.

desk-close.py appends one line per closed card that had a brief to ~/orbit-scratch/desk/score.jsonl: the brief's
recommended option and draft (the guess), the option the user pressed and the verb recorded (the outcome). This script
reads the ledger and prints the hit rate, overall, per lane and per day, plus the misses.

A hit on the option: the pressed label equals the recommended one (without a pick, the recorded verb equals the
recommended option's verb). A hit on the draft: a brief with a draft whose pick was "Send it" or "I sent it already";
"Edit then send" is a miss (the draft needed work). Defer and Drop say nothing about the draft and are left out.

  desk-score.py [--since YYYY-MM-DD] [--days N] [--misses] [--json]

The number to watch is the option hit rate per lane over two weeks. When it holds above 90 % for a lane, that lane's
recommended option can run without a pick; until then the brief only saves reading time.
"""
import collections
import datetime
import json
import os
import sys

LEDGER = os.path.expanduser('~/orbit-scratch/desk/score.jsonl')
SENT = {'Send it', 'I sent it already'}
NO_SIGNAL = {'Defer', 'Defer until a trigger', 'Drop', 'Drop (unpin)', 'Drop (accepted risk, recorded)', 'Drop (reply as human: declined)'}


def rows(since):
    try:
        lines = open(LEDGER, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and (r.get('date') or '') < since:
            continue
        out.append(r)
    return out


def option_hit(r):
    g, d = r.get('guess') or {}, r.get('did') or {}
    if not g.get('option'):
        return None
    if d.get('pick'):
        return d['pick'] == g['option']
    return d.get('verb') == g.get('verb') if g.get('verb') else None


def draft_hit(r):
    g, d = r.get('guess') or {}, r.get('did') or {}
    if not (g.get('draft') or '').strip() or not d.get('pick') or d['pick'] in NO_SIGNAL:
        return None
    return d['pick'] in SENT


def rate(pairs):
    judged = [h for h in pairs if h is not None]
    return (sum(judged), len(judged))


def fmt(hits, n):
    return f'{hits}/{n}' + (f' ({100 * hits // n} %)' if n else '')


def main(argv):
    since, misses, as_json = None, False, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--since':
            since = argv[i + 1]; i += 1
        elif a == '--days':
            since = (datetime.date.today() - datetime.timedelta(days=int(argv[i + 1]))).isoformat(); i += 1
        elif a == '--misses':
            misses = True
        elif a == '--json':
            as_json = True
        elif a in ('-h', '--help'):
            print(__doc__); return 0
        i += 1
    rs = rows(since)
    if not rs:
        print(f'no scored closes since {since}' if since and rows(None) else f'no scored closes yet ({LEDGER}); a close of a card that had a brief writes the first line'); return 0
    by_lane = collections.defaultdict(list)
    by_day = collections.defaultdict(list)
    for r in rs:
        by_lane[r.get('lane') or '?'].append(r)
        by_day[r.get('date') or '?'].append(r)
    report = {
        'since': since, 'scored': len(rs),
        'option': rate([option_hit(r) for r in rs]), 'draft': rate([draft_hit(r) for r in rs]),
        'lanes': {k: {'scored': len(v), 'option': rate([option_hit(r) for r in v]), 'draft': rate([draft_hit(r) for r in v])} for k, v in sorted(by_lane.items())},
        'days': {k: {'scored': len(v), 'option': rate([option_hit(r) for r in v])} for k, v in sorted(by_day.items())},
    }
    if as_json:
        print(json.dumps(report, indent=1, ensure_ascii=False)); return 0
    print(f"scored closes: {len(rs)}" + (f' since {since}' if since else ''))
    print(f"option hit: {fmt(*report['option'])}   draft sent as drafted: {fmt(*report['draft'])}")
    print('\nper lane')
    for k, v in report['lanes'].items():
        print(f"  {k:8} {v['scored']:3}  option {fmt(*v['option']):14}  draft {fmt(*v['draft'])}")
    print('\nper day')
    for k, v in report['days'].items():
        print(f"  {k}  {v['scored']:3}  option {fmt(*v['option'])}")
    if misses:
        print('\nmisses (guess -> did)')
        for r in rs:
            oh, dh = option_hit(r), draft_hit(r)
            if oh is False or dh is False:
                g, d = r['guess'], r['did']
                what = [] if oh is not False else [f"option: {g.get('option')} -> {d.get('pick') or d.get('verb')}"]
                if dh is False: what.append(f"draft: {d.get('pick')}")
                print(f"  {r.get('date')} {r.get('id')}: {'; '.join(what)}   note: {(d.get('note') or '')[:80]}")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
