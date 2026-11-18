#!/usr/bin/env python3
"""send-message.py <session-id> <text>: type the text into the session's terminal in its Superset workspace and press Enter.

A session that does not run is resumed in the background with the text as its first prompt. Prints the result in one line."""
import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_profile import current  # noqa: E402

DATA = current()["data"]
# The Superset CLI: ~/.superset/bin on the Mac, ~/superset/bin on the devbox.
SUPERSET = next((str(p) for p in (Path.home() / ".superset" / "bin" / "superset", Path.home() / "superset" / "bin" / "superset") if p.exists()),
                shutil.which("superset") or "superset")


def main():
    session, text = sys.argv[1], " ".join(sys.argv[2:]).strip()
    if not text:
        sys.exit("No text.")
    workspace = json.loads((DATA / "tabs.json").read_text() or "{}").get(session)
    out = run([SUPERSET, "terminals", "list", "--local", "--workspace", workspace, "--json"]) if workspace else None
    terminals = [t for t in (json.loads(out).get("sessions") or []) if not t.get("exited")] if out else []
    if not terminals:
        # The session does not run (no workspace yet, or its terminal closed): resume it in the background
        # with the message as its first prompt, the same as a send.
        start(session, text)
        return
    # the oldest live terminal is the one the desk started claude in
    terminal = min(terminals, key=lambda t: t.get("createdAt") or 0)["terminalId"]
    if run([SUPERSET, "terminals", "send", "--local", "--workspace", workspace, "--terminal", terminal, "--text", text]) is None:
        sys.exit("Superset did not take the message.")
    print("Message sent.")


def start(session, text):
    cards = json.loads((DATA / "cards.json").read_text() or "{}").get("cards", [])
    card = next((c for c in cards if any(x.get("id") == session for x in c.get("sessions", []))), {})
    title = next((x.get("title") for x in card.get("sessions", []) if x.get("id") == session), "") or card.get("status", "session")
    res = subprocess.run([sys.executable, str(Path(__file__).with_name("open-session.py")), "resume", session, title[:60],
                          card.get("project") or "MISC", text], capture_output=True, text=True, timeout=90,
                         env={**os.environ, "DESK_BACKGROUND": "1"})
    if res.returncode != 0:
        sys.exit(f"Not sent: {(res.stderr or res.stdout).strip()[:120]}")
    print("Session started again with the message.")


def run(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        print(res.stderr.strip(), file=sys.stderr)
        return None
    return res.stdout


if __name__ == "__main__":
    main()
