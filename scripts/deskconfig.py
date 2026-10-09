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
TICKET_OWNER = CFG.get('ticketOwner') or 'the ticket owner'
