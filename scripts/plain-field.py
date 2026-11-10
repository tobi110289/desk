#!/usr/bin/env python3
"""plain-field.py <th_…> show|claim|release [--dry] — read or move the triage claim on a Plain thread.

2026-11-09: the claim of the /triage skill (step 2.5) without the Plain MCP, for the desk's unattended validation and
for a session whose MCP is down. `claim` writes triage_owner=local and triage_claimed_at=<now, strict ISO> and refuses
while a person (or another local run) holds an active claim (younger than 24 h); `release` deletes both fields. No other
field can be written with it.

--dry, or DESK_PLAIN_DRY=1: print what would change, write nothing. Exit 0 ok, 2 usage, 3 refused, 1 Plain error.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskplain  # noqa: E402


def main(argv):
    if '-h' in argv or '--help' in argv or len(argv) < 2 or argv[1] not in ('show', 'claim', 'release'):
        print(__doc__); return 2
    thread, cmd = argv[0], argv[1]
    dry = '--dry' in argv or deskplain.dry_env()
    if not thread.startswith('th_'):
        print('refused: give the thread id (th_…)', file=sys.stderr); return 2
    try:
        if cmd == 'show':
            f = deskplain.thread_head(thread)['fields']
            print(json.dumps({k: f.get(k) for k in deskplain.CLAIM_KEYS + ('agent_readiness',)} | {'activeClaim': deskplain.active_claim(f)}))
        elif cmd == 'claim':
            print(json.dumps(deskplain.claim(thread, dry)))
        else:
            print(json.dumps(deskplain.release(thread, dry)))
    except RuntimeError as e:
        refused = 'claimed' in str(e) or 'holds' in str(e)
        print(('refused: ' if refused else 'failed: ') + str(e), file=sys.stderr)
        return 3 if refused else 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
