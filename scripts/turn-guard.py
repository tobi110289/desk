#!/usr/bin/env python3
"""Stop hook: a reply may not end with a menu of open choices in prose.

Why (2026-11-09, all desk transcripts): "one decision at a time, on the pane, never as a menu in chat" was asked 25
times over five months; five memories and a batch rule did not stop it. So the turn is refused while its last reply
lists two or more open choices, and the model is told to put each one on the desk (desk_card) or ask it alone with
AskUserQuestion. Exit 2 blocks the stop and hands stderr to the model; stop_hook_active true lets the retry through.

Register in settings.json: Stop, command `python3 <path>/turn-guard.py`. `--test` runs the self-checks.
"""
import json
import re
import sys

CHOICE_LINE = re.compile(r'^\s*(?:[-*•]|\d+[.)]|[a-dA-D][.)]|\(?[a-dA-D]\))\s+.*\?\s*$', re.M)
OPTION_PAIR = re.compile(r'\boption\s+(?:a|1)\b.*\boption\s+(?:b|2)\b', re.I | re.S)
EITHER_OR = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s+.{0,120}\b(?:or|oder)\b.{0,120}\?\s*$', re.M)


def last_assistant_text(payload):
    text = payload.get('last_assistant_message')
    if isinstance(text, str) and text.strip():
        return text
    path = payload.get('transcript_path')
    if not path:
        return ''
    found = ''
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tail = f.tell()
            f.seek(max(0, size - 400_000))
            lines = f.read().decode('utf-8', 'ignore').splitlines()
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get('type') != 'assistant' or r.get('isSidechain'):
                continue
            c = (r.get('message') or {}).get('content')
            parts = [b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text'] if isinstance(c, list) else [c or '']
            text = '\n'.join(parts).strip()
            if text:
                found = text
                break
    except Exception:
        return ''
    return found


def menu_size(text):
    """How many open choices the reply ends with; 0 when it ends with none."""
    tail = text[-2500:]
    choices = len(CHOICE_LINE.findall(tail))
    if OPTION_PAIR.search(tail):
        choices = max(choices, 2)
    return choices


def main():
    if '--test' in sys.argv:
        return test()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get('stop_hook_active'):
        return 0
    n = menu_size(last_assistant_text(payload))
    if n < 2:
        return 0
    sys.stderr.write(f'Not finished: the reply ends with {n} open choices in prose. Put each one to the person on its own: '
                     'on the desk pane as a card (desk_card) when the choice belongs to a card, otherwise one AskUserQuestion at a time, '
                     'with one or two sentences of context and the recommended option first. Then end the turn.\n')
    return 2


def test():
    assert menu_size('Done. The PR is green and merged.') == 0
    assert menu_size('Two things need you:\n1. Should Luna send the draft now?\n2. Do we keep the old flag?') == 2
    assert menu_size('- Option A: ship today\n- Option B: wait for staging\nWhich one?') >= 2
    assert menu_size('One question: do we keep the flag?') == 0
    assert menu_size('a) send now?\nb) wait?\nc) drop?') == 3
    assert menu_size('Is that an active tenant?') == 0
    print('turn-guard: 6 checks pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
