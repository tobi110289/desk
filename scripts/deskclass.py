"""deskclass.py — which kind of work an option picked on the pane needs (2026-10-23).

One table, read by desk-decide.py (to label the record) and desk-execute.py (to run it). The labels are the lanes'
fixed option sets from CARDS.md and desk-prepare.py OPTIONS; an unknown label needs the session.

  script   no model: a send as Luna, a close, a drop, a defer with the trigger the brief proposed
  model    a headless run with the brief, the recipe and the one option (phase 2; until then the session takes it)
  session  the person or the session: an edit, a review, a validation, an answer, a command shown first
"""

SEND = {'Send it', 'Reply with a status'}
SCRIPT = SEND | {'I sent it already', 'Nobody to tell (mark done)', 'Drop', 'Drop (unpin)', 'Drop (accepted risk, recorded)',
                 'Close the loop (nothing owed)'}
DEFER = {'Defer', 'Defer until a trigger', 'Defer until the deploy lands'}
MODEL = {'Hand to a worker', 'Hand the rework to its agent', 'Hand it to agents', 'Hand to a worker / ticket',
         'Go (agent runs it now)', 'Close the PR',
         # 2026-11-03: single SEND and TICKET cards. A line goes to its worker's terminal, a ticket file gets its Status:
         # both are Bash work with the lane recipe. Notion moves (push, archive, ticket it) need the Notion MCP: session.
         'Send as staged', 'Drop the line', 'Ready for an agent', 'Ready for a person', "Won't fix",
         'Close (PR landed)',
         # 2026-11-09: FAST cards. "Claim + validate now" was picked 28 of 43 times, took about five minutes in the
         # session, and the card came back a second time for the reply (31 cards scored twice). This one claims,
         # validates, notes and drafts in one background run; the card returns once with "Send it" first.
         'Validate and draft (unattended)'}
# everything else: 'Edit then send', 'Review it now', 'Claim + validate now' (the session path, also when the
# unattended run failed or the thread has no Hermes brief), 'Draft the reply', 'Start it now',
# 'Answer now', 'Answer', 'Show me the command first', 'Drop (reply as human: declined)', 'Close as read',
# 'Reply with a note' (the a2a answers carry the person's words), …

# 2026-10-23 phase 2: the model class runs as a headless claude -p in desk-execute.py (run_model)
MODEL_BUILT = True


def classify(brief, label):
    if label == 'Drop' and brief.get('lane') == 'TICKET':
        return 'model'   # a local ticket file gets Status: wontfix, not just a closed card
    if label in SCRIPT:
        if label in SEND and not (brief.get('replyReady') and (brief.get('draft') or '').strip()):
            return 'session'   # a send without a checked draft is composed in the session
        if label == 'Close the loop (nothing owed)' and not brief.get('resolved'):
            return 'session'   # closing a promise that is not shown resolved needs a note the brief did not write
        return 'script'
    if label in DEFER:
        return 'script' if (brief.get('deferTrigger') or '').strip() and (brief.get('deferTrigger') or '').strip().lower() != 'later' else 'session'
    if label in MODEL:
        return 'model' if MODEL_BUILT else 'session'
    return 'session'
