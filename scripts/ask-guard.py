#!/usr/bin/env python3
"""PreToolUse guard for AskUserQuestion: one decision at a time, context first, a recommendation, plain words.

Why a hook and not a memory (2026-11-09, from 30 days of transcripts): "one decision at a time" had three memories and
the person asked for it after each one; "context and recommendation first" nine times after two memories; plain words
eleven times. A rule read at answer time comes too late. The guard refuses the question before it reaches the person,
and the refusal tells the model how to ask instead. A false refusal costs one rewrite; a missed one costs the reader.

Register in settings.json: PreToolUse, matcher AskUserQuestion, command `python3 <path>/ask-guard.py`.
Exit 2 blocks the call and hands stderr to the model. `--test` runs the self-checks.
"""
import json
import re
import sys

QUESTION_MIN = 40        # a question with no story is a label, not a decision
OPTION_NOTE_MIN = 15     # each option says what happens when picked
URL = re.compile(r'\bhttps?://\S+')

# The plain-words rules of the desk pane (mod/hooks/plainWords.ts), ported. Thread refs, PR numbers and dates stay.
RULES = [
    ('backtick', re.compile(r'`[^`\n]*`?')),
    ('file path', re.compile(r'(?:[\w.~-]+/)+[\w-]+\.[a-z][a-z0-9]{0,4}\b', re.I)),
    ('file name', re.compile(r'\b[\w-]+\.(?:tsx?|jsx?|mjs|py|json|jsonl|md|ya?ml|sql|sh|toml)\b', re.I)),
    ('UUID', re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)),
    ('id or hash', re.compile(r'\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b', re.I)),
    ('code name', re.compile(r'\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*){2,}\b')),
    ('code name', re.compile(r'\b(?:[A-Z][a-z0-9]+){3,}\b')),
    ('code name', re.compile(r'\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b')),
    ('function call', re.compile(r'\b[A-Za-z_]\w*\(\)')),
]


def code_words(text):
    prose = URL.sub(' ', text or '')
    found = []
    for what, pat in RULES:
        for m in pat.finditer(prose):
            hit = m.group(0)
            if hit not in [f[1] for f in found]:
                found.append((what, hit))
    return found


def problems(tool_input):
    qs = (tool_input or {}).get('questions') or []
    out = []
    if len(qs) > 1:
        out.append(f'{len(qs)} questions in one call; ask one decision at a time and wait for the answer')
    for q in qs[:1] if len(qs) > 1 else qs:
        text = q.get('question') or ''
        if len(text.strip()) < QUESTION_MIN:
            out.append('the question has no context; say in one or two sentences what the case is and what changed, then ask')
        opts = q.get('options') or []
        if not q.get('multiSelect') and opts and not re.search(r'\(recommended\)\s*$', opts[0].get('label') or '', re.I):
            out.append('the first option is not marked "(Recommended)"; put the option you recommend first and say why in its description')
        for i, o in enumerate(opts, 1):
            if len((o.get('description') or '').strip()) < OPTION_NOTE_MIN:
                out.append(f'option {i} does not say what happens when it is picked')
        fields = [('question', text)] + [(f'option {i}', (o.get('label') or '') + ' ' + (o.get('description') or '')) for i, o in enumerate(opts, 1)]
        for name, value in fields:
            words = code_words(value)
            if words:
                out.append(f'{name} has code words: ' + ', '.join(f'"{h}" ({w})' for w, h in words[:4]) + '; use product words a tired reader follows, the identifiers go into the file or the reply')
    return out


def main():
    if '--test' in sys.argv:
        return test()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get('tool_name') != 'AskUserQuestion':
        return 0
    found = problems(payload.get('tool_input'))
    if not found:
        return 0
    sys.stderr.write('Not asked. ' + '. '.join(found) + '.\n'
                     'Ask again as one decision: one or two plain sentences of context in the question, the recommended option first '
                     'labelled "(Recommended)", one line per option on what happens, no paths or code names.\n')
    return 2


def test():
    good = {'questions': [{'question': 'The customer asked twice and the fix has been on staging since Tuesday. Send the answer as Luna now?',
                           'header': 'Send', 'multiSelect': False,
                           'options': [{'label': 'Send it (Recommended)', 'description': 'Luna sends the drafted text now; the thread stays open until the customer confirms'},
                                       {'label': 'Edit then send', 'description': 'You change the text first, then it goes out'}]}]}
    assert problems(good) == [], problems(good)
    two = {'questions': [good['questions'][0], good['questions'][0]]}
    assert any('one decision' in p for p in problems(two))
    bare = {'questions': [{'question': 'A or B?', 'header': 'Pick', 'multiSelect': False,
                           'options': [{'label': 'A', 'description': ''}, {'label': 'B', 'description': ''}]}]}
    ps = problems(bare)
    assert any('no context' in p for p in ps) and any('(Recommended)' in p for p in ps) and any('option 1 does not say' in p for p in ps), ps
    code = {'questions': [{'question': 'The brief in desk-prepare.py recommends handleSelectionChange() for th_01ABCDEF12 on the shipment page. Change it?',
                           'header': 'Code', 'multiSelect': False,
                           'options': [{'label': 'Yes (Recommended)', 'description': 'The rule changes in the prepare script and the next gather uses it'}]}]}
    ps = problems(code)
    assert any('code words' in p for p in ps), ps
    multi = {'questions': [{'question': 'Three workers are idle after the review round. Which of them should get their babysit line now?',
                            'header': 'Workers', 'multiSelect': True,
                            'options': [{'label': 'Worker one', 'description': 'It addresses the review on its open PR'},
                                        {'label': 'Worker two', 'description': 'It fixes the red check on its PR'}]}]}
    assert problems(multi) == [], problems(multi)
    print('ask-guard: 5 checks pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
