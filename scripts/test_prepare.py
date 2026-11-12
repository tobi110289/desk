#!/usr/bin/env python3
"""test_prepare.py — the menu and first-option rules of desk-prepare.py, without a model or a network.

  python3 scripts/test_prepare.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
spec = importlib.util.spec_from_file_location('desk_prepare', f'{HERE}/desk-prepare.py')
dp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp)
dp.unattended_failed = lambda card: False

HOLD = '## Who holds this thread\nassignee: nobody\n'
FAST = {'id': 'today:T-1', 'ref': 'T-1', 'lane': 'FAST', 'thread': 'th_1'}


def material(validated=False, hermes=True, examples=0, ours=False):
    return (HOLD + f"local validation note: {'Sam at 10:00' if validated else 'none'}\n"
            + f"Hermes brief: {'bot at 09:00' if hermes else 'none in the entries read'}\n"
            + f"newest visible message: {'ours, after the customer' if ours else 'the customer'}\n"
            + f"examples named in the newest customer message: {examples}" + ('' if ours or not examples else ' and nobody has replied since'))


def brief(card, so, m):
    return dp.brief_of(card, so, dp.facts_of(card, m))


def opt(label):
    return {'label': label, 'decide': f'{label} Q?', 'then': f'{label} then.'}


def answer(order, **more):
    return {'why': 'w', 'fact': 'f', 'draft': '', 'options': [opt(o) for o in order], 'checks': [], 'openChecks': [],
            'resolved': False, 'replyReady': False, 'threadAfter': 'leave', **more}


def test_question_belongs_to_the_first_option():
    # the model ranks a record of a send first; the facts say nobody checked the thread, so the validation leads
    b = brief(FAST, answer(['I sent it already', 'Defer', dp.UNATTENDED, 'Drop (unpin)'], openChecks=['x']), material(ours=True))
    assert b['options'][0]['label'] == dp.UNATTENDED
    assert b['decide'] == f'{dp.UNATTENDED} Q?' and b['then'] == f'{dp.UNATTENDED} then.'


def test_menu_holds_only_what_can_be_right():
    f = dp.facts_of(FAST, material())
    menu = dp.menu_of(FAST, f)
    assert dp.UNATTENDED in menu and 'Claim + validate now' not in menu
    assert 'I sent it already' not in menu and 'Draft the reply' not in menu and 'Hand to a worker' not in menu
    assert 'Check the examples, then answer' not in menu
    validated = dp.menu_of(FAST, dp.facts_of(FAST, material(validated=True)))
    assert 'Hand to a worker' in validated and dp.UNATTENDED not in validated and 'Claim + validate now' not in validated
    no_hermes = dp.menu_of(FAST, dp.facts_of(FAST, material(hermes=False)))
    assert 'Claim + validate now' in no_hermes and dp.UNATTENDED not in no_hermes
    assert 'Hand the rework to its agent' not in dp.menu_of({'id': 'gh:1', 'lane': 'REVIEW'}, dp.facts_of({'lane': 'REVIEW'}, ''))


def test_a_checked_draft_leads_with_the_send():
    so = answer(['Hand to a worker', 'Send it', 'Defer'], draft='Hallo', replyReady=True)
    b = brief(FAST, so, material(validated=True))
    assert [o['label'] for o in b['options']][:1] == ['Send it'] and b['replyReady'] and b['decide'] == 'Send it Q?'


def test_examples_come_before_the_answer():
    so = answer(['Send it', dp.UNATTENDED], draft='Hallo', replyReady=True)
    b = brief(FAST, so, material(examples=2))
    assert b['options'][0]['label'] == 'Check the examples, then answer' and not b['replyReady']


def test_defer_leads_only_on_a_machine_trigger():
    task = {'id': 'task:x', 'lane': 'TASK'}
    assert brief(task, answer(['Defer', 'Start it now'], deferTrigger='manual:Sam answers'), '')['options'][0]['label'] == 'Start it now'
    assert brief(task, answer(['Defer', 'Start it now'], deferTrigger='2026-12-01'), '')['options'][0]['label'] == 'Defer'
    again = {**task, 'deferred': 2}
    assert brief(again, answer(['Defer', 'Start it now'], deferTrigger='2026-12-01'), '')['options'][0]['label'] == 'Start it now'


def test_a_kept_promise_closes_the_loop():
    card = {'id': 'promise:T-2', 'ref': 'T-2', 'lane': 'PROMISE', 'thread': 'th_2'}
    b = brief(card, answer(['Defer', 'Reply with a status'], resolved=True, deferTrigger='2026-12-01'), material(validated=True, ours=True))
    assert b['options'][0]['label'] == 'Close the loop (nothing owed)'


def test_a_holding_reply_is_dropped_and_the_check_leads():
    # 2026-11-12: validated, cause not reproducible, and the brief drafted "we do not know yet, we keep checking"
    holding = 'Hallo Dana,\n\nWarum die Warnung bei dir erschien, wissen wir noch nicht sicher. Wir prüfen das weiter und melden uns, sobald wir die Ursache kennen.'
    b = brief(FAST, answer(['Draft the reply', 'Hand to a worker', 'Defer', 'Drop (unpin)'], draft=holding, replyReady=True), material(validated=True))
    assert b['causeOpen'] and b['draft'] == '' and not b['replyReady'], b
    assert b['options'][0]['label'] == 'Check the examples, then answer', b['options']
    assert b['decide'] == dp.CAUSE_DECIDE and 'Draft the reply' not in [o['label'] for o in b['options']], b
    assert b['openChecks'][0] == dp.CAUSE_CHECK, b['openChecks']
    # the model's own flag does the same without a draft
    b = brief(FAST, answer(['Draft the reply', 'Defer'], causeOpen=True), material(validated=True))
    assert b['causeOpen'] and b['options'][0]['label'] == 'Check the examples, then answer', b
    # a reply with a known cause and a promise tied to a deploy is no holding reply
    fine = 'Hallo Dana,\n\nder Fehler lag an der Sortierung der Stopps. Der Fix ist gemergt, wir melden uns, sobald er live ist.'
    b = brief(FAST, answer(['Send it', 'Draft the reply', 'Defer'], draft=fine, replyReady=True), material(validated=True))
    assert not b['causeOpen'] and b['draft'] == fine, b
    for text in ('We are still investigating why this happened.', 'Die Ursache ist bisher unklar.', "We don't know yet why the warning showed."):
        assert dp.HOLDING.search(text), text
    for text in ('Wir prüfen deine Rechnung und schicken sie dir morgen.', 'Danke, wir wissen Bescheid.'):
        assert not dp.HOLDING.search(text), text


def test_a_colleague_gets_the_fix_and_no_customer_reply():
    # 2026-11-12: a colleague reported a fault and the brief drafted a status reply to send as Luna
    m = HOLD + 'requester: a colleague (Dana), not a customer\n' + material(validated=True)[len(HOLD):]
    b = brief(FAST, answer(['Hand to a worker', dp.ANSWER_COLLEAGUE, 'Defer', 'Drop (unpin)'], draft='Hi Dana, here is the status.', replyReady=True), m)
    labels = [o['label'] for o in b['options']]
    assert labels[0] == 'Hand to a worker' and not b['replyReady'], b
    assert not {'Send it', 'Edit then send', 'Draft the reply'} & set(labels), labels
    # a pure question: the model ranks the answer first, and it still is no send
    b = brief(FAST, answer([dp.ANSWER_COLLEAGUE, 'Hand to a worker', 'Defer'], draft='Ja, seit gestern live.', replyReady=True), m)
    assert b['options'][0]['label'] == dp.ANSWER_COLLEAGUE and b['options'][0]['verb'] == 'do' and not b['replyReady'], b
    # no validation run is offered on a colleague's thread, checked or not
    m = HOLD + 'requester: a colleague (Dana), not a customer\n' + material()[len(HOLD):]
    assert dp.menu_of(FAST, dp.facts_of(FAST, m)) == dp.COLLEAGUE


def test_the_pane_shows_lead_alternative_and_one_way_out():
    b = brief({'id': 'task:x', 'lane': 'TASK'}, answer(['Start it now', 'Drop', 'Defer', 'Hand it to agents']), '')
    assert [o['label'] for o in b['options']] == ['Start it now', 'Hand it to agents', 'Drop', 'Defer'] and b['shown'] == 3


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests:
        t()
        print('pass', t.__name__)
    print(f'{len(tests)} pass')
