#!/usr/bin/env python3
"""test_thread.py — the thread status after a pick the session carried out (desk-close.py, deskthread.py), on a copy of
the desk in a temp folder, with the Plain read and writes stubbed.

  python3 scripts/test_thread.py
"""
import json
import os
import runpy
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
# the resolver reads the folders once, at import: point it away from the real desk before any desk module loads
os.environ['DESK_DIR'] = tempfile.mkdtemp(prefix='desk-test-')
import deskconfig  # noqa: E402
import deskanchor  # noqa: E402
import deskplain  # noqa: E402
import deskthread  # noqa: E402

CID = 'notify:T-1'
THREAD = 'th_1'
PREPARED = '2026-11-17T21:30:00Z'


def desk_with_session_pick(folder):
    """A NOTIFY card picked "Send it" on the pane, class session, its brief still on disk."""
    for sub in ('briefs', 'decisions'):
        os.makedirs(f'{folder}/{sub}')
    card = {'id': CID, 'ref': 'T-1', 'lane': 'NOTIFY', 'thread': THREAD, 'key': 'reply:T-1', 'since': 'deployed:production:x'}
    json.dump({'cards': {}, 'log': []}, open(f'{folder}/desk.json', 'w'))
    json.dump({'cards': [card], 'parked': []}, open(f'{folder}/cards.json', 'w'))
    brief = f'{folder}/briefs/notify_T-1.json'
    json.dump({'ref': 'T-1', 'lane': 'NOTIFY', 'preparedAt': PREPARED, 'options': [{'label': 'Send it', 'verb': 'do'}],
               'draft': 'The fix is live.'}, open(brief, 'w'))
    rec = f'{folder}/decisions/notify_T-1.json'
    json.dump({'cardId': CID, 'ref': 'T-1', 'lane': 'NOTIFY', 'option': 'Send it', 'verb': 'do', 'class': 'session',
               'pickedAt': '2026-11-17T22:50:00Z', 'briefFile': brief, 'thread': THREAD, 'state': 'queued', 'result': ''},
              open(rec, 'w'))
    return rec


def close_in_session(newest, verb='do'):
    """desk-close.py as the desk_close tool runs it after the session sent; returns the record and the Plain writes."""
    writes = []
    deskanchor.thread_anchor = lambda t: {'kind': 'thread', 'status': 'TODO', **newest}
    deskplain.mark_done = lambda t, dry=False: writes.append(('done', t))
    deskplain.snooze_waiting = lambda t, dry=False: writes.append(('waiting', t))
    deskthread.start_settle = deskthread.settle_record   # inline instead of a detached child
    with tempfile.TemporaryDirectory() as folder:
        rec_path = desk_with_session_pick(folder)
        os.environ['DESK_DIR'] = folder
        deskconfig.DESK = folder   # desk-close.py reads the folder from the resolver
        os.environ.pop('DESK_DECISION', None)
        argv = sys.argv
        sys.argv = ['desk-close.py', CID, verb, 'replied as Luna 22:51Z', '--pick', 'Send it']
        try:
            runpy.run_path(f'{HERE}/desk-close.py', run_name='__main__')
        finally:
            sys.argv = argv
        return json.load(open(rec_path)), writes


def test_a_session_send_with_nothing_awaited_sets_the_thread_done():
    rec, writes = close_in_session({'newestAt': '2026-11-17T22:51:00', 'newestWho': 'Luna', 'newestActor': 'MachineUserActor'})
    assert writes == [('done', THREAD)], writes
    assert rec['state'] == 'done'
    assert rec['threadAfter']['action'] == 'done', rec['threadAfter']
    assert 'thread set to done' in rec['result'], rec['result']


def test_a_teammate_reply_after_the_brief_leaves_the_status():
    rec, writes = close_in_session({'newestAt': '2026-11-17T22:55:00', 'newestWho': 'Sam', 'newestActor': 'UserActor'})
    assert writes == [], writes
    assert rec['threadAfter']['action'] == 'leave', rec['threadAfter']
    assert 'Sam wrote the newest message' in rec['threadAfter']['reason']


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn(); print(f'ok   {name}')
            except Exception as e:
                failed += 1; print(f'FAIL {name}: {e}')
    sys.exit(1 if failed else 0)
