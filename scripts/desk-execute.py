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
                               (the unattended validation runs in full, but every Plain write is dry: DESK_PLAIN_DRY=1)

"Validate and draft (unattended)" (FAST cards, 2026-11-09) is a model run of its own (run_unattended): this script claims
the thread, one headless run (DESK_VALIDATE_MODEL, default opus, in the Orbit checkout so its production guard hook
applies) checks the Hermes brief by the /triage rules, posts the note through plain-note.py and answers with the
verdict and a drafted reply; this script releases the claim, writes the draft file, captions the card without closing
it, drops the brief and starts a prepare pass for the card, which comes back with "Send it" first.

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
import deskplain  # noqa: E402
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('desk_harvest', f'{HERE}/desk-harvest.py'); deskharvest = _ilu.module_from_spec(_spec); _spec.loader.exec_module(deskharvest)

DESK = os.environ.get('DESK_DIR') or os.path.expanduser('~/orbit-scratch/desk')
DECISIONS = f'{DESK}/decisions'
BRIEFS = f'{DESK}/briefs'
DRAFTS = f'{DESK}/drafts'
LOCK_S = 10 * 60
MODEL = os.environ.get('DESK_EXECUTE_MODEL', 'sonnet')
VALIDATE_MODEL = os.environ.get('DESK_VALIDATE_MODEL', 'opus')
ORBIT = os.environ.get('DESK_ORBIT_REPO') or os.path.expanduser('~/orbit')
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
    try:  # 2026-11-09: the per-card record is overwritten by the next pick; this line is never
        with open(f'{DESK}/decisions.log.jsonl', 'a') as f:
            f.write(json.dumps({'at': rec['finishedAt'], 'cardId': rec['cardId'], 'lane': (card or {}).get('lane') or (brief or {}).get('lane'),
                                'option': rec.get('option'), 'class': rec.get('class'), 'state': state, 'result': result[:200],
                                'queuedAt': rec.get('queuedAt'), 'startedAt': rec.get('startedAt')}, ensure_ascii=False) + '\n')
    except Exception:
        pass
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


# 2026-11-09: the writes live in deskplain.py. The field write here lacked Plain's `identifier` and the note its
# customer id, so the readiness flip before a send to a ready-for-human thread and the "loop closed" note never landed.
readiness_of = deskplain.readiness_of
set_readiness = deskplain.set_readiness
mark_done = deskplain.mark_done
add_note = deskplain.add_note


def close(rec, verb, note, caption=None, trigger=None, act=None, env=None, sent=False):
    """desk-close.py with the pick, so the brief is scored like a session close. `sent`: the customer has the answer, so
    the thread's reply wait stays closed until they write again (2026-11-09)."""
    argv = ['python3', f'{HERE}/desk-close.py', rec['cardId'], verb]
    if verb == 'defer':
        argv += [trigger, act or '']
    else:
        argv += [note]
    argv += ['--pick', rec['option']]
    if sent:
        argv += ['--sent']
    if caption:
        argv += ['--caption', caption]
    code, out = sh(argv, 60, env)
    if code:
        raise RuntimeError(f'desk-close failed: {out[:200]}')
    rec['closed'] = True   # the card is closed from here on: a later error must not undo that in the record
    if 'Not closed' in out:
        raise RuntimeError(out[:200])
    return out


def card_of(cid):
    try:
        cache = json.load(open(f'{DESK}/cards.json'))
    except Exception:
        return None
    return next((c for c in cache.get('cards', []) if c.get('id') == cid), None)


def bounce(path, rec, reason, dry=False):
    """Nothing ran. The brief goes away so the next prepare pass writes a fresh one; the card stays on the desk."""
    if dry:
        rec['state'] = 'queued'; write_json(path, rec); log(f"{rec['cardId']}: dry, would bounce: {reason}"); return
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
            # 2026-11-09 (all desk history): every script-class failure was "Send it" on a thread at ready-for-human
            # (6 of 29). The documented recovery runs here: flip the readiness to needs-info, send, put it back.
            was = readiness_of(thread)
            if was in ('ready-for-human', 'wontfix'):
                set_readiness(thread, 'needs-info')
                try:
                    code, out = sh(argv + ['--allow-terminal-readiness'], 200, env)
                    last = (out.splitlines() or [''])[-1]
                finally:
                    set_readiness(thread, was)
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
        close(rec, verb, note, caption=caption, env=env, sent=True)
        finish(path, rec, 'done', f'sent as Luna {at}', brief, card)
        return
    if label == 'I sent it already':
        at = hhmm()
        close(rec, 'do', 'Tobias sent it himself (pane pick)', caption=f'replied {at}; done' if thread else None, env=env, sent=True)
        finish(path, rec, 'done', 'recorded: sent by hand', brief, card); return
    if label == 'Nobody to tell (mark done)':
        if thread:
            mark_done(thread)
        close(rec, 'do', 'nobody to tell (pane pick)', caption='done; nobody to tell' if thread else None, env=env, sent=True)
        finish(path, rec, 'done', 'thread set to done, nobody to tell', brief, card); return
    if label == 'Close the loop (nothing owed)':
        if thread:
            try:
                add_note(thread, f'desk: loop closed, nothing owed. {fact}'.strip())
            except Exception as e:
                log(f"{rec['cardId']}: note failed: {e}")
        close(rec, 'do', f'nothing owed (pane pick): {fact}', env=env, sent=True)
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
    if label == deskplain.VALIDATE:
        return run_unattended(path, rec, brief, card, dry, rehearse)
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
    finish(path, rec, 'done', v.get('result') or 'done', brief, card)


UNATTENDED_VERDICT = {
    'type': 'object',
    'properties': {
        **VERDICT['properties'],
        'done': {'type': 'boolean', 'description': 'true only when the validation note was posted (plain-note.py printed "posted:") and the reply below is drafted or deliberately empty'},
        'verdict': {'type': 'string', 'enum': ['confirmed', 'partially-confirmed', 'refuted', 'stale', 'none'], 'description': "the note's verdict on the Hermes brief; none when no note was posted"},
        'readiness': {'type': 'string', 'description': 'the readiness you judge the thread at (ready-for-agent, ready-for-human, needs-info, wontfix); the note says it, nothing writes it'},
        'draft': {'type': 'string', 'description': 'the customer reply to send as Luna, or empty when no reply is the move (a teammate holds the thread, nothing true to tell the customer yet)'},
        'checks': {'type': 'array', 'items': {'type': 'string'}, 'description': 'each claim in the draft with the evidence that backs it (a note row, an entity read, a code line)'},
        'openChecks': {'type': 'array', 'items': {'type': 'string'}, 'description': 'claims the reply would need that you could not back; they stay out of the draft'},
    },
    'required': VERDICT['required'] + ['verdict', 'readiness', 'draft', 'checks', 'openChecks'],
}
TRIAGE_STEPS = ('## Forensic Gotchas', '## Step 3', '## Step 4', '## Step 10: Write Back')


def triage_rules():
    """The /triage command's reading, validation and note rules, from origin/develop of the Orbit repo (the checkout lags)."""
    code, md = sh(['git', '-C', ORBIT, 'show', 'origin/develop:.claude/commands/triage.md'], 30)
    if code:
        md = _read(f'{ORBIT}/.claude/commands/triage.md', 60000)
    out = [sec.strip() for sec in re.split(r'(?m)^(?=## )', md) if sec.startswith(TRIAGE_STEPS)]
    return '\n\n'.join(out)[:24000] or '(the triage rules could not be read: validate by the note format below and say so)'


def safe(name):
    return re.sub(r'[^A-Za-z0-9._-]', '_', name)


def _prepare_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location('desk_prepare', f'{HERE}/desk-prepare.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def unattended_prompt(rec, brief, card, rehearse):
    ref, thread = card.get('ref') or '', rec.get('thread') or card.get('thread')
    timeline = _prepare_mod().plain_timeline(thread, n=40, width=8000)[:70000]
    ctx = _read(brief.get('context') or '', 60000)
    memory = re.search(r'(?ms)^## Support memory block.*?(?=^## |\Z)', ctx)
    prod = 'STAGE=production pnpm -s -C ' + ORBIT   # the one shape the production guard lets through
    parts = [f"""You validate ONE support thread ({ref}) for the person who runs a support desk, unattended, and draft the reply.
The person picked "{deskplain.VALIDATE}" on their pane and is not watching. You run headless with Bash, Read, Glob and Grep,
no MCP servers, in the Orbit checkout ({ORBIT}). No one answers questions.

The desk has already claimed the thread for you (triage_owner=local) and releases the claim after you answer. Your job:
1. Read the thread below. Find the newest full Hermes pass (by its marker, see the rules) and extract its claim sheet.
2. Validate the load-bearing claims by the /triage rules below, in about six minutes of work: CONFIRMED, REFUTED or
   UNVERIFIED, each with your own evidence. Sample the rest.
3. Post ONE internal note with: python3 {HERE}/plain-note.py {thread} --file <your note file>
   Write the note file under {DECISIONS}/runs/ first. The note's first line is exactly:
   {deskplain.UNATTENDED_LINE}
   then an empty line, then the note in the "## Local triage validation (Claude)" format of step 10a, with these changes:
   - right under the heading: "> _Unattended desk run ({VALIDATE_MODEL}); no MCP, so no Sentry, PostHog replay or attachment reads._"
   - "**Validated against:**" names what you read (branch@commit, entities re-queried, files) and what you could not read.
   - a line "**Readiness (unattended view):** <ready-for-agent | ready-for-human | needs-info | wontfix>" with one reason.
   - keep the format's sections: the claim table, "### Corrections" when something was refuted or missing, and
     "### Hermes feedback" with its `<!-- hermes-feedback: <flaw-class>, … -->` comment (or the line "No feedback; brief was accurate.").
   Keep it under 9000 characters. plain-note.py refuses a note without the first line; read its output: "posted:" means it landed.
4. Draft the customer reply from what you validated (rules in "Support memory" and "Sending as Luna" below): German "du"
   unless the customer wrote "Sie", warm and short, signed as Luna, no ids, no internal words, no memory quotes. Every claim
   in it backed by your evidence (`checks`); a claim you could not back stays out (`openChecks`). Leave `draft` empty when the
   "Who holds this thread" block shows a teammate holds it, or when there is nothing true to tell the customer yet.
5. Answer with the JSON verdict. `caption`: under 50 characters, e.g. "validated unattended 21:40Z; reply drafted".

Hard rules (they override everything below):
- Your ONLY write outside {DECISIONS}/runs/ is that one note through plain-note.py (and, when the note carries Hermes
  feedback, one entry appended to ~/orbit-scratch/hermes-feedback/LOG.md with "unattended desk run" in its heading).
  Never reply to the customer, never send as Luna, never write a Plain field, status, label or assignee, never write to any
  database, never change, commit or push code, never check out a branch in {ORBIT}, never run claude, desk-close.py,
  desk-decide.py, desk-execute.py or luna-send.py.
- Code: read it at the branch of the reported stage without touching the working tree: `git -C {ORBIT} fetch -q origin`,
  then `git -C {ORBIT} show origin/production:<path>` and `git -C {ORBIT} grep -n <pattern> origin/production -- <dir>`.
- DynamoDB: keyed reads only, through the dynamo-query wrapper; never a scan, never `aws dynamodb` by hand. The stage is an
  environment variable, never a flag: `{prod} dynamo:query <Entity> --id=… --tenant=… --format=json`
  (staging: `STAGE=staging pnpm -s dynamo:query …`). `--env=` is not a flag of that script. When a guard hook rejects the
  call it prints the one path it accepts after `pnpm -C`: re-run with that literal path. `--keys-only` debugs a key shape.
  An AWS login error means the session expired: say so in the note and in `result`; do not retry for minutes.
- More of the thread: `python3 ~/orbit-scratch/agent-loop/jev/plain.py timeline {thread} 60` (read-only JSON).
- When the thread shows a person's claim, a teammate's open question, or no Hermes brief at all: post no note, done=false,
  and `result` says why in plain words. The session takes the card then.
- `result` is read on a pane by someone who has not opened any file: plain words, no paths, ids, hashes or code words.
"""]
    if rehearse:
        parts.append('REHEARSAL: a test of the chain. Do the whole job, but plain-note.py runs dry here (it prints "dry, would post:" and writes '
                     'nothing): treat that line as the note landing. Append nothing to the feedback log. Answer as you would for real.\n')
    parts.append('## The card\n' + json.dumps({k: card.get(k) for k in ('id', 'ref', 'lane', 'impact', 'waits', 'status', 'note', 'act', 'pr', 'thread')}, ensure_ascii=False, indent=1))
    parts.append('## What the brief saw (prepared before the pick)\n' + json.dumps({k: brief.get(k) for k in ('why', 'fact', 'then', 'checks', 'openChecks')}, ensure_ascii=False, indent=1))
    parts.append(f'## The Plain thread, read now (newest first; notes and mails up to 8000 characters)\n{timeline}')
    parts.append(memory.group(0).strip() if memory else '## Support memory block\nnone in the brief: the company is unmapped; never guess the tenant from the mail domain')
    parts.append('## The /triage rules (Orbit repo, .claude/commands/triage.md on origin/develop)\n'
                 'Where they name an MCP tool (get_thread, add_internal_note, upsert_thread_field, Sentry, PostHog) you have none: use the commands above '
                 'and name what you could not read. `--env=<stage>` on dynamo:query in these rules is outdated: the stage goes in STAGE.\n\n' + triage_rules())
    parts.append('## The lane recipe and the customer-text rules (CARDS.md)\n' + (recipe('FAST', card['id']) or '(no recipe section)'))
    return '\n\n'.join(parts)


def drop_brief(rec):
    for g in (rec.get('briefFile') or '', (rec.get('briefFile') or '').replace('.json', '.ctx.md')):
        try:
            if g: os.remove(g)
        except FileNotFoundError:
            pass


def reprepare(cid):
    """A prepare pass for this card alone, detached: the card comes back with a brief written from the new thread state."""
    env = {k: v for k, v in os.environ.items() if k not in ('DESK_PREPARE', 'CLAUDE_CODE_PLUGIN_DIRS', 'DESK_DECISION', 'DESK_PLAIN_DRY')}
    try:
        subprocess.Popen(['python3', f'{HERE}/desk-prepare.py', cid], env=env, cwd=DESK, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f'{cid}: prepare not started: {e}')


def run_unattended(path, rec, brief, card, dry, rehearse):
    """Claim, validate, note, draft: one background job for a FAST card (2026-11-09: "Claim + validate now" was picked
    28 of 43 times, took about five minutes, and 31 cards then came back a second time for the reply)."""
    cid, ref = rec['cardId'], card.get('ref') or ''
    thread = rec.get('thread') or card.get('thread')
    if not thread or not re.match(r'^T-\d+$', ref):
        raise RuntimeError('the card names no support thread; the session validates it')
    if dry:
        finish(path, rec, 'queued', f'dry: would claim {ref}, run a {VALIDATE_MODEL} validation, post its note and draft the reply'); return
    rec['rehearsal'] = bool(rehearse)
    stem = f"{DECISIONS}/runs/{os.path.basename(path)[:-5]}"
    os.makedirs(f'{DECISIONS}/runs', exist_ok=True)
    receipt = f'{path}.note'   # plain-note.py writes it; never *.json, the pane reads those as picks
    try:
        os.remove(receipt)
    except FileNotFoundError:
        pass
    claimed = False
    try:
        if not rehearse:
            deskplain.claim(thread)   # raises while a person or another local run holds the thread
            claimed = True
        p = unattended_prompt(rec, brief, card, rehearse)
        with open(f'{stem}.prompt.md', 'w') as f:
            f.write(p)
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_PLUGIN_DIRS'}
        env.update({'DESK_DECISION': path, 'DESK_PREPARE': '1'})
        if rehearse:
            env['DESK_PLAIN_DRY'] = '1'
        # cwd = the Orbit checkout: its project hooks (the production guard) load in the headless run too
        r = subprocess.run(['claude', '-p', '--model', VALIDATE_MODEL, '--effort', 'medium', '--no-session-persistence',
                            '--tools', 'Bash,Read,Glob,Grep', '--permission-mode', 'bypassPermissions',
                            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--output-format', 'json',
                            '--json-schema', json.dumps(UNATTENDED_VERDICT), p],
                           capture_output=True, text=True, timeout=int(8.5 * 60), env=env, cwd=ORBIT)
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
        try:
            note = json.load(open(receipt))
        except Exception:
            note = None
        # the receipt, not the model's word, says whether a note landed
        landed = bool(note) and note.get('firstLine') == deskplain.UNATTENDED_LINE and bool(note.get('dry')) == bool(rehearse)
        if not v.get('done') or not landed:
            why = v.get('result') if not v.get('done') else 'the run said done, but no note landed on the thread'
            raise RuntimeError(f"not done: {why or 'no reason given'}")
    except Exception:
        if claimed:
            try:
                deskplain.release(thread)
            except Exception as e:
                log(f'{cid}: claim not released: {e}')
        if not rehearse:
            drop_brief(rec)   # the next brief sees the failed record and leads with the session's claim instead
        raise
    if claimed:
        try:
            deskplain.release(thread)
        except Exception as e:
            log(f'{cid}: claim not released (the 24 h limit frees it): {e}')
    draft = (v.get('draft') or '').strip()
    side = {'at': now_iso(), 'cardId': cid, 'ref': ref, 'verdict': v.get('verdict'), 'readiness': v.get('readiness'),
            'draft': draft, 'checks': v.get('checks') or [], 'openChecks': v.get('openChecks') or [], 'model': VALIDATE_MODEL}
    if rehearse:
        with open(f'{stem}.draft.json', 'w') as f:
            json.dump(side, f, ensure_ascii=False, indent=1)
        finish(path, rec, 'failed', f"rehearsal: the note would be posted ({note.get('chars')} characters, verdict {v.get('verdict')}); "
                                    f"reply {'drafted' if draft else 'not drafted'}. {v.get('result') or ''}".strip())
        return
    os.makedirs(DRAFTS, exist_ok=True)
    if draft:
        with open(f'{DRAFTS}/{safe(ref)}.txt', 'w', encoding='utf-8') as f:
            f.write(draft + '\n')
    with open(f'{DRAFTS}/{safe(ref)}.unattended.json', 'w', encoding='utf-8') as f:
        json.dump(side, f, ensure_ascii=False, indent=1)
    caption = ((v.get('caption') or '').strip() or f"validated unattended {hhmm()}; {'reply drafted' if draft else 'no reply drafted'}")[:49]
    # a caption, not a verb: a verb would unpin a FAST card, and the card has to come back with the drafted reply
    code, outc = sh(['python3', f'{HERE}/desk-close.py', cid, 'caption', caption], 60, {'DESK_DECISION': path})
    if code:
        log(f'{cid}: caption not written: {outc[:160]}')
    drop_brief(rec)
    finish(path, rec, 'done', v.get('result') or f"validated and drafted ({v.get('verdict')})", brief, card)
    reprepare(cid)


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
            bounce(path, rec, 'the card left the desk before this ran', dry); return 'bounced'
        if card.get('verb'):
            bounce(path, rec, f"the card already has its verb ({card['verb']})", dry); return 'bounced'
        if rec.get('key') and (card.get('key') != rec.get('key') or card.get('since') != rec.get('since')):
            bounce(path, rec, 'the card waits on something new since the pick', dry); return 'bounced'
        try:
            brief = json.load(open(rec['briefFile']))
        except Exception:
            bounce(path, rec, 'the brief is gone; a new one is on its way', dry); return 'bounced'
        if brief.get('fingerprint') != rec.get('fingerprint'):
            bounce(path, rec, 'the brief was replaced after the pick', dry); return 'bounced'
        reasons = deskanchor.changed(rec.get('anchor') or {}, deskanchor.anchor(card))
        if reasons:
            bounce(path, rec, '; '.join(reasons), dry); return 'bounced'
        try:
            if rec.get('class') == 'model':
                run_model(path, rec, brief, card, dry, rehearse)
            else:
                run_script(path, rec, brief, card, dry)
        except Exception as e:
            if rec.get('closed'):
                # 2026-10-26: a crash after desk-close (a typo in the done path) marked a finished hand-off failed, and the
                # pane showed a closed card with nothing to press. The close is the fact; the error goes into the result.
                finish(path, rec, 'done', f'closed; then an error after the close: {e}'); return 'done'
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
