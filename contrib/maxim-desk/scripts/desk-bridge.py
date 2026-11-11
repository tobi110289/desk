#!/usr/bin/env python3
"""desk-bridge.py: runs on the Mac and does the Mac work for a desk that runs on the devbox.

  desk-bridge.py [ssh-host]     default ssh-host: my-devbox

It reads ~/desk/bridge/outbox.jsonl on the devbox over SSH (tail -F) and runs each line here:
  open        open links (https, http, superset://, vscode:// only)
  copy        put the text on the Mac clipboard
  sound       play the arrival sound
  clip        answer a clipboard check: is there an image, and its change count; with "save", upload it

The connection goes only from the Mac to the devbox. The devbox cannot run anything else on the Mac.
A launchd agent (com.maxim.desk-bridge) keeps it running; it reconnects when the link drops."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOST = sys.argv[1] if len(sys.argv) > 1 else "my-devbox"
SCHEMES = ("https://", "http://", "superset://", "vscode://")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
       "-o", "ControlMaster=auto", "-o", f"ControlPath={Path.home()}/.ssh/cm-desk-bridge-%C", "-o", "ControlPersist=10m"]
CLIP = Path(__file__).resolve().parent / "clip-image.py"


def log(text):
    print(time.strftime("%H:%M:%S"), text, flush=True)


def remote(cmd, data=None):
    return subprocess.run([*SSH, HOST, cmd], input=data, capture_output=True, timeout=30)


def answer_clip(req):
    rid = "".join(ch for ch in str(req.get("id", "")) if ch.isalnum() or ch in "-_")[:40]
    if not rid:
        return
    check = subprocess.run(["python3", str(CLIP), "check"], capture_output=True, text=True).stdout.strip()
    try:
        state = json.loads(check)
    except ValueError:
        state = {"count": 0, "isImage": False}
    if req.get("save") and state.get("isImage"):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "clip.png"
            subprocess.run(["python3", str(CLIP), "save", str(png)], capture_output=True)
            if png.exists():
                remote(f"mkdir -p ~/desk/bridge/clip && cat > ~/desk/bridge/clip/{rid}.png", png.read_bytes())
                state["png"] = f"~/desk/bridge/clip/{rid}.png"
    remote(f"mkdir -p ~/desk/bridge/clip && cat > ~/desk/bridge/clip/{rid}.json.tmp && mv ~/desk/bridge/clip/{rid}.json.tmp ~/desk/bridge/clip/{rid}.json",
           json.dumps(state).encode())


def handle(line):
    try:
        req = json.loads(line)
    except ValueError:
        return
    kind = req.get("type")
    if kind == "open":
        urls = [u for u in req.get("urls", []) if isinstance(u, str) and u.startswith(SCHEMES)]
        if urls:
            subprocess.run(["open", *urls], check=False)
            log(f"open {' '.join(urls)[:120]}")
    elif kind == "copy" and isinstance(req.get("text"), str):
        subprocess.run(["pbcopy"], input=req["text"].encode(), check=False)
        log("copy")
    elif kind == "sound":
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        log("sound")
    elif kind == "clip":
        answer_clip(req)
        log(f"clip {'save' if req.get('save') else 'check'}")


def main():
    while True:
        log(f"connecting to {HOST}")
        proc = subprocess.Popen(
            [*SSH, HOST, "mkdir -p ~/desk/bridge && touch ~/desk/bridge/outbox.jsonl && exec tail -n0 -F ~/desk/bridge/outbox.jsonl"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in proc.stdout:
            line = line.strip()
            if line:
                try:
                    handle(line)
                except Exception as err:  # one bad line never stops the bridge
                    log(f"error: {err}")
        proc.wait()
        log("link dropped, retrying in 10 s")
        time.sleep(10)


if __name__ == "__main__":
    os.environ.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    main()
