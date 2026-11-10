"""deskplain.py — the few Plain writes the desk makes in the background, over GraphQL with PLAIN_API_KEY (2026-11-09).

One place for the input shapes, read off Plain's schema: a field write names its thread and key under `identifier`,
a note needs the thread's customer id. Until this module the executor sent both without them, so its readiness flip
and its "loop closed" note failed on every call.

Dry mode: `dry=True`, or DESK_PLAIN_DRY=1 in the environment (desk-execute.py --rehearse sets it for its child), returns
what would have been written and writes nothing.

The unattended validation (option VALIDATE) is the one background job that writes a note a person did not read. Its
note opens with UNATTENDED_LINE, so a reader, the desk and the loop can tell it from a validation done in a session.
"""
import datetime
import json
import os
import re
import urllib.request

URL = os.environ.get('PLAIN_API_URL', 'https://core-api.uk.plain.com/graphql/v1')
VALIDATE = 'Validate and draft (unattended)'
UNATTENDED_LINE = 'Written unattended by the desk; a person has not read this thread.'
CLAIM_KEYS = ('triage_owner', 'triage_claimed_at')
CLAIM_TTL_S = 24 * 3600
NOTE_MAX = 9500   # Plain refuses notes past ~10 KB


def dry_env():
    return os.environ.get('DESK_PLAIN_DRY') == '1'


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def gql(query, variables, timeout=40):
    key = os.environ.get('PLAIN_API_KEY')
    if not key:
        raise RuntimeError('no PLAIN_API_KEY')
    req = urllib.request.Request(URL, data=json.dumps({'query': query, 'variables': variables}).encode(), headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json', 'User-Agent': 'desk/1 (+python-urllib)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    if out.get('errors'):
        raise RuntimeError(str(out['errors'])[:300])
    data = out['data']
    err = next((v.get('error') for v in data.values() if isinstance(v, dict) and v.get('error')), None)
    if err:
        raise RuntimeError(err.get('message') or str(err)[:200])
    return data


def thread_head(thread_id):
    """{customerId, fields{key: value}} of a thread, one read."""
    d = gql('query($id: ID!){ thread(threadId:$id){ id customer{ id } threadFields{ key stringValue } } }', {'id': thread_id})
    t = (d or {}).get('thread') or {}
    if not t:
        raise RuntimeError('thread not found')
    return {'customerId': (t.get('customer') or {}).get('id'), 'fields': {f['key']: f.get('stringValue') or '' for f in t.get('threadFields') or []}}


def upsert_field(thread_id, key, value, type_='STRING', dry=False):
    if dry or dry_env():
        return {'dry': True, 'write': 'field', 'key': key, 'value': value}
    gql('mutation($input: UpsertThreadFieldInput!){ upsertThreadField(input:$input){ error{ message } } }',
        {'input': {'identifier': {'threadId': thread_id, 'key': key}, 'type': type_, 'stringValue': value}})
    return {'dry': False, 'write': 'field', 'key': key, 'value': value}


def delete_field(thread_id, key, dry=False):
    if dry or dry_env():
        return {'dry': True, 'write': 'delete', 'key': key}
    try:
        gql('mutation($input: DeleteThreadFieldInput!){ deleteThreadField(input:$input){ error{ message code } } }',
            {'input': {'identifier': {'threadId': thread_id, 'key': key}}})
    except RuntimeError as e:
        if 'not_found' not in str(e) and 'not found' not in str(e).lower():
            raise
    return {'dry': False, 'write': 'delete', 'key': key}


def add_note(thread_id, text, dry=False):
    """An internal note (never customer-visible); `text` is markdown."""
    if len(text) > NOTE_MAX:
        raise RuntimeError(f'note is {len(text)} characters; Plain takes about {NOTE_MAX}: shorten it')
    if dry or dry_env():
        return {'dry': True, 'write': 'note', 'chars': len(text), 'firstLine': text.splitlines()[0] if text else ''}
    cid = thread_head(thread_id)['customerId']
    gql('mutation($input: CreateNoteInput!){ createNote(input:$input){ error{ message } } }',
        {'input': {'customerId': cid, 'threadId': thread_id, 'text': text, 'markdown': text}})
    return {'dry': False, 'write': 'note', 'chars': len(text), 'firstLine': text.splitlines()[0] if text else ''}


def readiness_of(thread_id):
    return thread_head(thread_id)['fields'].get('agent_readiness', '')


def set_readiness(thread_id, value, dry=False):
    return upsert_field(thread_id, 'agent_readiness', value, 'ENUM', dry)


def mark_done(thread_id, dry=False):
    if dry or dry_env():
        return {'dry': True, 'write': 'done'}
    gql('mutation($input: MarkThreadAsDoneInput!){ markThreadAsDone(input:$input){ error{ message } } }', {'input': {'threadId': thread_id}})
    return {'dry': False, 'write': 'done'}


def _age_s(stamp):
    """Seconds since a claim stamp in either shape Plain hands back (strict ISO, or 'YYYY-MM-DD HH:MM:SS.000' in UTC)."""
    s = re.sub(r'\.\d+$', '', (stamp or '').strip().replace('T', ' ').rstrip('Z'))
    try:
        t = datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()


def active_claim(fields):
    """'human' / 'local' while a claim younger than 24 h stands, else None (the /triage claim rule, step 2.5)."""
    owner = fields.get('triage_owner') or ''
    age = _age_s(fields.get('triage_claimed_at'))
    if owner and age is not None and age < CLAIM_TTL_S:
        return owner
    return None


def claim(thread_id, dry=False):
    """Claim the thread for a local run, or raise when a person or another local run holds it."""
    holder = active_claim(thread_head(thread_id)['fields'])
    if holder == 'human':
        raise RuntimeError('a person has claimed this thread')
    if holder == 'local':
        raise RuntimeError('another local run holds this thread')
    stamp = now_iso()
    return [upsert_field(thread_id, 'triage_owner', 'local', 'ENUM', dry), upsert_field(thread_id, 'triage_claimed_at', stamp, 'STRING', dry)]


def release(thread_id, dry=False):
    return [delete_field(thread_id, k, dry) for k in CLAIM_KEYS]
