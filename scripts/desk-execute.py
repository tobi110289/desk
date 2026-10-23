#!/usr/bin/env python3
"""desk-execute.py — run the decisions the pane recorded (desk-decide.py), in the background, without the session.

2026-10-23: the person picks on the pane from a prepared brief; this script does the work the pick names and records
the verb with desk-close.py. Before it acts it reads the brief's anchors again (deskanchor.py): a customer who wrote
after the brief, a thread that moved, a PR with new commits. Any change bounces the decision: nothing runs, the brief
is removed so the next prepare pass writes a new one, and the pane shows why. A decision also bounces when the card
left the desk or already has its verb.

Classes (deskclass.py): `script` runs here with no model; `model` is phase 2 (until then such picks go through the
session); `session` records are never touched here, the session executes them and desk-close.py marks them done.

  desk-execute.py              drain every queued script decision, up to 3 at a time
  desk-execute.py --one <file> run one record (any class but session), for a look by hand
  desk-execute.py --dry        say what would run

The state of a record goes queued → running → done | bounced | failed; every write is atomic. The mod starts a drain
after every pick and shows decisions/*.json as the results strip. Env: PLAIN_API_KEY, DESK_DIR (tests).
The executor exports DESK_DECISION=<record> to its children: luna-send.py refuses to send under a record whose option
is not a send, so no child can send on its own initiative.
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import deskanchor  # noqa: E402
import deskclass  # noqa: E402

DESK = os.environ.get('DESK_DIR') or os.path.expanduser('~/orbit-scratch/desk')
DECISIONS = f'{DESK}/decisions'
BRIEFS = f'{DESK}/briefs'
DRAFTS = f'{DESK}/drafts'
LOCK_S = 10 * 60
log_lock = threading.Lock()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def hhmm():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%H:%MZ')


def log(msg):
    with log_lock:
        os.makedirs(DECISIONS, exist_ok=True)
        with open(f'{DECISIONS}/execute.log', 'a') as f:
            f.write(f'{now_iso()} {msg}\n')


def write_json(path, obj):
    tmp = f'{path}.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def finish(path, rec, state, result):
    rec['state'] = state
    rec['result'] = result[:300]
    rec['finishedAt'] = now_iso()
    write_json(path, rec)
    log(f"{rec['cardId']} {rec['option']!r} → {state}: {result[:160]}")


def sh(argv, timeout=180, env=None):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})
    return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()


def gql(query, variables):
    key = os.environ.get('PLAIN_API_KEY')
    if not key:
        raise RuntimeError('no PLAIN_API_KEY')
    import urllib.request
    body = json.dumps({'query': query, 'variables': variables}).encode()
    req = urllib.request.Request('https://core-api.uk.plain.com/graphql/v1', data=body, headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json', 'User-Agent': 'desk-execute/1 (+python-urllib)'})
    with urllib.request.urlopen(req, timeout=40) as r:
        out = json.load(r)
    if out.get('errors'):
        raise RuntimeError(str(out['errors'])[:300])
    data = out['data']
    err = next((v.get('error') for v in data.values() if isinstance(v, dict) and v.get('error')), None)
    if err:
        raise RuntimeError(err.get('message') or str(err)[:200])
    return data


def mark_done(thread_id):
    gql('mutation($input: MarkThreadAsDoneInput!){ markThreadAsDone(input:$input){ error{ message } } }', {'input': {'threadId': thread_id}})


def add_note(thread_id, text):
    gql('mutation($input: CreateNoteInput!){ createNote(input:$input){ error{ message } } }', {'input': {'threadId': thread_id, 'text': text}})


def close(rec, verb, note, caption=None, trigger=None, act=None, env=None):
    """desk-close.py with the pick, so the brief is scored like a session close."""
    argv = ['python3', f'{HERE}/desk-close.py', rec['cardId'], verb]
    if verb == 'defer':
        argv += [trigger, act or '']
    else:
        argv += [note]
    argv += ['--pick', rec['option']]
    if caption:
        argv += ['--caption', caption]
    code, out = sh(argv, 60, env)
    if code:
        raise RuntimeError(f'desk-close failed: {out[:200]}')
    if 'Not closed' in out:
        raise RuntimeError(out[:200])
    return out


def card_of(cid):
    try:
        cache = json.load(open(f'{DESK}/cards.json'))
    except Exception:
        return None
    return next((c for c in cache.get('cards', []) if c.get('id') == cid), None)


def bounce(path, rec, reason):
    """Nothing ran. The brief goes away so the next prepare pass writes a fresh one; the card stays on the desk."""
    for g in (rec.get('briefFile') or '', (rec.get('briefFile') or '').replace('.json', '.ctx.md')):
        try:
            if g: os.remove(g)
        except FileNotFoundError:
            pass
    finish(path, rec, 'bounced', reason)


def run_script(path, rec, brief, card, dry):
    label = rec['option']
    verb = rec.get('verb') or 'do'
    ref = rec.get('ref') or card.get('ref') or ''
    thread = rec.get('thread') or card.get('thread')
    lane = rec.get('lane') or card.get('lane') or ''
    fact = (brief.get('fact') or '').strip()
    env = {'DESK_DECISION': path}
    if dry:
        finish(path, rec, 'queued', f'dry: would run {label}'); return
    if label in deskclass.SEND:
        if not thread:
            raise RuntimeError('no thread on the card: a send needs one')
        os.makedirs(DRAFTS, exist_ok=True)
        draft_path = f"{DRAFTS}/{re.sub(r'[^A-Za-z0-9._-]', '_', ref or thread)}.txt"
        with open(draft_path, 'w', encoding='utf-8') as f:
            f.write((brief.get('draft') or '').strip() + '\n')
        argv = ['python3', f'{HERE}/luna-send.py', thread, '--file', draft_path]
        if re.match(r'^T-\d+$', ref):
            argv += ['--ref', ref]
        code, out = sh(argv, 200, env)
        last = (out.splitlines() or [''])[-1]
        if code == 3:
            raise RuntimeError(f'Luna refused: {last[:200]}')
        if code == 4:
            raise RuntimeError(f'queued, not confirmed in time: {last[:200]}')
        if code:
            raise RuntimeError(f'send failed: {last[:200]}')
        at = hhmm()
        if lane == 'NOTIFY':
            try:
                mark_done(thread)
            except Exception as e:
                log(f"{rec['cardId']}: sent, but mark done failed: {e}")
        caption = f'replied as Luna {at}; done' if lane != 'PROMISE' else None
        note = f'replied as Luna {at} (pane pick): {fact}' if lane == 'PROMISE' else f'replied as Luna {at} (pane pick)'
        close(rec, verb, note, caption=caption, env=env)
        finish(path, rec, 'done', f'sent as Luna {at}')
        return
    if label == 'I sent it already':
        at = hhmm()
        close(rec, 'do', 'Tobias sent it himself (pane pick)', caption=f'replied {at}; done' if thread else None, env=env)
        finish(path, rec, 'done', 'recorded: sent by hand'); return
    if label == 'Nobody to tell (mark done)':
        if thread:
            mark_done(thread)
        close(rec, 'do', 'nobody to tell (pane pick)', caption='done; nobody to tell' if thread else None, env=env)
        finish(path, rec, 'done', 'thread set to done, nobody to tell'); return
    if label == 'Close the loop (nothing owed)':
        if thread:
            try:
                add_note(thread, f'desk: loop closed, nothing owed. {fact}'.strip())
            except Exception as e:
                log(f"{rec['cardId']}: note failed: {e}")
        close(rec, 'do', f'nothing owed (pane pick): {fact}', env=env)
        finish(path, rec, 'done', 'closed, nothing owed'); return
    if label.startswith('Drop'):
        close(rec, 'drop', f'dropped from the pane: {fact or label}', env=env)
        finish(path, rec, 'done', 'dropped'); return
    if label in deskclass.DEFER:
        trigger = (brief.get('deferTrigger') or '').strip()
        act = (brief.get('deferAct') or '').strip() or card.get('act') or ''
        close(rec, 'defer', '', trigger=trigger, act=act, env=env)
        finish(path, rec, 'done', f'deferred until {trigger}'); return
    raise RuntimeError(f'no script for {label!r}')


def run_one(path, dry=False, force_class=False):
    lock = f'{path}.lock'
    for _ in (1, 2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)); break
        except FileExistsError:
            try:
                if datetime.datetime.now().timestamp() - os.stat(lock).st_mtime < LOCK_S:
                    return 'locked'
                os.remove(lock)   # a runner died with its lock; take it over
            except FileNotFoundError:
                pass
    else:
        return 'locked'
    try:
        rec = json.load(open(path))
        if rec.get('state') != 'queued':
            return rec.get('state')
        if rec.get('class') == 'session' and not force_class:
            return 'session'
        if rec.get('class') == 'model' and not deskclass.MODEL_BUILT:
            return 'model'
        rec['state'] = 'running'; rec['startedAt'] = now_iso(); write_json(path, rec)
        cid = rec['cardId']
        card = card_of(cid)
        if card is None:
            bounce(path, rec, 'the card left the desk before this ran'); return 'bounced'
        if card.get('verb'):
            bounce(path, rec, f"the card already has its verb ({card['verb']})"); return 'bounced'
        if rec.get('key') and (card.get('key') != rec.get('key') or card.get('since') != rec.get('since')):
            bounce(path, rec, 'the card waits on something new since the pick'); return 'bounced'
        try:
            brief = json.load(open(rec['briefFile']))
        except Exception:
            bounce(path, rec, 'the brief is gone; a new one is on its way'); return 'bounced'
        if brief.get('fingerprint') != rec.get('fingerprint'):
            bounce(path, rec, 'the brief was replaced after the pick'); return 'bounced'
        reasons = deskanchor.changed(rec.get('anchor') or {}, deskanchor.anchor(card))
        if reasons:
            bounce(path, rec, '; '.join(reasons)); return 'bounced'
        try:
            run_script(path, rec, brief, card, dry)
        except Exception as e:
            finish(path, rec, 'failed', str(e)); return 'failed'
        return rec.get('state')
    finally:
        try: os.remove(lock)
        except FileNotFoundError: pass


def main(argv):
    if '-h' in argv or '--help' in argv:
        print(__doc__); return 0
    dry = '--dry' in argv
    os.makedirs(DECISIONS, exist_ok=True)
    if '--one' in argv:
        path = argv[argv.index('--one') + 1]
        print(run_one(path, dry, force_class=True)); return 0
    paths = sorted(glob.glob(f'{DECISIONS}/*.json'), key=os.path.getmtime)
    todo = []
    for p in paths:
        try:
            r = json.load(open(p))
        except Exception:
            continue
        if r.get('state') == 'queued' and r.get('class') == 'script':
            todo.append(p)
    if not todo:
        print('execute: nothing queued'); return 0
    with ThreadPoolExecutor(max_workers=min(3, len(todo))) as ex:
        states = list(ex.map(lambda p: run_one(p, dry), todo))
    print('execute: ' + ', '.join(f'{os.path.basename(p)[:-5]} {s}' for p, s in zip(todo, states)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]) or 0)
