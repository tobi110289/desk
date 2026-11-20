"""deskthread.py — the Plain thread status after a pick that settles a thread: done, waiting or leave.

2026-11-10: one press closes the card and moves the Plain thread (asked 11 times, 09-09 to 11-06; until then three
memories held the rule and the session set the status by hand). The rule, once:
  done     nothing is owed, or we only wait for the customer's feedback (Plain reopens the thread on their next message)
  waiting  the customer's answer feeds a change we still owe: snoozed as "Waiting for customer", so it stays open work
  leave    a teammate wrote the newest visible message after the brief was prepared, a person already set the status
           with his own reply, or we still owe work the reply only reports on: the status stays as it is

2026-11-20: the rule lived in desk-execute.py, so only picks the executor runs (class script or model) got it. A pick of
class session (the session sends, desk-close.py records the verb) closed the card and left the thread at TODO: four
NOTIFY "Send it" picks in W47 sat open for days until closed by hand. This module is
the one owner now; desk-execute.py and desk-close.py both call it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskanchor  # noqa: E402
import deskclass  # noqa: E402
import deskplain  # noqa: E402

THREAD_AFTER = ('done', 'waiting', 'leave')
NAMES_DONE = {'Nobody to tell (mark done)', 'Close the loop (nothing owed)'}   # the option itself names the status
SETTLES = deskclass.SEND | NAMES_DONE | {'I sent it already'}


def thread_after(label, brief, live):
    """(action, reason) for the thread after the pick. `live`: the thread anchor read right before the write
    (deskanchor.thread_anchor), or {} when it could not be read."""
    live = live or {}
    prepared = (brief.get('preparedAt') or '')[:19]
    if live.get('newestActor') == 'UserActor' and prepared and (live.get('newestAt') or '') > prepared:
        return 'leave', f"{live.get('newestWho') or 'a teammate'} wrote the newest message after the brief; their status stands"
    if label in NAMES_DONE:
        return 'done', 'the option names it'
    if not live.get('status'):
        return 'leave', 'the thread could not be read, so a teammate reply cannot be ruled out'
    if label == 'I sent it already' and live.get('status') in ('SNOOZED', 'DONE'):
        return 'leave', f"the reply went out by hand and the thread is already {live['status'].lower()}"
    said = (brief.get('threadAfter') or '').strip().lower()
    if said in THREAD_AFTER:
        return said, 'the brief says so'
    # briefs from before the field: a status reply on a promise reports work we still owe; an awaited answer counts
    # as one that feeds a change (the memory: when in doubt, waiting), since a done thread hides what we owe
    if brief.get('lane') == 'PROMISE' and label != 'I sent it already':
        return 'leave', 'a status reply on a promise; the work is still owed'
    if (brief.get('deferTrigger') or '').strip().lower().startswith('customer replies'):
        return 'waiting', 'an answer from the customer is awaited'
    return 'done', 'nothing is awaited'


def apply_thread_after(thread, action, live, dry=False):
    """Write the status `thread_after` chose; return one plain line for the result."""
    if not thread or action == 'leave':
        return 'thread status left as it is'
    tag = ' (dry)' if dry or deskplain.dry_env() else ''
    if action == 'done':
        if (live or {}).get('status') == 'DONE':
            return 'thread already done'
        deskplain.mark_done(thread, dry)
        return 'thread set to done' + tag
    deskplain.snooze_waiting(thread, dry)
    return 'thread snoozed as waiting for the customer' + tag


def live_thread(thread):
    if not thread:
        return {}
    try:
        return deskanchor.thread_anchor(thread)
    except Exception as e:
        return {'error': str(e)[:200]}


def settle_thread(rec, brief, thread, live=None, dry=False, log=print):
    """The status rule for one pick, applied; returns the line for the result. A failed write is logged, never fatal:
    the customer has the answer and the card is closed either way."""
    if not thread:
        return ''
    live = live if live is not None else live_thread(thread)
    if live.get('error'):
        live = {}
    action, why = thread_after(rec['option'], brief, live)
    rec['threadAfter'] = {'action': action, 'reason': why}
    try:
        line = apply_thread_after(thread, action, live, dry)
    except Exception as e:
        log(f"{rec['cardId']}: thread status {action} failed: {e}")
        return f'thread status {action} failed'
    log(f"{rec['cardId']}: {line} ({why})")
    return f'{line} ({why})'


def settles_in_session(rec, verb):
    """True when desk-close.py must apply the rule for this close: a session pick whose option settles a thread, closed
    with a verb that carries the pick out. A defer or a drop means the session did not do what the pick named."""
    return (rec.get('class') == 'session' and rec.get('option') in SETTLES and bool(rec.get('thread'))
            and verb not in ('defer', 'drop', 'caption'))


BRIEF_KEYS = ('preparedAt', 'threadAfter', 'lane', 'deferTrigger')   # all thread_after reads of a brief


def brief_facts(rec):
    """What the rule needs from the brief the pick was taken on, or None. The next prepare pass removes the brief, so
    desk-close.py copies these into the record before anything else can."""
    try:
        b = json.load(open(rec.get('briefFile') or ''))
    except Exception:
        return None
    return None if b.get('error') else {k: b.get(k) for k in BRIEF_KEYS}


def settle_session_pick(rec, log=print):
    """The executor's rule for a pick the session carried out, on the brief facts desk-close.py copied into the record.
    Without them the time of the brief and its own say are unknown, so the status stays."""
    facts = rec.get('briefFacts')
    if not facts:
        rec['threadAfter'] = {'action': 'leave', 'reason': 'the brief is gone, so a teammate reply after it cannot be ruled out'}
        log(f"{rec['cardId']}: thread status left as it is (the brief is gone)")
        return 'thread status left as it is (the brief is gone)'
    return settle_thread(rec, facts, rec.get('thread'), log=log)


def write_json(path, obj):
    tmp = f'{path}.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def settle_record(path):
    """`deskthread.py settle <decision file>`: apply the rule, add the line to the record's result, log it next to the
    executor's lines. desk-close.py starts this detached: the mod gives desk-close.py 30 s, and a Plain read and write
    can take longer, which would report a close that landed as failed."""
    log_path = os.path.join(os.path.dirname(path), 'execute.log')

    def log(msg):
        with open(log_path, 'a') as f:
            f.write(f"{deskplain.now_iso()} {msg}\n")
    rec = json.load(open(path))
    picked = rec.get('pickedAt')
    line = settle_session_pick(rec, log)
    now = json.load(open(path))
    if now.get('pickedAt') != picked:   # a new pick on the card replaced the record meanwhile: it is not ours to touch
        return line
    now['threadAfter'] = rec.get('threadAfter')
    now['result'] = (f"{now.get('result') or ''}; {line}" if line else now.get('result') or '')[:300]
    write_json(path, now)
    return line


def start_settle(path):
    """Run settle_record in a child that outlives desk-close.py."""
    import subprocess
    subprocess.Popen([sys.executable, os.path.realpath(__file__), 'settle', path], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'settle':
        print(settle_record(sys.argv[2]))
    else:
        print('deskthread.py settle <decision file>'); sys.exit(2)
