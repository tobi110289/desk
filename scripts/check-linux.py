#!/usr/bin/env python3
"""check-linux.py: prove that the gather and the prepare rules run on Linux, without the box and without a network.

  python3 scripts/check-linux.py              run here (on a Linux box, or on the Mac as a dry run of the same steps)
  python3 scripts/check-linux.py --docker     run the same check inside a python:3.12 Linux container
  python3 scripts/check-linux.py --desk DIR   use a copy of DIR as the desk folder instead of the made-up one
  python3 scripts/check-linux.py --keep       keep the temporary folder and print its path

Everything happens in a new temporary folder. HOME points there too, so no real login, token or desk file is read.
Every folder comes in through the resolver's variables (deskconfig.py). The sources are stubs:
  Plain       a jev/plain.py whose reader answers every thread list with no threads
  GitHub      a `gh` on PATH that answers []
  Jev         stub pr_health, desk_lanes and loopstate modules in the stub jev folder
  Superset    a stub desk_screens module, and a `superset` on PATH that fails the check when called
  A2A         a local HTTP server with one made-up inbox message, named in the token file
  the model   a `claude` on PATH that answers the brief schema with an empty brief
`open`, `pbcopy`, `afplay`, `ssh`, `osascript` and the other Mac tools are traps on PATH: a call is logged and fails
the check. The data is made up here; nothing comes from a real desk unless --desk names one.

Steps: the path grep, desk.py, desk-prepare.py, test_prepare.py, sso-state.py, and the fail-loud cases. Exit 0 when
all pass.
"""
import http.server
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPTS = f'{REPO}/scripts'
IMAGE = 'python:3.12-slim'
# the send path and the executor move in their own slice (orbot#921 "Out of scope")
OUT_OF_SCOPE = {'desk-execute.py', 'luna-send.py', 'desk-events.py'}
TRAPS = ['open', 'pbcopy', 'pbpaste', 'afplay', 'say', 'osascript', 'ssh', 'superset', 'launchctl', 'terminal-notifier']
# the home paths only the resolver may name; built from parts so this file does not name them itself
HOME_PATHS = re.compile('~/' + '(?:orbit-scratch|orbit\\b|orbot\\b|\\.claude/skills)' + '|\\{HOME\\}/' + '(?:orbit|orbot)\\b')

results = []


def step(name, ok, detail=''):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail and not ok else (f"  ({detail})" if detail else '')))


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


# ---------------------------------------------------------------- the stubs
STUB_PLAIN = '''"""stub of the one Plain reader: every thread list is empty, every other read finds nothing"""
def gql(query, variables=None, timeout=25):
    return try_gql(query, variables, timeout)
def try_gql(query, variables=None, timeout=25):
    if 'threads(' in query:
        return {'threads': {'pageInfo': {'hasNextPage': False, 'endCursor': None}, 'edges': []}}
    return None
'''
STUB_PR_HEALTH = '''"""stub: no open PRs"""
def load(max_age_s=900):
    return {'generatedAt': '2026-01-01T00:00:00Z', 'prs': {}, 'tracked': {}}
def build():
    return load()
def cards(doc):
    return []
'''
STUB_LANES = '''"""stub: Jev has no opinion"""
def prefetch(rows):
    return None
def lane(ref):
    return None
def person(ref):
    return None
'''
STUB_SCREENS = '''"""stub: no worker terminals"""
def cached(max_age_s=900):
    return {}
def screen(state):
    return {}
'''
STUB_LOOPSTATE = '''"""stub: nothing archived"""
def revive(ref):
    return False
'''
STUB_GH = '#!/bin/sh\necho "[]"\n'
STUB_CLAUDE = '''#!/usr/bin/env python3
import json
print(json.dumps({"is_error": False, "total_cost_usd": 0, "duration_ms": 1, "structured_output": {
    "why": "A made-up card for the Linux check.", "fact": "Nothing happened.", "draft": "", "options": [],
    "checks": [], "openChecks": [], "resolved": False, "causeOpen": False, "replyReady": False, "threadAfter": "leave"}}))
'''
TRAP = '#!/bin/sh\necho "$(basename "$0") $*" >> "$DESK_CHECK_CALLS"\nexit 1\n'


class A2A(http.server.BaseHTTPRequestHandler):
    feed = {}

    def do_GET(self):
        body = json.dumps(self.feed if self.path.startswith('/inbox/feed') else {'agents': []}).encode()
        self.send_response(200)
        self.send_header('content-type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def build(root, desk_from):
    """The made-up world: a desk folder, a loop folder with stub helpers, one local ticket, one task, stub tools."""
    scratch = f'{root}/scratch'
    desk = f'{scratch}/desk'
    loop = f'{scratch}/agent-loop'
    if desk_from:
        shutil.copytree(desk_from, desk, symlinks=False, ignore=shutil.ignore_patterns('*.py', '__pycache__', '*.lock'))
    else:
        write(f'{desk}/config.json', json.dumps({
            'plainWorkspace': 'w_example', 'repo': 'example-org/example-app', 'botRepo': 'example-org/example-bot',
            'team': ['Pat', 'Lee'], 'me': 'Sam', 'people': ['Pat', 'Lee'], 'internalDomains': ['example.com']}, indent=1))
        write(f'{desk}/desk.json', json.dumps({'cards': {}, 'log': []}))
        write(f'{desk}/tasks.json', json.dumps({'tasks': [{'id': 'try-the-box', 'title': 'Try the desk on the box',
                                                            'since': '2026-11-01', 'steps': ['run the check']}]}))
    for f in os.listdir(SCRIPTS):   # the scripts sit in the desk folder, as install.sh links them
        if f.endswith('.py') and not os.path.exists(f'{desk}/{f}'):
            os.symlink(f'{SCRIPTS}/{f}', f'{desk}/{f}')
    write(f'{loop}/state.json', json.dumps({'threads': {'T-101': {
        'phase': 'blocked', 'status': 'waiting on Pat: pick the label', 'note': '', 'updated': '2026-11-01T09:00:00Z'}}}))
    write(f'{loop}/sweep-cache.json', json.dumps({'threads': {}}))
    for name, text in (('plain.py', STUB_PLAIN), ('pr_health.py', STUB_PR_HEALTH), ('desk_lanes.py', STUB_LANES),
                       ('desk_screens.py', STUB_SCREENS), ('loopstate.py', STUB_LOOPSTATE)):
        write(f'{loop}/jev/{name}', text)
    write(f'{scratch}/example-feature/issues/01-example.md',
          '# 01 Example ticket\n\nStatus: ready-for-agent\nCreated: 2026-11-01\n\nA made-up ticket for the Linux check.\n')
    write(f'{scratch}/hermes-feedback/LOG.md', '# Hermes feedback\n')
    os.makedirs(f'{scratch}/sentry-sweeps', exist_ok=True)
    os.makedirs(f'{root}/skills')
    os.symlink(f'{REPO}/skill', f'{root}/skills/desk')
    for name in TRAPS:
        write(f'{root}/traps/{name}', TRAP, 0o755)
    write(f'{root}/stubs/gh', STUB_GH, 0o755)
    write(f'{root}/stubs/claude', STUB_CLAUDE, 0o755)
    os.makedirs(f'{root}/home', exist_ok=True)
    return desk


def run(argv, env, cwd):
    r = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout, r.stderr


def one_line(err, *words):
    lines = [l for l in err.strip().splitlines() if l.strip()]
    return len(lines) == 1 and all(w in lines[0] for w in words), (lines[-1] if lines else '(no output)')


def check(desk_from=None, keep=False):
    print(f"check-linux on {platform.system()} {platform.release()}, python {platform.python_version()}")
    # 1. no script outside the resolver names a home path (the out-of-scope send path and executor excepted)
    hits = []
    for f in sorted(os.listdir(SCRIPTS)):
        if f.endswith('.py') and f != 'deskconfig.py' and f not in OUT_OF_SCOPE:
            for n, line in enumerate(open(f'{SCRIPTS}/{f}'), 1):
                if HOME_PATHS.search(line):
                    hits.append(f'{f}:{n}: {line.strip()[:100]}')
    step('no script outside deskconfig.py names a home path', not hits, '; '.join(hits[:5]))

    root = tempfile.mkdtemp(prefix='desk-check-')
    try:
        desk = build(root, desk_from)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), A2A)
        A2A.feed = {'waitingOnMe': [{'taskId': 'stub-1', 'kind': 'message', 'from': 'pat', 'text': 'A made-up message.',
                                     'createdAt': '2026-11-01T09:00:00Z'}], 'waitingOnOthers': []}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        write(f'{root}/a2a.env', f'A2A_TOKEN=stub\nA2A_HUB_URL=http://127.0.0.1:{server.server_address[1]}\n')
        calls = f'{root}/mac-calls.log'
        env = {
            'HOME': f'{root}/home', 'LANG': 'C.UTF-8', 'TZ': 'UTC',
            'PATH': os.pathsep.join([f'{root}/traps', f'{root}/stubs', os.path.dirname(sys.executable), '/usr/local/bin', '/usr/bin', '/bin']),
            'DESK_SCRATCH': f'{root}/scratch', 'DESK_DIR': desk, 'DESK_LOOP_DIR': f'{root}/scratch/agent-loop',
            'DESK_ORBIT_REPO': f'{root}/orbit', 'DESK_ORBOT_REPO': f'{root}/orbot', 'DESK_SKILLS_DIR': f'{root}/skills',
            'DESK_A2A_ENV': f'{root}/a2a.env', 'DESK_AWS_DIR': f'{root}/home/.aws', 'DESK_CHECK_CALLS': calls,
        }

        # 2. the gather: desk.py against the copied folder writes cards.json
        code, out, err = run(['python3', f'{desk}/desk.py', '--fresh'], {**env, 'PLAIN_API_KEY': 'stub'}, desk)
        try:
            cards = json.load(open(f'{desk}/cards.json'))['cards']
        except Exception:
            cards = []
        ids = sorted(c['id'] for c in cards)
        step('desk.py gathers and writes cards.json', code == 0 and bool(cards), f"exit {code}, {len(cards)} cards: {', '.join(ids)}" + (f"; {err.strip()[-300:]}" if code else ''))
        if not desk_from:
            want = {'a2a:stub-1', 'task:try-the-box', 'local:example-feature:01'}
            step('the stubbed sources reach the desk (A2A, task, local ticket)', want <= set(ids), '' if want <= set(ids) else f"missing {sorted(want - set(ids))}")

        # 3. the prepare rules: desk-prepare.py writes a brief per prepared card (the model is the stub)
        code, out, err = run(['python3', f'{desk}/desk-prepare.py', '--top', '3'], env, desk)
        briefs = sorted(f for f in os.listdir(f'{desk}/briefs') if f.endswith('.json')) if os.path.isdir(f'{desk}/briefs') else []
        step('desk-prepare.py prepares the top cards', code == 0 and bool(briefs), f"exit {code}, {len(briefs)} briefs; {out.strip()}" + (f"; {err.strip()[-300:]}" if code else ''))
        code, out, err = run(['python3', f'{SCRIPTS}/test_prepare.py'], env, desk)
        step('test_prepare.py', code == 0, (out + err).strip().splitlines()[-1] if code == 0 else (out + err).strip()[-300:])

        # 4. a script that does not need a thing still runs: no AWS folder
        code, out, err = run(['python3', f'{desk}/sso-state.py'], env, desk)
        step('sso-state.py says "no AWS" and exits 0', code == 0 and out.strip() == 'no AWS', f"exit {code}: {out.strip()} {err.strip()}")

        # 5. a script that needs a thing stops with one line that names it
        ok, line = one_line(run(['python3', f'{desk}/desk.py'], {**env, 'DESK_DIR': f'{root}/nowhere'}, root)[2], 'desk folder missing', 'DESK_DIR')
        step('desk.py without the desk folder stops with one line', ok, line)
        ok, line = one_line(run(['python3', f'{desk}/desk.py'], {**env, 'DESK_JEV_DIR': f'{root}/nowhere'}, desk)[2], 'loop helpers (jev) missing', 'DESK_JEV_DIR')
        step('desk.py without the loop helpers stops with one line', ok, line)
        write(f'{root}/no-repo.json', json.dumps({'botRepo': 'example-org/example-bot'}))
        ok, line = one_line(run(['python3', f'{desk}/desk.py'], {**env, 'DESK_CONFIG': f'{root}/no-repo.json'}, desk)[2], "config key 'repo' missing")
        step('desk.py without the repo key stops with one line', ok, line)
        ok, line = one_line(run(['python3', f'{desk}/desk-prepare.py'], {**env, 'DESK_SKILLS_DIR': f'{root}/nowhere'}, desk)[2], 'desk skill missing')
        step('desk-prepare.py without the desk skill stops with one line', ok, line)
        ok, line = one_line(run(['python3', f'{desk}/memory.py', 'pull'], env, desk)[2], 'orbot clone missing', 'DESK_ORBOT_REPO')
        step('memory.py pull without the orbot clone stops with one line', ok, line)

        # 6. no Mac tool was called on the way
        made = open(calls).read().strip() if os.path.exists(calls) else ''
        step('no call to open, pbcopy, afplay, ssh or another Mac tool', not made, made[:300])
        server.shutdown()
    finally:
        if keep:
            print(f'kept: {root}')
        else:
            shutil.rmtree(root, ignore_errors=True)
    print(f"{sum(results)} of {len(results)} passed")
    return 0 if all(results) else 1


def docker(args):
    if not shutil.which('docker'):
        sys.exit('desk: docker missing: run the check on a Linux machine instead')
    argv = ['docker', 'run', '--rm', '-v', f'{REPO}:/desk:ro']
    rest = [a for a in args if a != '--docker']
    if '--desk' in rest:
        src = os.path.abspath(rest[rest.index('--desk') + 1])
        argv += ['-v', f'{src}:/desk-copy:ro']
        rest[rest.index('--desk') + 1] = '/desk-copy'
    return subprocess.run(argv + [IMAGE, 'python3', '/desk/scripts/check-linux.py', *rest]).returncode


if __name__ == '__main__':
    a = sys.argv[1:]
    if '-h' in a or '--help' in a:
        print(__doc__); sys.exit(0)
    if '--docker' in a:
        sys.exit(docker(a))
    sys.exit(check(a[a.index('--desk') + 1] if '--desk' in a else None, '--keep' in a))
