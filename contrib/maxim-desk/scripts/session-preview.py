#!/usr/bin/env python3
"""session-preview.py <session-id> [count]: the last messages of a Claude session as JSON, for the pane preview.

Prints [{"role": "you"|"claude", "text": "..."}], oldest first. Tool calls and meta lines are left out.
The last element is {"role": "activity", "text": "..."} when Claude's newest step is a tool call: what it runs now."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk import PROJECTS, SESSIONS, assistant_text, user_text  # noqa: E402


# A long message keeps its end: the pane shows the newest rows, so the end must be there.
MAX_CHARS = 20_000

def main():
    sid, count = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 30
    hits = list(PROJECTS.glob(f"{SESSIONS}/{sid}*.jsonl"))
    if not hits:
        print("[]")
        return
    messages = []
    activity = ""
    with hits[0].open(errors="ignore") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "user" and not ev.get("isMeta"):
                text = user_text(ev)
                if text:
                    messages.append({"role": "you", "text": text[-MAX_CHARS:]})
                    activity = ""
            elif ev.get("type") == "assistant":
                text = assistant_text(ev)
                if text:
                    messages.append({"role": "claude", "text": text[-MAX_CHARS:]})
                    activity = ""
                activity = tool_step(ev) or activity
    out = messages[-count:]
    if activity:
        out.append({"role": "activity", "text": activity})
    print(json.dumps(out, ensure_ascii=False))


def tool_step(ev):
    """The newest tool call of an assistant event in plain words: "Running a command", "Reading desk.py"."""
    calls = [c for c in ev.get("message", {}).get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use"]
    if not calls:
        return ""
    call = calls[-1]
    name = call.get("name", "")
    args = call.get("input") or {}
    file = Path(str(args.get("file_path") or args.get("notebook_path") or "")).name
    if name == "Bash":
        # Claude's own short description, never the command itself.
        about = " ".join(str(args.get("description") or "").split())
        return about[:70] if about else "Running a command"
    if name == "Read":
        return f"Reading {file}" if file else "Reading a file"
    if name in ("Edit", "Write", "NotebookEdit"):
        return f"Editing {file}" if file else "Editing a file"
    if name in ("Grep", "Glob", "ToolSearch"):
        return "Searching the code"
    if name in ("Agent", "Task", "Workflow"):
        return "Running a subagent"
    if name in ("WebFetch", "WebSearch"):
        return "Searching the web"
    if name.startswith("mcp__"):
        return f"Using {name.split('__')[1].replace('claude_ai_', '').replace('_', ' ')}"
    return f"Using {name}" if name else ""

if __name__ == "__main__":
    main()
