#!/usr/bin/env python3
"""memory.py — the desk's window onto the orbot support memory (tenant + requester profiles, known issues).

Reads come from the local checkout ~/orbot/support-memory (git pull at desk pre-flight); writes never touch the
canonical files: they queue as proposals here and go up as one reviewed orbot PR (policy.md rule 3).

  memory.py pull                          fast-forward ~/orbot to origin/main, print the head (desk.py does this)
  memory.py show <T-ref|th_id> [--json]   resolve the thread's customer/company and print the memory block
  memory.py lookup --company co_… --customer c_… [--tenant-field x] [--domain d] [--email e]   JSON for scripts
  memory.py propose --ref T-… --tenant <id> [--person c_… --name "…" --role "…" --language de]
                    --section approved|observed|inferred --confidence low|medium|high --source "…" "<fact>"
  memory.py queue                         list queued proposals (state queued / pr:<n>)
  memory.py drop <id>                     drop a queued proposal
  memory.py pr                            apply every queued proposal on a branch in ~/orbot, validate, push, open the PR
  memory.py sync                          mark pr:<n> proposals merged once the PR merged (desk.py does this)

Identity: Plain company id → plain-company-tenant-map.json → tenant dir; else the thread's tenant_id field; else the
customer's domain against tenant aliases. No match means no memory, never a guess (policy: ambiguous → absent).
"""
import json, os, re, sys, subprocess, datetime, hashlib

HOME = os.path.expanduser('~')
ORBOT = f'{HOME}/orbot'
SM = f'{ORBOT}/support-memory'
DESK = f'{HOME}/orbit-scratch/desk'
QUEUE = f'{DESK}/memory-proposals.jsonl'
CACHE = f'{DESK}/memory-cache.json'          # thread id → customer/company identity (a thread's customer never changes)
import deskconfig
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


def pull():
    """Fast-forward main. A dirty tree or a non-main branch is left alone (memory.py pr may have a branch checked out)."""
    try:
        branch = sh(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
        dirty = [l for l in sh(['git', 'status', '--porcelain']).splitlines() if not l.startswith('??')]
        if branch != 'main' or dirty:
            return {'ok': False, 'head': head(), 'reason': f'branch {branch}' if branch != 'main' else 'dirty tree'}
        sh(['git', 'pull', '-q', '--ff-only', 'origin', 'main'], timeout=40, check=True)
        return {'ok': True, 'head': head()}
    except Exception as e:
        return {'ok': False, 'head': head(), 'reason': str(e)[:120]}


# ---------------------------------------------------------------- profile files
def parse_profile(path):
    """Frontmatter (the YAML subset the orbot parser accepts: scalars and '- item' lists) + body sections."""
    try: text = open(path, encoding='utf-8').read()
    except Exception: return None
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
    return {'path': os.path.relpath(path, ORBOT), 'fm': fm, 'sections': sections, 'title': title.group(1).strip() if title else '', 'raw': text}


def company_map():
    try: return {m['plain_company_id']: m['orbit_tenant_id'] for m in json.load(open(f'{SM}/plain-company-tenant-map.json'))['mappings']}
    except Exception: return {}


def tenants():
    try: return sorted(d for d in os.listdir(f'{SM}/tenants') if os.path.isfile(f'{SM}/tenants/{d}/profile.md'))
    except Exception: return []


def tenant_profile(t):
    return parse_profile(f'{SM}/tenants/{t}/profile.md')


def _norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def resolve(company=None, customer=None, tenant_field=None, domain=None, email=None):
    """Tenant id or None, with the rule that decided it."""
    cm = company_map()
    if company and company in cm: return cm[company], f'company {company}'
    ts = tenants()
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
        path = f'{SM}/tenants/{t}/people/plain-{customer}.md'
        if os.path.isfile(path): return parse_profile(path), t
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
    sys.path.insert(0, os.path.expanduser('~/orbit-scratch/agent-loop/jev')); import plain   # the one Plain reader
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
    return {'tenant': tenant, 'rule': rule, 'tenantProfile': tp and {'path': tp['path'], 'fm': tp['fm'], 'sections': tp['sections'], 'title': tp['title']},
            'person': person and {'path': person['path'], 'fm': person['fm'], 'sections': person['sections'], 'title': person['title'], 'tenantDir': ptenant},
            'knownIssues': known_issues(title) if tenant else [], 'files': files, 'summary': summary, 'head': head(), 'customer': customer}


def render(mem, ident=None):
    L = []
    if ident: L.append(f"## Support memory for {ident.get('ref')} · {ident.get('name') or '?'} <{ident.get('email') or '?'}> · company {ident.get('companyName') or ident.get('company') or '?'}")
    else: L.append('## Support memory')
    L.append(f"orbot {mem['head']} · " + (', '.join(mem['files']) if mem['files'] else 'no files') + f" · resolved by {mem['rule']}")
    if not mem['tenantProfile']:
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
        print(f'tenant {tenant} has no profile; create it in the orbot repo first (schemas/tenant-profile.schema.json)'); return 2
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
    s = queue_status(); print(f"{s['queued']} queued" + (f", oldest {s['oldest']}" if s['oldest'] else '') + (f", open PR #{s['openPr']}" if s['openPr'] else ''))


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


# ---------------------------------------------------------------- PR
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


def _bump_reviewed(text):
    return re.sub(r'^last_reviewed_at: .*$', f'last_reviewed_at: {today}', text, count=1, flags=re.M)


def make_pr():
    rows = load_queue(); q = [r for r in rows if r['state'] == 'queued']
    if not q: print('nothing queued'); return 0
    s = queue_status()
    if s['openPr']: print(f"PR #{s['openPr']} is still open: merge or close it first (memory.py sync)"); return 2
    p = pull()
    if not p['ok']: print(f"cannot start from a clean main: {p['reason']}"); return 2
    branch = f"support-memory/desk-{datetime.datetime.now().strftime('%Y%m%d-%H%M')}"
    sh(['git', 'checkout', '-q', '-b', branch, 'origin/main'], check=True)
    touched = set()
    try:
        for r in q:
            if r['target'] == 'person':
                _, pdir = person_profile(r['tenant'], r['customer'])
                tp = tenant_profile(r['tenant'])
                pdir = pdir or (tp['fm']['shared_people_from'] if tp and isinstance(tp['fm'].get('shared_people_from'), str) else r['tenant'])
                path = f"{SM}/tenants/{pdir}/people/plain-{r['customer']}.md"
                if not os.path.isfile(path):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    who = ', '.join(x for x in (r.get('name'), r.get('role')) if x) or r['customer']
                    open(path, 'w').write(PERSON_TEMPLATE.format(cid=r['customer'], tenant=pdir, role=r['role'], lang=r.get('language') or 'de', conf=r['confidence'], today=today, who=who))
            else:
                path = f"{SM}/tenants/{r['tenant']}/profile.md"
            text = open(path, encoding='utf-8').read()
            bullet = f"{r['text'].rstrip('.')}. (Source: {r['source']}; confidence: {r['confidence']}; reviewed: {today} by Tobias via desk)"
            text = _bump_reviewed(_append_bullet(text, SECTIONS[r['section']], bullet))
            open(path, 'w', encoding='utf-8').write(text); touched.add(os.path.relpath(path, ORBOT))
        # the orbot parser is the gate: every touched profile must parse and validate before the PR exists
        check = subprocess.run(['node', '--input-type=module', '-e', VALIDATE_JS, '--', *sorted(touched)], cwd=ORBOT, capture_output=True, text=True, timeout=120)
        if check.returncode: raise RuntimeError('validation failed:\n' + (check.stdout + check.stderr).strip())
        test = subprocess.run(['pnpm', 'exec', 'vitest', 'run', 'packages/shared/src/supportMemoryProfiles.test.ts'], cwd=ORBOT, capture_output=True, text=True, timeout=300)
        if test.returncode: raise RuntimeError('supportMemoryProfiles.test.ts failed:\n' + test.stdout[-1500:])
        refs = sorted({r['ref'] for r in q if r.get('ref')}); tns = sorted({r['tenant'] for r in q})
        title = f"support-memory: {len(q)} fact{'s' if len(q) != 1 else ''} from desk sittings ({', '.join(tns)})"
        body = ['Facts Tobias approved one by one in desk sittings (memory.py queue → this PR). Every bullet carries its source and confidence; requester files created here are `review_status: draft`.', '']
        for r in q: body.append(f"- **{r['tenant']}{'/' + r['customer'] if r.get('customer') else ''}** · {r['section']} ({r['confidence']}) · {r['text']} · _{r['source']}_")
        body += ['', 'Threads: ' + ', '.join(f'[{x}]({PLAIN_URL}{x})' if x.startswith('th_') else x for x in refs) if refs else '', '', 'Validation: node parse+validate on every touched profile, `pnpm exec vitest run packages/shared/src/supportMemoryProfiles.test.ts`.', '', '🤖 Generated with [Claude Code](https://claude.com/claude-code)']
        sh(['git', 'add', *sorted(touched)], check=True)
        sh(['git', 'commit', '-q', '-m', title + '\n\n' + '\n'.join(body[2:2 + len(q)]) + '\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'], check=True)
        sh(['git', 'push', '-q', '-u', 'origin', branch], timeout=90, check=True)
        url = sh(['gh', 'pr', 'create', '--repo', deskconfig.BOT_REPO, '--base', 'main', '--head', branch, '--title', title, '--body', '\n'.join(body)], timeout=90, check=True)
        n = url.rstrip('/').rsplit('/', 1)[-1]
        for r in rows:
            if r['state'] == 'queued': r['state'] = f'pr:{n}'; r['pr'] = url
        save_queue(rows)
        print(f"PR opened: {url} ({len(q)} facts, {len(touched)} files). Merge it yourself on orbot; the deploy pulls it onto the VPS.")
        return 0
    except Exception as e:
        print(f'aborted: {e}')
        return 2
    finally:
        sh(['git', 'checkout', '-q', '-f', 'main'])
        sh(['git', 'branch', '-D', branch]) if sh(['git', 'rev-parse', '--verify', '-q', f'refs/remotes/origin/{branch}']) == '' else None


VALIDATE_JS = r"""
import fs from "node:fs";
import { parseSupportMemoryMarkdown, validateSupportMemoryProfile } from "./packages/shared/src/supportMemoryProfiles.ts";
let bad = 0;
for (const p of process.argv.slice(2)) {
  try {
    const errs = validateSupportMemoryProfile(parseSupportMemoryMarkdown(fs.readFileSync(p, "utf8")));
    if (errs.length) { bad++; console.log(p + ": " + errs.join("; ")); }
  } catch (e) { bad++; console.log(p + ": " + (e && e.message ? e.message : e)); }
}
process.exit(bad ? 1 : 0);
"""


# ---------------------------------------------------------------- cli
if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(0)
    cmd, rest = a[0], a[1:]
    if cmd == 'pull': r = pull(); print(json.dumps(r)); sys.exit(0 if r['ok'] else 1)
    if cmd == 'show': sys.exit(show(rest[0], '--json' in rest) if rest else 2)
    if cmd == 'lookup':
        kv = {rest[i][2:].replace('-', '_'): rest[i + 1] for i in range(0, len(rest) - 1, 2) if rest[i].startswith('--')}
        print(json.dumps(lookup(kv.get('company'), kv.get('customer'), kv.get('tenant_field'), kv.get('domain'), kv.get('email'), kv.get('title')), indent=1, ensure_ascii=False)); sys.exit(0)
    if cmd == 'propose': sys.exit(propose(rest))
    if cmd == 'queue': list_queue(); sys.exit(0)
    if cmd == 'drop': drop(rest[0]); sys.exit(0)
    if cmd == 'sync': print(json.dumps({'changed': sync(), **queue_status()})); sys.exit(0)
    if cmd == 'pr': sys.exit(make_pr())
    print(__doc__); sys.exit(2)
