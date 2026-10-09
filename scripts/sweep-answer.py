#!/usr/bin/env python3
"""sweep-answer.py <report.md> <Dx> <who> <answer…>

Move one decision from the sweep report's `## Decisions needed` to `## Answered since`
(strikethrough question, bold answer with who + date), the way the sentry-sweep report
format keeps answered decisions. Replace-don't-edit: temp file + rename.
"""
import datetime, os, re, sys, tempfile

if len(sys.argv) < 5:
    sys.exit(__doc__)
path, did, who = sys.argv[1], sys.argv[2], sys.argv[3]
answer = ' '.join(sys.argv[4:]).strip()
path = os.path.expanduser(path)
text = open(path).read()
today = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')

def section_span(name):
    m = re.search(r'^## ' + re.escape(name) + r'\s*\n', text, re.M)
    if not m:
        sys.exit(f'no section "## {name}" in {path}')
    start = m.end()
    n = re.search(r'^## ', text[start:], re.M)
    end = start + n.start() if n else len(text)
    return start, end

ds, de = section_span('Decisions needed')
lines = text[ds:de].split('\n')
hit = None
for i, line in enumerate(lines):
    if re.match(r'^\d+\.\s+\*\*', line) and re.search(r'\b' + re.escape(did) + r'\b', line):
        hit = i
        break
if hit is None:
    sys.exit(f'{did} not found under "## Decisions needed" in {path}')
question = re.sub(r'^\d+\.\s+', '', lines.pop(hit)).strip()
# 2026-09-28: decisions carry indented sub-bullets since 09-25 (context for the owners); take them along, or they
# stay orphaned under `## Decisions needed` and the desk keeps a headless card.
detail = []
while hit < len(lines) and re.match(r'^\s+\S', lines[hit]):
    detail.append(lines.pop(hit))
question = re.sub(r'\s*@\w+\??', '', question).rstrip()          # drop the @owner tags
# renumber what stays
k = 0
for i, line in enumerate(lines):
    if re.match(r'^\d+\.\s+', line):
        k += 1
        lines[i] = re.sub(r'^\d+\.', f'{k}.', line, count=1)
new_decisions = '\n'.join(lines)
if not new_decisions.strip():
    new_decisions = '\n(none open)\n\n'
text = text[:ds] + new_decisions + text[de:]

as_, ae = section_span('Answered since')
body = text[as_:ae]
body = re.sub(r'^\(none yet\)\s*\n', '', body, flags=re.M)
existing = len(re.findall(r'^\d+\.\s+', body, re.M))
row = f'{existing + 1}. ~~{question}~~ **@{who}, {today}:** {answer}\n'
if detail:
    row += '\n'.join(detail) + '\n'
body = body.rstrip('\n') + ('\n' if body.strip() else '') + row + '\n'
text = text[:as_] + '\n' + body.lstrip('\n') + text[ae:]

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.sweep-answer-')
with os.fdopen(fd, 'w') as f:
    f.write(text)
os.replace(tmp, path)
print(f'{did} → Answered since ({who}, {today}): {answer[:80]}')
