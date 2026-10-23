#!/usr/bin/env python3
"""desk-execute.py — run the decisions the pane recorded (desk-decide.py), in the background, without the session.

2026-10-23: the person picks on the pane from a prepared brief; this script does the work the pick names and records
the verb with desk-close.py. Before it acts it reads the brief's anchors again (deskanchor.py): a customer who wrote
after the brief, a thread that moved, a PR with new commits. Any change bounces the decision: nothing runs, the brief
is removed so the next prepare pass writes a new one, and the pane shows why. A decision also bounces when the card
left the desk or already has its verb.

Classes (deskclass.py): `script` runs here with no model; `model` is one headless `claude -p` (Bash, Read, Glob and
Grep, no MCP, DESK_EXECUTE_MODEL, default sonnet) that gets the brief, its raw material, the lane recipe and the one
option, acts, and answers with a JSON verdict (done, result, note, caption, learned) which this script records with
desk-close.py; the model never closes a card itself and cannot send as Luna (the guard below). `session` records are
never touched here, the session executes them and desk-close.py marks them done.

  desk-execute.py              drain every queued script and model decision, up to 3 at a time
  desk-execute.py --one <file> run one record (any class but session), for a look by hand
  desk-execute.py --dry        say what would run
  desk-execute.py --rehearse   model runs only look and report (done=false), nothing is executed: a test of the chain

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
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('desk_harvest', f'{HERE}/desk-harvest.py'); deskharvest = _ilu.module_from_spec(_spec); _spec.loader.exec_module(deskharvest)

DESK = os.environ.get('DESK_DIR') or os.path.expanduser('~/orbit-scratch/desk')
DECISIONS = f'{DESK}/decisions'
BRIEFS = f'{DESK}/briefs'
DRAFTS = f'{DESK}/drafts'
LOCK_S = 10 * 60
MODEL = os.environ.get('DESK_EXECUTE_MODEL', 'sonnet')
LOOP = os.path.expanduser('~/orbit-scratch/agent-loop')
LOOP_SKILL = os.path.expanduser('~/.claude/skills/plain-agent-loop/SKILL.md')
VERDICT = {
    'type': 'object',
    'properties': {
        'done': {'type': 'boolean', 'description': 'true only when the option was carried out in full and verified'},
        'result': {'type': 'string', 'description': 'one plain sentence for the pane: what happened, or why nothing did (under 160 characters, no paths, ids or code words)'},
        'note': {'type': 'string', 'description': 'the close note for the desk log: what was done, with the identifiers the log may carry (PR numbers, terminal or workspace names, times); under 300 characters'},
        'caption': {'type': 'string', 'description': "the loop caption under 50 characters when the lane recipe names one (e.g. 'rework sent 14:02Z; agent on HORST round'); empty otherwise"},
        'learned': {'type': 'array', 'items': {'type': 'string'}, 'description': 'facts about the tenant or requester the memory lacked, one line each with its source; usually empty'},
    },
    'required': ['done', 'result', 'note', 'caption', 'learned'],
}
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


def finish(path, rec, state, result, brief=None, card=None):
    rec['state'] = state
    rec['result'] = result[:300]
    rec['finishedAt'] = now_iso()
    write_json(path, rec)
    log(f"{rec['cardId']} {rec['option']!r} → {state}: {result[:160]}")
    if state == 'done':
        harvest(rec, brief, card)


def harvest(rec, brief, card):
    """What the brief and the run learned goes to the harvest queue; the person answers one question for all of it."""
    lines = list((brief or {}).get('learned') or []) + list(rec.get('learned') or [])
    if not lines:
        return
    mem = (card or {}).get('memory') or {}
    try:
        n = deskharvest.add(rec['cardId'], lines, rec.get('ref') or '', mem.get('tenant') or '', mem.get('customer') or '')
        if n:
            log(f"{rec['cardId']}: {n} learned lines queued for the harvest")
    except Exception as e:
        log(f"{rec['cardId']}: harvest failed: {e}")


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
        finish(path, rec, 'done', f'sent as Luna {at}', brief, card)
        return
    if label == 'I sent it already':
        at = hhmm()
        close(rec, 'do', 'Tobias sent it himself (pane pick)', caption=f'replied {at}; done' if thread else None, env=env)
        finish(path, rec, 'done', 'recorded: sent by hand', brief, card); return
    if label == 'Nobody to tell (mark done)':
        if thread:
            mark_done(thread)
        close(rec, 'do', 'nobody to tell (pane pick)', caption='done; nobody to tell' if thread else None, env=env)
        finish(path, rec, 'done', 'thread set to done, nobody to tell', brief, card); return
    if label == 'Close the loop (nothing owed)':
        if thread:
            try:
                add_note(thread, f'desk: loop closed, nothing owed. {fact}'.strip())
            except Exception as e:
                log(f"{rec['cardId']}: note failed: {e}")
        close(rec, 'do', f'nothing owed (pane pick): {fact}', env=env)
        finish(path, rec, 'done', 'closed, nothing owed', brief, card); return
    if label.startswith('Drop'):
        close(rec, 'drop', f'dropped from the pane: {fact or label}', env=env)
        finish(path, rec, 'done', 'dropped', brief, card); return
    if label in deskclass.DEFER:
        trigger = (brief.get('deferTrigger') or '').strip()
        act = (brief.get('deferAct') or '').strip() or card.get('act') or ''
        close(rec, 'defer', '', trigger=trigger, act=act, env=env)
        finish(path, rec, 'done', f'deferred until {trigger}', brief, card); return
    raise RuntimeError(f'no script for {label!r}')


def _read(path, n=12000):
    try:
        return open(path).read()[:n]
    except Exception as e:
        return f'(unreadable: {e})'


def recipe(lane, cid):
    import importlib.util
    spec = importlib.util.spec_from_file_location('desk_prepare', f'{HERE}/desk-prepare.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.recipe(lane, cid)


def loop_entry(card):
    try:
        st = json.load(open(f'{LOOP}/state.json'))['threads']
    except Exception:
        return None
    ref = card.get('ref') or ''
    e = st.get(ref)
    if e is None and card.get('pr'):
        e = next((v for v in st.values() if str(v.get('pr')) == str(card['pr'])), None)
    return e


def model_prompt(rec, brief, card, rehearse):
    label = rec['option']
    parts = [f"""You carry out ONE decision the person took on their support desk. The desk prepared the card (the brief below) and the
person picked the option {label!r}. Do exactly what that option means per the lane recipe, nothing else, then answer with
the JSON verdict the schema asks for. You run headless on the person's laptop with Bash, Read, Glob and Grep. No one answers questions.

Hard rules:
- Never send or post anything a customer can see, never write to Plain (no reply, note, status or field), never merge a PR,
  never push to develop, staging or production, never write to a production or staging database, never run claude, and never
  run desk-close.py, desk-decide.py or desk-execute.py: the desk records the verb from your verdict.
- The option names the one outward act you may do (a message to an agent's terminal, a worker spawn, closing a PR with a
  comment, running the command a RUN card names). Anything else that would be needed means done=false with the reason.
- When a precondition the recipe names is missing (no validated brief on the thread, the terminal is not idle, the command
  touches production), do not improvise: done=false, result says what is missing in plain words.
- Verify what you did (read the terminal back, check the PR state, read the spawn script's last line) before done=true.
- `result` is read on a pane by someone who has not opened any file: plain words, no paths, ids, hashes or code words.
"""]
    if rehearse:
        parts.append('REHEARSAL: this is a test of the plumbing. Look at everything you would need, but execute NOTHING outward '
                     '(no superset send, no spawn, no gh pr close, no command). Answer done=false and put in `result` the one '
                     'sentence that says what you would do and whether every precondition holds.\n')
    parts.append('## The card\n' + json.dumps({k: card.get(k) for k in ('id', 'ref', 'lane', 'impact', 'waits', 'status', 'note', 'act', 'pr', 'repo', 'thread', 'url')}, ensure_ascii=False, indent=1))
    parts.append('## The brief (prepared ahead, the person picked from its options)\n' + json.dumps(
        {k: brief.get(k) for k in ('why', 'decide', 'then', 'fact', 'draft', 'checks', 'openChecks', 'options', 'preparedAt')}, ensure_ascii=False, indent=1))
    parts.append(f'## The picked option\n{label} (verb {rec.get("verb")})')
    parts.append('## The lane recipe (CARDS.md)\n' + (recipe(card.get('lane') or '', card['id']) or '(no recipe section)'))
    entry = loop_entry(card)
    if entry:
        parts.append('## The agent loop entry for this card (state.json; workspace, host, terminal, phase)\n' + json.dumps(entry, ensure_ascii=False, indent=1)[:4000])
    if label.startswith('Hand'):
        parts.append('## How workers are spawned and reached (plain-agent-loop skill, Superset constants)\n' + '\n'.join(_read(LOOP_SKILL, 40000).splitlines()[28:62]))
        parts.append('## spawn-worker.sh and worker-prompt.sh (usage headers)\n' + '\n'.join(_read(f'{LOOP}/spawn-worker.sh', 6000).splitlines()[:30])
                     + '\n\n' + '\n'.join(_read(f'{LOOP}/worker-prompt.sh', 4000).splitlines()[:12]))
        parts.append('Terminals of a running worker: `superset terminals list --workspace <ws> [--host <host>] --json`, read one back with '
                     '`superset terminals read <id> …`, send with `superset terminals send <id> --text "…"` (only when the screen shows it idle).')
    ctx = (brief.get('context') or '')
    if ctx:
        parts.append('## Raw material the brief was written from\n' + _read(ctx, 30000))
    return '\n\n'.join(parts)


def run_model(path, rec, brief, card, dry, rehearse):
    label = rec['option']
    if dry:
        finish(path, rec, 'queued', f'dry: would run a {MODEL} run for {label}'); return
    p = model_prompt(rec, brief, card, rehearse)
    os.makedirs(f'{DECISIONS}/runs', exist_ok=True)
    stem = f"{DECISIONS}/runs/{os.path.basename(path)[:-5]}"
    with open(f'{stem}.prompt.md', 'w') as f:
        f.write(p)
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_PLUGIN_DIRS'}
    env.update({'DESK_DECISION': path, 'DESK_PREPARE': '1'})
    r = subprocess.run(['claude', '-p', '--model', MODEL, '--effort', 'medium', '--no-session-persistence',
                        '--tools', 'Bash,Read,Glob,Grep', '--permission-mode', 'bypassPermissions',
                        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--output-format', 'json',
                        '--json-schema', json.dumps(VERDICT), p],
                       capture_output=True, text=True, timeout=7 * 60, env=env, cwd=DESK)   # the mod cuts the drain at 10 min
    with open(f'{stem}.out.json', 'w') as f:
        f.write(r.stdout or '')
    try:
        out = json.loads(r.stdout)
    except Exception:
        raise RuntimeError(f'model run gave no JSON (exit {r.returncode}): {(r.stderr or r.stdout)[-200:]}')
    if out.get('is_error') or not out.get('structured_output'):
        raise RuntimeError(f"model run failed: {str(out.get('result'))[:200]}")
    v = out['structured_output']
    rec['learned'] = [str(x) for x in (v.get('learned') or [])][:10]
    rec['costUsd'] = out.get('total_cost_usd')
    if not v.get('done'):
        finish(path, rec, 'failed', f"not done: {v.get('result') or 'no reason given'}"); return
    caption = (v.get('caption') or '').strip() or None
    if rec['cardId'].startswith(('loop:', 'gap:')) and not caption:
        caption = (v.get('result') or label)[:49]
    close(rec, rec.get('verb') or 'do', (v.get('note') or v.get('result') or label)[:300], caption=caption, env={'DESK_DECISION': path})
    finish(path, rec, 'done', v.get('result', brief, card) or 'done')


def run_one(path, dry=False, force_class=False, rehearse=False):
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
        if rec.get('class') not in ('script', 'model'):
            return rec.get('class')
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
            if rec.get('class') == 'model':
                run_model(path, rec, brief, card, dry, rehearse)
            else:
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
    rehearse = '--rehearse' in argv
    os.makedirs(DECISIONS, exist_ok=True)
    if '--one' in argv:
        path = argv[argv.index('--one') + 1]
        print(run_one(path, dry, force_class=True, rehearse=rehearse)); return 0
    paths = sorted(glob.glob(f'{DECISIONS}/*.json'), key=os.path.getmtime)
    todo = []
    for p in paths:
        try:
            r = json.load(open(p))
        except Exception:
            continue
        if r.get('state') == 'running' and not os.path.exists(f'{p}.lock'):
            # a drain the mod cut off (ten-minute cap) left the record running with no runner behind it; never re-run
            # a send blindly: the person picks again from a fresh brief
            try:
                t0 = datetime.datetime.strptime(r.get('startedAt') or '', '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
                stale = (datetime.datetime.now(datetime.timezone.utc) - t0).total_seconds() > 12 * 60
            except Exception:
                stale = True
            if stale:
                finish(p, r, 'failed', 'the background run was cut off before it finished; pick again')
            continue
        if r.get('state') == 'queued' and r.get('class') in ('script', 'model'):
            todo.append(p)
    if not todo:
        print('execute: nothing queued'); return 0
    with ThreadPoolExecutor(max_workers=min(3, len(todo))) as ex:
        states = list(ex.map(lambda p: run_one(p, dry, rehearse=rehearse), todo))
    print('execute: ' + ', '.join(f'{os.path.basename(p)[:-5]} {s}' for p, s in zip(todo, states)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]) or 0)
