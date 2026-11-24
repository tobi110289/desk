#!/usr/bin/env python3
"""Stop hook: append what the session spent on a desk card to the desk's decision log.

Why (2026-11-09, all desk history): briefs log their cost (47 USD so far, 56 % of it never met a pick) and the pane's
picks log their class and time, but the session's own work on a card was invisible. This reads the transcript since
its last stop, sums the model usage of the turn, names the card the turn worked on (the last desk_card or desk_close
call), and appends one line to decisions.log.jsonl. Runs async; never blocks.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402
DESK = deskconfig.DESK
CURSORS = f'{DESK}/cache/session-cost'


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    path = payload.get('transcript_path'); sid = payload.get('session_id') or 'unknown'
    if not path or not os.path.exists(path):
        return 0
    os.makedirs(CURSORS, exist_ok=True)
    cur = f'{CURSORS}/{re.sub(r"[^A-Za-z0-9_-]", "_", sid)}.json'
    try:
        start = int(json.load(open(cur)).get('offset') or 0)
    except Exception:
        start = 0
    usage = {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}; turns = 0; card = None; tools = 0; last_at = None
    with open(path, 'rb') as f:
        size = f.seek(0, 2)
        if start > size:
            start = 0
        f.seek(start)
        for raw in f:
            try:
                r = json.loads(raw)
            except Exception:
                continue
            if r.get('type') != 'assistant' or r.get('isSidechain'):
                continue
            m = r.get('message') or {}; u = m.get('usage') or {}
            if u:
                turns += 1
                usage['input'] += int(u.get('input_tokens') or 0); usage['output'] += int(u.get('output_tokens') or 0)
                usage['cacheRead'] += int(u.get('cache_read_input_tokens') or 0); usage['cacheWrite'] += int(u.get('cache_creation_input_tokens') or 0)
            last_at = r.get('timestamp') or last_at
            for b in m.get('content') or []:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    tools += 1
                    if b.get('name') in ('mcp__desk__desk_card', 'mcp__desk__desk_close'):
                        card = (b.get('input') or {}).get('card_id') or card
        end = f.tell()
    json.dump({'offset': end}, open(cur, 'w'))
    if turns == 0:
        return 0
    with open(f'{DESK}/decisions.log.jsonl', 'a') as out:
        out.write(json.dumps({'at': last_at, 'kind': 'session-turn', 'sessionId': sid, 'cardId': card, 'turns': turns, 'toolCalls': tools, 'usage': usage}, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
