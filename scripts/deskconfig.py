"""deskconfig.py: the one resolver, where every desk file and folder is, and the ids that belong to one company.

Folders. Every desk script reads its paths from here and names no home path of its own. Each folder has an
environment variable; without it the default is the path on the Mac where the desk started, so that desk does not
change. On another machine set the variables (README, "Point the desk at other folders"):

  DESK_SCRATCH      the scratch root: local ticket folders (<feature>/issues), sweep folders   ~/orbit-scratch
  DESK_DIR          the desk folder: desk.json, cards.json, briefs, decisions, caches, config  <scratch>/desk
  DESK_CONFIG       the company ids (config.example.json lists every key)                       <desk>/config.json
  DESK_LOOP_DIR     the agent loop: state.json, sweep-cache.json                                <scratch>/agent-loop
  DESK_JEV_DIR      the loop helpers: plain.py (the one Plain reader), pr_health, desk_lanes      <loop>/jev
  DESK_ORBIT_REPO   the orbit clone                                                             ~/orbit
  DESK_ORBOT_REPO   the orbot clone (support memory)                                            ~/orbot
  DESK_SKILLS_DIR   the Claude skills folder (desk, plain-agent-loop)                           ~/.claude/skills
  DESK_HERMES_LOG   the Hermes feedback log                                                     <scratch>/hermes-feedback/LOG.md
  DESK_SWEEPS_DIR   the weekly Sentry sweep: known-issues.md and the run reports                <scratch>/sentry-sweeps
  DESK_A2A_ENV      the A2A hub token file                                                      ~/.orbit-a2a/config.env
  DESK_AWS_DIR      the AWS login caches                                                        ~/.aws

`python3 deskconfig.py` prints every path and whether it exists; `python3 deskconfig.py DESK` prints one (install.sh).

Fail loud (rule 5). A script that needs a folder, a file or a config key calls need() or key() at its start: when the
thing is missing, the script stops with one line that names it. A script that does not need it still runs. A missing
config file or key otherwise gives an empty value.
"""
import json, os, sys


def _path(var, default):
    return os.path.abspath(os.path.expanduser(os.environ.get(var) or default))


HOME = os.path.expanduser('~')
SCRATCH = _path('DESK_SCRATCH', '~/orbit-scratch')
DESK = _path('DESK_DIR', os.path.join(SCRATCH, 'desk'))
CONFIG = _path('DESK_CONFIG', os.path.join(DESK, 'config.json'))
LOOP = _path('DESK_LOOP_DIR', os.path.join(SCRATCH, 'agent-loop'))
JEV = _path('DESK_JEV_DIR', os.path.join(LOOP, 'jev'))
ORBIT_DIR = _path('DESK_ORBIT_REPO', '~/orbit')
ORBOT_DIR = _path('DESK_ORBOT_REPO', '~/orbot')
SKILLS = _path('DESK_SKILLS_DIR', '~/.claude/skills')
HERMES_LOG = _path('DESK_HERMES_LOG', os.path.join(SCRATCH, 'hermes-feedback', 'LOG.md'))
SWEEPS = _path('DESK_SWEEPS_DIR', os.path.join(SCRATCH, 'sentry-sweeps'))
A2A_ENV = _path('DESK_A2A_ENV', '~/.orbit-a2a/config.env')
AWS_DIR = _path('DESK_AWS_DIR', '~/.aws')
# the loop helpers (jev/loopstate.py) read the loop folder from this variable
os.environ.setdefault('AGENT_LOOP_DIR', LOOP)

# name, path, the variable that moves it
PATHS = [('scratch root', SCRATCH, 'DESK_SCRATCH'), ('desk folder', DESK, 'DESK_DIR'), ('config file', CONFIG, 'DESK_CONFIG'),
         ('loop folder', LOOP, 'DESK_LOOP_DIR'), ('loop helpers (jev)', JEV, 'DESK_JEV_DIR'),
         ('orbit clone', ORBIT_DIR, 'DESK_ORBIT_REPO'), ('orbot clone', ORBOT_DIR, 'DESK_ORBOT_REPO'),
         ('skills folder', SKILLS, 'DESK_SKILLS_DIR'), ('Hermes log', HERMES_LOG, 'DESK_HERMES_LOG'),
         ('sweep folder', SWEEPS, 'DESK_SWEEPS_DIR'), ('A2A token file', A2A_ENV, 'DESK_A2A_ENV'), ('AWS folder', AWS_DIR, 'DESK_AWS_DIR')]


def show(path):
    """A path as the person reads it: the home folder as ~ (card texts keep the form they had before the resolver)."""
    return '~' + path[len(HOME):] if path == HOME or path.startswith(HOME + os.sep) else path


class Missing(RuntimeError):
    """A folder, a file or a config key a script needs is not there."""


def _stop_or_raise(msg, stop):
    if stop:
        sys.exit(f'desk: {msg}')
    raise Missing(msg)


def need(path, what=None, stop=True):
    """The path, when it exists. Else stop the script with one line that names it (stop=False raises Missing, for
    library code whose caller decides). `what` defaults to the name in PATHS."""
    if os.path.exists(path):
        return path
    var = next((v for n, p, v in PATHS if p == path), None)
    name = what or next((n for n, p, v in PATHS if p == path), 'file')
    _stop_or_raise(f"{name} missing: {show(path)}" + (f" (set {var})" if var else ''), stop)


try: CFG = json.load(open(CONFIG))
except Exception: CFG = {}


def key(name, stop=True):
    """A config key a script needs. Missing or empty: stop with one line that names it (stop=False raises Missing)."""
    v = CFG.get(name)
    if v in (None, '', [], {}):
        _stop_or_raise(f"config key '{name}' missing in {show(CONFIG)}", stop)
    return v


PLAIN_THREAD = f"https://app.plain.com/workspace/{CFG.get('plainWorkspace', '')}/thread/"
REPO = CFG.get('repo', '')
BOT_REPO = CFG.get('botRepo', '')
A2A_HUB = CFG.get('a2aHub', '')
SEND_HOST = CFG.get('sendHost', '')
SEND_ENV_FILE = CFG.get('sendEnvFile', '')
ASK_PATTERN = CFG.get('askPattern') or r'human gates|policy|prioritis'
PEOPLE = CFG.get('people') or []
# 2026-10-22: the teammates by first name, for "the customer tagged a colleague"; `people` also holds customer contacts we wait on
TEAM = CFG.get('team') or []
# 2026-11-10: the first name of the person who runs the desk. A thread a teammate holds (Plain assignee, Slack takeover,
# their reply after the customer's) leaves the desk; one the person holds stays. Empty: nobody in TEAM is the person.
ME = (CFG.get('me') or '').strip()
NOTION_ME = (CFG.get('notionUser') or '').strip()   # the person's Notion user url (user://…); a ticket is theirs when it names it
COLLEAGUES = [p.strip() for p in TEAM if p.strip() and p.strip().lower() != ME.lower()]
# Slack user id -> teammate first name, so a Slack mention (<@U…>) in a customer message reads as that teammate
SLACK_USERS = CFG.get('slackUsers') or {}
# 2026-10-22: a thread whose requester writes from one of these mail domains is a colleague's thread (a Sentry auto-flag
# the loop filed under an engineer's address, an internal test), never a customer waiting for a first answer.
INTERNAL_DOMAINS = [d.lower().lstrip('@') for d in (CFG.get('internalDomains') or [])]
def is_internal(email):
    dom = (email or '').rsplit('@', 1)[-1].lower() if '@' in (email or '') else ''
    return bool(dom) and dom in INTERNAL_DOMAINS
TICKET_OWNER = CFG.get('ticketOwner') or 'the ticket owner'
# 2026-11-10: a new FAST thread with an unchecked Hermes brief is validated in the background before the person is asked
# (desk-prepare.py start_validation). Off unless config.json says autoValidate: true; at most autoValidateMax runs a day.
AUTO_VALIDATE = bool(CFG.get('autoValidate'))
AUTO_VALIDATE_MAX = int(CFG.get('autoValidateMax') or 8)


if __name__ == '__main__':
    if sys.argv[1:]:
        names = {'SCRATCH', 'DESK', 'CONFIG', 'LOOP', 'JEV', 'ORBIT_DIR', 'ORBOT_DIR', 'SKILLS', 'HERMES_LOG', 'SWEEPS', 'A2A_ENV', 'AWS_DIR'}
        if sys.argv[1] not in names:
            sys.exit(f"deskconfig.py: no path named {sys.argv[1]} (one of {', '.join(sorted(names))})")
        print(globals()[sys.argv[1]]); sys.exit(0)
    for name, path, var in PATHS:
        print(f"{name:20} {var:16} {'ok     ' if os.path.exists(path) else 'missing'} {path}")
