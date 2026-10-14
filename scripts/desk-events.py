#!/usr/bin/env python3
"""desk-events.py: the desk's client for the adapter's live feed (GET /api/operator/desk-events).

  desk-events.py poll [--wait 20] [--stub <file>]   one long-poll round trip: read the cursor, ask the adapter for the
                                                    events after it, append each to events.jsonl, keep the new cursor,
                                                    print the events as one JSON list on stdout
  desk-events.py tail [n]                           print the last n stored events (default 10)

The adapter listens on 127.0.0.1:8787 on the box only, so the call is the one luna-send.py makes: one `ssh <sendHost>`
round trip that runs curl there with the operator bearer token read from the adapter's .env (config.json sendHost,
sendEnvFile). The token never leaves the box. The feed holds the request up to `wait` seconds (25 at most) and answers
as soon as an event newer than the cursor lands; `since=0` returns no history, only the current cursor.

--stub <file> reads a feed answer ({"cursor": "...", "events": [...]}) from a file instead of the network: for tests
and for the pane while the adapter change is not deployed. DESK_DIR overrides ~/orbit-scratch/desk.

Exit 0 = the round trip worked (the list may be empty); 4 = ssh or the feed failed, one line on stderr; 2 = usage.
Never raises.
"""
import json, os, shlex, subprocess, sys, datetime, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskconfig

DESK = os.environ.get('DESK_DIR') or os.path.expanduser('~/orbit-scratch/desk')
CURSOR = f'{DESK}/events-cursor.json'
LOG = f'{DESK}/events.jsonl'
LOG_KEEP = 2000   # lines kept when the log grows past twice that
BASE = 'http://127.0.0.1:8787/api/operator/desk-events'


def fetch(since, wait):
    """One ssh round trip. Returns (http status, parsed body)."""
    url = f"{BASE}?since={urllib.parse.quote(since, safe='')}&wait={wait}"
    cmd = (f"tok=$(sudo grep -m1 '^ORBOT_OPERATOR_API_TOKEN=' {deskconfig.SEND_ENV_FILE} | cut -d= -f2-); out=$(mktemp); "
           f"curl -s --max-time {wait + 10} -o \"$out\" -w '%{{http_code}}' -H \"Authorization: Bearer $tok\" {shlex.quote(url)}; "
           f"echo; cat \"$out\"; rm -f \"$out\"")
    r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', deskconfig.SEND_HOST, cmd],
                       capture_output=True, text=True, timeout=wait + 30)
    if r.returncode: raise RuntimeError(f'ssh failed: {r.stderr.strip()[:160] or r.returncode}')
    status, _, out = r.stdout.partition('\n')
    try: return int(status or 0), json.loads(out) if out.strip() else {}
    except ValueError: return int(status or 0), {'raw': out.strip()[:120]}


def read_cursor():
    try: return str(json.load(open(CURSOR)).get('cursor') or '0')
    except Exception: return '0'


def write_cursor(cursor):
    tmp = CURSOR + '.tmp'
    json.dump({'cursor': cursor, 'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}, open(tmp, 'w'))
    os.replace(tmp, CURSOR)


def store(events):
    if not events: return
    with open(LOG, 'a', encoding='utf-8') as f:
        for e in events: f.write(json.dumps(e, ensure_ascii=False) + '\n')
    try:
        lines = open(LOG, encoding='utf-8').read().splitlines()
        if len(lines) > 2 * LOG_KEEP:
            open(LOG, 'w', encoding='utf-8').write('\n'.join(lines[-LOG_KEEP:]) + '\n')
    except OSError: pass


def poll(argv):
    wait = min(25, max(0, int(argv[argv.index('--wait') + 1]))) if '--wait' in argv else 20
    stub = argv[argv.index('--stub') + 1] if '--stub' in argv else None
    since = read_cursor()
    if stub:
        body, status = json.load(open(stub)), 200
    else:
        if not deskconfig.SEND_HOST or not deskconfig.SEND_ENV_FILE:
            raise RuntimeError('config.json has no sendHost or sendEnvFile')
        status, body = fetch(since, wait)
    if status != 200 or not isinstance(body, dict) or 'cursor' not in body:
        raise RuntimeError(f"feed answered {status}: {str(body.get('error') or body.get('reason') or body.get('raw') or body)[:120] if isinstance(body, dict) else str(body)[:120]}")
    events = [e for e in body.get('events') or [] if isinstance(e, dict)]
    store(events)
    write_cursor(str(body['cursor']))
    print(json.dumps(events, ensure_ascii=False))
    return 0


def tail(argv):
    n = int(argv[0]) if argv and argv[0].isdigit() else 10
    try: lines = open(LOG, encoding='utf-8').read().splitlines()[-n:]
    except OSError: lines = []
    print(json.dumps([json.loads(l) for l in lines if l.strip()], indent=1, ensure_ascii=False))
    return 0


def main(argv):
    if not argv or argv[0] not in ('poll', 'tail'):
        print(__doc__); return 2
    try:
        return poll(argv[1:]) if argv[0] == 'poll' else tail(argv[1:])
    except Exception as e:
        print('desk-events: ' + ' '.join(str(e).split())[:200], file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
