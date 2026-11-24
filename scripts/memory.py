#!/usr/bin/env python3
"""memory.py — the desk's window onto the orbot support memory (tenant + requester profiles, known issues).

Since orbot #1013 (2026-11-23) tenant and requester profiles live in Notion. OrBot reads a checked copy of them on the
box (the mirror), and the desk reads that same copy over the operator API: one `ssh <sendHost> curl`, cached in
memory-mirror.json. Known issues and the company map stay in the orbot checkout. Writes queue as proposals here and
go to Notion through the box's add tool (create only: it never changes or deletes a row).

  memory.py pull                          fetch the mirror from the box, fast-forward the orbot checkout (desk.py does this)
  memory.py show <T-ref|th_id> [--json]   resolve the thread's customer/company and print the memory block
  memory.py lookup --company co_… --customer c_… [--tenant-field x] [--domain d] [--email e]   JSON for scripts
  memory.py propose --ref T-… --tenant <id> [--person c_… --name "…" --role "…" --language de]
                    --section approved|observed|inferred --confidence low|medium|high --source "…" "<fact>"
  memory.py queue                         list queued proposals (state queued / added)
  memory.py drop <id>                     drop a queued proposal
  memory.py add [--dry-run]               add every queued proposal to Notion as reviewed facts (`pr` is the old name)
  memory.py sync                          mark old pr:<n> proposals merged once the PR merged (desk.py does this)

Identity: Plain company id → plain-company-tenant-map.json → tenant; else the thread's tenant_id field; else the
customer's domain against tenant aliases. No match means no memory, never a guess (policy: ambiguous → absent).
"""
import json, os, re, sys, subprocess, datetime, hashlib

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402
ORBOT = deskconfig.ORBOT_DIR
SM = f'{ORBOT}/support-memory'
DESK = deskconfig.DESK
QUEUE = f'{DESK}/memory-proposals.jsonl'
CACHE = f'{DESK}/memory-cache.json'          # thread id → customer/company identity (a thread's customer never changes)
MIRROR = f'{DESK}/memory-mirror.json'        # the box's support-memory mirror: {revision, fetchedAt, files: {path: text}}
MIRROR_MAX_AGE_H = 24                        # an older copy is not read: the desk says "memory unavailable", never a stale fact
BOX_ADAPTER = os.path.dirname(deskconfig.SEND_ENV_FILE)   # the adapter's folder on the box (config.json sendEnvFile)
PLAIN_URL = deskconfig.PLAIN_THREAD
today = datetime.date.today().isoformat()
SECTIONS = {'approved': 'Approved facts', 'observed': 'Observed facts', 'inferred': 'Inferred facts'}


# ---------------------------------------------------------------- checkout
def sh(args, cwd=ORBOT, timeout=60, check=False):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def head():
    try: return sh(['git', 'rev-parse', '--short', 'HEAD'])
    except Exception: return '?'


def pull_repo():
    """Fast-forward main (known issues and the company map live there). A dirty tree or a non-main branch is left alone."""
    try:
        deskconfig.need(ORBOT, stop=False)
        branch = sh(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
        dirty = [l for l in sh(['git', 'status', '--porcelain']).splitlines() if not l.startswith('??')]
        if branch != 'main' or dirty:
            return {'ok': False, 'reason': f'branch {branch}' if branch != 'main' else 'dirty tree'}
        sh(['git', 'pull', '-q', '--ff-only', 'origin', 'main'], timeout=40, check=True)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'reason': str(e)[:120]}


# ---------------------------------------------------------------- the mirror (orbot #1013)
def box(cmd, stdin=None, timeout=60):
    """One ssh round trip to the box, the call luna-send.py and desk-events.py make. Returns (exit code, stdout, stderr)."""
    if not deskconfig.SEND_HOST: raise RuntimeError('config.json has no sendHost')
    r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', deskconfig.SEND_HOST, cmd],
                       input=stdin, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')


def fetch_mirror():
    """Read the mirror over the operator API and cache it. The token is read on the box and never leaves it.
    No fallback to the repo files: on a failure the old cache stays as it is and the caller gets the reason."""
    try:
        cmd = (f"tok=$(sudo grep -m1 '^ORBOT_OPERATOR_API_TOKEN=' {deskconfig.SEND_ENV_FILE} | cut -d= -f2-); "
               "curl -s -m 40 -w '\\n%{http_code}' -H \"Authorization: Bearer $tok\" http://127.0.0.1:8787/api/operator/support-memory-mirror")
        rc, out, err = box(cmd, timeout=70)
        if rc: return {'ok': False, 'reason': f'ssh failed: {err.strip()[:100] or rc}'}
        body, _, code = out.rpartition('\n')
        try: d = json.loads(body)
        except Exception: return {'ok': False, 'reason': f'mirror route answered {code.strip() or "?"} with no JSON'}
        if code.strip() != '200' or not d.get('ok') or not isinstance(d.get('files'), list):
            return {'ok': False, 'reason': f"mirror route {code.strip()}: {d.get('reason') or 'no files'}"}
        files = {f['path']: f['text'] for f in d['files'] if isinstance(f, dict) and isinstance(f.get('path'), str) and isinstance(f.get('text'), str)}
        if not any(re.match(r'^tenants/[^/]+/profile\.md$', k) for k in files): return {'ok': False, 'reason': 'mirror holds no tenant profile'}
        tmp = MIRROR + '.tmp'
        json.dump({'revision': d.get('revision'), 'fetchedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'files': files}, open(tmp, 'w'), ensure_ascii=False)
        os.replace(tmp, MIRROR)                      # a reader sees the old copy or the new one, never half a file
        global _MIRROR; _MIRROR = None
        return {'ok': True, 'revision': d.get('revision'), 'files': len(files)}
    except Exception as e:
        return {'ok': False, 'reason': str(e)[:120]}


_MIRROR = None
def mirror():
    """{revision, fetchedAt, files, problem}. `files` is empty and `problem` says why when there is no copy or it is too old."""
    global _MIRROR
    if _MIRROR is not None: return _MIRROR
    try:
        m = json.load(open(MIRROR))
        age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(m['fetchedAt'])).total_seconds() / 3600
        if age > MIRROR_MAX_AGE_H: m = {'revision': m.get('revision'), 'fetchedAt': m.get('fetchedAt'), 'files': {}, 'problem': f'mirror copy is {int(age)} h old (memory.py pull)'}
        else: m['problem'] = None
    except Exception:
        m = {'revision': None, 'fetchedAt': None, 'files': {}, 'problem': 'no mirror copy yet (memory.py pull)'}
    _MIRROR = m
    return m


def revision():
    return mirror().get('revision') or '?'


def pull():
    """Desk pre-flight: fetch the mirror (profiles) and fast-forward the checkout (known issues, company map).
    `ok` is about the mirror: without it no card gets a memory block."""
    repo = pull_repo(); mir = fetch_mirror()
    out = {'ok': bool(mir.get('ok')), 'head': f"{revision()}" if mir.get('ok') else head(), 'revision': mir.get('revision'), 'repo': repo}
    if not mir.get('ok'): out['reason'] = f"mirror: {mir.get('reason')}"
    elif not repo.get('ok'): out['repoReason'] = repo.get('reason')
    return out


# ---------------------------------------------------------------- profile files
def parse_profile(path):
    """A profile file in the orbot checkout (known issues)."""
    try: text = open(path, encoding='utf-8').read()
    except Exception: return None
    return parse_text(text, os.path.relpath(path, ORBOT))


def mirror_profile(rel):
    """A tenant or requester profile from the mirror, by its path inside the revision."""
    text = mirror()['files'].get(rel)
    return parse_text(text, f'notion-mirror/{rel}') if text is not None else None


def parse_text(text, shown_path):
    """Frontmatter (the YAML subset the orbot parser accepts: scalars and '- item' lists) + body sections."""
    m = re.match(r'^---\n(.*?)\n---\n(.*)$', text, re.S)
    if not m: return None
    fm, body = {}, m.group(2)
    key = None
    for line in m.group(1).splitlines():
        if re.match(r'^\s+- ', line) and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list): fm[key].append(line.split('- ', 1)[1].strip())
        elif ':' in line:
            key, _, val = line.partition(':')
            key = key.strip(); val = val.strip()
            fm[key] = val if val else []
    sections = {}
    for name in SECTIONS.values():
        sm = re.search(rf'^## {re.escape(name)}\n(.*?)(?=^## |\Z)', body, re.S | re.M)
        bullets = re.findall(r'^- (.+?)(?=^- |\Z)', sm.group(1), re.S | re.M) if sm else []
        sections[name] = [re.sub(r'\s+', ' ', b).strip() for b in bullets if b.strip() and b.strip() != 'None.']
    title = re.search(r'^# (.+)$', body, re.M)
    return {'path': shown_path, 'fm': fm, 'sections': sections, 'title': title.group(1).strip() if title else '', 'raw': text}


def company_map():
    try: return {m['plain_company_id']: m['orbit_tenant_id'] for m in json.load(open(f'{SM}/plain-company-tenant-map.json'))['mappings']}
    except Exception: return {}


def tenants():
    return sorted(m.group(1) for k in mirror()['files'] for m in [re.match(r'^tenants/([^/]+)/profile\.md$', k)] if m)


def tenant_profile(t):
    return mirror_profile(f'tenants/{t}/profile.md')


def _norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def resolve(company=None, customer=None, tenant_field=None, domain=None, email=None):
    """Tenant id or None, with the rule that decided it."""
    if mirror()['problem']: return None, f"memory unavailable: {mirror()['problem']}"
    cm = company_map(); ts = tenants()
    if company and company in cm and cm[company] in ts: return cm[company], f'company {company}'

    if tenant_field and tenant_field in ts: return tenant_field, f'tenant_id field'
    doms = [d for d in (domain, (email or '').rsplit('@', 1)[-1] if email and '@' in email else None) if d]
    try: extra = json.load(open(f'{DESK}/tenants.json'))
    except Exception: extra = {}
    for d in doms:
        for t in ts:
            if d in extra.get(t, []) or _norm(t) in _norm(d): return t, f'domain {d}'
            p = tenant_profile(t)
            if p and any(_norm(a) and _norm(a) == _norm(d) for a in (p['fm'].get('aliases') or [])): return t, f'alias {d}'
    return None, (f'company {company} unmapped' if company else 'no company') + (f', domain {doms[0]}' if doms else '')


def person_profile(tenant, customer):
    if not (tenant and customer): return None, None
    p = tenant_profile(tenant)
    for t in [tenant] + ([p['fm']['shared_people_from']] if p and isinstance(p['fm'].get('shared_people_from'), str) else []):
        prof = mirror_profile(f'tenants/{t}/people/plain-{customer}.md')
        if prof: return prof, t
    return None, None


def known_issues(title):
    """Known-issue entries whose fingerprints hit the thread title (substring, or two shared words of 4+ letters)."""
    out = []
    words = {w for w in re.findall(r'[a-zäöüß0-9]{4,}', (title or '').lower())}
    for f in sorted(os.listdir(f'{SM}/known-issues') if os.path.isdir(f'{SM}/known-issues') else []):
        p = parse_profile(f'{SM}/known-issues/{f}')
        if not p: continue
        fps = p['fm'].get('symptom_fingerprints') or []
        hit = any(fp.lower() in (title or '').lower() or len({w for w in re.findall(r'[a-zäöüß0-9]{4,}', fp.lower())} & words) >= 2 for fp in fps if isinstance(fp, str))
        if hit: out.append({'file': p['path'], 'id': p['fm'].get('id'), 'title': p['fm'].get('title'), 'status': p['fm'].get('status'), 'cause': (p['fm'].get('confirmed_root_cause') or '')[:240]})
    return out


# ---------------------------------------------------------------- Plain identity
def plain_gql(q, variables):
    if not os.environ.get('PLAIN_API_KEY'): return None
    sys.path.insert(0, deskconfig.JEV); import plain   # the one Plain reader
    return plain.try_gql(q, variables)


_IDENT = 'id ref title customer{ id fullName email{ email } company{ id name domainName } } threadFields{ key stringValue }'


def thread_identity(ref_or_id):
    """{thread, ref, title, customer, name, email, company, domain, tenant_field} from the cache or Plain."""
    cache = {}
    try: cache = json.load(open(CACHE))
    except Exception: pass
    hit = cache.get(ref_or_id) or next((v for v in cache.values() if v.get('ref') == ref_or_id), None)
    if hit: return hit
    if ref_or_id.startswith('th_'): d = plain_gql('query($id:ID!){ thread(threadId:$id){ ' + _IDENT + ' } }', {'id': ref_or_id}); n = (d or {}).get('thread')
    else: d = plain_gql('query($r:String!){ threadByRef(ref:$r){ ' + _IDENT + ' } }', {'r': ref_or_id}); n = (d or {}).get('threadByRef')
    if not n: return None
    c = n.get('customer') or {}; f = {x['key']: x.get('stringValue') for x in n.get('threadFields') or []}
    ident = {'thread': n['id'], 'ref': n['ref'], 'title': n.get('title') or '', 'customer': c.get('id'), 'name': c.get('fullName') or '',
             'email': (c.get('email') or {}).get('email') or '', 'company': (c.get('company') or {}).get('id'),
             'companyName': (c.get('company') or {}).get('name') or '', 'domain': (c.get('company') or {}).get('domainName') or '',
             'tenant_field': f.get('tenant_id'), 'at': today}
    cache[n['id']] = ident
    try: json.dump(cache, open(CACHE, 'w'), indent=1, ensure_ascii=False)
    except Exception: pass
    return ident


# ---------------------------------------------------------------- lookup + render
def _comm(person):
    """The requester's address form and habits, from the Communication lines (du/Sie, greeting, register)."""
    if not person: return []
    lines = [b for s in person['sections'].values() for b in s if b.lower().startswith('communication:')]
    return [re.sub(r'^communication:\s*', '', b, flags=re.I) for b in lines]


def _address(person, tenant):
    text = ' '.join(_comm(person)) if person else ' '.join(tenant['sections']['Approved facts']) if tenant else ''
    if re.search(r'\bSie\b', text) and not re.search(r'\bdu\b', text): return 'Sie'
    if re.search(r'\b(du|informal)\b', text, re.I): return 'du'
    return None


def lookup(company=None, customer=None, tenant_field=None, domain=None, email=None, title=None):
    tenant, rule = resolve(company, customer, tenant_field, domain, email)
    tp = tenant_profile(tenant) if tenant else None
    person, ptenant = person_profile(tenant, customer)
    files = [x['path'] for x in (tp, person) if x]
    greeting = next((m.group(1) for b in _comm(person) for m in [re.search(r'opens with "([^"]+)"', b)] if m), None)
    if tp:
        who = person['title'].split('(', 1)[1].rstrip(')') if person and '(' in person['title'] else (customer or '')
        bits = [tenant, who or 'no requester file', (tp['fm'].get('primary_language') or '?') + ('/' + _address(person, tp) if _address(person, tp) else '')]
        if greeting: bits.append(f'"{greeting}"')
        if person and person['fm'].get('role'): bits.append('role: ' + re.sub(r'\s+\S*$', '', str(person['fm']['role'])[:56]) + ('…' if len(str(person['fm']['role'])) > 56 else ''))
        summary = 'memory: ' + ' · '.join(b for b in bits if b)
    else:
        summary = f'memory: none ({rule})'
    return {'tenant': tenant, 'rule': rule, 'revision': revision(), 'tenantProfile': tp and {'path': tp['path'], 'fm': tp['fm'], 'sections': tp['sections'], 'title': tp['title']},
            'person': person and {'path': person['path'], 'fm': person['fm'], 'sections': person['sections'], 'title': person['title'], 'tenantDir': ptenant},
            'knownIssues': known_issues(title) if tenant else [], 'files': files, 'summary': summary, 'head': head(), 'customer': customer}


def render(mem, ident=None):
    L = []
    if ident: L.append(f"## Support memory for {ident.get('ref')} · {ident.get('name') or '?'} <{ident.get('email') or '?'}> · company {ident.get('companyName') or ident.get('company') or '?'}")
    else: L.append('## Support memory')
    L.append(f"notion mirror {mem.get('revision') or '?'} · " + (', '.join(mem['files']) if mem['files'] else 'no files') + f" · resolved by {mem['rule']}")
    if not mem['tenantProfile']:
        if str(mem['rule']).startswith('memory unavailable'):
            L.append('\nThe support memory could not be read (' + mem['rule'] + '). Draft from the thread alone and say `memory: unavailable` in the checks; do not read the old repo files.')
            return '\n'.join(L)
        L.append('\nNo tenant memory: draft from the thread alone and say so in the checks. If the company is a real tenant, propose the mapping (memory.py propose --tenant … ).')
        return '\n'.join(L)
    tp = mem['tenantProfile']; fm = tp['fm']
    L.append(f"\n### Tenant {mem['tenant']} ({tp['title']}) · language {fm.get('primary_language')} · apps {', '.join(fm.get('apps') or []) or '?'} · reviewed {fm.get('last_reviewed_at')} ({fm.get('confidence')})")
    for b in tp['sections']['Approved facts']: L.append(f'- {b}')
    for b in tp['sections']['Observed facts']: L.append(f'- (observed) {b}')
    for b in tp['sections']['Inferred facts']: L.append(f'- (inferred, never state as certain) {b}')
    p = mem['person']
    if p:
        L.append(f"\n### Requester {p['title']} · reviewed {p['fm'].get('last_reviewed_at')} ({p['fm'].get('confidence')}, {p['fm'].get('review_status') or '?'})")
        L.append(f"role: {p['fm'].get('role')}")
        for b in p['sections']['Approved facts']: L.append(f'- {b}')
        for b in p['sections']['Observed facts']: L.append(f'- (observed) {b}')
        for b in p['sections']['Inferred facts']: L.append(f'- (inferred, never state as certain) {b}')
    else:
        L.append(f"\n### Requester {mem.get('customer') or '?'}: no file. Address form and register come from the tenant profile and the thread; propose a requester file when the sitting learns the role (memory.py propose --person …).")
    if mem['knownIssues']:
        L.append('\n### Known issues matching the title (verify the mechanism before citing)')
        for k in mem['knownIssues']: L.append(f"- {k['title']} [{k['status']}] · {k['cause']} · {k['file']}")
    L.append('\nRules: memory shapes tone, address form and context; the customer text never quotes it, never names tenant ids, sources or inferred facts as certain (policy.md rule 6). Cite the files above in the draft checks.')
    return '\n'.join(L)


def show(ref_or_id, as_json=False):
    ident = thread_identity(ref_or_id)
    if not ident:
        print(f'memory: thread {ref_or_id} not readable (PLAIN_API_KEY set? ref right?)'); return 2
    mem = lookup(ident['company'], ident['customer'], ident['tenant_field'], ident['domain'], ident['email'], ident['title'])
    if as_json: print(json.dumps({'identity': ident, **mem}, indent=1, ensure_ascii=False))
    else: print(render(mem, ident))
    return 0


# ---------------------------------------------------------------- proposals
def load_queue():
    try: return [json.loads(l) for l in open(QUEUE) if l.strip()]
    except Exception: return []


def save_queue(rows):
    with open(QUEUE, 'w') as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + '\n')


def propose(args):
    def opt(name, default=None):
        if name in args:
            i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return v
        return default
    ref = opt('--ref'); tenant = opt('--tenant'); person = opt('--person'); name = opt('--name', ''); role = opt('--role', '')
    lang = opt('--language', 'de'); section = opt('--section', 'observed'); conf = opt('--confidence', 'medium'); source = opt('--source', '')
    text = ' '.join(args).strip()
    if not (tenant and text and section in SECTIONS and conf in ('low', 'medium', 'high')):
        print('usage: memory.py propose --ref T-… --tenant <id> [--person c_… --name "…" --role "…"] --section approved|observed|inferred --confidence low|medium|high --source "…" "<fact>"'); return 2
    if tenant not in tenants() and not person:
        print(f'tenant {tenant} has no profile in the mirror; create the tenant row in Notion first (Tenants database), or run memory.py pull'); return 2
    if person and not re.match(r'^c_[A-Za-z0-9_]+$', person): print('--person must be a Plain customer id (c_…)'); return 2
    if person and not person_profile(tenant, person)[0] and not role:
        print(f'no requester file for {person} yet: --role "<support-relevant role>" is required to create one'); return 2
    row = {'id': hashlib.sha1(f'{tenant}|{person}|{text}'.encode()).hexdigest()[:8], 'at': today, 'ref': ref, 'tenant': tenant,
           'target': 'person' if person else 'tenant', 'customer': person, 'name': name, 'role': role, 'language': lang,
           'section': section, 'confidence': conf, 'source': source or (f'Plain {ref}' if ref else 'desk sitting'), 'text': text, 'state': 'queued'}
    rows = load_queue()
    if any(r['id'] == row['id'] and r['state'] != 'dropped' for r in rows): print(f"already queued: {row['id']}"); return 0
    rows.append(row); save_queue(rows)
    n = len([r for r in rows if r['state'] == 'queued'])
    print(f"queued {row['id']} ({row['target']} {tenant}{'/' + person if person else ''}, {section}, {conf}) · {n} queued"); return 0


def queue_status():
    rows = load_queue()
    q = [r for r in rows if r['state'] == 'queued']; pr = sorted({r['state'] for r in rows if r['state'].startswith('pr:')})
    oldest = min((r['at'] for r in q), default=None)
    return {'queued': len(q), 'oldest': oldest, 'age': (datetime.date.today() - datetime.date.fromisoformat(oldest)).days if oldest else 0,
            'openPr': pr[0].split(':', 1)[1] if pr else None, 'tenants': sorted({r['tenant'] for r in q})}


def list_queue():
    rows = load_queue()
    for r in rows:
        if r['state'] == 'dropped': continue
        print(f"{r['id']} · {r['state']} · {r['at']} · {r['target']} {r['tenant']}{'/' + r['customer'] if r.get('customer') else ''} · {r['section']} ({r['confidence']}) · {r['text'][:90]}")
    s = queue_status(); print(f"{s['queued']} queued" + (f", oldest {s['oldest']}" if s['oldest'] else '') + (f", old open PR #{s['openPr']} (orbot CI refuses it now: close it, memory.py sync)" if s['openPr'] else ''))


def drop(pid):
    rows = load_queue(); hit = False
    for r in rows:
        if r['id'] == pid and r['state'] == 'queued': r['state'] = 'dropped'; hit = True
    save_queue(rows); print(f"{pid}: {'dropped' if hit else 'not queued'}")


def sync():
    """pr:<n> → merged once GitHub says MERGED (desk.py runs this each start)."""
    rows = load_queue(); changed = []
    for n in sorted({r['state'].split(':', 1)[1] for r in rows if r['state'].startswith('pr:')}):
        st = sh(['gh', 'pr', 'view', n, '--repo', deskconfig.BOT_REPO, '--json', 'state', '-q', '.state'], cwd=DESK, timeout=30)
        if st == 'MERGED':
            for r in rows:
                if r['state'] == f'pr:{n}': r['state'] = 'merged'; r['mergedAt'] = today
            changed.append(n)
        elif st == 'CLOSED':
            for r in rows:
                if r['state'] == f'pr:{n}': r['state'] = 'queued'; r['note'] = f'PR #{n} closed unmerged {today}'
            changed.append(n + ' (closed, back to queued)')
    if changed: save_queue(rows)
    return changed


# ---------------------------------------------------------------- add to Notion
PERSON_TEMPLATE = """---
profile_type: person
plain_customer_id: {cid}
orbit_tenant_id: {tenant}
role: {role}
primary_language: {lang}
confidence: {conf}
last_reviewed_at: {today}
review_status: draft
---

# Plain requester {cid} ({who})

## Approved facts

- None.

## Observed facts

- None.

## Inferred facts

- None.
"""


def _append_bullet(text, section, bullet):
    heading = f'## {section}'
    i = text.find(heading)
    if i < 0: raise RuntimeError(f'section {section} missing')
    start = i + len(heading)
    j = text.find('\n## ', start); j = len(text) if j < 0 else j
    body = text[start:j].replace('\n- None.', '').rstrip('\n')
    if not body.strip(): body = '\n'          # an empty section keeps the blank line after its heading
    return text[:start] + body + '\n- ' + bullet + '\n\n' + text[j:].lstrip('\n')


def build_add_files(q):
    """{mirror path: new text} for the queued rows: the mirror text plus the picked bullets at the end of their section.
    Frontmatter is left alone: the add tool writes facts and new rows only, a changed property is a person's edit in Notion."""
    files = {}
    base = mirror()['files']
    for r in q:
        if r['target'] == 'person':
            _, pdir = person_profile(r['tenant'], r['customer'])
            tp = tenant_profile(r['tenant'])
            pdir = pdir or (tp['fm']['shared_people_from'] if tp and isinstance(tp['fm'].get('shared_people_from'), str) else r['tenant'])
            rel = f"tenants/{pdir}/people/plain-{r['customer']}.md"
            if rel not in files and rel not in base:
                who = ', '.join(x for x in (r.get('name'), r.get('role')) if x) or r['customer']
                files[rel] = PERSON_TEMPLATE.format(cid=r['customer'], tenant=pdir, role=r['role'], lang=r.get('language') or 'de', conf=r['confidence'], today=today, who=who)
        else:
            rel = f"tenants/{r['tenant']}/profile.md"
            if rel not in base: raise RuntimeError(f"tenant {r['tenant']} has no profile in the mirror")
        text = files.get(rel, base.get(rel))
        bullet = f"{r['text'].rstrip('.')}. (Source: {r['source']}; confidence: {r['confidence']}; reviewed: {today} by Tobias via desk)"
        files[rel] = _append_bullet(text, SECTIONS[r['section']], bullet)
    return files


def run_add_tool(files, write):
    """Ship the files to the box and run orbot's add tool there (it has the Notion key and the deployed code).
    Returns (exit code, output). The tool prints counts and classes, never fact text."""
    import io, tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for rel, text in sorted(files.items()):
            data = text.encode('utf-8'); info = tarfile.TarInfo(rel); info.size = len(data); info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    if not BOX_ADAPTER: raise RuntimeError('config.json has no sendEnvFile')
    flag = ' --write' if write else ''
    cmd = ("d=$(mktemp -d) && tar xzf - -C \"$d\" && "
           # the box's own .env names its support-memory folder (known issues and the route map for the tool's budget check)
           f"sudo bash -c 'cd {BOX_ADAPTER} && set -a && . ./.env && set +a && "
           f"node dist/cli/support-memory-notion-add.js --from '\"$d\"' --status reviewed --root \"$ORBOT_SUPPORT_MEMORY_DIR\"{flag}'; "
           "rc=$?; rm -rf \"$d\"; exit $rc")
    rc, out, err = box(cmd, stdin=buf.getvalue(), timeout=300)
    return rc, (out + ('\n' + err if err.strip() and rc else '')).strip()


def add_to_notion(dry_run=False):
    rows = load_queue(); q = [r for r in rows if r['state'] == 'queued']
    if not q: print('nothing queued'); return 0
    mir = fetch_mirror()                              # start from what OrBot reads now, never from an old copy
    if not mir.get('ok'): print(f"cannot read the mirror: {mir.get('reason')}. Nothing was written."); return 2
    try:
        files = build_add_files(q)
    except Exception as e:
        print(f'aborted before any write: {e}'); return 2
    try:
        rc, out = run_add_tool(files, write=False)
        print(f"dry run on mirror {mir.get('revision')} ({len(q)} facts, {len(files)} files):\n{out}")
        if rc:
            print('\nThe add tool refused the batch: nothing was written. Fix or drop the named row (memory.py drop <id>), or edit in Notion.'); return 2
        if dry_run: return 0
        rc, out = run_add_tool(files, write=True)
        print(f'\nwrite:\n{out}')
        landed = 'written:' in out
        if rc and not landed:
            print('\nNothing was written. The queue is unchanged.'); return 2
        if rc:
            print('\nRows landed, and the read after the write found a problem (the line above names the class). Stop here: a person repairs it in Notion '
                  '(orbot docs/operations/support-memory-notion-import.md, section 5). The queue is unchanged; a second run adds only what is missing.'); return 2
        for r in rows:
            if r['state'] == 'queued': r['state'] = 'added'; r['addedAt'] = today; r['revision'] = mir.get('revision')
        save_queue(rows)
        print(f"\n{len(q)} facts are in Notion as reviewed. OrBot reads them at the next sync, within 5 minutes. No PR to merge.")
        return 0
    except Exception as e:
        print(f'aborted: {e}'); return 2


# ---------------------------------------------------------------- cli
if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(0)
    cmd, rest = a[0], a[1:]
    if cmd in ('pull', 'pr'): deskconfig.need(ORBOT)   # both work in the clone; show and lookup read it when it is there
    if cmd in ('pr', 'sync'): deskconfig.key('botRepo')
    if cmd == 'pull': r = pull(); print(json.dumps(r)); sys.exit(0 if r['ok'] else 1)
    if cmd == 'show': sys.exit(show(rest[0], '--json' in rest) if rest else 2)
    if cmd == 'lookup':
        kv = {rest[i][2:].replace('-', '_'): rest[i + 1] for i in range(0, len(rest) - 1, 2) if rest[i].startswith('--')}
        print(json.dumps(lookup(kv.get('company'), kv.get('customer'), kv.get('tenant_field'), kv.get('domain'), kv.get('email'), kv.get('title')), indent=1, ensure_ascii=False)); sys.exit(0)
    if cmd == 'propose': sys.exit(propose(rest))
    if cmd == 'queue': list_queue(); sys.exit(0)
    if cmd == 'drop': drop(rest[0]); sys.exit(0)
    if cmd == 'sync': print(json.dumps({'changed': sync(), **queue_status()})); sys.exit(0)
    if cmd in ('add', 'pr'): sys.exit(add_to_notion('--dry-run' in rest))
    print(__doc__); sys.exit(2)
