"""deskconfig.py — the ids and names that belong to one company, read from ~/orbit-scratch/desk/config.json.
The file is not in the repo; config.example.json lists every key. A missing file or key gives an empty value, so a
script that needs none of them still runs."""
import json, os

PATH = os.path.expanduser('~/orbit-scratch/desk/config.json')
try: CFG = json.load(open(PATH))
except Exception: CFG = {}

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
