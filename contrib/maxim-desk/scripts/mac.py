#!/usr/bin/env python3
"""mac.py: the things only Maxim's Mac can do (browser, clipboard, sound, the Superset app).

  mac.py open <url>...        open links in the Mac's default browser
  mac.py code <path>...       open files in VS Code
  mac.py copy <text>          put text on the Mac clipboard
  mac.py sound                play the arrival sound
  mac.py workspace <id>       show a Superset workspace in the desktop app

On macOS the command runs here. On another host (the devbox) it goes as one JSON line to
~/desk/bridge/outbox.jsonl; desk-bridge.py on the Mac reads that file over SSH and runs it there."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BRIDGE = Path.home() / "desk" / "bridge"
IS_MAC = sys.platform == "darwin"
SCHEMES = ("https://", "http://", "superset://", "vscode://")
# The SSH name of this host as the Mac knows it; VS Code Remote opens files through it.
SSH_HOST = os.environ.get("DESK_SSH_HOST", "my-devbox")


def send(kind, **fields):
    BRIDGE.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"type": kind, "at": time.time(), **fields}, ensure_ascii=False)
    with open(BRIDGE / "outbox.jsonl", "a") as f:
        f.write(line + "\n")


def run_mac(kind, args):
    if kind == "open":
        subprocess.run(["open", *[u for u in args if u.startswith(SCHEMES)]], check=False)
    elif kind == "code":
        subprocess.run(["open", "-a", "Visual Studio Code", *args], check=False)
    elif kind == "copy":
        subprocess.run(["pbcopy"], input=args[0].encode(), check=True)
    elif kind == "sound":
        subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
    elif kind == "workspace":
        superset = str(Path.home() / ".superset" / "bin" / "superset")
        subprocess.run([superset, "workspaces", "open", args[0], "--local"], check=False, capture_output=True)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("open", "code", "copy", "sound", "workspace"):
        sys.exit(__doc__)
    kind, args = sys.argv[1], sys.argv[2:]
    if IS_MAC:
        run_mac(kind, args)
        return
    if kind == "open":
        send("open", urls=[u for u in args if u.startswith(SCHEMES)])
    elif kind == "code":
        send("open", urls=[f"vscode://vscode-remote/ssh-remote+{SSH_HOST}{os.path.abspath(p)}" for p in args])
    elif kind == "copy":
        send("copy", text=args[0] if args else sys.stdin.read())
    elif kind == "sound":
        send("sound")
    elif kind == "workspace":
        send("open", urls=[f"superset://v2-workspace/{args[0]}"])


if __name__ == "__main__":
    main()
