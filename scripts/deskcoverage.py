"""Coverage: the desk compared with what its sources saw, once per gather (2026-11-09).

"Something is missing from my desk" was the most frequent desk complaint since September (25 times), and each time it
stopped only when code read the missing source. This pass makes a missing item a card instead of silence: every source
names the items it saw, every rule that keeps an item off the desk on purpose names its reason, and an item that reached
no card and no reason is a gap. One RUN card per source with gaps (`coverage:<source>`, desk.py).

An item counts as reached when any of these holds:
- a card for it went through add(): live, parked, closed by a hold, dropped by ignore.json, later folded into a batch or
  removed for a stronger card (add() itself decides those, so a card offered to it is a decision, not silence);
- a source rule recorded why it builds no card (explain(): an agent works it, a teammate owns it, no move for a person);
- it leans on another item that was reached (via(): an open PR whose loop entry speaks for it);
- ignore.json lists its ref, or its title matches an ignore pattern.

Covered sources and their seen sets (reused from what the gather already fetched, no extra network call):
- threads: every Plain TODO thread on the todo-gap page (todo-gap.py lists them all, bucketed);
- prs: own open orbit and orbot PRs (the PR cache, or the gh list it falls back to);
- workers: loop entries in flight with a terminal (the screen cache's rows);
- inbox: A2A tasks waiting on me (the feed).
Not covered: SNOOZED threads (Plain wakes a snoozed thread itself, and it comes back as TODO; the PROMISE source reads
them by timeline), loop entries without a terminal or thread (their PR or thread is covered instead), Notion tickets,
local ticket files, the Sentry sweep and tasks (each of those sources makes a card for every open item it reads, so
nothing there is dropped silently), and scoped runs (a tenant or today sitting is a subset by design).
"""
import re

# in this order: an item that is a gap of an earlier source is not listed again (a stalled worker's thread is one gap)
SOURCES = {'workers': 'worker terminals', 'prs': 'open PRs', 'threads': 'open support threads', 'inbox': 'inbox tasks'}
GAP_CAP = 10   # ids listed on the card's status


class Coverage:
    def __init__(self):
        self.seen = {s: {} for s in SOURCES}   # source -> {item: label}
        self.why = {}                           # item -> reason a rule keeps it off the desk
        self.alias = {}                         # item -> the item that speaks for it
        self.offered = []                       # every card handed to add()

    def see(self, src, item, label=''):
        if item: self.seen[src][str(item)] = label

    def explain(self, item, reason):
        if item: self.why.setdefault(str(item), reason)

    def via(self, item, owner):
        if item and owner: self.alias[str(item)] = str(owner)

    def offer(self, card):
        self.offered.append(card)

    @staticmethod
    def pr_item(n, card=None, repo=''):
        """The PR's item id: '#<n>' for orbit, 'orbot#<n>' for orbot."""
        orbot = 'orbot' in (repo or '') or 'orbot' in ((card or {}).get('repo') or '') or ((card or {}).get('id') or '').startswith('orbot:')
        return f"orbot#{n}" if orbot else f"#{n}"

    def _touched(self):
        out = set()
        for c in self.offered:
            for m in [c] + list(c.get('members') or []):
                for k in ('ref', 'thread', 'task'):
                    if m.get(k): out.add(str(m[k]))
                if m.get('pr'): out.add(self.pr_item(m['pr'], c))
            if c.get('a2a'): out.add(str(c['a2a'].get('taskId')))
            if c.get('a2a_task'): out.add(str(c['a2a_task']))
        return out

    def gaps(self, ignore=None):
        """{source: [(item, label), ...]} for every item no card, reason, alias or ignore entry reached."""
        touched = self._touched()
        refs = set((ignore or {}).get('refs') or [])
        pats = [re.compile(p, re.I) for p in (ignore or {}).get('titles') or []]

        def reached(item, depth=0):
            if item in touched or item in self.why or item in refs: return True
            a = self.alias.get(item)
            return bool(a) and depth < 3 and reached(a, depth + 1)

        out = {}; listed = set()
        for src, items in self.seen.items():
            g = [(i, lab) for i, lab in sorted(items.items()) if i not in listed and not reached(i) and not any(p.search(lab or '') for p in pats)]
            listed |= {i for i, _ in g}
            if g: out[src] = g
        return out

    def report(self, ignore=None):
        """One line per seen item and how it was reached (DESK_DEBUG=1 prints it), for tuning the reasons."""
        touched = self._touched(); refs = set((ignore or {}).get('refs') or [])
        gaps = {i for g in self.gaps(ignore).values() for i, _ in g}
        lines = []
        for src, items in self.seen.items():
            lines.append(f'coverage {src}: {len(items)} seen')
            for i, lab in sorted(items.items()):
                how = ('GAP' if i in gaps else 'card' if i in touched else f'why: {self.why[i]}' if i in self.why
                       else f'via {self.alias[i]}' if i in self.alias else 'ignore.json' if i in refs else 'ignore title')
                lines.append(f'  {i:<14} {how:<60} {lab}')
        return '\n'.join(lines)

    def cards(self, ignore=None, today=''):
        """One RUN card per source with gaps, in the person's tier (waits: person)."""
        out = []
        for src, g in self.gaps(ignore).items():
            ids = [i for i, _ in g]; n = len(ids); noun = SOURCES[src]
            shown = ', '.join(ids[:GAP_CAP]) + (f' and {n - GAP_CAP} more' if n > GAP_CAP else '')
            out.append({'id': f'coverage:{src}', 'ref': f'coverage {src}', 'lane': 'RUN', 'mins': 2 + n, 'impact': 'P1', 'age': 0, 'waits': 'person',
                        'pr': None, 'task': None, 'thread': None, 'url': None, 'bonus': 40, 'score': 0,
                        'status': f'{n} {noun} reached no card: {shown}',
                        'note': '; '.join(f'{i} ({lab})' if lab else i for i, lab in g)[:800],
                        'gaps': [{'item': i, 'label': lab} for i, lab in g],
                        # the gap set is the wait: a closed card comes back only when the set changes
                        'since': ','.join(ids),
                        'act': f'{n} {noun} reached no card this gather: {shown}. Fix the source rule that dropped them, add them to ignore.json, or defer'})
        return out

    def phrase(self, ignore=None):
        g = self.gaps(ignore)
        if not g: return 'coverage: no gaps'
        return 'coverage: ' + ', '.join(f'{len(v)} {k}' for k, v in g.items()) + ' unexplained'
