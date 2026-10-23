#!/usr/bin/env python3
"""desk-decide.py <card id> "<option label>" — record a pick taken on the pane from a prepared brief.

2026-10-23: deciding and executing are two steps. The pane writes one decision record per pick, the session is not
involved, and desk-execute.py runs the records in the background. The record names the class of work the option needs:

  script   runs without a model (a send, a close, a drop, a defer with a trigger the brief proposed)
  model    a headless run with the brief, the recipe and the one option (a handoff, a run card's go, a PR close)
  session  needs the person or the session (an edit, a review, a validation, an answer): the pane lists it under
           "needs you" and `w` on the card submits today's prompt

Exit 0: record written, the class on stdout. 2: the option is not one of the brief's. 3: no fresh brief for the card
(the pick must come through the session). 4: a decision for the card is already queued or running.

Files: ~/orbit-scratch/desk/decisions/<card id>.json (DESK_DIR overrides the desk folder for tests).
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskclass  # noqa: E402

DESK = os.environ.get('DESK_DIR') or os.path.expanduser('~/orbit-scratch/desk')
BRIEFS = f'{DESK}/briefs'
DECISIONS = f'{DESK}/decisions'


def safe(cid):
    return re.sub(r'[^A-Za-z0-9._-]', '_', cid)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_json(path, obj):
    tmp = f'{path}.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main(argv):
    if len(argv) < 2 or argv[0] in ('-h', '--help'):
        print(__doc__); return 2
    cid, label = argv[0], argv[1].strip()
    os.makedirs(DECISIONS, exist_ok=True)
    try:
        brief = json.load(open(f'{BRIEFS}/{safe(cid)}.json'))
    except Exception:
        print(f'no brief for {cid}: work it through the session (w)'); return 3
    if brief.get('error'):
        print(f'the brief for {cid} failed: work it through the session (w)'); return 3
    try:
        cache = json.load(open(f'{DESK}/cards.json'))
    except Exception:
        cache = {'cards': []}
    card = next((c for c in cache.get('cards', []) if c.get('id') == cid), None)
    if card is None:
        print(f'{cid} is not on the desk'); return 3
    if card.get('verb'):
        print(f'{cid} already has its verb ({card["verb"]})'); return 3
    bound = (brief.get('key') == card.get('key') and brief.get('since') == card.get('since')) if brief.get('key') else \
        (brief.get('status') == card.get('status') and brief.get('act') == card.get('act'))
    if not bound:
        print(f'the brief for {cid} is for an older wait: work it through the session (w)'); return 3
    opt = next((o for o in brief.get('options', []) if o.get('label') == label), None)
    if opt is None:
        print(f'"{label}" is not an option of this brief: {", ".join(o.get("label", "") for o in brief.get("options", []))}'); return 2
    path = f'{DECISIONS}/{safe(cid)}.json'
    try:
        old = json.load(open(path))
        if old.get('state') in ('queued', 'running'):
            print(f'{cid} already has a {old["state"]} decision ({old.get("option")})'); return 4
    except Exception:
        pass
    klass = deskclass.classify(brief, label)
    rec = {
        'cardId': cid, 'ref': card.get('ref') or brief.get('ref'), 'lane': brief.get('lane') or card.get('lane'),
        'option': label, 'verb': opt.get('verb') or 'do', 'class': klass,
        'pickedAt': now_iso(), 'briefFile': f'{BRIEFS}/{safe(cid)}.json', 'fingerprint': brief.get('fingerprint'),
        'key': card.get('key'), 'since': card.get('since'), 'anchor': brief.get('anchor') or {},
        'thread': card.get('thread'), 'pr': card.get('pr'),
        'state': 'queued', 'startedAt': None, 'finishedAt': None, 'result': '', 'learned': [],
    }
    write_json(path, rec)
    print(klass)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
