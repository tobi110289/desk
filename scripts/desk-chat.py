#!/usr/bin/env python3
"""desk-chat.py "<message>" [--card <card id>]  — one message from the person to Claude on the desk host, and the answer.

The phone desk has a text field. A message typed there (on the list, or on a card) comes here. The script writes the
message to chat.jsonl, runs one headless Claude run in the desk folder, and writes the answer to the same file. The page
shows the file. Messages of one sitting share a Claude session (resumed by id), so "and the second one?" works; after
SITTING_H hours without a message the next one starts a new session.

One run at a time: a second message waits for the first (a file lock), so the answers come in the order of the questions.

The run has the tools of a background pick (Bash, Read, Glob, Grep) and the rules in RULES below: nothing goes out to a
customer or a teammate, no card is closed and nothing is merged unless the message itself says so.

Files in the desk folder: chat.jsonl (one JSON object per line: at, from "you" | "claude" | "desk", text, card, no, id /
re), chat-session.json (the session id of the sitting).

Exit 0: answered. 1: the run failed; the reason is in chat.jsonl as a line from "desk".
"""
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402
deskconfig.forward(__file__)

DESK = deskconfig.DESK
CHAT = f'{DESK}/chat.jsonl'
SESSION = f'{DESK}/chat-session.json'
MODEL = os.environ.get('DESK_CHAT_MODEL') or deskconfig.CFG.get('chatModel') or 'opus'
SITTING_H = 6
TIMEOUT_S = 9 * 60   # the phone server cuts the run at 10 min
MAX_TEXT = 4000

RULES = f"""You are Claude at the person's support desk, on the desk host. The person writes to you from the desk page on their phone, between other things. This is a conversation: answer the message.

How to answer
- Plain text for a phone: short sentences, no tables, no headings, no code blocks unless the person asks for a command. Lead with the answer. At most 1200 characters unless the person asks for more.
- Say what you did and what you did not check. When you do not know, say so and say what would settle it.
- Name a card by its desk number (D-12) and its reference.

Where things are
- The working folder is the desk folder: cards.json (the cards of the last gather; "no" is the desk number), briefs/ (one prepared brief per card), decisions/ (the picks and their results), desk.json (holds, closes, numbers), chat.jsonl (this conversation).
- The desk scripts are the *.py files in this folder; each one explains itself with --help or in its first lines. desk-id.py resolves a desk number to a card. The lane rules are in {deskconfig.SKILLS}/desk/CARDS.md.
- A message sent from a card names that card. Read its brief before you answer.

What you may do
- Read everything: the desk, Plain through the desk scripts, GitHub with gh, production and staging data read-only.
- Change the desk only when this message says so: close, defer or drop a card with desk-close.py, write a status, ask for a new brief (desk-prepare.py <card id> --force), start a gather (desk.py).
- Never send anything to a customer or a teammate (no reply as Luna, no Plain reply or note, no Slack message, no mail) unless this message tells you to send that exact text.
- Never merge or close a pull request, never push to a deploy branch, never write to production.
- Work that needs the laptop (a code change, a review with the person, a test in a browser): say that it waits for a laptop session and say in one line what to open there.
"""


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def append(row):
    with open(CHAT, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def card_of(cid):
    try:
        cache = json.load(open(f'{DESK}/cards.json'))
    except Exception:
        return None
    return next((c for c in (cache.get('cards') or []) + (cache.get('parked') or []) if c.get('id') == cid), None)


def sitting():
    """The session id to resume, or None when the last message of the sitting is too old."""
    try:
        s = json.load(open(SESSION))
        last = datetime.datetime.strptime(s['lastAt'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
        if (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() < SITTING_H * 3600:
            return s.get('id') or None
    except Exception:
        pass
    return None


def safe(cid):
    return re.sub(r'[^A-Za-z0-9._-]', '_', cid)


def message_for(text, card):
    if not card:
        return text
    keep = {k: card.get(k) for k in ('id', 'no', 'ref', 'lane', 'impact', 'status', 'act', 'note', 'pr', 'thread', 'waits', 'age')}
    return (f"{text}\n\n(Sent from the card D-{card.get('no') or '?'} on the phone desk. The card:\n"
            f"{json.dumps(keep, ensure_ascii=False, indent=1)}\n"
            f"Its brief, when one exists: briefs/{safe(card['id'])}.json)")


def ask(text, card, session_id):
    argv = ['claude', '-p', '--model', MODEL, '--tools', 'Bash,Read,Glob,Grep', '--permission-mode', 'bypassPermissions',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--output-format', 'json',
            '--append-system-prompt', RULES]
    if session_id:
        argv += ['--resume', session_id]
    argv += [message_for(text, card)]
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_PLUGIN_DIRS'}
    env['DESK_CHAT'] = '1'
    r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, env=env, cwd=DESK)
    try:
        out = json.loads(r.stdout)
    except Exception:
        raise RuntimeError(f'no answer (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-200:]}')
    if out.get('is_error'):
        raise RuntimeError(str(out.get('result'))[:300])
    return (out.get('result') or '').strip(), out.get('session_id'), out.get('total_cost_usd')


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__); return 2
    text = argv[0].strip()[:MAX_TEXT]
    cid = argv[argv.index('--card') + 1] if '--card' in argv[1:] and len(argv) > argv.index('--card') + 1 else None
    if not text:
        print('empty message', file=sys.stderr); return 2
    card = card_of(cid) if cid else None
    mid = uuid.uuid4().hex[:10]
    row = {'at': now_iso(), 'from': 'you', 'text': text, 'id': mid}
    if card:
        row.update({'card': card['id'], 'no': card.get('no') or 0, 'ref': card.get('ref') or ''})
    append(row)
    tag = {k: row[k] for k in ('card', 'no', 'ref') if k in row}
    with open(f'{DESK}/.chat.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        session_id = sitting()
        try:
            try:
                answer, session_id, cost = ask(text, card, session_id)
            except RuntimeError:
                if not session_id:
                    raise
                answer, session_id, cost = ask(text, card, None)   # the session is gone: start a new sitting
        except subprocess.TimeoutExpired:
            append({'at': now_iso(), 'from': 'desk', 're': mid, 'text': f'Claude did not finish in {TIMEOUT_S // 60} minutes. Ask again, or ask for less.', **tag}); return 1
        except Exception as e:
            append({'at': now_iso(), 'from': 'desk', 're': mid, 'text': f'Claude did not answer: {str(e)[:300]}', **tag}); return 1
        append({'at': now_iso(), 'from': 'claude', 're': mid, 'text': answer or '(no text)', 'costUsd': cost, **tag})
        if session_id:
            tmp = f'{SESSION}.tmp'
            json.dump({'id': session_id, 'lastAt': now_iso()}, open(tmp, 'w'))
            os.replace(tmp, SESSION)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
