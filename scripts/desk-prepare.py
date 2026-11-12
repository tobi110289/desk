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

2026-11-10, from 190 scored closes (the first option was the pick 43 % of the time):
  - The code picks the menu and the first option from facts (who holds the thread, a validation note, named examples, a
    defer that came back). The model ranks that menu and writes one question and one consequence PER option, so the
    question on the pane always belongs to the option that leads. Before this the code reordered the options after the
    model had written its question for another one.
  - The pane shows three options: the lead, the best alternative, one way out (Defer or Drop). The rest are one key away.
  - A new FAST thread with an unchecked Hermes brief is validated in the background before the person is asked
    (config.json autoValidate): "Claim + validate now" was the pick 41 of 108 times, a routine step and no decision.

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

# What a card may queue for the memory harvest. From the walk of 2026-11-11: 15 of 60 queued lines were stored.
LEARNED_RULE = "Queue a line only when it is ONE lasting fact about the tenant or a person there: who a person is (role, account, language, address form, test tenant), who owns what, how the tenant works day to day, a standing wish. One fact per line: never join it to a state, an incident or a product behaviour. Never queue: a state with a date on it (a release, fix or deploy state, a staging or production read 'as of', an app version, a thread's assignee or counter, a meeting), product behaviour or a defect that holds for every tenant, the contents of configuration (tariff rules, template wording, profile settings, filters), the mood or a dispute of one day, a fact the memory block already holds, a line whose tenant is unknown. Each line ends with its source and a confidence in parentheses. Usually empty."

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskanchor  # noqa: E402

HOME = os.path.expanduser('~')
DESK = f'{HOME}/orbit-scratch/desk'
BRIEFS = f'{DESK}/briefs'
SKILL = os.path.expanduser('~/.claude/skills/desk')
LOCK_S = 5 * 60
MODEL = os.environ.get('DESK_PREPARE_MODEL', 'sonnet')
# lanes whose recipe has a prepare step that scripts can feed; SEND/STALE/TICKET are batches, SWEEP runs and sweep tickets take the session over
LANES = {'REPLY', 'NOTIFY', 'PROMISE', 'FAST', 'REVIEW', 'RUN', 'INBOX', 'TASK', 'SWEEP', 'SEND', 'TICKET'}   # SEND, TICKET: single since 2026-11-03
SKIP_ID = re.compile(r'^(batch:|sweep:|sweep-ticket:|prh:|memory:)')
# the fixed option sets per lane (CARDS.md lanes table); the model picks the order, never the set
OPTIONS = {
    'REPLY': ['Send it', 'I sent it already', 'Edit then send', 'Defer until a trigger', 'Drop'],
    'NOTIFY': ['Send it', 'I sent it already', 'Edit then send', 'Nobody to tell (mark done)', 'Defer'],
    'PROMISE': ['Reply with a status', 'I sent it already', 'Close the loop (nothing owed)', 'Hand to a worker / ticket', 'Defer', 'Drop'],
    'FAST': ['Check the examples, then answer', 'Validate and draft (unattended)', 'Claim + validate now', 'Draft the reply', 'I sent it already', 'Hand to a worker', 'Defer', 'Drop (unpin)'],
    'REVIEW': ['Hand the rework to its agent', 'Review it now', 'Close the PR', 'Defer'],
    'RUN': ['Go (agent runs it now)', 'Show me the command first', 'Defer', 'Drop'],
    'INBOX': ['Answer now', 'Defer', 'Drop (reply as human: declined)', 'Close as read', 'Reply with a note'],
    'TASK': ['Start it now', 'Hand it to agents', 'Defer', 'Drop'],
    'SWEEP': ['Answer', 'Defer', 'Drop (accepted risk, recorded)'],
    # 2026-11-03: a worker's own line, a picker or a sign-off is its own card; the draft is the line to type
    'SEND': ['Send as staged', 'Edit then send', 'Drop the line', 'Defer'],
}
# 2026-11-10: the release-notes card (desk.py source 8) is a RUN card with its own set (CARDS.md lanes table)
RELEASE_NOTES_OPTIONS = ['Write the notes now', 'Defer until the deploy lands', 'Drop']
# TICKET cards differ by kind (desk.py source 9 / 9b); the batch held them until 2026-11-03
TICKET_OPTIONS = {
    'notion': ['Push (ask the owner / assign)', 'Hand to an agent', 'Archive', 'Defer'],
    'local-agent': ['Hand to a worker', 'Ticket it in Notion', 'Drop', 'Defer'],
    'local-call': ['Ready for an agent', 'Ready for a person', "Won't fix", 'Defer'],
    'local-human': ['Close (PR landed)', 'Do it now', 'Hand to a worker', 'Defer'],
}


MACHINE_TRIGGER = re.compile(r'^(?:trigger:\s*)?(?:\d{4}-\d{2}-\d{2}|deployed:|merged:|pr-merged:|appstore:|a2a:|deploy-after:)')


def facts_of(card, material):
    """What the material settles before any model reads it. The menu and the first option hang on these."""
    m = material or ''
    has_thread = bool(card.get('thread')) and '## Who holds this thread' in m
    return {
        'thread': has_thread,
        # a validation note written in a session or by the unattended run
        'validated': has_thread and 'local validation note: none' not in m,
        'hermes': has_thread and 'Hermes brief:' in m and 'Hermes brief: none' not in m,
        # the customer pointed at a video, a screenshot or a case and nobody has looked (2026-11-09: five drafts stopped for this)
        'examples': bool(re.search(r'examples named in the newest customer message: [1-9]\d* and nobody has replied since', m)),
        'ours_newest': 'newest visible message: ours' in m,
        'unattended_draft': 'Draft from the unattended validation' in m,
        'deferred': bool(card.get('deferred')),
        'agent': bool(card.get('terminal') or card.get('workspace')),
    }


def menu_of(card, f, ready=False, cause_open=False):
    """The options a card may show, from facts. An option that cannot be the right pick is not on the menu:
    - a checked draft (ready) puts the send set on a customer-text card;
    - a FAST card offers a validation only while no note exists, the unattended one only while it can run, and a handoff
      or a bare draft only after a validation (2026-11-09: "Draft the reply" led 11 times and was the pick once);
    - "I sent it already" records a reply of ours, so it needs one on top of the thread (it led 19 times on FAST, 0 hits);
    - "Hand the rework to its agent" needs a live terminal (10 of 25 background runs failed without one)."""
    lane = card.get('lane')
    if (card.get('id') or '').startswith('release-notes:'):
        return list(RELEASE_NOTES_OPTIONS)
    if lane == 'TICKET':
        return list(TICKET_OPTIONS.get(card.get('kind') or 'notion', TICKET_OPTIONS['notion']))
    if lane in REPLY_LANES and ready:
        return [o for o in DRAFTED if o != 'I sent it already' or f['ours_newest']]
    opts = list(OPTIONS.get(lane, []))
    drop = set()
    if lane == 'FAST' and not f['thread']:
        # a pinned ticket without a support thread: nothing to validate, the handoff and the draft stay
        drop |= {UNATTENDED, 'Claim + validate now', 'Check the examples, then answer'}
    elif lane == 'FAST':
        can_run = f['thread'] and f['hermes'] and not f['validated'] and re.match(r'^T-\d+$', card.get('ref') or '') and not unattended_failed(card)
        if f['validated']:
            drop |= {UNATTENDED, 'Claim + validate now'}
        else:
            drop |= {'Draft the reply', 'Hand to a worker', 'Claim + validate now' if can_run else UNATTENDED}
        if cause_open:
            # the check comes first whatever the note says; a bare draft would be the holding reply again
            drop -= {'Check the examples, then answer'}
            drop.add('Draft the reply')
        elif not f['examples']:
            drop.add('Check the examples, then answer')
    if lane in REPLY_LANES and not f['ours_newest']:
        drop.add('I sent it already')
    if lane == 'REVIEW' and not f['agent']:
        drop.add('Hand the rework to its agent')
    return [o for o in opts if o not in drop]


EXITS = ('Defer', 'Drop', 'Close as read', 'Archive', "Won't fix")


def is_exit(label):
    return label.startswith(EXITS)


def lead_of(card, f, so, order, ready):
    """The first option, from facts first and the model's ranking second (score ledger, 22 Oct to 10 Nov: Defer first
    missed 23 of 31; briefs with open checks hit 39 %, without 64 %; a card back from a defer was deferred again in 111
    of 321 cases; on PROMISE "Close the loop" was the pick 7 of 13 times while Defer led 9 times)."""
    lane = card.get('lane')
    validation = next((o for o in (UNATTENDED, 'Claim + validate now') if o in order), None)
    if ready and 'Send it' in order:
        return 'Send it'
    if (so.get('causeOpen') or (f['examples'] and not f['validated'])) and 'Check the examples, then answer' in order:
        return 'Check the examples, then answer'
    if lane == 'PROMISE' and so.get('resolved') and 'Close the loop (nothing owed)' in order:
        return 'Close the loop (nothing owed)'
    if validation and (so.get('openChecks') or lane == 'FAST'):
        # no note on the thread yet: the check comes before a draft, a record of a send, or a handoff
        if not (order and is_exit(order[0]) and may_lead_exit(card, f, so, order[0])):
            return validation
    for label in order:
        if is_exit(label) and not may_lead_exit(card, f, so, label):
            continue
        if label == 'Send it' and not ready and lane in ('FAST', 'PROMISE'):
            continue
        return label
    return order[0] if order else ''


def may_lead_exit(card, f, so, label):
    """A Drop may lead when the model ranks it first. A Defer leads only on a trigger a machine can check, and never on
    a card that already came back from a defer."""
    if not label.startswith('Defer'):
        return True
    return not f['deferred'] and bool(MACHINE_TRIGGER.match((so.get('deferTrigger') or '').strip()))


def pane_order(lead, order):
    """Lead, best alternative, one way out, then the rest. The pane shows the first three."""
    rest = [o for o in order if o != lead]
    alt = next((o for o in rest if not is_exit(o)), None)
    out = next((o for o in rest if is_exit(o)), None)
    if is_exit(lead):   # the lead is the way out: two real alternatives follow
        head = [lead] + [o for o in rest if not is_exit(o)][:2]
    else:
        head = [o for o in (lead, alt, out) if o]
    return head + [o for o in rest if o not in head]


SHOWN = 3
AUTO_MAX_AGE_DAYS = 2


# 2026-10-14: a customer-text card whose reply is drafted and checked puts the send on the pane, first. The pick is the
# send approval; no second question. OPTIONS above stay the set while checks are open or the thread is not yet claimed.
REPLY_LANES = {'REPLY', 'NOTIFY', 'PROMISE', 'FAST'}
UNATTENDED = 'Validate and draft (unattended)'   # deskplain.VALIDATE; desk-execute.py runs it as one background job
DRAFTED = ['Send it', 'Edit then send', 'I sent it already', 'Defer', 'Drop']


def drafted(lane, so):
    """True when the brief carries a reply ready to send: a customer-text lane, a draft, no open checks, not resolved."""
    return (lane in REPLY_LANES and bool((so.get('draft') or '').strip()) and not so.get('openChecks')
            and not so.get('resolved') and bool(so.get('replyReady')))
VERBS = {'Send it': 'delegate', 'I sent it already': 'do', 'Edit then send': 'delegate', 'Defer': 'defer', 'Defer until a trigger': 'defer',
         'Drop': 'drop', 'Drop (unpin)': 'drop', 'Nobody to tell (mark done)': 'do', 'Reply with a status': 'delegate',
         'Close the loop (nothing owed)': 'do', 'Hand to a worker / ticket': 'delegate', 'Claim + validate now': 'do', UNATTENDED: 'delegate',
         'Draft the reply': 'delegate', 'Hand to a worker': 'delegate', 'Hand the rework to its agent': 'delegate',
         'Review it now': 'do', 'Check the examples, then answer': 'do', 'Close the PR': 'drop', 'Go (agent runs it now)': 'do', 'Show me the command first': 'decide',
         'Answer now': 'decide', 'Drop (reply as human: declined)': 'drop', 'Close as read': 'do', 'Reply with a note': 'delegate',
         'Start it now': 'do', 'Hand it to agents': 'delegate', 'Answer': 'decide', 'Drop (accepted risk, recorded)': 'drop',
         'Send as staged': 'delegate', 'Drop the line': 'drop', 'Push (ask the owner / assign)': 'delegate', 'Hand to an agent': 'delegate',
         'Archive': 'drop', 'Ticket it in Notion': 'delegate', 'Ready for an agent': 'decide', 'Ready for a person': 'decide',
         "Won't fix": 'drop', 'Close (PR landed)': 'do', 'Do it now': 'do',
         'Write the notes now': 'do', 'Defer until the deploy lands': 'defer'}
SCHEMA = {
    'type': 'object',
    'properties': {
        'why': {'type': 'string', 'description': 'one plain sentence: what put this card on the desk, since when, and who waits'},
        'fact': {'type': 'string', 'description': 'the single fact that decides this card, one plain sentence under 140 characters, no code words or ids'},
        'draft': {'type': 'string', 'description': 'the drafted consequence: the customer text, the reply, the command, or the decision; empty when nothing is drafted'},
        'options': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'label': {'type': 'string'},
            'decide': {'type': 'string', 'description': 'the one yes/no question this option answers, one plain sentence under 160 characters ("Send Dana this answer as Luna?")'},
            'then': {'type': 'string', 'description': 'one or two plain sentences under 300 characters: what this option sets in motion, and what is at stake if nothing happens'}},
            'required': ['label', 'decide', 'then']},
                    'description': 'every option of the menu, best first, each with its own question and consequence'},
        'checks': {'type': 'array', 'items': {'type': 'string'}, 'description': 'each claim in the draft with the evidence line in the context that backs it'},
        'openChecks': {'type': 'array', 'items': {'type': 'string'}, 'description': 'claims the draft needs that the context does not back; Claude verifies these live before desk_card'},
        'resolved': {'type': 'boolean', 'description': 'true when the context shows the card is already resolved (reply sent, PR merged, line submitted)'},
        'causeOpen': {'type': 'boolean', 'description': 'true when the customer reports a fault or asks why something happened and the material does not establish the cause; then `draft` stays empty'},
        'replyReady': {'type': 'boolean', 'description': 'true when `draft` is customer text the person could send as it stands: every claim in checks or no refutable claim at all, openChecks empty, a reply is the move, no teammate holds the thread'},
        'deferTrigger': {'type': 'string', 'description': "when a Defer is among the sensible options: the one machine-checkable trigger it would use (a date YYYY-MM-DD, 'customer replies', 'manual:<person> answers …', 'deployed:<env>:<pr>', 'merged:<pr>'); empty when no defer makes sense. Never 'later'."},
        'deferAct': {'type': 'string', 'description': 'what to do when that trigger fires, one imperative sentence; empty when deferTrigger is empty'},
        'threadAfter': {'type': 'string', 'enum': ['done', 'waiting', 'leave'], 'description': "the Plain thread's status once the recommended reply or close has happened: done (nothing owed, or only the customer's feedback awaited), waiting (the customer's answer feeds a change we still owe), leave (we still owe work the reply only reports on, or a teammate holds the thread)"},
        'learned': {'type': 'array', 'items': {'type': 'string'}, 'description': LEARNED_RULE},
    },
    'required': ['why', 'fact', 'draft', 'options', 'checks', 'openChecks', 'resolved', 'causeOpen', 'replyReady', 'threadAfter'],
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
    # 2026-11-09, from the score ledger: "I sent it already" was recommended 19 times and right 3 times; in 10 of the
    # misses the person picked "Claim + validate now". The brief saw our reply and called the card done, while the
    # thread still had no validation note. So the material states it outright.
    validated = next((r for r in rows if r['kind'] == 'Note' and 'triage validation' in r['text'].lower()), None)
    out.append(f"local validation note: {validated['who']} at {validated['at']}" if validated else 'local validation note: none')
    # 2026-11-09: the unattended validation needs a Hermes brief to check; the material says whether one is in view
    hermes = next((r for r in rows if r['kind'] == 'Note' and any(m in r['text'] for m in HERMES_MARKERS)), None)
    out.append(f"Hermes brief: {hermes['who']} at {hermes['at']}" if hermes else 'Hermes brief: none in the entries read')
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
    # 2026-11-09, from 30 days of corrections: five drafts were stopped with "check the examples first". The person
    # had named a video, a screenshot or a case, and the draft answered before anyone had looked. A memory said so twice;
    # the brief still led with the answer. So the material counts the examples, and prepare() refuses a ready draft
    # while they are unchecked.
    out.append('newest visible message: ' + ('ours, after the customer' if ours_after else 'the customer' if newest_cust else 'ours'))
    n = examples_named(body)
    out.append(f"examples named in the newest customer message: {n}" + ('' if ours_after or not n else ' and nobody has replied since'))
    return '## Who holds this thread\n' + '\n'.join(out) + '\n\n## Timeline\n'


HERMES_MARKERS = ('<!-- ORBOT_CANONICAL_TRIAGE_BRIEF', '<!-- ORBOT_TRIAGE_DELTA', 'TRIAGE_CLAIMS_JSON', '## Triage note part', '## Agent Brief')   # jev/plain.py


def unattended_failed(card):
    """True when the unattended validation already failed for this card's current wait: the session takes it then."""
    try:
        rec = json.load(open(f"{DESK}/decisions/{re.sub(r'[^A-Za-z0-9._-]', '_', card['id'])}.json"))
    except Exception:
        return False
    return (rec.get('option') == UNATTENDED and rec.get('state') in ('failed', 'bounced')
            and rec.get('key') == card.get('key') and rec.get('since') == card.get('since'))


def unattended_draft(ref):
    """The reply the unattended validation drafted (drafts/<ref>.unattended.json), or None."""
    try:
        return json.load(open(f"{DESK}/drafts/{re.sub(r'[^A-Za-z0-9._-]', '_', ref)}.unattended.json"))
    except Exception:
        return None


EXAMPLE_WORDS = re.compile(r'https?://\S+|\b(?:screenshot|screenshots|video|videos|anhang|anbei|angehängt|attached|attachment|beispiel|beispiele|example|examples|z\.\s?b\.|zum beispiel|siehe|see below|bild|bilder|aufnahme|recording|replay)\b', re.I)


def examples_named(body):
    """How many examples the newest customer message points at: links, attachments, screenshots, named cases."""
    return len(EXAMPLE_WORDS.findall(body or ''))


def fingerprint(card):
    """The wait the brief is written for (2026-10-22: key + since, so an age tick or a reworded act does not rebuild a
    brief; a card without a key, from an older gather, binds to its caption as before)."""
    if card.get('key'):
        return hashlib.sha1(f"{card['key']}|{card.get('since') or ''}|{card.get('lane') or ''}".encode()).hexdigest()[:12]
    return hashlib.sha1(f"{card.get('status') or ''}|{card.get('act') or ''}".encode()).hexdigest()[:12]


def errors_before(cid):
    try:
        return int(json.load(open(brief_path(cid))).get('errors') or 0)
    except Exception:
        return 0


def brief_path(cid):
    return f"{BRIEFS}/{re.sub(r'[^A-Za-z0-9._-]', '_', cid)}.json"


def fresh(card):
    try:
        b = json.load(open(brief_path(card['id'])))
    except Exception:
        return False
    if b.get('fingerprint') != fingerprint(card):
        return False
    if b.get('auto'):
        # the "being checked" brief lives while its background run does, 20 minutes at most
        try:
            state = json.load(open(decision_path(card['id']))).get('state')
        except Exception:
            state = None
        age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.strptime(b['preparedAt'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)).total_seconds()
        return state in ('queued', 'running') and age < 20 * 60
    if not b.get('error'):
        return True
    # 2026-11-09: the same cards failed 23 passes in a row on one API outage (93 errors). After an error the card waits
    # 15, 30, then 60 minutes before the next try.
    try:
        waited = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.strptime(b['preparedAt'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)).total_seconds() / 60
    except Exception:
        return False
    return waited < min(60, 15 * (2 ** (int(b.get('errors', 1)) - 1)))


def recipe(lane, cid):
    """The lane's recipe section from CARDS.md (plus the memory and Luna sections for customer-text lanes)."""
    try:
        md = open(f'{SKILL}/CARDS.md').read()
    except Exception:
        return ''
    want = {lane}
    if cid.startswith('notion:'):
        want.add('RUN `notion:<ref>`')
    if cid.startswith('release-notes:'):
        want = {'RUN `release-notes:<date>`'}
    out = []
    for m in re.finditer(r'^### (.+?)$(.*?)(?=^##+ |\Z)', md, re.S | re.M):
        head = m.group(1)
        if any(head.startswith(w) for w in want) or (lane in ('REPLY', 'NOTIFY', 'PROMISE', 'FAST') and (head.startswith('Support memory') or head.startswith('Sending as Luna'))):
            out.append(f'### {head}\n{m.group(2).strip()}\n')
    return '\n'.join(out)[:14000]   # 2026-11-09: 9000 cut the Luna sending rules off the FAST recipe


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
    u = unattended_draft(ref) if ref else None
    if u and (u.get('draft') or '').strip():
        parts.append(f"## Draft from the unattended validation ({u.get('at')}, verdict {u.get('verdict') or '?'})\n"
                     f"Written by the desk's background run right after its validation note; checked against that note.\n"
                     f"checks: {json.dumps(u.get('checks') or [], ensure_ascii=False)}\nopen checks: {json.dumps(u.get('openChecks') or [], ensure_ascii=False)}\n\n{u['draft'][:2500]}")
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


def prompt(card, ctx, f):
    lane = card['lane']
    reply = lane in REPLY_LANES
    menu = menu_of(card, f)
    if reply and 'Send it' not in menu:
        menu = ['Send it'] + menu
    lane_rules = ''
    if reply:
        lane_rules = """
- Customer text: German "du" unless the customer wrote "Sie"; warm and short; signed as Luna, never with the person's name. It never quotes memory, tenant ids, sources or inferred facts. Address form and greeting habits come from the memory block.
- Every claim in the draft is backed by a line in the material: put each claim with its evidence into `checks`. A claim you need and cannot back goes into `openChecks` and stays out of the draft. `openChecks` holds only claims the draft itself would state.
- Never draft a holding reply. When the customer reports a fault or asks why something happened and the material does not establish the cause, set `causeOpen` true, leave `draft` empty, set `replyReady` false, say in `fact` what is known and what is not, and name in `openChecks` the live reads that would find the cause. A draft never says that we do not know yet, that we are still checking, or that we will write once we know more. A validation note that could not reproduce the fault leaves the cause open.
- `replyReady` is true when the person could send `draft` as it stands: every claim backed, or no claim a check could refute (a how-to from the material, a thanks, a question back). An unclaimed thread does not make it unready.
- "Send it" is on the menu so that it has its question. The desk shows it only when `replyReady` is true; rank it where it belongs.
- When "Who holds this thread" shows a teammate assigned, or the newest customer message names a teammate and we have not replied since, the answer is theirs: rank Defer (trigger: that teammate answers) or the drop option first, name the holder in `why`, leave `draft` empty.
- When the material carries a draft from the unattended validation and no customer message came after it, start from that draft: keep it unless a line refutes it, and carry its checks over.
- `threadAfter`: the thread's status once the reply is sent or the card closed. `done`: nothing owed, or only the customer's feedback awaited. `waiting`: their answer feeds a change we still owe (also when in doubt between the two). `leave`: we still owe work, or a teammate holds the thread."""
    elif lane == 'REVIEW':
        lane_rules = '\n- `draft` is the recommended move and its one-line reason. Set `replyReady` false and `threadAfter` "leave".'
    elif lane == 'RUN':
        lane_rules = '\n- `draft` is the exact command or action. Set `replyReady` false and `threadAfter` "leave".'
    else:
        lane_rules = '\n- `draft` on a task or batch card is at most three numbered lines: each names the thing in product words, says whether it is still true, and ends with the verb. Set `replyReady` false and `threadAfter` "leave".'
    return f"""You prepare ONE card of a support desk for the person who runs it. You cannot use tools: everything you may rely on is in this message. Answer with the JSON the schema asks for.

## What you write
Write every field in English and speak to the person as "you". Only customer text inside `draft` is in the customer's language.
- `why`: one sentence under 160 characters. What put the card on the desk, since when, and who waits ("Dana wrote at 09:11 today and has had no answer"). From `seen` 7 on, say how long ("on the desk for 12 days").
- `fact`: the single fact that decides the card, one sentence under 140 characters.
- `draft`: only the text that would go out (or the command, or the move), under 1200 characters. No remarks about the draft inside it.
- `options`: EVERY label of the menu below, verbatim, best first. Each carries its own `decide` (one yes/no question for exactly that option, never "A or B?") and `then` (what that option sets in motion, and what is at stake if nothing happens). The desk picks which option leads and shows that option's question, so each pair must stand alone.
- `resolved`: true when the material shows the card is already settled (reply sent and nothing else owed, PR merged, promise kept). Then say so in `fact` and leave `draft` empty.
- `deferTrigger` and `deferAct`: the one trigger a Defer would wait on (a date YYYY-MM-DD, "customer replies", "manual:<person> answers …", "deployed:<env>:<pr>", "merged:<pr>") and the act when it fires. Empty when no defer makes sense. Never "later".
- `learned`: {LEARNED_RULE}{lane_rules}

## Plain words
The person reads `why`, `fact`, `draft`, `decide` and `then` on a small pane without opening any file, often at the end of a long day. Never in those fields: the card's id or a task slug, the `seen` number as a number of views, file paths, function or variable names, commit hashes, entity ids, status enum values, ticket folder slugs, backticked code. PR numbers, thread refs, ticket ids, dates and amounts may stay. `checks` and `openChecks` are for the agent and may carry identifiers.

## The menu
{json.dumps(menu, ensure_ascii=False)}

## The card
{json.dumps({k: card.get(k) for k in ('id', 'ref', 'lane', 'impact', 'mins', 'waits', 'age', 'seen', 'deferred', 'status', 'note', 'act', 'pr', 'url')}, ensure_ascii=False, indent=1)}

## The lane recipe (CARDS.md)
{recipe(card['lane'], card['id']) or '(no recipe section; work from the act line)'}

## Raw material
{ctx}
"""


def write_error(card, message):
    cid = card['id']
    json.dump({'cardId': cid, 'fingerprint': fingerprint(card), 'error': str(message)[:300], 'errors': errors_before(cid) + 1, 'preparedAt': now_iso()},
              open(brief_path(cid), 'w'))


# ---- 2026-11-10: the validation runs before the person is asked ------------------------------------------------------
def decision_path(cid):
    return f"{DESK}/decisions/{re.sub(r'[^A-Za-z0-9._-]', '_', cid)}.json"


def auto_runs_today():
    today = now_iso()[:10]
    n = 0
    for path in glob.glob(f'{DESK}/decisions/*.json'):
        try:
            rec = json.load(open(path))
        except Exception:
            continue
        n += rec.get('via') == 'auto' and (rec.get('pickedAt') or '')[:10] == today
    return n


def auto_validate_due(card, f):
    """A FAST support thread of the last two days with a Hermes brief nobody checked, no examples to look at first, no
    run that already failed on this wait, and room under the day's cap."""
    sys.path.insert(0, DESK)
    import deskconfig
    if not deskconfig.AUTO_VALIDATE or card.get('lane') != 'FAST' or card.get('verb'):
        return False
    if UNATTENDED not in menu_of(card, f) or f['examples'] or f['unattended_draft']:
        return False
    # a thread of the last days only: on an older one the person knows more than the thread says (a call, a chat),
    # and the unattended check stays one press away as the first option
    if int(card.get('age') or 0) > AUTO_MAX_AGE_DAYS:
        return False
    try:
        if json.load(open(decision_path(card['id']))).get('state') in ('queued', 'running'):
            return False
    except Exception:
        pass
    return auto_runs_today() < deskconfig.AUTO_VALIDATE_MAX


def start_validation(card, ctx_path, anc):
    """Write the brief that says "being checked", record the pick as the desk's own (via auto), start the executor.
    The executor's run_unattended claims the thread, validates, posts its marked note, drafts, and asks for a new brief."""
    cid = card['id']
    brief = {
        'cardId': cid, 'ref': card.get('ref'), 'lane': card['lane'], 'fingerprint': fingerprint(card),
        'status': card.get('status'), 'act': card.get('act'), 'key': card.get('key'), 'since': card.get('since'),
        'why': 'A support thread whose triage brief nobody has checked yet.',
        'decide': 'Nothing to decide yet: the desk checks the brief first.',
        'then': 'The check runs in the background for about eight minutes. The card comes back with a checked draft, or says why the check stopped.',
        'fact': 'The desk is checking this thread before it asks you.',
        'draft': '', 'options': [{'label': UNATTENDED, 'verb': VERBS[UNATTENDED], 'decide': '', 'then': ''}], 'shown': 1,
        'checks': [], 'openChecks': [], 'resolved': False, 'replyReady': False, 'preparedAt': now_iso(), 'model': '',
        'deferTrigger': '', 'deferAct': '', 'threadAfter': '', 'learned': [], 'anchor': anc, 'context': ctx_path, 'auto': True,
    }
    json.dump(brief, open(brief_path(cid), 'w'), ensure_ascii=False, indent=1)
    rec = {
        'cardId': cid, 'ref': card.get('ref'), 'lane': card['lane'], 'option': UNATTENDED, 'verb': VERBS[UNATTENDED], 'class': 'model',
        'via': 'auto', 'pickedAt': now_iso(), 'briefFile': brief_path(cid), 'fingerprint': brief['fingerprint'],
        'key': card.get('key'), 'since': card.get('since'), 'anchor': anc, 'thread': card.get('thread'), 'pr': card.get('pr'),
        'state': 'queued', 'startedAt': None, 'finishedAt': None, 'result': '', 'learned': [],
    }
    os.makedirs(f'{DESK}/decisions', exist_ok=True)
    tmp = decision_path(cid) + '.tmp'
    json.dump(rec, open(tmp, 'w'), ensure_ascii=False, indent=1)
    os.replace(tmp, decision_path(cid))
    env = {k: v for k, v in os.environ.items() if k not in ('DESK_PREPARE', 'CLAUDE_CODE_PLUGIN_DIRS')}
    subprocess.Popen(['python3', f'{os.path.dirname(os.path.realpath(__file__))}/desk-execute.py'], env=env, cwd=DESK,
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f'{cid}: validation started in the background (auto)')


def gone_reason(card, anc):
    """Why a card has nothing left to decide, or ''. A REVIEW card asks what happens with an open PR; once GitHub says
    the PR is merged or closed there is no question. 2026-11-12: a PR merged 95 seconds before a gather that still read
    its cached PR list; the brief then asked "review it even though it is merged?" with the lane's three options."""
    state = ((anc or {}).get('pr') or {}).get('state')
    if card.get('lane') == 'REVIEW' and state in ('MERGED', 'CLOSED'):
        return f"the pull request is {state.lower()}"
    return ''


def prepare(card, dry=False):
    cid = card['id']
    t0 = datetime.datetime.now()
    anc = deskanchor.anchor(card)   # read before the model runs: a message that lands during the run is "after the brief"
    gone = gone_reason(card, anc)
    if gone:
        # no model run and no brief: the pane drops the card on this marker, and the next gather drops it for good
        log(f"{cid}: gone, {gone}")
        if not dry:
            json.dump({'cardId': cid, 'fingerprint': fingerprint(card), 'key': card.get('key'), 'since': card.get('since'),
                       'status': card.get('status'), 'act': card.get('act'), 'gone': gone, 'preparedAt': now_iso(), 'anchor': anc},
                      open(brief_path(cid), 'w'), ensure_ascii=False, indent=1)
        return
    ctx = material(card)
    f = facts_of(card, ctx)
    p = prompt(card, ctx, f)
    ctx_path = brief_path(cid).replace('.json', '.ctx.md')
    with open(ctx_path, 'w') as fh:
        fh.write(p)
    if dry:
        log(f"{cid}: dry, context {len(p)} chars at {ctx_path}; menu {menu_of(card, f)}; auto validation {'due' if auto_validate_due(card, f) else 'not due'}")
        return
    if auto_validate_due(card, f):
        start_validation(card, ctx_path, anc)
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
        write_error(card, e)
        return
    if out.get('is_error') or not out.get('structured_output'):
        log(f"{cid}: model error: {str(out.get('result'))[:200]}")
        write_error(card, out.get('result'))
        return
    brief = brief_of(card, out['structured_output'], f)
    brief.update({'preparedAt': now_iso(), 'model': MODEL, 'costUsd': out.get('total_cost_usd'), 'ms': out.get('duration_ms'),
                  # 2026-10-23: the facts the brief stands on; desk-execute.py reads them again before it acts on a pane pick
                  'anchor': anc, 'context': ctx_path})
    json.dump(brief, open(brief_path(cid), 'w'), ensure_ascii=False, indent=1)
    log(f"{cid}: prepared in {(datetime.datetime.now() - t0).seconds}s, {len(brief['openChecks'])} open checks, first {brief['options'][0]['label'] if brief['options'] else '-'}, ${out.get('total_cost_usd', 0):.3f}")


# 2026-11-12: a brief drafted "Warum die Warnung bei dir erschien, wissen wir noch nicht sicher. Wir prüfen das
# weiter und melden uns" on a thread nobody was checking any more. A reply that says the cause is unknown, or that we
# are still looking, is a holding reply: the check comes first, the draft after it.
HOLDING = re.compile(
    r"wissen wir (?:noch |bisher |aktuell |leider )*nicht|k[öo]nnen wir (?:noch |bisher |aktuell |leider )*nicht (?:sagen|nachvollziehen|erkl[äa]ren)"
    r"|(?:ursache|grund)\b[^.!?\n]{0,80}\b(?:unklar|unbekannt|offen|noch nicht)|noch (?:nicht|un)gekl[äa]rt"
    r"|wir (?:pr[üu]fen|untersuchen|schauen uns|analysieren|kl[äa]ren)\b[^.!?\n]{0,40}\b(?:weiter|noch|gerade|aktuell|derzeit|intern)"
    r"|sobald wir (?:die ursache|den grund|mehr|n[äa]heres|genaueres)"
    r"|we (?:do not|don't|cannot|can't) (?:yet )?(?:know|say|tell|explain)|(?:still|currently|keep) (?:investigating|looking into|checking)"
    r"|cause (?:is|remains) (?:still )?(?:unknown|unclear|open)|once we (?:know|have found|find) (?:the cause|more|why)", re.I)
CAUSE_CHECK = 'Find the cause before any reply: read the named cases live (data, logs, replay, code). The draft was a holding reply and is dropped.'
CAUSE_DECIDE = 'Find the cause first, then draft the reply?'
CAUSE_THEN = 'Claude reads the named cases live in this session and writes the finding into the draft. No reply goes out while the cause is open.'


def cause_open(lane, so):
    """True when a customer-text card has no cause to tell yet: the model says so, or its draft is a holding reply."""
    if lane not in REPLY_LANES or so.get('resolved'):
        return False
    return bool(so.get('causeOpen')) or bool(HOLDING.search(so.get('draft') or ''))


def brief_of(card, so, f):
    """The model's answer becomes the brief: the menu and the first option come from facts, and the question on the
    pane is the one the model wrote for exactly that option."""
    lane = card['lane']
    if cause_open(lane, so):
        # no draft and no send while the cause is open: the check leads, and the pane says why
        so = {**so, 'causeOpen': True, 'draft': '', 'replyReady': False,
              'openChecks': [CAUSE_CHECK] + [c for c in so.get('openChecks') or [] if c != CAUSE_CHECK]}
    else:
        so = {**so, 'causeOpen': False}
    ready = drafted(lane, so) and not (f['examples'] and not f['validated'])   # the examples come before the answer
    menu = menu_of(card, f, ready, so['causeOpen'])
    wrote = {o.get('label'): o for o in so.get('options') or [] if o.get('label')}
    if so['causeOpen']:
        wrote['Check the examples, then answer'] = {'label': 'Check the examples, then answer', 'decide': CAUSE_DECIDE, 'then': CAUSE_THEN}
    order = [label for label in wrote if label in menu] + [label for label in menu if label not in wrote]
    lead = lead_of(card, f, so, order, ready)
    order = pane_order(lead, order)
    options = [{'label': label, 'verb': VERBS.get(label, 'do'),
                'decide': (wrote.get(label) or {}).get('decide', '').strip(), 'then': (wrote.get(label) or {}).get('then', '').strip()} for label in order]
    first = options[0] if options else {'label': '', 'decide': '', 'then': ''}
    return {
        'cardId': card['id'], 'ref': card.get('ref'), 'lane': lane, 'fingerprint': fingerprint(card),
        'status': card.get('status'), 'act': card.get('act'), 'key': card.get('key'), 'since': card.get('since'),
        'why': so.get('why', ''), 'decide': first['decide'] or f"{first['label']}?", 'then': first['then'],
        'fact': so.get('fact', ''), 'draft': so.get('draft', ''), 'options': options, 'shown': SHOWN,
        'checks': so.get('checks', []), 'openChecks': so.get('openChecks', []), 'resolved': bool(so.get('resolved')),
        'causeOpen': so['causeOpen'], 'replyReady': ready,
        'deferTrigger': (so.get('deferTrigger') or '').strip(), 'deferAct': (so.get('deferAct') or '').strip(),
        # 2026-11-10: the thread's status after the pick (desk-execute.py thread_after applies it)
        'threadAfter': so.get('threadAfter') if so.get('threadAfter') in ('done', 'waiting', 'leave') else '',
        'learned': [str(x).strip() for x in (so.get('learned') or []) if str(x).strip()][:6],
    }


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
