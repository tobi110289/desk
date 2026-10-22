"""deskevent.py: apply one live event from the adapter's desk-events feed to cards.json, in well under a second.

`desk.py --event <json>` calls apply(); desk-events.py feeds it. The full gather (desk.py, every 10 minutes) stays the
truth: this module only moves the pane forward between two gathers, from the event fields alone. It never writes
desk.json, board-sync.json or release-watch.json, never runs the pre-flight, and keeps cards.json `generatedAt` (the
pane's gather clock reads it), stamping `eventAt` instead.

Event types:
  (a thread the loop owns gets the card too when the customer's message is the newest: "customer waits")
  thread.created           add or refresh the today:<ref> FAST card, ranked like a thread opened today (1800+)
  thread.customer_message  refresh that card, lift any live card of the ref (1700+), release a manual hold, or add
                           a today:<ref> card when nothing carries the thread
  thread.status_changed    DONE: drop the thread's today:/tenant:/owe: cards (they come from TODO threads only);
                           loop, pin, notify and parked cards stay, their act often outlives the status
  thread.fields_changed    a new agent_readiness rewrites the today: card's act
  luna.reply_sent          rewrite the caption of the thread's cards
  triage.writeback_done    same

DESK_DIR overrides ~/orbit-scratch/desk, so a test runs against a copy and never touches the live files.
"""
import datetime, fcntl, json, os, re, sys, zoneinfo
import deskconfig

DESK = os.environ.get('DESK_DIR') or os.path.expanduser('~/orbit-scratch/desk')
LOOP = os.path.expanduser('~/orbit-scratch/agent-loop')
JEV = f'{LOOP}/jev'   # home of plain.py, the one Plain reader
BERLIN = zoneinfo.ZoneInfo('Europe/Berlin')
GATHER_LOCK_S = 3 * 60   # the pane's .gather marker counts this long (register.tsx GATHER_LOCK_MS)
STALE_RUN = 75           # same exit as desk.py when a run must not write: the caller tries again

IMPACT = {'P0': 8, 'RBP': 6, 'RBS': 5, 'P1': 4, 'P2': 2}
READINESS_ACT = {
    'ready-for-agent': 'validate the Hermes brief and hand it to a worker (the loop never picked it up)',
    'ready-for-human': 'read the Hermes brief, then draft the reply or take the decision it waits on',
    'needs-info':      'check whether the customer answered; nudge them or close the thread',
    'needs-triage':    'claim it, validate, draft the reply',
    'wontfix':         'the sweep graded it wontfix: tell the customer in one line and close the thread',
    'duplicate':       'the sweep graded it a duplicate: link it to the canonical thread and close it',
}
THREAD_PREFIXES = ('today:', 'tenant:', 'owe:')   # cards built from the TODO thread list itself


def fast_score(lvl, who, age):
    """The score fast_thread_cards gives a thread card before the lift."""
    return round((IMPACT.get(lvl, 1) + {'customer': 3, 'person': 2, 'nobody': 0}[who] + min(age / 3, 2)) / 10 * 10, 1)


def lift_new(score): return round(1800 + min(score, 99) / 100, 2)        # a thread opened today
def lift_answered(score): return round(1700 + min(score, 99) / 100, 2)   # a customer message today


def relift(score, new):
    """The lift a live card gets from a live event, never a step down: desk.json top (2000+) and a card already in
    the bracket keep their score; a 1700 card whose thread turns out to be new moves up with its base score."""
    if score >= 2000 or score >= (1800 if new else 1700): return score
    if score >= 1700: return lift_new(round((score - 1700) * 100, 2))
    return (lift_new if new else lift_answered)(score)


def parse_iso(iso):
    try: return datetime.datetime.fromisoformat((iso or '').replace('Z', '+00:00'))
    except Exception: return None


def berlin_day(iso):
    t = parse_iso(iso); return t.astimezone(BERLIN).strftime('%Y-%m-%d') if t else ''


def load(p, default):
    try: return json.load(open(p))
    except Exception: return default


def plain_link(thread):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        return deskconfig.PLAIN_THREAD + thread
    except Exception:
        return None


def resolve_ref(thread):
    """ref, title and customer mail of a thread from Plain, once, when the event carried no ref."""
    try:
        if JEV not in sys.path: sys.path.insert(0, JEV)
        import plain
        t = plain.gql('query($id:ID!){ thread(threadId:$id){ ref title customer{ email{ email } } } }', {'id': thread})['thread'] or {}
        return t.get('ref'), t.get('title'), ((t.get('customer') or {}).get('email') or {}).get('email')
    except Exception:
        return None, None, None


def gather_running():
    try:
        started = parse_iso(open(f'{DESK}/.gather').read().strip())
    except Exception:
        return False
    return bool(started) and (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds() < GATHER_LOCK_S


def ignored(ref, title):
    ig = load(f'{DESK}/ignore.json', {})
    if ref in (ig.get('refs') or []): return True
    return any(re.search(p, title or '', re.I) for p in (ig.get('titles') or []))


def caption(status, customer_at, opened_at):
    """The caption fast_thread_cards writes: the thread status, the newest customer entry, the opening time."""
    facts = [f"Plain {status or 'TODO'}"]
    if customer_at: facts.append(f"customer wrote {customer_at[:10]}")
    if opened_at:
        t = parse_iso(opened_at)
        if t: facts.append(f"opened {t.astimezone(BERLIN).strftime('%Y-%m-%d %H:%M')} Berlin")
    return '; '.join(facts)[:300]


def _hm(iso):
    t = parse_iso(iso); return t.astimezone(datetime.timezone.utc).strftime('%H:%MZ') if t else '?'


def apply(ev, now=None):
    """Apply one event. Returns (exit code, one line for stdout)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today_utc = now.strftime('%Y-%m-%d')
    today = now.astimezone(BERLIN).strftime('%Y-%m-%d')
    kind = ev.get('type') or ''
    if kind not in ('thread.created', 'thread.customer_message', 'thread.status_changed', 'thread.fields_changed',
                    'luna.reply_sent', 'triage.writeback_done'):
        return 0, f"skip: unknown event type {kind or '?'}"
    if gather_running():
        return STALE_RUN, 'skip: a full gather is running, apply after it'
    thread = ev.get('threadId') or ''
    ref, title = ev.get('ref'), ev.get('title') or ''
    email = (ev.get('customer') or {}).get('email') or ''
    if not ref and thread:
        r, t, e = resolve_ref(thread)
        ref, title, email = r, title or t or '', email or e or ''
    if not ref:
        return 0, f"skip: {kind} without a ref (thread {thread[:12] or '?'} not readable)"
    if deskconfig.is_internal(email):
        return 0, f"skip: {ref} requester {email} is internal (config internalDomains), no customer event"
    at = ev.get('at') or now.isoformat()

    lock = open(f'{DESK}/.desk.lock', 'w'); fcntl.flock(lock, fcntl.LOCK_EX)   # desk.py and desk-close.py hold it too
    path = f'{DESK}/cards.json'
    data = load(path, None)
    if not isinstance(data, dict) or 'cards' not in data:
        return 0, 'skip: no cards.json yet, the full gather writes the first one'
    live, parked = data['cards'], data.get('parked') or []
    desk = load(f'{DESK}/desk.json', {}).get('cards', {})
    scope = data.get('scope') or ''
    prefix = 'tenant' if scope.startswith('tenant ') else 'today'
    if prefix == 'tenant' and ev.get('tenantId') and scope != f"tenant {ev['tenantId']}":
        return 0, f"skip: {ref} is outside the {scope} scope"
    mine = lambda c: c.get('ref') == ref or (thread and c.get('thread') == thread) or any(m.get('ref') == ref for m in c.get('members') or [])
    cid = f'{prefix}:{ref}'
    out = []

    def thread_card(customer_at, opened_at, status):
        rd = ev.get('readiness') or 'needs-triage'
        detail = ev.get('detail') if isinstance(ev.get('detail'), dict) else {}
        lvl = detail.get('impact_level') if detail.get('impact_level') in IMPACT else 'P2'
        return {'id': cid, 'ref': ref, 'lane': 'FAST', 'mins': 10, 'impact': lvl, 'age': 0, 'waits': 'customer',
                'pr': None, 'task': None, 'thread': thread or None, 'url': plain_link(thread) if thread else None,
                'status': caption(status, customer_at, opened_at),
                'note': (title[:120] + ' · ' + f"{rd}; owner nobody")[:300],
                'act': f"{ref} ({title[:60]}, {email}): {READINESS_ACT.get(rd, READINESS_ACT['needs-triage'])}",
                'score': fast_score(lvl, 'customer', 0), 'seen': 1, 'live': kind}

    def held(card):
        """desk.json still holds this card: closed on this caption today, or deferred on this caption."""
        prev = desk.get(card['id']) or {}
        cl = prev.get('closed') or {}
        if cl and (cl.get('status') == card['status'] or (cl.get('status') is None and cl.get('date') == today_utc)): return 'closed'
        d = prev.get('defer') or {}
        if d and (d.get('status') is None or d.get('status') == card['status']): return 'parked'
        return None

    def waiting_card(new):
        """The card unanswered_cards() in desk.py makes: the customer's message is the newest one, so a loop entry,
        a worker or another card of the thread hides nothing."""
        card = thread_card(at, at if new else None, ev.get('status') or 'TODO')
        card['act'] = f"{ref} ({title[:60]}, {email}): customer waits for a first answer since {_hm(at)}: draft the reply"
        card['unanswered'] = at
        why = held(card)
        if why:
            out.append(f"skip: {cid} is {why} on this caption"); return
        card['score'] = relift(card['score'], new); live.append(card)
        out.append(f"{cid} added, the customer waits ({card['score']})")

    if kind in ('thread.created', 'thread.customer_message'):
        new = kind == 'thread.created'
        own = next((c for c in live if c['id'] == cid), None)
        other = [c for c in live if mine(c) and c['id'] != cid]
        if own:
            m = re.search(r'; opened [^;]+ Berlin', own.get('status') or '')
            own['status'] = caption(ev.get('status') or 'TODO', at, at) if new or not m else caption(ev.get('status') or 'TODO', at, None) + m.group(0)
            own['score'] = relift(own['score'], new)
            own['live'] = kind
            out.append(f"{cid} refreshed ({own['score']})")
        for c in other:
            c['score'] = relift(c['score'], new)
            c['note'] = f"customer wrote {_hm(at)} (live): " + re.sub(r'^customer wrote \S+ \(live\): ', '', c.get('note') or '')
            c['live'] = kind
            out.append(f"{c['id']} lifted ({c['score']})")
        if not own and other and not ignored(ref, title):
            waiting_card(new)   # another card (loop:, gap:, owe:) carries the thread; the gather adds this one beside it
        if not own and not other:
            hold = next((c for c in parked if mine(c)), None)
            loop = load(f'{LOOP}/state.json', {'threads': {}}).get('threads', {})
            if hold:
                trig = ((hold.get('deferred') or {}).get('trigger') or '')
                if trig.startswith('manual:') and not new:
                    # the same release add() does on its next run: a customer message newer than a manual hold ends it
                    parked.remove(hold)
                    hold['status'] = f"customer answered {at[:16]}Z (live)"
                    hold['act'] = (hold.get('act') or '') + f" (hold was: {trig[7:][:60]})"
                    hold['lane'] = 'REPLY' if hold['lane'] in ('STALE', 'NOTIFY', 'RUN', 'PROMISE') else hold['lane']
                    hold['score'] = 900; hold['released'] = True; hold.pop('deferred', None); hold['live'] = kind
                    live.append(hold); out.append(f"{hold['id']} released from its manual hold")
                else:
                    out.append(f"{hold['id']} stays parked ({trig[:40] or 'hold'}); the full gather decides")
            elif ref in loop and not ignored(ref, title):
                waiting_card(new)
            elif ignored(ref, title):
                out.append(f"skip: {ref} is on ignore.json")
            else:
                card = thread_card(at, at if new else None, ev.get('status') or 'TODO')
                why = held(card)
                if why:
                    out.append(f"skip: {cid} is {why} on this caption")
                else:
                    card['score'] = relift(card['score'], new); live.append(card)
                    out.append(f"{cid} added ({card['score']})")
    elif kind == 'thread.status_changed':
        st = (ev.get('status') or '').upper()
        if st == 'DONE':
            gone = [c for c in live if mine(c) and c['id'].startswith(THREAD_PREFIXES)]
            for c in gone: live.remove(c)
            out.append(f"{', '.join(c['id'] for c in gone)} dropped (thread DONE)" if gone else f"{ref} DONE, no thread card to drop")
        else:
            for c in live:
                if mine(c) and c['id'].startswith(THREAD_PREFIXES) and st:
                    c['status'] = re.sub(r'^Plain \S+', f'Plain {st}', c.get('status') or f'Plain {st}'); c['live'] = kind
                    out.append(f"{c['id']} now Plain {st}")
    elif kind == 'thread.fields_changed':
        rd = ev.get('readiness')
        for c in live:
            if rd and mine(c) and c['id'].startswith(THREAD_PREFIXES):
                c['act'] = re.sub(r'\): .*$', f"): {READINESS_ACT.get(rd, READINESS_ACT['needs-triage'])}", c.get('act') or '')
                c['note'] = re.sub(r' · [a-z-]+; owner ', f' · {rd}; owner ', c.get('note') or ''); c['live'] = kind
                out.append(f"{c['id']} readiness {rd}")
    else:
        if kind == 'luna.reply_sent':
            # our reply answers the customer: an unanswered-customer card has done its job
            gone = [c for c in live if mine(c) and c.get('unanswered')]
            for c in gone: live.remove(c); out.append(f"{c['id']} dropped (Luna answered)")
        what = 'Luna replied' if kind == 'luna.reply_sent' else 'triage writeback'
        detail = ev.get('detail') if isinstance(ev.get('detail'), str) else ''
        for c in live:
            if mine(c):
                c['status'] = f"{what} {_hm(at)}" + (f": {detail[:80]}" if detail else '') + ' (live)'
                c['live'] = kind
                out.append(f"{c['id']} caption: {c['status'][:50]}")

    if not out or all(o.startswith('skip') or 'stays parked' in o or 'no thread card' in o for o in out):
        return 0, '; '.join(out) or f"{ref}: nothing on the desk to change"
    live.sort(key=lambda c: (-c['score'], c['ref']))
    data['eventAt'] = now.isoformat()
    tmp = f'{path}.event.tmp'
    json.dump(data, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return 0, '; '.join(out)


def main(raw):
    try:
        ev = json.loads(raw)
    except ValueError as e:
        print(f'--event needs one JSON object: {e}', file=sys.stderr); return 2
    events = ev if isinstance(ev, list) else [ev]
    code = 0
    for one in events:
        try:
            c, line = apply(one if isinstance(one, dict) else {})
        except Exception as e:
            c, line = 1, f'event failed: {str(e)[:160]}'
        print(line)
        code = max(code, c)
    return code
