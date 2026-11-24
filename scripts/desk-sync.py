#!/usr/bin/env python3
"""desk-sync.py: keep the loop state the same on this machine and on the machine that holds the desk.

  desk-sync.py loop            merge state.json and state-archive.json with the desk host (config.json deskHost)
  desk-sync.py feeds [--dry-run]  copy what only this machine produces to the desk host: the sweep cache, the loop
                               helpers, the Hermes log, the sweep reports, the local tickets, the AWS login state,
                               the Notion tickets
  desk-sync.py pull            copy the desk folder from the desk host to this machine: cards, briefs, decisions, holds
  desk-sync.py tick            what the pane runs every few seconds: pull, and once a minute loop and feeds
  desk-sync.py all             loop and feeds

Two machines write the loop state. The agent loop writes it here (phase, workspace, task). The desk writes it on the
desk host (a caption after a close, a revived entry). Neither side stamps every write, so a "newest wins" copy would
lose one of them. The merge is three-way, per entry and per field, against the copy from the last sync
(.sync-base.json in the loop folder):

  only one side changed a field      that side's value
  both sides changed it              the desk host wins for `status` and `note` (a person's close), this machine
                                     wins for every other field (the loop knows the phase and the workspace)

A tick that holds lock.d keeps its own copy of the state in memory, so the sync waits for the next run.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402

FILES = ('state.json', 'state-archive.json')
DESK_WINS = ('status', 'note')
# the loop folder on the desk host, from its home folder (ssh starts there); config.json deskHostLoop moves it
# the scratch root has the same path on the desk host (README, "One desk on a host")
REMOTE_SCRATCH = deskconfig.CFG.get('deskHostScratch') or deskconfig.SCRATCH
REMOTE_LOOP = deskconfig.CFG.get('deskHostLoop') or f'{REMOTE_SCRATCH}/agent-loop'
GONE = object()   # a missing entry or field, so "removed" is a change like any other


def pick(base, mine, theirs, desk_wins=False):
    """One value from three: the side that changed it, and on a conflict the side the rule names."""
    if mine == theirs or theirs == base:
        return mine
    if mine == base:
        return theirs
    return theirs if desk_wins else mine


def merge_entry(base, mine, theirs):
    """One loop entry. A side that removed it while the other changed it loses: the changed entry stays."""
    if mine == theirs or theirs == base:
        return mine
    if mine == base:
        return theirs
    if mine is GONE or theirs is GONE:
        return theirs if mine is GONE else mine
    base = base if isinstance(base, dict) else {}
    out = {}
    for field in list(mine) + [f for f in theirs if f not in mine]:
        value = pick(base.get(field, GONE), mine.get(field, GONE), theirs.get(field, GONE), field in DESK_WINS)
        if value is not GONE:
            out[field] = value
    return out


def merge_threads(base, mine, theirs):
    """The `threads` map of one file, in this machine's order with the desk host's new entries after it."""
    out = {}
    for ref in list(mine) + [r for r in theirs if r not in mine] + [r for r in base if r not in mine and r not in theirs]:
        entry = merge_entry(base.get(ref, GONE), mine.get(ref, GONE), theirs.get(ref, GONE))
        if entry is not GONE:
            out[ref] = entry
    return out


def merge_doc(base, mine, theirs):
    """A whole file: its threads merged, every other top-level key from this machine."""
    return {**mine, 'threads': merge_threads(base.get('threads', {}), mine.get('threads', {}), theirs.get('threads', {}))}


def _load(path):
    try:
        return json.load(open(path))
    except Exception:
        return {'threads': {}}


def _save(path, doc):
    tmp = f'{path}.sync.tmp'
    json.dump(doc, open(tmp, 'w'), indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _remote(host, script, data=None):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host, script], input=data,
                          capture_output=True, text=True, timeout=60)


def sync_loop(host):
    loop = deskconfig.need(deskconfig.LOOP)
    if os.path.exists(f'{loop}/lock.d'):
        return 'a tick holds lock.d; the next run syncs'
    base_path = f'{loop}/.sync-base.json'
    bases = _load(base_path) if os.path.exists(base_path) else {}
    got = _remote(host, f'cd {REMOTE_LOOP} && for f in ' + ' '.join(FILES) + '; do echo "@@$f"; cat "$f" 2>/dev/null; echo; done')
    if got.returncode != 0:
        sys.exit(f'desk-sync: {host} did not answer: {got.stderr.strip()[:200]}')
    remote = {}
    for part in got.stdout.split('@@')[1:]:
        name, _, body = part.partition('\n')
        try:
            remote[name] = json.loads(body)
        except ValueError:
            remote[name] = None   # no file yet on the desk host
    changed = []
    merged = {}
    for name in FILES:
        mine = _load(f'{loop}/{name}')
        theirs = remote.get(name)
        base = bases.get(name) or {'threads': {}}
        # the first sync, or a desk host with no file: this machine's copy is the start
        merged[name] = merge_doc(base, mine, theirs if theirs is not None and name in bases else mine)
        if merged[name] != mine:
            _save(f'{loop}/{name}', merged[name]); changed.append(f'{name} here')
        if merged[name] != theirs:
            put = _remote(host, f'cd {REMOTE_LOOP} && cat > {name}.sync.tmp && mv {name}.sync.tmp {name}',
                          json.dumps(merged[name], indent=2, ensure_ascii=False))
            if put.returncode != 0:
                sys.exit(f'desk-sync: could not write {name} on {host}: {put.stderr.strip()[:200]}')
            changed.append(f'{name} on {host}')
    _save(base_path, merged)
    return ', '.join(changed) or 'in sync'


# What only this machine produces and the gather on the desk host reads. One direction, nothing is deleted there.
# The paths are under the scratch root, in rsync's filter form; the first rule that matches a path decides.
FEEDS = [
    '- __pycache__/', '- *.bak*', '- *.pyc',
    '+ /agent-loop/', '+ /agent-loop/sweep-cache.json', '+ /agent-loop/jev/', '- /agent-loop/jev/logs/', '+ /agent-loop/jev/**', '- /agent-loop/*',
    '+ /hermes-feedback/', '+ /hermes-feedback/LOG.md', '- /hermes-feedback/*',
    '+ /sentry-sweeps/', '+ /sentry-sweeps/*.md', '- /sentry-sweeps/*',
    '+ /desk/', '+ /desk/notion-tickets.json', '+ /desk/cache/', '+ /desk/cache/sso-state.json', '- /desk/cache/*', '- /desk/*',
    '+ /*/', '+ /*/issues/', '+ /*/issues/*.md', '- /*/issues/*', '- /*/*',
    '- /*',
]


def feed_argv(scratch, host, dry=False):
    rules = [arg for rule in FEEDS for arg in ('-f', rule)]
    return ['rsync', '-az', '--prune-empty-dirs', '--itemize-changes', *(['--dry-run'] if dry else []), *rules,
            f'{scratch}/', f'{host}:{REMOTE_SCRATCH}/']


def sync_feeds(host, dry=False):
    ran = subprocess.run(feed_argv(deskconfig.need(deskconfig.SCRATCH), host, dry), capture_output=True, text=True, timeout=300)
    if ran.returncode != 0:
        sys.exit(f'desk-sync: rsync to {host} failed: {ran.stderr.strip()[:300]}')
    sent = [line.split(' ', 1)[1] for line in ran.stdout.splitlines() if line[:1] in '<>' ]
    return sent


# The desk folder belongs to the desk host. These files in it belong to this machine and are never pulled over:
# the scripts (links into the repo), the config, the key file, what the pane and the session write here.
KEEP_LOCAL = ['*.py', '*.pyc', '__pycache__/', 'config.json', '.env', '.auto', '.events', '.desk.lock', '.sync-*',
              'notion-tickets.json', 'cache/sso-state.json', '*.bak*', '*.pre-repo', '*.tmp']


def pull_argv(desk, host):
    rules = [arg for name in KEEP_LOCAL for arg in ('--exclude', name)]
    # --delete: a brief or a decision that left the desk host leaves this copy too; excluded files are never deleted
    return ['rsync', '-az', '--delete', *rules, f'{host}:{REMOTE_SCRATCH}/desk/', f'{desk}/']


def pull(host):
    ran = subprocess.run(pull_argv(deskconfig.need(deskconfig.DESK), host), capture_output=True, text=True, timeout=120)
    if ran.returncode != 0:
        sys.exit(f'desk-sync: pull from {host} failed: {ran.stderr.strip()[:300]}')


def tick(host):
    """The pane's beat. The pull is cheap and runs every time; the loop state and the feeds go once a minute."""
    import time
    pull(host)
    stamp = f'{deskconfig.DESK}/.sync-tick'
    try: age = time.time() - os.path.getmtime(stamp)
    except OSError: age = 1e9
    if age < 60:
        return
    open(stamp, 'w').write('')
    sync_loop(host); sync_feeds(host)


if __name__ == '__main__':
    what = sys.argv[1:2]
    if what in (['pull'], ['tick']) and deskconfig.DESK_HOST:
        (pull if what == ['pull'] else tick)(deskconfig.DESK_HOST)
        sys.exit(0)
    if what not in (['loop'], ['feeds'], ['all']):
        sys.exit(__doc__)
    if not deskconfig.DESK_HOST:
        sys.exit("desk-sync: config key 'deskHost' is empty; the desk is on this machine")
    if what != ['feeds']:
        print('loop state:', sync_loop(deskconfig.DESK_HOST))
    if what != ['loop']:
        dry = '--dry-run' in sys.argv
        sent = sync_feeds(deskconfig.DESK_HOST, dry)
        print(f"feeds: {len(sent)} files {'would go' if dry else 'sent'}" + (': ' + ', '.join(sent[:8]) + (' …' if len(sent) > 8 else '') if sent else ''))
