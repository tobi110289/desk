#!/usr/bin/env python3
"""live.py <session-id>...: print {"live": [ids where Claude works now], "mtimes": {id: last message time},
"context": {id: {"tokens": n, "window": n}}}.

A session works when its newest user or assistant event does not end Claude's turn and the file
changed in the last 10 minutes (the same rule as desk.py). Reads only the end of each file, so the
pane can call it every few seconds."""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_profile import current  # noqa: E402

PROJECTS = Path.home() / ".claude" / "projects"
SESSIONS = current()["sessions"] or "-no-work-folder-"
TAIL = 256 * 1024


def main():
    ids = set(sys.argv[1:])
    live, mtimes, context = [], {}, {}
    for project in PROJECTS.glob(SESSIONS):
        for sid in list(ids):
            path = project / f"{sid}.jsonl"
            if not path.exists():
                continue
            mtimes[sid] = last_message_time(path)
            used = context_tokens(path)
            if used:
                context[sid] = used
            ids.discard(sid)
            if works(path) or background_runs(path):
                live.append(sid)
    print(json.dumps({"live": live, "mtimes": mtimes, "context": context}))


def last_message_time(path):
    """The time of the newest message from Maxim or Claude. Lines that the harness adds (a plugin reload,
    a mode change, a compaction summary) also change the file, but they are not activity.
    When the tail holds no message (a long summary or many reload lines), the whole file is read."""
    size = path.stat().st_size
    for start in (max(0, size - TAIL), 0):
        with path.open("rb") as f:
            f.seek(start)
            lines = f.read().decode(errors="ignore").splitlines()
        for line in reversed(lines):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (ev.get("type") in ("user", "assistant") and ev.get("timestamp") and not ev.get("isMeta")
                    and not ev.get("isCompactSummary") and not ev.get("isVisibleInTranscriptOnly")):
                return datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00")).timestamp()
        if start == 0:
            break
    return path.stat().st_mtime


LAUNCH_RE = re.compile(r"(?:Command running in background with ID: |Async agent launched successfully[\s\S]*?agentId: )([A-Za-z0-9]+)")
DONE_RE = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>.*?<status>([^<]+)</status>", re.S)
QUIET_S = 1800


def background_runs(path):
    """True while a sub agent or a background shell of the session still runs, also when Claude's own turn ended.
    A launch counts until its task notification arrives. A launch whose output did not change for 30 minutes
    does not count: the session was closed, or the command hangs."""
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - TAIL))
        lines = f.read().decode(errors="ignore").splitlines()
    running, done, launches = {}, set(), set()
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        # The calls that can start background work: a sub agent, or a shell command with run_in_background.
        if ev.get("type") == "assistant":
            for part in (ev.get("message") or {}).get("content") or []:
                if isinstance(part, dict) and part.get("type") == "tool_use" and (
                        part.get("name") in ("Agent", "Task") or (part.get("input") or {}).get("run_in_background")):
                    launches.add(part.get("id"))
            continue
        # The finish notice: a user message between turns, or a queue event or attachment during a turn.
        if ev.get("type") in ("queue-operation", "attachment") or (ev.get("type") == "user" and ev.get("attachment")):
            for use_id, status in DONE_RE.findall(json.dumps(ev).replace("\\n", "\n")):
                if status.strip() != "running":
                    done.add(use_id.strip())
            continue
        if ev.get("type") != "user":
            continue
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, str):
            for use_id, status in DONE_RE.findall(content):
                if status.strip() != "running":
                    done.add(use_id.strip())
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_result" or part.get("tool_use_id") not in launches:
                continue
            # Only a result that starts with the launch text: a grep or a log can quote it too.
            body = part.get("content")
            if isinstance(body, list):
                body = next((b.get("text", "") for b in body if isinstance(b, dict) and b.get("type") == "text"), "")
            m = LAUNCH_RE.match(body if isinstance(body, str) else "")
            if m:
                running[part.get("tool_use_id")] = (m.group(1), ev.get("timestamp", ""))
    tasks = Path("/private/tmp") / f"claude-{os.getuid()}" / path.parent.name / path.stem / "tasks"
    now = time.time()
    for use_id, (task_id, started) in running.items():
        if use_id in done:
            continue
        out = tasks / f"{task_id}.output"
        try:
            changed = out.resolve().stat().st_mtime
        except OSError:
            changed = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp() if started else 0
        if now - changed < QUIET_S:
            return True
    return False


# Opus and Sonnet 5 sessions run with the 1M window; Haiku with 200k.
def window_of(model):
    return 200_000 if "haiku" in (model or "") else 1_000_000


def context_tokens(path):
    """The context of the session now: the input of Claude's newest call (fresh, cached and cache-written tokens)
    plus its output, and the window of its model. None when the end of the file has no call."""
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - TAIL))
        lines = f.read().decode(errors="ignore").splitlines()
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant" or ev.get("isSidechain"):
            continue
        msg = ev.get("message") or {}
        u = msg.get("usage") or {}
        tokens = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        if tokens:
            return {"tokens": tokens, "window": window_of(msg.get("model"))}
    return None


def works(path):
    if time.time() - path.stat().st_mtime > 600:
        return False
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - TAIL))
        lines = f.read().decode(errors="ignore").splitlines()
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            return (ev.get("message") or {}).get("stop_reason") not in ("end_turn", "stop_sequence", "max_tokens")
        if ev.get("type") == "user" and not ev.get("isMeta"):
            # Esc writes this line; the turn broke off
            return "[Request interrupted" not in json.dumps(ev.get("message", {}).get("content", ""))
    return False


if __name__ == "__main__":
    main()
