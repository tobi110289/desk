#!/usr/bin/env python3
"""deskanchor.py — the facts a brief stands on, so a decision taken on the brief can be checked before it runs.

desk-prepare.py writes `anchor` into every brief; desk-execute.py reads the same facts again right before it acts and
bounces the decision when one changed (2026-10-23: the person picks on the pane from a brief that may be minutes old,
and the executor, not the person, catches a customer who wrote in the meantime).

A Plain thread anchors to its status, the newest customer-visible message (who and when) and the newest customer
message. A PR anchors to its head commit, state and review decision. A card without either anchors to nothing, and
desk-execute.py only checks that the card is still on the desk with the same wait.

  deskanchor.py <card id>     prints the anchor of a card on cards.json (for a look by hand)
"""
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser('~')
DESK = f'{HOME}/orbit-scratch/desk'
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402

Q_THREAD = '''query($id: ID!){ thread(threadId:$id){ status timelineEntries(first: 40){ edges{ node{ id timestamp{ iso8601 }
 actor{ __typename ... on UserActor{ user{ fullName } } ... on MachineUserActor{ machineUser{ fullName } } ... on CustomerActor{ customer{ fullName } } }
 entry{ __typename } } } } } }'''
VISIBLE = {'EmailEntry', 'ChatEntry', 'SlackMessageEntry', 'SlackReplyEntry', 'ThreadDiscussionMessageEntry', 'CustomEntry'}


def gql(query, variables):
    key = os.environ.get('PLAIN_API_KEY')
    if not key:
        raise RuntimeError('no PLAIN_API_KEY')
    import urllib.request
    body = json.dumps({'query': query, 'variables': variables}).encode()
    req = urllib.request.Request('https://core-api.uk.plain.com/graphql/v1', data=body, headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json', 'User-Agent': 'desk-anchor/1 (+python-urllib)'})
    with urllib.request.urlopen(req, timeout=40) as r:
        out = json.load(r)
    if out.get('errors'):
        raise RuntimeError(str(out['errors'])[:300])
    return out['data']


def thread_anchor(thread_id):
    """status, newest customer-visible message (who, when, actor kind), newest customer message time."""
    t = gql(Q_THREAD, {'id': thread_id})['thread']
    if not t:
        return {'kind': 'thread', 'missing': True}
    visible = []
    for e in t['timelineEntries']['edges']:
        n = e['node']; a = n.get('actor') or {}; en = (n.get('entry') or {}).get('__typename', '')
        if en not in VISIBLE:
            continue
        who = ((a.get('user') or {}).get('fullName') or (a.get('machineUser') or {}).get('fullName')
               or (a.get('customer') or {}).get('fullName') or a.get('__typename') or '')
        visible.append({'id': n['id'], 'at': n['timestamp']['iso8601'][:19], 'who': who, 'actor': a.get('__typename') or ''})
    newest = visible[0] if visible else None
    cust = next((v for v in visible if v['actor'] == 'CustomerActor'), None)
    return {'kind': 'thread', 'status': t['status'], 'newestId': newest and newest['id'], 'newestAt': newest and newest['at'],
            'newestWho': newest and newest['who'], 'newestActor': newest and newest['actor'], 'customerAt': cust and cust['at']}


def pr_anchor(pr, repo):
    r = subprocess.run(['gh', 'pr', 'view', str(pr), '--repo', repo, '--json', 'headRefOid,state,reviewDecision,mergeable'],
                       capture_output=True, text=True, timeout=40)
    if r.returncode:
        raise RuntimeError(f'gh: {r.stderr.strip()[:200]}')
    d = json.loads(r.stdout)
    return {'kind': 'pr', 'head': d.get('headRefOid'), 'state': d.get('state'), 'review': d.get('reviewDecision'), 'mergeable': d.get('mergeable')}


def repo_of(card):
    ref = card.get('ref') or ''
    return deskconfig.BOT_REPO if ref.startswith('orbot#') or (card.get('id') or '').startswith('orbot:') else deskconfig.REPO


def anchor(card):
    """The anchors of a card, keyed by kind. Each source that fails records its error instead of a value, so a
    brief still gets written and the executor treats an unreadable anchor as unchanged."""
    out = {}
    if card.get('thread'):
        try:
            out['thread'] = thread_anchor(card['thread'])
        except Exception as e:
            out['thread'] = {'kind': 'thread', 'error': str(e)[:200]}
    if card.get('pr') and repo_of(card):
        try:
            out['pr'] = pr_anchor(card['pr'], repo_of(card))
        except Exception as e:
            out['pr'] = {'kind': 'pr', 'error': str(e)[:200]}
    return out


def _hhmm(iso):
    m = re.match(r'\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})', iso or '')
    return f'{m.group(1)}Z' if m else (iso or '?')


def changed(old, new):
    """Plain-word reasons the world moved between the brief and now; empty when it did not. An anchor that could not
    be read on either side is skipped: a flaky read must not bounce a good decision."""
    reasons = []
    for kind in ('thread', 'pr'):
        a, b = (old or {}).get(kind), (new or {}).get(kind)
        if not a or not b or a.get('error') or b.get('error') or a.get('missing') or b.get('missing'):
            continue
        if kind == 'thread':
            if b.get('newestId') != a.get('newestId') and b.get('newestAt'):
                who = 'the customer' if b.get('newestActor') == 'CustomerActor' else (b.get('newestWho') or 'someone')
                reasons.append(f"{who} wrote at {_hhmm(b['newestAt'])}, after the brief")
            if b.get('status') != a.get('status'):
                reasons.append(f"the thread moved from {str(a.get('status')).lower()} to {str(b.get('status')).lower()}")
        else:
            if b.get('head') != a.get('head'):
                reasons.append('the PR got new commits')
            if b.get('state') != a.get('state'):
                reasons.append(f"the PR is now {str(b.get('state')).lower()}")
            if b.get('review') != a.get('review'):
                reasons.append(f"the review decision changed to {str(b.get('review') or 'none').lower().replace('_', ' ')}")
    return reasons


if __name__ == '__main__':
    cid = sys.argv[1] if len(sys.argv) > 1 else ''
    cache = json.load(open(f'{DESK}/cards.json'))
    card = next((c for c in cache.get('cards', []) + cache.get('parked', []) if c.get('id') == cid or c.get('ref') == cid), None)
    if not card:
        print(f'no card {cid}', file=sys.stderr); sys.exit(1)
    print(json.dumps(anchor(card), indent=1))
