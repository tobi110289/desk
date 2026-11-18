#!/usr/bin/env python3
"""clip-image.py check | save <path.png>

check: prints {"count": <pasteboard change count>, "isImage": true|false} for the macOS clipboard.
save: writes the clipboard image as PNG to <path.png> and prints the path.
On another host (the devbox) both ask the Mac through desk-bridge.py: a request line goes to
~/desk/bridge/outbox.jsonl, and the bridge writes the answer (and the PNG) to ~/desk/bridge/clip/."""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

BRIDGE = Path.home() / "desk" / "bridge"

JXA_CHECK = """
ObjC.import('AppKit');
const pb = $.NSPasteboard.generalPasteboard;
const types = ObjC.deepUnwrap(pb.types) || [];
JSON.stringify({count: Number(pb.changeCount), isImage: types.some(t => t === 'public.png' || t === 'public.tiff')});
"""

JXA_SAVE = """
ObjC.import('AppKit');
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard;
  let data = pb.dataForType('public.png');
  if (data.isNil()) {
    const tiff = pb.dataForType('public.tiff');
    if (tiff.isNil()) { return ''; }
    data = $.NSBitmapImageRep.imageRepWithData(tiff).representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $());
  }
  data.writeToFileAtomically(argv[0], true);
  return argv[0];
}
"""


def ask_mac(save):
    """The Mac's answer through the bridge, or None when the bridge does not answer in 6 s."""
    rid = uuid.uuid4().hex[:12]
    (BRIDGE / "clip").mkdir(parents=True, exist_ok=True)
    with open(BRIDGE / "outbox.jsonl", "a") as f:
        f.write(json.dumps({"type": "clip", "id": rid, "save": save, "at": time.time()}) + "\n")
    answer = BRIDGE / "clip" / f"{rid}.json"
    for _ in range(60):
        if answer.exists():
            try:
                state = json.loads(answer.read_text())
            except ValueError:
                state = None
            answer.unlink(missing_ok=True)
            return state
        time.sleep(0.1)
    return None


def main_remote():
    if sys.argv[1] == "check":
        print(json.dumps(ask_mac(False) or {"count": 0, "isImage": False}))
        return
    state = ask_mac(True) or {}
    png = Path(os.path.expanduser(state.get("png", ""))) if state.get("png") else None
    if not png or not png.exists():
        sys.exit("No image on the clipboard.")
    shutil.move(str(png), sys.argv[2])
    print(sys.argv[2])


def main():
    if sys.platform != "darwin":
        main_remote()
        return
    if sys.argv[1] == "check":
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_CHECK], capture_output=True, text=True)
        print(out.stdout.strip() or json.dumps({"count": 0, "isImage": False}))
        return
    out = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_SAVE, sys.argv[2]], capture_output=True, text=True)
    path = out.stdout.strip()
    if not path:
        sys.exit("No image on the clipboard.")
    print(path)


if __name__ == "__main__":
    main()
