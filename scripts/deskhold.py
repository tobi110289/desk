"""deskhold.py — the one place that says whether a card is held: closed, parked, or live.

2026-10-22 (the retro's deep dive): a closure used to bind to the card's display text, so every changing fact in that
text (an age, a date, an actor) reopened the card, and every fix stripped one more fact from the comparison. The hold
logic also lived in four copies (the gather, the live feed, the pane, the close script) and drifted. Now:

  key    what waits, stable: `<kind>:<entity>`. kind is the kind of wait (reply, review, send, run, …), entity the thread
         ref, the PR or the source's own id. Nine card prefixes can describe one Plain thread; they share a key when they
         describe the same wait.
  since  the fact that started this occurrence of the wait: the customer's message time, HORST's approval, the deploy, the
         loop caption, the file's Status line. A source sets it on purpose; the display text is free to carry anything.

A closure holds while the key's `since` is the one it was closed with. A drop holds every wait of the entity for the
day. A deferral holds while `since` is unchanged (a new customer message, a new caption, a new HORST round ends the
hold) and until its trigger fires; the trigger checks stay with the gather (desk.py fire()), which has gh and Plain.
A batch binds to its member set: a new member reopens a closed batch and voids its hold.

desk.json keeps `holds[key]` = {closed: {...}, defer: {...}}; `cards[id]` keeps what the pane shows (seen, lane, act).
migrate() moves the records written before this module into `holds`, keyed from the card id and lane they were written
for; a closure from before holds for its own day only, a deferral adopts the card's `since` the first time it matches.
"""
# 2026-11-09 (all-time desk report: 39 % of closes hit an id closed before; in W45 17 of 76 closes came back within a
# day, about 12 of them with nothing new): one reply wait (`reply:<ref>`) is fed by four sources that each write their
# own `since` (today/fast "TODO|<customer time>", owe "<bucket>:<grade>", promise "<newest entry>", notify
# "deployed:<env>:<at>"), so a closure bound to one card's since never matched its siblings, and our own grade or
# status writes moved it. A closure that answered the customer (a send, "sent it already", nothing owed) is now `sent`:
# it holds every reply wait of the thread until the customer writes again (or, on a NOTIFY card, a newer deploy).
import datetime, re, zoneinfo

BERLIN = zoneinfo.ZoneInfo('Europe/Berlin')
KIND = {'SEND': 'send', 'REVIEW': 'review', 'STUCK': 'review', 'REPLY': 'reply', 'NOTIFY': 'reply', 'PROMISE': 'reply',
        'FAST': 'reply', 'RUN': 'run', 'STALE': 'stale', 'INBOX': 'inbox', 'TICKET': 'ticket', 'TASK': 'task', 'SWEEP': 'sweep'}
REF_RE = re.compile(r'^(T|ORB|ORBIT|SWEEP|[A-Z]{2,8})-\d+$|^(orbot)?#\d+$')
# Ranking tiers (desk.py rank(), deskevent relift): score = tier * 1000 + base, base under 1000. Order, top first.
TIER = {'top': 9, 'new': 8, 'answered': 7, 'pin': 6, 'fired': 5, 'me': 4, 'customer': 3, 'person': 2, 'nobody': 1, 'bottom': 0}
ME_LANES = {'SEND', 'REVIEW', 'INBOX', 'STUCK'}   # an agent or a PR stopped for the person
IMPACT = {'P0': 8, 'RBP': 6, 'RBS': 5, 'P1': 4, 'P2': 2}


def today_berlin(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(BERLIN).strftime('%Y-%m-%d')


def entity_of(card):
    """The thing that waits: a thread or ticket ref, a PR, or the source's own id (the id tail)."""
    ref = card.get('ref') or ''
    if REF_RE.match(ref): return ref
    cid = card.get('id') or ''
    return cid.split(':', 1)[-1] if ':' in cid else cid


REPLY_KINDS = ('reply', 'unanswered')
ISO_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
# the notes a send leaves (desk-execute.py writes the first three; session closes word it the same way)
SENT_RE = re.compile(r'(replied|sent|told)\b[^.;]{0,40}\bluna\b|\bluna (replied|sent|told)\b|sent it (himself|already)|nobody to tell|nothing owed', re.I)


def reply_wait(card):
    """True for a card of the thread's reply wait (today, fast, owe, promise, notify, gap, unanswered)."""
    return key_of(card).split(':', 1)[0] in REPLY_KINDS


def customer_at(card):
    """The newest customer message the card knows of: the source's field, else the time inside a 'STATUS|<iso>' since or
    an unanswered since. None when the source does not know (owe reads only the sweep grade)."""
    if card.get('customerAt'): return card['customerAt']
    s = str(card.get('since') or '')
    if '|' in s and ISO_RE.match(s.split('|', 1)[1]): return s.split('|', 1)[1]
    if key_of(card).startswith('unanswered:') and ISO_RE.match(s): return s
    return None


def news_after_send(card, c):
    """What reopens a `sent` closure: a customer message newer than the one the send answered, or on a NOTIFY card a
    deploy newer than the send. The text says what changed, None while nothing did."""
    sent_at = c.get('at') or f"{c.get('date') or ''}T00:00:00"   # a closure from before `at`: the start of its day
    ref = c.get('customerAt') or sent_at
    cust = customer_at(card)
    if cust and cust[:19] > ref[:19]: return f"the customer wrote again {cust[:16]}Z"
    # a source that cannot tell the customer from us (owe reads the sweep cache) reopens on any entry after the send
    act = card.get('activityAt')
    if not cust and act and act[:19] > sent_at[:19]: return f"new entries on the thread since the send ({act[:16]}Z)"
    m = re.match(r'deployed:(\w+):(\S+)', str(card.get('since') or ''))
    if card.get('lane') == 'NOTIFY' and m and m.group(2)[:19] > (c.get('at') or f"{c.get('date') or ''}T23:59:59")[:19]:
        return f"a newer deploy on {m.group(1)} {m.group(2)[:16]}Z"
    return None


def norm(since):
    """2026-11-09: an idle worker reads as idle_empty_prompt or finished_summary from one capture to the next; the desk
    treats both the same (screen_card), so they are one wait."""
    return re.sub(r'^screen:finished_summary:', 'screen:idle_empty_prompt:', since) if isinstance(since, str) else since


def key_of(card):
    if card.get('key'): return card['key']
    cid = card.get('id') or ''
    if cid.startswith('batch:'): return cid
    # a loop entry is one wait whatever lane its caption lands in (the lane follows the caption; a closure on the
    # caption "retired; done" held it in RUN and must hold it in STALE)
    if cid.startswith('loop:'): return cid
    kind = KIND.get(card.get('lane') or '', (card.get('lane') or 'x').lower())
    return f"{kind}:{entity_of(card)}"


def since_of(card):
    """The wait's start fact; the display text only when a source set nothing (then the card behaves as before)."""
    s = card.get('since')
    return s if s is not None else (card.get('status') or '')


def member_key(m):
    """A batch member's identity. 2026-10-19: a staged line is Claude Code's ghost text and changes every worker turn,
    so a staged member keys on its state; a picker or sign-off still keys on its text."""
    if m.get('screen') == 'staged_line_typed': return f"{m.get('ref')}|{m.get('task') or ''}|staged_line_typed"
    return f"{m.get('ref')}|{m.get('task') or ''}|{m.get('status') or ''}"


def base_score(impact, age, bonus=0):
    """Within a tier: impact first, then age (capped), then a source's own nudge. Always under 1000."""
    return min(999, IMPACT.get(impact or '', 1) * 10 + min(int(age or 0), 30) + int(bonus or 0))


def score(tier, base):
    return TIER[tier] * 1000 + min(max(int(base), 0), 999)


def tier_of(card, today_new=(), today_answered=(), top=(), bottom=(), pinned=False):
    """The tier a live card ranks in. Parked and fired cards are set by the gather itself."""
    cid = card.get('id') or ''
    if cid in top: return 'top'
    if cid in bottom: return 'bottom'
    ref = card.get('ref')
    if ref in today_new: return 'new'
    if ref in today_answered: return 'answered'
    if pinned or cid.startswith('fast:'): return 'pin'
    if card.get('lane') in ME_LANES: return 'me'
    return {'customer': 'customer', 'person': 'person', 'agent': 'person', 'nobody': 'nobody'}.get(card.get('waits') or 'nobody', 'nobody')


# ---------------------------------------------------------------- the hold

def legacy_key(cid, entry):
    """The key a record written before this module belongs to, from its id and the lane the card had."""
    if cid.startswith(('batch:', 'loop:')): return cid
    lane = (entry or {}).get('lane') or ''
    prefix, _, tail = cid.partition(':')
    if prefix == 'fast': lane = ''   # a pin is its own wait, whatever lane it showed in
    ref = (entry or {}).get('ref') or tail
    ent = ref if REF_RE.match(ref or '') else tail
    kind = KIND.get(lane) or {'loop': 'run', 'gap': 'reply', 'notify': 'reply', 'promise': 'reply', 'today': 'reply', 'tenant': 'reply',
                              'owe': 'reply', 'fast': 'pin', 'ticket-done': 'reply', 'gh': 'review', 'orbot': 'review', 'prh': 'review',
                              'a2a': 'inbox', 'local': 'ticket', 'ticket': 'ticket', 'task': 'task', 'notion': 'run', 'sweep': 'sweep',
                              'sweep-decision': 'sweep', 'sweep-ticket': 'run', 'memory': 'run'}.get(prefix, prefix)
    return f"{kind}:{ent}"


def migrate(desk):
    """Move closed/defer records from cards[id] into holds[key] once. Returns the number moved."""
    holds = desk.setdefault('holds', {})
    n = 0
    for cid, entry in list((desk.get('cards') or {}).items()):
        moved = False
        for field in ('closed', 'defer'):
            rec = entry.get(field)
            if not rec: continue
            key = legacy_key(cid, entry)
            h = holds.setdefault(key, {})
            have = h.get(field)
            if field == 'closed':
                # several old ids can map to one key (today:, owe:, gap: on one thread): the newest closure wins and the
                # display texts of all of them keep holding (legacyStatuses)
                texts = [x for x in ((have or {}).get('legacyStatuses') or [(have or {}).get('status')]) if x] + ([rec.get('status')] if rec.get('status') else [])
                if have is None or (rec.get('date') or '') >= (have.get('date') or ''): h[field] = {**rec, 'legacy': True, 'id': cid}
                h[field]['legacyStatuses'] = sorted(set(texts))
            elif have is None or (rec.get('since') or '') >= (have.get('since') or ''):
                h[field] = {**rec, 'legacy': True, 'id': cid}
            moved = True
            entry.pop(field, None)
        if moved: n += 1
    return n


def hold(desk, card, today):
    """('closed', rec) when the card stays closed, ('parked', defer) when a deferral holds it (trigger not yet checked),
    ('void', defer) when a deferral was bound to an older occurrence of the wait and ends now, None when the card is live."""
    holds = desk.setdefault('holds', {})
    key = key_of(card); since = since_of(card)
    cid = card.get('id') or ''
    members = card.get('members')
    # a drop holds every wait of the entity for the day (2026-10-20: the same thread came back under another prefix)
    if not cid.startswith('fast:') and not members:
        ent = entity_of(card)
        for k, h in holds.items():
            c = (h or {}).get('closed') or {}
            if c.get('verb') == 'drop' and c.get('date') == today and k.split(':', 1)[-1] == ent: return ('closed', c)
    h = holds.get(key) or {}
    c = h.get('closed')
    if c and c.get('sent') and not members and reply_wait(card):
        if not news_after_send(card, c): return ('closed', c)
    elif c:
        if members:
            # a batch stays closed only today and only while its member set has not grown (2026-09-10: a SEND batch closed
            # on 09-09 hid a staged line for a day)
            if c.get('date') == today and {member_key(m) for m in members} <= set(c.get('members') or []): return ('closed', c)
        elif c.get('legacy'):
            # a closure from before this module: the old rule, the same display text (a text never recorded: its own day)
            texts = c.get('legacyStatuses') or ([c['status']] if c.get('status') else [])
            if not texts:
                if c.get('date') == today: return ('closed', c)
            elif any(_cmp(cid, x) == _cmp(cid, card.get('status') or '') for x in texts): return ('closed', c)
        elif norm(c.get('since')) == norm(since): return ('closed', c)
    d = h.get('defer')
    if not d and not members:
        # 2026-09-14: loop:<ref> and the thread's reply wait (gap:<ref>) are one thread and one act; a deferral on one
        # parks the other while both are bound to the same caption
        ent = entity_of(card); sib = f"loop:{ent}" if not key.startswith('loop:') else f"reply:{ent}"
        sd = (holds.get(sib) or {}).get('defer')
        if sd and not sd.get('legacy') and sd.get('since_wait') == since: return ('parked', {**sd, 'borrowed': sib})
        if sd and sd.get('legacy') and sd.get('status') is not None and _strip(sd['status']) == _strip(card.get('status') or ''): return ('parked', {**sd, 'borrowed': sib})
    if not d: return None
    if members:
        if d.get('refs') is not None:
            new = sorted({m.get('ref') for m in members} - set(d['refs']))
            if new: return ('void', {**d, 'why': f"new members {', '.join(new)}"})
        return ('parked', d)
    if d.get('legacy'):
        if card.get('rebuilt'): return ('parked', d)   # a rebuilt card carries no real since: the next real card adopts
        # adopt the card's since the first time the old binding still matches; void when the caption had moved on
        st = d.get('status')
        if st is not None and _cmp(cid, st) != _cmp(cid, card.get('status') or '') and not card.get('rebuilt'):
            return ('void', {**d, 'why': 'caption changed'})
        d.pop('legacy', None); d.pop('status', None); d['since_wait'] = since
        return ('parked', d)
    if d.get('since_wait') is not None and norm(d['since_wait']) != norm(since) and not card.get('rebuilt'):
        return ('void', {**d, 'why': 'the wait moved on'})
    return ('parked', d)


def _strip(s): return re.sub(r'^loop done, thread TODO: ', '', s or '')


def _cmp(cid, s):
    """The old display-text comparison, kept only for records written before this module."""
    if cid.startswith('tenant:'): return re.sub(r';\s*last entry [^;]*', '', s or '')
    if cid.startswith('promise:'): return re.sub(r'\(\d+d,', '(Nd,', s or '')
    return s or ''


def close(desk, card, verb, today, note='', since=None, sent=False):
    """Record a verb on the card's wait. `since` overrides the card's (a caption written in the same call). `sent` (or a
    note that says the reply went out) closes every reply wait of the thread until the customer writes again."""
    key = key_of(card)
    rec = {'verb': verb, 'date': today, 'note': note, 'id': card.get('id'),
           'since': since if since is not None else since_of(card)}
    if card.get('members'): rec['members'] = [member_key(m) for m in card['members']]; rec.pop('since', None)
    h = desk.setdefault('holds', {}).setdefault(key, {})
    h['closed'] = rec; h.pop('defer', None)
    if not card.get('members') and verb != 'defer' and (sent or SENT_RE.search(note or '')) and reply_wait(card):
        rec['sent'] = True; rec['at'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        if customer_at(card): rec['customerAt'] = customer_at(card)
        # the sibling waits of the thread hold too; a deferral there is its own plan and stays
        for k in (f"reply:{entity_of(card)}", f"unanswered:{entity_of(card)}"):
            sh = desk['holds'].setdefault(k, {})
            if k != key and not sh.get('defer'): sh['closed'] = {**rec, 'via': key}
    return key


def last_close(desk, card):
    """Why a card that had a verb is back: the verb, its date and what changed. None when it never had one."""
    key = key_of(card); cid = card.get('id') or ''
    c = ((desk.get('holds') or {}).get(key) or {}).get('closed')
    week = (datetime.date.fromisoformat(today_berlin()) - datetime.timedelta(days=7)).isoformat()
    if c and (c.get('date') or '') < week: c = None   # an old verb is history, not news
    if c and not card.get('members'):
        if c.get('sent'): what = news_after_send(card, c) or 'new'
        else: what = f"the wait moved: {str(c.get('since'))[:40]!r} -> {str(since_of(card))[:40]!r}"
        return f"{c.get('verb')} {c.get('date')}: {(c.get('note') or '')[:80]}; since then {what}"
    if card.get('members'): return None
    for k, h in (desk.get('holds') or {}).items():
        o = (h or {}).get('closed') or {}
        if k != key and o.get('id') == cid and not o.get('via') and o.get('date') == today_berlin():
            return f"{o.get('verb')} {o.get('date')} on its other wait {k.split(':', 1)[0]}: {(o.get('note') or '')[:80]}; this wait ({key.split(':', 1)[0]}) is still open"
    return None


def mark_sent(desk):
    """2026-11-09, once: a reply-wait closure written before `sent` existed whose note says the reply went out counts as
    sent. Returns the number marked; runs once (desk['sentMarked']), later sends are marked by close() in order."""
    if desk.get('sentMarked'): return 0
    desk['sentMarked'] = True
    n = 0
    for key, h in (desk.get('holds') or {}).items():
        c = (h or {}).get('closed') or {}
        if c and not c.get('sent') and not c.get('members') and key.split(':', 1)[0] in REPLY_KINDS and SENT_RE.search(c.get('note') or ''):
            c['sent'] = True
            s = str(c.get('since') or '')
            if '|' in s and ISO_RE.match(s.split('|', 1)[1]): c['customerAt'] = s.split('|', 1)[1]
            elif key.startswith('unanswered:') and ISO_RE.match(s): c['customerAt'] = s
            n += 1
    # the sibling wait of the thread holds too, unless it carries a deferral or a newer verb of its own
    for key, h in list((desk.get('holds') or {}).items()):
        c = (h or {}).get('closed') or {}
        if not c.get('sent') or c.get('via'): continue
        kind, _, ent = key.partition(':')
        if kind not in REPLY_KINDS: continue
        sib = desk['holds'].setdefault(f"{'unanswered' if kind == 'reply' else 'reply'}:{ent}", {})
        have = sib.get('closed') or {}
        if sib.get('defer') or have.get('sent') or (have.get('date') or '') > (c.get('date') or ''): continue
        sib['closed'] = {**c, 'via': key}
    return n


def defer(desk, card, trigger, act, today, since=None):
    key = key_of(card)
    rec = {'trigger': trigger, 'act': act, 'since': today, 'id': card.get('id'),
           'since_wait': since if since is not None else since_of(card)}
    if card.get('members'): rec['refs'] = sorted({m.get('ref') for m in card['members'] if m.get('ref')}); rec.pop('since_wait', None)
    h = desk.setdefault('holds', {}).setdefault(key, {})
    h['defer'] = rec; h.pop('closed', None)
    return key


def rebind(desk, ref, old_since, new_since):
    """A caption written while closing one card of a thread must not end a hold on a sibling wait of the same thread
    (2026-10-19): every hold of the entity bound to the old caption follows it."""
    n = 0
    for key, h in (desk.get('holds') or {}).items():
        if key.split(':', 1)[-1] != ref: continue
        for field in ('closed', 'defer'):
            rec = h.get(field) or {}
            f = 'since' if field == 'closed' else 'since_wait'
            if rec.get(f) == old_since and old_since != new_since: rec[f] = new_since; n += 1
    return n
