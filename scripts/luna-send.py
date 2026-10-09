#!/usr/bin/env python3
"""luna-send.py — hand an approved customer reply to the orbot adapter, which sends it as Luna (the OrBot machine user)
through the Luna send path: pre-send checks, send, verification, and a luna_conversation_turns row, so the customer's
answer routes into the adapter's luna_followup job instead of a fresh triage. Spec: ~/orbit-scratch/luna-operator-send/PRD.md.

  luna-send.py <th_id|T-ref> --file <draft.txt> [--ref T-106] [--intent operator_reply] [--force] [--wait 90] [--json]
  luna-send.py --status <actionId>

The adapter listens on 127.0.0.1:8787 on the VPS only; every call goes `ssh <sendHost> curl …`. The bearer token lives in
the adapter's .env on the box (config.json sendEnvFile, ORBOT_OPERATOR_API_TOKEN) and never leaves the box: the curl reads it there.
Exit 0 = sent and verified; 3 = refused before queueing (guard 422, flag off, auth) → fall back per CARDS.md; 4 = queued
but not sent within --wait (poll again with --status); 5 = failed / needs_human_recovery / max_turns_reached.
"""
import json, sys, os, subprocess, time, shlex, re

import deskconfig
HOST = deskconfig.SEND_HOST
BASE = 'http://127.0.0.1:8787/api/operator/luna-reply'
ENV = deskconfig.SEND_ENV_FILE


def remote_curl(method, path, body=None, timeout=40):
    """One ssh round trip: curl with the token read from the adapter's .env on the box. Returns (http status, json)."""
    data = f"--data-binary {shlex.quote(json.dumps(body, ensure_ascii=False))}" if body is not None else ''
    cmd = (f"tok=$(sudo grep -m1 '^ORBOT_OPERATOR_API_TOKEN=' {ENV} | cut -d= -f2-); "
           f"curl -s -o /tmp/luna-send.out -w '%{{http_code}}' -X {method} -H \"Authorization: Bearer $tok\" -H 'Content-Type: application/json' {data} {shlex.quote(BASE + path)}; "
           f"echo; cat /tmp/luna-send.out")
    r = subprocess.run(['ssh', HOST, cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode: raise RuntimeError(f'ssh failed: {r.stderr.strip()[:200]}')
    status, _, out = r.stdout.partition('\n')
    try: return int(status), json.loads(out) if out.strip() else {}
    except ValueError: return int(status or 0), {'raw': out[:400]}


def thread_id(ref_or_id):
    if ref_or_id.startswith('th_'): return ref_or_id
    sys.path.insert(0, os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import plain   # the one Plain reader
    return plain.thread_id(ref_or_id)


def status(action_id):
    code, d = remote_curl('GET', f'/{action_id}')
    return code, d


def main(argv):
    if '--status' in argv:
        code, d = status(argv[argv.index('--status') + 1]); print(json.dumps(d, indent=1, ensure_ascii=False)); return 0 if code == 200 else 3
    if not argv or argv[0].startswith('--'): print(__doc__); return 2
    target = argv[0]
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    path = opt('--file')
    if not path: print('--file <draft.txt> is required (the draft on the clipboard: pbpaste > draft.txt)'); return 2
    text = open(path, encoding='utf-8').read().strip()
    if not text: print('empty draft'); return 2
    ref = opt('--ref') or (target if re.match(r'^T-\d+$', target) else None)
    tid = thread_id(target)
    body = {'threadId': tid, 'text': text, 'operator': 'tobias', 'source': 'desk', 'intent': opt('--intent', 'operator_reply')}
    if ref: body['ref'] = ref
    if '--force' in argv: body['force'] = True
    code, d = remote_curl('POST', '', body)
    if code in (200, 202) and d.get('ok'):
        aid = d['actionId']; wait = int(opt('--wait', '90')); poll = max(2, int(d.get('pollAfterMs', 3000)) / 1000)
        print(f"queued {aid} on {tid} (tenant {d.get('tenantId') or '?'}{'' if d.get('tenantResolved', True) else ', unresolved'}), status {d.get('status')}")
        t0 = time.time(); last = None
        while time.time() - t0 < wait:
            time.sleep(poll)
            sc, s = status(aid)
            if sc != 200: continue
            last = s
            if s.get('status') == 'sent':
                turn = (s.get('turn') or {}).get('iteration')
                print(f"sent as Luna · verification {s.get('verificationStatus')} · turn {turn}/4 · action {aid}")
                if '--json' in argv: print(json.dumps(s, indent=1, ensure_ascii=False))
                return 0
            if s.get('status') in ('failed', 'needs_human_recovery', 'max_turns_reached'):
                print(f"NOT sent: {s.get('status')} · {s.get('reason') or s.get('lastError')} · action {aid}")
                return 5
        print(f"still {(last or {}).get('status', 'pending')} after {wait}s · poll: luna-send.py --status {aid}")
        return 4
    print(f"refused ({code}): {d.get('reason') or d.get('raw') or d}")
    if d.get('guardResult'): print(json.dumps(d['guardResult'], indent=1, ensure_ascii=False)[:1500])
    return 3


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
