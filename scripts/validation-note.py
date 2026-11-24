#!/usr/bin/env python3
"""validation-note.py <th_…> --file <note.md> --verdict <v> --readiness <r> [--pass orbot:<id>] [--dry] — record one
validation of OrBot's pass on the thread's ticket record (its Notion page), next to the pass it judges.

2026-11-09: the tool the unattended validation (desk-execute.py, option "Validate and draft (unattended)") writes its
note with; the headless run has no MCP. 2026-11-15 (orbot #984): the note is no longer a Plain note. It goes through
the one writer, agent-loop jev/plain.py record_validation (POST ticket-validation over ssh), whole, with no part split.
--verdict: confirmed | partially-confirmed | refuted | stale | already-shipped. --readiness: ready-for-agent | ready-for-human | needs-info
| wontfix; agent-ready is ready-for-agent. The judged pass is the one the executor wrote to the run's pass file (the
record's `passSection`); without a decision record, give --pass.

Under a desk decision (DESK_DECISION=<record>) it refuses unless the record's option is that validation and the note
opens with the unattended line, so a background run cannot pass off its note as a person's. It writes a receipt
(<record>.note) the executor checks before it calls the run done.

--dry, or DESK_PLAIN_DRY=1 (set by desk-execute.py --rehearse): print what would be recorded, write nothing.
Exit 0 recorded (or dry), 2 usage, 3 refused, 4 too large (shorten the note), 5 the judged pass is not on the page,
1 any other answer.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402
deskconfig.forward(__file__)
import deskplain  # noqa: E402

sys.path.append(deskconfig.JEV)
READINESS = ('ready-for-agent', 'ready-for-human', 'needs-info', 'wontfix')


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def main(argv):
    if '-h' in argv or '--help' in argv or len(argv) < 3 or '--file' not in argv:
        print(__doc__); return 2
    import plain
    thread = argv[0]
    path = opt(argv, '--file')
    verdict, readiness, judged = opt(argv, '--verdict'), opt(argv, '--readiness'), opt(argv, '--pass')
    dry = '--dry' in argv or deskplain.dry_env()
    if not thread.startswith('th_'):
        print('refused: give the thread id (th_…)', file=sys.stderr); return 2
    if plain.api_verdict(verdict) not in plain.VERDICTS or readiness not in READINESS:
        print(f"refused: --verdict one of {', '.join(plain.VERDICTS)} and --readiness one of {', '.join(READINESS)}", file=sys.stderr); return 2
    text = open(path, encoding='utf-8').read().strip()
    rec_path = os.environ.get('DESK_DECISION')
    run = None
    if rec_path:
        try:
            rec = json.load(open(rec_path))
        except Exception as e:
            print(f'refused: the decision record is unreadable ({e})', file=sys.stderr); return 3
        if rec.get('option') != deskplain.VALIDATE:
            print(f"refused: the picked option is {rec.get('option')!r}, which writes no validation", file=sys.stderr); return 3
        if (rec.get('thread') or thread) != thread:
            print('refused: this is not the thread of the decision', file=sys.stderr); return 3
        if (text.splitlines() or [''])[0].strip() != deskplain.UNATTENDED_LINE:
            print(f'refused: the note must open with the line "{deskplain.UNATTENDED_LINE}"', file=sys.stderr); return 3
        judged = rec.get('passSection') or judged   # the pass the executor wrote to the run's pass file
        run = f"{rec.get('cardId')}:{rec.get('startedAt')}"   # one run of one pick: a retry of it stores nothing twice
    if not judged:
        print('refused: no judged pass (--pass orbot:<id>, or a decision record with passSection)', file=sys.stderr); return 2
    agent_ready = readiness == 'ready-for-agent'
    out = {'dry': dry, 'write': 'validation', 'chars': len(text), 'firstLine': text.splitlines()[0] if text else '',
           'verdict': plain.api_verdict(verdict), 'agentReady': agent_ready, 'pass': judged}
    if not dry:
        # orbot #1003 (2026-11-18): a validation no person read carries its own author, so the loop reads that from
        # the record and no longer from the note's first line. The key keeps 'desk': a retry of an old run stores nothing twice.
        unattended = (text.splitlines() or [''])[0].strip() == deskplain.UNATTENDED_LINE
        key = plain.validation_key('desk', run or hashlib.sha256(text.encode()).hexdigest()[:16], thread, judged)
        try:
            r = plain.record_validation(thread, verdict, agent_ready, judged, text, 'desk-unattended' if unattended else 'desk', key)
        except ValueError as e:
            print(f'refused: {e}', file=sys.stderr); return 2
        out.update({'result': r['result'], 'section': r['section'], 'status': r['status']})
        if r['result'] not in ('queued', 'duplicate'):
            why = r.get('message') or r.get('reason') or r['result']
            if r['result'] == 'too_large' and r.get('rendered'):
                why = f"{r['rendered']['size']} {r['rendered']['unit']} as the {r['rendered']['reader']} reader gets it; the limit is {r['rendered']['limit']}"
            print(f"not recorded ({r['result']}): {why}", file=sys.stderr)
            return {'too_large': 4, 'pass_not_found': 5}.get(r['result'], 1)
    out.update({'thread': thread, 'at': deskplain.now_iso()})
    if rec_path:
        with open(f'{rec_path}.note', 'w') as f:
            json.dump(out, f, ensure_ascii=False)
    print(('dry, would record: ' if dry else 'recorded: ') + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
