#!/usr/bin/env python3
"""desk-prepare.py — prepare the top cards ahead of time, so pressing `w` shows the options at once.

The slow part of a card is the push-right step: reading the thread, the memory block, the PR, then drafting.
This script does that for the top N open cards in the background, right after a gather: it collects the raw
material per card (scripts only, no LLM), hands one headless `claude -p` per card a self-contained context, and
writes the answer to briefs/<card id>.json. The desk mod shows a prepared card with a marker and tells Claude to
start from the brief; Claude only checks the brief is still current before it calls desk_card.

A customer-text card (REPLY, NOTIFY, PROMISE, FAST) whose brief carries a draft, no open checks and replyReady gets the
options Send it · Edit then send · I sent it already · Defer · Drop, Send it first; any other brief keeps the lane's set.
Ready is a property of the draft alone (2026-10-22, from the score ledger: the brief said "validate first" while the
person pressed Send on a draft with nothing to refute): every claim backed, or no refutable claim; the thread's claim
or validation state does not block it. The material opens with who holds the thread (assignee, triage claim, last
internal note, teammates named in the newest customer message), and a thread a teammate holds is theirs to answer.

A brief is bound to the card's caption (status + act). When the caption changes the brief is stale and the next
pass writes a new one. Batch cards and lanes that take the session over are never prepared.

  desk-prepare.py [--top N] [--force] [--dry] [--help] [<card id> …]

Env: PLAIN_API_KEY (thread reads), DESK_PREPARE_MODEL (default sonnet), DESK_PREPARE_TOP (default 5).
Never runs inside a prepare child (DESK_PREPARE=1) so a child cannot start children.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser('~')
DESK = f'{HOME}/orbit-scratch/desk'
BRIEFS = f'{DESK}/briefs'
SKILL = os.path.expanduser('~/.claude/skills/desk')
LOCK_S = 5 * 60
MODEL = os.environ.get('DESK_PREPARE_MODEL', 'sonnet')
# lanes whose recipe has a prepare step that scripts can feed; SEND/STALE/TICKET are batches, SWEEP runs and sweep tickets take the session over
LANES = {'REPLY', 'NOTIFY', 'PROMISE', 'FAST', 'REVIEW', 'RUN', 'INBOX', 'TASK', 'SWEEP'}
SKIP_ID = re.compile(r'^(batch:|sweep:|sweep-ticket:|prh:|memory:)')
# the fixed option sets per lane (CARDS.md lanes table); the model picks the order, never the set
OPTIONS = {
    'REPLY': ['Send it', 'I sent it already', 'Edit then send', 'Defer until a trigger', 'Drop'],
    'NOTIFY': ['Send it', 'I sent it already', 'Edit then send', 'Nobody to tell (mark done)', 'Defer'],
    'PROMISE': ['Reply with a status', 'I sent it already', 'Close the loop (nothing owed)', 'Hand to a worker / ticket', 'Defer', 'Drop'],
    'FAST': ['Claim + validate now', 'Draft the reply', 'I sent it already', 'Hand to a worker', 'Defer', 'Drop (unpin)'],
    'REVIEW': ['Hand the rework to its agent', 'Review it now', 'Close the PR', 'Defer'],
    'RUN': ['Go (agent runs it now)', 'Show me the command first', 'Defer', 'Drop'],
    'INBOX': ['Answer now', 'Defer', 'Drop (reply as human: declined)', 'Close as read', 'Reply with a note'],
    'TASK': ['Start it now', 'Hand it to agents', 'Defer', 'Drop'],
    'SWEEP': ['Answer', 'Defer', 'Drop (accepted risk, recorded)'],
}
# 2026-10-14: a customer-text card whose reply is drafted and checked puts the send on the pane, first. The pick is the
# send approval; no second question. OPTIONS above stay the set while checks are open or the thread is not yet claimed.
REPLY_LANES = {'REPLY', 'NOTIFY', 'PROMISE', 'FAST'}
DRAFTED = ['Send it', 'Edit then send', 'I sent it already', 'Defer', 'Drop']


def drafted(lane, so):
    """True when the brief carries a reply ready to send: a customer-text lane, a draft, no open checks, not resolved."""
    return (lane in REPLY_LANES and bool((so.get('draft') or '').strip()) and not so.get('openChecks')
            and not so.get('resolved') and bool(so.get('replyReady')))
VERBS = {'Send it': 'delegate', 'I sent it already': 'do', 'Edit then send': 'delegate', 'Defer': 'defer', 'Defer until a trigger': 'defer',
         'Drop': 'drop', 'Drop (unpin)': 'drop', 'Nobody to tell (mark done)': 'do', 'Reply with a status': 'delegate',
         'Close the loop (nothing owed)': 'do', 'Hand to a worker / ticket': 'delegate', 'Claim + validate now': 'do',
         'Draft the reply': 'delegate', 'Hand to a worker': 'delegate', 'Hand the rework to its agent': 'delegate',
         'Review it now': 'do', 'Close the PR': 'drop', 'Go (agent runs it now)': 'do', 'Show me the command first': 'decide',
         'Answer now': 'decide', 'Drop (reply as human: declined)': 'drop', 'Close as read': 'do', 'Reply with a note': 'delegate',
         'Start it now': 'do', 'Hand it to agents': 'delegate', 'Answer': 'decide', 'Drop (accepted risk, recorded)': 'drop'}
SCHEMA = {
    'type': 'object',
    'properties': {
        'why': {'type': 'string', 'description': 'one plain sentence: what put this card on the desk, since when, and who waits'},
        'decide': {'type': 'string', 'description': 'the one question the person answers, in one plain sentence'},
        'then': {'type': 'string', 'description': 'one or two plain sentences: what the recommended option sets in motion, and what is at stake if nothing happens'},
        'fact': {'type': 'string', 'description': 'the single fact that decides this card, one plain sentence under 140 characters, no code words or ids'},
        'draft': {'type': 'string', 'description': 'the drafted consequence: the customer text, the reply, the command, or the decision; empty when nothing is drafted'},
        'options': {'type': 'array', 'items': {'type': 'object', 'properties': {'label': {'type': 'string'}, 'why': {'type': 'string'}}, 'required': ['label', 'why']},
                    'description': "the lane's fixed options, recommended first"},
        'checks': {'type': 'array', 'items': {'type': 'string'}, 'description': 'each claim in the draft with the evidence line in the context that backs it'},
        'openChecks': {'type': 'array', 'items': {'type': 'string'}, 'description': 'claims the draft needs that the context does not back; Claude verifies these live before desk_card'},
        'resolved': {'type': 'boolean', 'description': 'true when the context shows the card is already resolved (reply sent, PR merged, line submitted)'},
        'replyReady': {'type': 'boolean', 'description': 'true when `draft` is customer text the person could send as it stands: every claim in checks or no refutable claim at all, openChecks empty, a reply is the move, no teammate holds the thread'},
    },
    'required': ['why', 'decide', 'then', 'fact', 'draft', 'options', 'checks', 'openChecks', 'resolved', 'replyReady'],
}
log_lock = threading.Lock()


def log(msg):
    with log_lock:
        with open(f'{BRIEFS}/prepare.log', 'a') as f:
            f.write(f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')} {msg}\n")


def sh(argv, timeout=60, env=None):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})
        return (r.stdout or '') + (('\n[stderr] ' + r.stderr.strip()) if r.returncode and r.stderr.strip() else '')
    except Exception as e:
        return f'[failed: {e}]'


PLAIN_Q = '''query($id: ID!, $n: Int!){ thread(threadId:$id){ ref title status statusDetail{ __typename } threadFields{ key stringValue }
 assignedTo{ __typename ... on User{ fullName } ... on MachineUser{ fullName } }
 customer{ fullName email{ email } } timelineEntries(first: $n){ edges{ node{ timestamp{ iso8601 }
 actor{ __typename ... on UserActor{ user{ fullName } } ... on MachineUserActor{ machineUser{ fullName } } ... on CustomerActor{ customer{ fullName } } }
 entry{ __typename ... on NoteEntry{ noteText: text } ... on EmailEntry{ subject textContent } ... on ChatEntry{ chatText: text }
 ... on SlackMessageEntry{ slackText: text } ... on SlackReplyEntry{ slackReplyText: text } ... on ThreadStatusTransitionedEntry{ nextStatus } } } } } } }'''


def plain_timeline(thread_id, n=14, width=2500):
    """The thread's head and its newest n timeline entries, notes and mails in full (plain-tl.sh cuts them at 160 chars,
    which loses the Hermes brief and the validation note the draft depends on). Read-only, one request."""
    key = os.environ.get('PLAIN_API_KEY')
    if not key:
        return '(no PLAIN_API_KEY: thread not read)'
    import urllib.request
    body = json.dumps({'query': PLAIN_Q, 'variables': {'id': thread_id, 'n': n}}).encode()
    req = urllib.request.Request('https://core-api.uk.plain.com/graphql/v1', data=body, headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json', 'User-Agent': 'desk-prepare/1 (+python-urllib)'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            t = json.load(r)['data']['thread']
    except Exception as e:
        return f'(thread read failed: {e})'
    if not t:
        return '(thread not found)'
    lines = [f"{t['ref']} {t['status']}/{(t.get('statusDetail') or {}).get('__typename')} · {t['title']} · customer {t['customer']['fullName']} <{(t['customer'].get('email') or {}).get('email')}>",
             '; '.join(f"{f['key']}={f['stringValue']}" for f in t.get('threadFields') or [])]
    rows = []
    for e in t['timelineEntries']['edges']:
        n_ = e['node']; a = n_.get('actor') or {}; en = n_.get('entry') or {}
        who = ((a.get('user') or {}).get('fullName') or (a.get('machineUser') or {}).get('fullName') or (a.get('customer') or {}).get('fullName') or a.get('__typename'))
        text = en.get('noteText') or en.get('textContent') or en.get('chatText') or en.get('slackText') or en.get('slackReplyText') or en.get('nextStatus') or ''
        if en.get('subject') and en.get('textContent'):
            text = f"[{en['subject']}] {text}"
        text = re.sub(r'\n{3,}', '\n\n', text or '').strip()
        rows.append({'at': n_['timestamp']['iso8601'][:16], 'kind': en.get('__typename', '').replace('Entry', ''), 'who': who, 'actor': a.get('__typename') or '', 'text': text})
        lines.append(f"\n--- {rows[-1]['at']} {rows[-1]['kind']} {who}\n{text[:width]}{'…' if len(text) > width else ''}")
    return holder_block(t, rows) + '\n'.join(lines)


def holder_block(t, rows):
    """Who holds the thread, on top of the material (2026-10-22: a brief recommended "Draft the reply" while the customer
    had tagged a teammate and the answer was that teammate's). The Plain assignee, the triage claim, the last internal
    note, and every teammate (config.json team) named in the newest customer message, quoted history cut, after our last reply."""
    sys.path.insert(0, DESK)
    import deskconfig
    f = {x['key']: x.get('stringValue') for x in t.get('threadFields') or []}
    a = t.get('assignedTo') or {}
    out = [f"assignee: {a.get('fullName') or 'nobody'}" + (f" ({a['__typename']})" if a.get('__typename') else ''),
           f"triage claim: {f.get('triage_owner') or 'none'}" + (f" since {f['triage_claimed_at'][:16]}" if f.get('triage_claimed_at') else '')]
    notes = [r for r in rows if r['kind'] == 'Note' and r['actor'] == 'UserActor']
    if notes:
        n = notes[0]  # newest first in the Plain page
        out.append(f"last internal note: {n['who']} at {n['at']}: {n['text'][:200].replace(chr(10), ' ')}")
    visible = [r for r in rows if r['kind'] != 'Note' and r['kind'] != 'ThreadStatusTransitioned']
    newest_cust = next((r for r in visible if r['actor'] == 'CustomerActor'), None)
    ours_after = any(r['actor'] != 'CustomerActor' and r['at'] > newest_cust['at'] for r in visible) if newest_cust else False
    own = (newest_cust['who'] or '').split(' ')[0] if newest_cust else ''
    body = newest_cust['text'] if newest_cust else ''
    body = re.sub(r'<@([A-Z0-9]+)>', lambda m: '@' + deskconfig.SLACK_USERS.get(m.group(1), m.group(1)), body)   # a Slack mention is the teammate's name
    body = re.split(r'\n(?:>|Von:|From:|Am .{6,80} schrieb|On .{6,80} wrote|-{3,} ?Original)', body, 1)[0]   # the quoted history is not the message
    body = re.split(r'\n\s*(?:Viele Grüße|Beste Grüße|Liebe Grüße|Mit freundlichen Grüßen|Freundliche Grüße|Schöne Grüße|Danke und Gruß|Gruß|Grüße|VG|LG|MfG|Best regards|Kind regards|Regards|Thanks|--\s*$)', body, 1)[0]   # nor the signature
    tagged = [p for p in deskconfig.TEAM if p.strip() != own and re.search(r'(?<![A-Za-z])@?' + re.escape(p.strip()) + r'(?![A-Za-z])', body)]
    if tagged:
        out.append(f"the newest customer message names {', '.join(tagged)}" + (' and we have not replied since: the answer may be theirs' if not ours_after else '; we replied after it'))
    return '## Who holds this thread\n' + '\n'.join(out) + '\n\n## Timeline\n'


def fingerprint(card):
    return hashlib.sha1(f"{card.get('status') or ''}|{card.get('act') or ''}".encode()).hexdigest()[:12]


def brief_path(cid):
    return f"{BRIEFS}/{re.sub(r'[^A-Za-z0-9._-]', '_', cid)}.json"


def fresh(card):
    try:
        b = json.load(open(brief_path(card['id'])))
    except Exception:
        return False
    return b.get('fingerprint') == fingerprint(card) and not b.get('error')


def recipe(lane, cid):
    """The lane's recipe section from CARDS.md (plus the memory and Luna sections for customer-text lanes)."""
    try:
        md = open(f'{SKILL}/CARDS.md').read()
    except Exception:
        return ''
    want = {lane}
    if cid.startswith('notion:'):
        want.add('RUN `notion:<ref>`')
    out = []
    for m in re.finditer(r'^### (.+?)$(.*?)(?=^##+ |\Z)', md, re.S | re.M):
        head = m.group(1)
        if any(head.startswith(w) for w in want) or (lane in ('REPLY', 'NOTIFY', 'PROMISE', 'FAST') and (head.startswith('Support memory') or head.startswith('Sending as Luna'))):
            out.append(f'### {head}\n{m.group(2).strip()}\n')
    return '\n'.join(out)[:9000]


def material(card):
    """Raw inputs per card, collected by scripts: the thread timeline, the memory block, the PR, the local file."""
    parts = []
    ref = card.get('ref') or ''
    thread = card.get('thread')
    if thread:
        parts.append('## Plain thread (newest 14 timeline entries, newest last)\n' + plain_timeline(thread)[:30000])
    if card.get('memory') and (card['memory'].get('tenant') or card['memory'].get('customer')):
        parts.append('## Support memory block (memory.py show)\n' + sh(['python3', f'{DESK}/memory.py', 'show', ref], 60)[:8000])
    elif thread:
        parts.append('## Support memory block\nnone: company unmapped (the checks must say so; never guess from the domain)')
    pr = card.get('pr')
    if pr:
        # repo names live in config.json (deskconfig.py), never here
        sys.path.insert(0, DESK)
        import deskconfig
        repo = deskconfig.BOT_REPO if ref.startswith('orbot#') else deskconfig.REPO
        if repo:
            parts.append(f'## PR #{pr} ({repo})\n' + sh(['gh', 'pr', 'view', str(pr), '--repo', repo, '--json',
                                                     'title,state,url,isDraft,reviewDecision,mergeable,labels,statusCheckRollup,updatedAt,headRefName,baseRefName,body'], 40)[:6000])
            parts.append('## PR: newest comments\n' + sh(['gh', 'pr', 'view', str(pr), '--repo', repo, '--json', 'comments', '--jq',
                                                        '.comments[-6:][] | "\\(.createdAt[0:16]) \\(.author.login): \\(.body[0:900])\\n"'], 40)[:7000])
    for m in re.finditer(r'(~|/Users/[^/\s]+)(/orbit-scratch/[^\s"`]+\.md)', (card.get('act') or '') + ' ' + (card.get('note') or '')):
        p = HOME + m.group(2)
        try:
            parts.append(f'## File {m.group(1)}{m.group(2)}\n' + open(p).read()[:6000])
        except Exception:
            pass
    return '\n\n'.join(parts) if parts else '(no raw material: work from the card alone)'


def prompt(card, ctx):
    opts = OPTIONS.get(card['lane'], [])
    if card['lane'] in REPLY_LANES:
        rule = (f"- The options depend on the draft. When `draft` is a customer reply the person could send as it stands, set `replyReady` true and use this set with \"Send it\" first: {json.dumps(DRAFTED, ensure_ascii=False)}. "
                f"Ready means: every claim in the draft is backed by a line in the context (`checks`), or the draft makes no claim a check could refute (a how-to taken from the material, an acknowledgement, a question back). "
                f"An unclaimed thread or an unvalidated brief does not make it unready: the send claims the thread. `openChecks` holds only claims the draft itself states; a doubt about the case that the draft does not state goes into `fact` or `then`. "
                f"Otherwise (an open check the draft depends on, or the move is no reply) set `replyReady` false and use the lane's set, "
                f"in the order you recommend (best first): {json.dumps(opts, ensure_ascii=False)}. Use the labels verbatim.\n"
                f"- Who holds the thread decides before the draft does: when the material's \"Who holds this thread\" block shows a teammate assigned, or the newest customer message names a teammate and we have not replied since, the answer is theirs. "
                f"Recommend Defer (trigger: that teammate answers) or the lane's drop option, name the holder in `why`, and leave `draft` empty unless the material shows the teammate handed it back.")
    else:
        rule = f"- The options are this lane's fixed set, in the order you recommend (best first): {json.dumps(opts, ensure_ascii=False)}. Use these labels verbatim. Set `replyReady` false."
    return f"""You prepare ONE card of a support desk for the person who runs it. You cannot use tools: everything you may rely on is in this message. Answer with the JSON the schema asks for.

The desk's rules for a card (from its skill):
- A brief, not a draft: the deciding fact on top, the consequence already drafted. The person confirms or edits, never composes.
{rule}
- Customer text: German "du" unless the customer wrote "Sie"; warm and short; sign as Luna (never with the person's name); it never quotes memory, tenant ids, sources or inferred facts. Address form, greeting and sign-off habits come from the memory block.
- Every claim in a customer text must be backed by a line in the context. Put each claim and its evidence into `checks`. A claim you need but cannot back goes into `openChecks` and NOT into the draft; the person's agent verifies it live.
- When the context shows the card is already resolved (reply already sent, PR merged, line already submitted), say so in `fact`, set `resolved` true and leave `draft` empty.
- When the card is a REVIEW card, `draft` is the recommended move and the one-line reason; when it is a RUN card, `draft` is the exact command or action.
- `draft` holds only the text that would go out (or the command, or the decision). No remarks about the draft inside it: those belong in `fact` or `openChecks`.
- Keep `fact` to one sentence. Keep `draft` under 1200 characters.
- Every brief has a fixed shape so the person sees why the card is there, what to decide and for what. `why`: one sentence, what put the card on the desk, since when, and who waits ("Dana wrote at 09:11 today and has had no answer", "this file has sat untriaged since 26 Aug, nobody waits"). `decide`: the one question in one sentence ("Send Dana this answer as Luna?"). `then`: one or two sentences, what the recommended option sets in motion and what is at stake if nothing happens ("Luna sends it now; the thread stays open until she confirms on staging"). Keep `why` and `decide` under 160 characters and `then` under 300.
- Plain words on the pane: the person reads `why`, `decide`, `then`, `fact` and `draft` without opening any file, often at the end of a long day. `fact` is one plain sentence under 140 characters: what the case is and what changed. `draft` on a batch or task card is at most three numbered lines; each names the thing in product words (what a user sees or cannot do), says whether it is still true, and ends with the verb. A reply draft is one short paragraph of customer text; it keeps the customer-text rules above and skips the three-line cap. Never put into `fact` or `draft`: file paths, function, hook or variable names, commit hashes, entity ids or UUIDs, status enum values, ticket folder slugs, backticked code. PR numbers, thread refs, ticket ids, dates and amounts may stay. `checks` and `openChecks` are for the agent and may carry identifiers; `why`, `decide`, `then`, `fact` and `draft` may not. The desk refuses a brief that breaks this.

## The card
{json.dumps({k: card.get(k) for k in ('id', 'ref', 'lane', 'impact', 'mins', 'waits', 'age', 'status', 'note', 'act', 'pr', 'url')}, ensure_ascii=False, indent=1)}

## The lane recipe (CARDS.md)
{recipe(card['lane'], card['id']) or '(no recipe section; work from the act line)'}

## Raw material
{ctx}
"""


def prepare(card, dry=False):
    cid = card['id']
    t0 = datetime.datetime.now()
    ctx = material(card)
    p = prompt(card, ctx)
    ctx_path = brief_path(cid).replace('.json', '.ctx.md')
    with open(ctx_path, 'w') as f:
        f.write(p)
    if dry:
        log(f'{cid}: dry, context {len(p)} chars at {ctx_path}')
        return
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_PLUGIN_DIRS'}
    env['DESK_PREPARE'] = '1'
    try:
        r = subprocess.run(['claude', '-p', '--model', MODEL, '--effort', 'low', '--no-session-persistence', '--tools', '',
                            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--output-format', 'json',
                            '--json-schema', json.dumps(SCHEMA), p],
                           capture_output=True, text=True, timeout=240, env=env, cwd=DESK)
        out = json.loads(r.stdout)
    except Exception as e:
        log(f'{cid}: runner failed: {e}')
        json.dump({'cardId': cid, 'fingerprint': fingerprint(card), 'error': str(e)[:300], 'preparedAt': now_iso()}, open(brief_path(cid), 'w'))
        return
    if out.get('is_error') or not out.get('structured_output'):
        log(f"{cid}: model error: {str(out.get('result'))[:200]}")
        json.dump({'cardId': cid, 'fingerprint': fingerprint(card), 'error': str(out.get('result'))[:300], 'preparedAt': now_iso()}, open(brief_path(cid), 'w'))
        return
    so = out['structured_output']
    ready = drafted(card['lane'], so)
    allowed = DRAFTED if ready else OPTIONS.get(card['lane'], [])
    options = [{'label': o['label'], 'verb': VERBS.get(o['label'], 'do'), 'why': o.get('why', '')} for o in so.get('options', []) if o.get('label') in allowed]
    for label in allowed:  # the fixed set stays complete, recommended ones first
        if all(o['label'] != label for o in options):
            options.append({'label': label, 'verb': VERBS.get(label, 'do'), 'why': ''})
    if ready:  # a checked draft always leads with the send
        options.sort(key=lambda o: o['label'] != 'Send it')
    brief = {
        'cardId': cid, 'ref': card.get('ref'), 'lane': card['lane'], 'fingerprint': fingerprint(card),
        'status': card.get('status'), 'act': card.get('act'),
        'why': so.get('why', ''), 'decide': so.get('decide', ''), 'then': so.get('then', ''),
        'fact': so.get('fact', ''), 'draft': so.get('draft', ''), 'options': options,
        'checks': so.get('checks', []), 'openChecks': so.get('openChecks', []), 'resolved': bool(so.get('resolved')),
        'replyReady': ready, 'preparedAt': now_iso(), 'model': MODEL, 'costUsd': out.get('total_cost_usd'), 'ms': out.get('duration_ms'),
        'context': ctx_path,
    }
    json.dump(brief, open(brief_path(cid), 'w'), ensure_ascii=False, indent=1)
    log(f"{cid}: prepared in {(datetime.datetime.now() - t0).seconds}s, {len(brief['openChecks'])} open checks, ${out.get('total_cost_usd', 0):.3f}")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def main():
    if os.environ.get('DESK_PREPARE'):
        return 0
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__); return 0
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    top = int(os.environ.get('DESK_PREPARE_TOP', '5'))
    if '--top' in sys.argv:
        top = int(sys.argv[sys.argv.index('--top') + 1]); args = [a for a in args if a != str(top)]
    force = '--force' in sys.argv
    dry = '--dry' in sys.argv
    os.makedirs(BRIEFS, exist_ok=True)
    lock = f'{BRIEFS}/.prepare.lock'
    try:
        if (datetime.datetime.now().timestamp() - os.stat(lock).st_mtime) < LOCK_S and not force:
            print('prepare: a pass is in flight', file=sys.stderr); return 75
    except FileNotFoundError:
        pass
    open(lock, 'w').write(now_iso())
    try:
        cache = json.load(open(f'{DESK}/cards.json'))
        cards = [c for c in cache.get('cards', []) if c.get('lane') in LANES and not SKIP_ID.match(c.get('id', ''))]
        if args:
            cards = [c for c in cards if c['id'] in args or c.get('ref') in args]
        else:
            cards = cards[:top]
        todo = [c for c in cards if force or not fresh(c)]
        # briefs of cards that left the desk go away with their context
        live = {brief_path(c['id']) for c in cache.get('cards', [])}
        for f in glob.glob(f'{BRIEFS}/*.json'):
            if f not in live:
                for g in (f, f.replace('.json', '.ctx.md')):
                    try: os.remove(g)
                    except FileNotFoundError: pass
        if not todo:
            print(f'prepare: {len(cards)} candidates, all fresh'); return 0
        log(f"pass: {', '.join(c['id'] for c in todo)}")
        with ThreadPoolExecutor(max_workers=min(5, len(todo))) as ex:
            list(ex.map(lambda c: prepare(c, dry), todo))
        print(f"prepare: {len(todo)} prepared, {len(cards) - len(todo)} fresh")
        return 0
    finally:
        try: os.remove(lock)
        except FileNotFoundError: pass


if __name__ == '__main__':
    sys.exit(main() or 0)
