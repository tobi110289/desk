#!/usr/bin/env python3
"""scan.py — keeps private data out of this public repo. pre-commit runs it on the staged lines, pre-push on every
commit about to leave (added lines, messages, author and committer mail).

The words to refuse (customer names, teammates, company ids) are private themselves, so they live outside the repo:
~/orbit-scratch/desk/public-denylist.txt, one regex per line, `#` for comments. No file, no push: the check fails closed.
Built in here: real ticket numbers (the repo carries only the neutral T-1xx / ORB-2xx), vendor ids, mail addresses
that are no placeholder, and anything that looks like a key."""
import os, re, subprocess, sys

DENY = os.path.expanduser('~/orbit-scratch/desk/public-denylist.txt')
BUILT_IN = [
    (r'\bT-(?!1\d\d\b)\d{3,4}\b|\bORB(?:IT)?-(?!2\d\d\b)\d{3,4}\b', 'a real ticket number'),
    (r'\b(?:w|th|c|co|te)_01[A-Z0-9]{20,}\b', 'a Plain id'),
    (r'\bpwa_[a-f0-9]{16,}\b', 'an a2a task id'),
    (r'\b[UCD]0[A-Z0-9]{8,10}\b', 'a Slack id'),
    (r'[A-Za-z0-9._%+-]+@(?!(?:[a-z0-9-]+\.)*(?:example|example\.com|anthropic\.com|users\.noreply\.github\.com)\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', 'a mail address'),
    (r'(?i)(?:api[_-]?key|token|secret|password|bearer)\s*[:=]\s*["\'][A-Za-z0-9_\-]{16,}["\']|sk-[A-Za-z0-9]{20,}|gh[pous]_[A-Za-z0-9]{20,}|xox[bp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}', 'a credential'),
]
OK_MAIL = re.compile(r'(users\.noreply\.github\.com|noreply@anthropic\.com)$')


def rules():
    if not os.path.exists(DENY):
        sys.exit(f'public-repo check: {DENY} is missing, so nothing can be checked. Refused.')
    own = [(l.strip(), 'the private denylist') for l in open(DENY) if l.strip() and not l.startswith('#')]
    return [(re.compile(p), why) for p, why in BUILT_IN + own]


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout


def check(text, where, found, compiled):
    for n, line in enumerate(text.splitlines(), 1):
        for rx, why in compiled:
            m = rx.search(line)
            if m: found.append(f'{where}:{n}: {why}: …{line[max(0, m.start() - 30):m.end() + 30].strip()}…')


def added(diff):
    return '\n'.join(l[1:] for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))


def main():
    compiled, found = rules(), []
    if sys.argv[1:] == ['staged']:
        check(added(git('diff', '--cached', '-U0')), 'staged', found, compiled)
        mail = git('config', 'user.email').strip()
        if not OK_MAIL.search(mail): found.append(f'author mail {mail}: set the GitHub noreply address (git config user.email)')
    else:
        for line in sys.stdin:   # pre-push: <local ref> <local sha> <remote ref> <remote sha>
            ref, sha, _, remote = line.split()
            if set(sha) == {'0'}: continue
            if not ref.endswith('/main'): found.append(f'{ref}: only main goes to the public repo')
            span = sha if set(remote) == {'0'} else f'{remote}..{sha}'
            for c in git('rev-list', span).split():
                who = git('show', '-s', '--format=%ae%n%ce', c).split()
                found += [f'{c[:7]}: commit mail {m}: use the GitHub noreply address' for m in who if not OK_MAIL.search(m)]
                check(git('show', '-s', '--format=%B', c), f'{c[:7]} message', found, compiled)
                check(added(git('show', '-U0', '--format=', c)), c[:7], found, compiled)
    if found:
        print('public-repo check refused this:\n  ' + '\n  '.join(found[:40]), file=sys.stderr)
        sys.exit(1)


main()
