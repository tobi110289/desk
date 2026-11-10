#!/usr/bin/env python3
"""plain-note.py <th_…> --file <note.md> [--dry] — post one internal note (never customer-visible) on a Plain thread.

2026-11-09: the tool the unattended validation (desk-execute.py, option "Validate and draft (unattended)") posts its
note with; the headless run has no Plain MCP. Under a desk decision (DESK_DECISION=<record>) it refuses unless the
record's option is that validation and the note opens with the unattended line, so a background run cannot pass off
its note as a person's. It writes a receipt (<record>.note) the executor checks before it calls the run done.

--dry, or DESK_PLAIN_DRY=1 (set by desk-execute.py --rehearse): print what would be posted, write nothing to Plain.
Exit 0 posted (or dry), 2 usage, 3 refused, 1 Plain error.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskplain  # noqa: E402


def main(argv):
    if '-h' in argv or '--help' in argv or len(argv) < 3 or '--file' not in argv:
        print(__doc__); return 2
    thread = argv[0]
    path = argv[argv.index('--file') + 1]
    dry = '--dry' in argv or deskplain.dry_env()
    if not thread.startswith('th_'):
        print('refused: give the thread id (th_…)', file=sys.stderr); return 2
    text = open(path, encoding='utf-8').read().strip()
    rec_path = os.environ.get('DESK_DECISION')
    if rec_path:
        try:
            rec = json.load(open(rec_path))
        except Exception as e:
            print(f'refused: the decision record is unreadable ({e})', file=sys.stderr); return 3
        if rec.get('option') != deskplain.VALIDATE:
            print(f"refused: the picked option is {rec.get('option')!r}, which writes no note", file=sys.stderr); return 3
        if (rec.get('thread') or thread) != thread:
            print('refused: this is not the thread of the decision', file=sys.stderr); return 3
        if (text.splitlines() or [''])[0].strip() != deskplain.UNATTENDED_LINE:
            print(f'refused: the note must open with the line "{deskplain.UNATTENDED_LINE}"', file=sys.stderr); return 3
    try:
        out = deskplain.add_note(thread, text, dry=dry)
    except Exception as e:
        print(f'note failed: {e}', file=sys.stderr); return 1
    out.update({'thread': thread, 'at': deskplain.now_iso()})
    if rec_path:
        with open(f'{rec_path}.note', 'w') as f:
            json.dump(out, f, ensure_ascii=False)
    print(('dry, would post: ' if out['dry'] else 'posted: ') + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
