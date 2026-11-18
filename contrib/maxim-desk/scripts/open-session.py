#!/usr/bin/env python3
"""open-session.py: show a Claude session in its own Superset workspace, so every session keeps running.

  open-session.py resume <session-id> <name> <project> [message]
  open-session.py new <item-id> <name> <project> [first message]

A workspace uses the work folder of the profile, so `claude --resume` finds every session.
data/tabs.json maps a session id to its workspace; a second open jumps to the same workspace.
With DESK_BACKGROUND set, a new workspace is created but not opened (the w key).
The workspace carries the project as a tag, so the Superset sidebar groups sessions by project."""
import json
import shutil
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_profile import current  # noqa: E402

PROFILE = current()
DATA = PROFILE["data"]
# The Superset CLI: ~/.superset/bin on the Mac, ~/superset/bin on the devbox.
SUPERSET = next((str(p) for p in (Path.home() / ".superset" / "bin" / "superset", Path.home() / "superset" / "bin" / "superset") if p.exists()),
                shutil.which("superset") or "superset")
PROJECT_ID = PROFILE["superset_project"]
REPO = PROFILE["workdir"]
CLAUDE = "claude --dangerously-skip-permissions"


def main():
    if len(sys.argv) < 5 or sys.argv[1] not in ("resume", "new"):
        sys.exit(__doc__)
    kind, ref, name, project = sys.argv[1:5]
    message = " ".join(sys.argv[5:]).strip()
    tabs = load(DATA / "tabs.json", {})
    if kind == "resume":
        session = ref
        known = tabs.get(session)
        # A message (w on a session that does not run) goes in as the first prompt of the resumed session.
        command = f"cd {REPO} && export DESK_PROFILE={PROFILE['name']} && {CLAUDE} --resume {session}" + (f" {quote(message)}" if message else "")
        if known and workspace_alive(known):
            # After a restart of the Mac the workspace stays but its terminal is gone: start the session in it again.
            if not has_terminal(known):
                run([SUPERSET, "terminals", "create", "--local", "--workspace", known, "--command", command, "--json"])
            if not os.environ.get("DESK_BACKGROUND"):
                show(known)
            print(f"opened {known}")
            return
    else:
        # The pane picks the id itself (DESK_SESSION_ID), so it shows the session on the item before the workspace is up.
        session = os.environ.get("DESK_SESSION_ID") or str(uuid.uuid4())
        command = f"cd {REPO} && export DESK_PROFILE={PROFILE['name']} && {CLAUDE} --session-id {session} --name {quote(name)} {quote(briefing(ref, message))}"
        subprocess.run([sys.executable, str(Path(__file__).with_name("desk.py")), "link", session, ref], check=False)
    out = run([SUPERSET, "workspaces", "create", "--local", "--project", PROJECT_ID, "--checkout", "local",
               "--name", name, "--tag", project, "--command", command, "--json"])
    workspace = (json.loads(out).get("workspace") or {}).get("id") if out else None
    if not workspace:
        sys.exit("workspace not created")
    tabs[session] = workspace
    save(DATA / "tabs.json", tabs)
    # DESK_BACKGROUND: the w key starts the session with a message; Maxim stays in the desk, the workspace runs unseen.
    if not os.environ.get("DESK_BACKGROUND"):
        show(workspace)
    print(f"created {workspace} for {session}")


# The folder of each desk project inside the work folder, for the briefing (from the profile).
FOLDERS = PROFILE["folders"]


def briefing(item_id, message=""):
    """The first message of a new session: the desk item with its notes, so the session starts with the context.
    tasks.json is the source; cards.json adds the sessions and PRs once a gather has seen the item."""
    cards = load(DATA / "cards.json", {}).get("cards", [])
    card = next((c for c in cards if c.get("id") == item_id), {})
    ref = card.get("ref") or item_id.split(":", 1)[-1]
    task = next((t for t in load(DATA / "tasks.json", {}).get("tasks", []) if t.get("id") == ref), {})
    project = card.get("project") or task.get("project") or "MISC"
    if project == "CHAT":
        return chat_briefing(ref, message)
    folder = FOLDERS.get(project)
    # With a first message from the w key, the session starts on it; without one, it waits.
    head = f"Desk item {item_id}. This is context only. Do nothing yet: no tools, no changes. Reply with one line that you have the context, then wait for me."
    if message:
        head = f"Desk item {item_id}. The context is below. My first message is at the end."
    lines = [head, ""]
    for label, value in (
        ("Title", card.get("status") or task.get("title")),
        ("Desk id", item_id),
        ("Project", f"{project} (folder: {folder})" if folder else project),
        ("Kind", card.get("kind")),
        ("Created", task.get("since")),
        ("Source", task.get("source")),
        ("Impact", card.get("impact") or task.get("impact")),
        ("Waits on", card.get("waits") or task.get("waits")),
        ("Status", card.get("note")),
        ("Next", card.get("act")),
        ("Parent", card.get("parent") or (f"task:{task['parent']}" if task.get("parent") else None)),
        ("Description", task.get("body")),
    ):
        if value:
            lines.append(f"{label}: {value}")
    notes = card.get("notes") or task.get("notes") or []
    links = card.get("links") or [u for u in [task.get("url"), *task.get("links", [])] if u] or ([card["url"]] if card.get("url") else [])
    for title, rows in (("Steps", [s if isinstance(s, str) else s.get("text", "") for s in task.get("steps", [])]),
                        ("Links", links),
                        ("Notes", [f"{n.get('date', '')} {n.get('text', '')}".strip() for n in notes]),
                        ("Meeting transcripts", card.get("meetings") or task.get("meetings") or []),
                        ("PRs", [f"#{p.get('num')} {p.get('title', '')} ({p.get('state', '')}) {p.get('url', '')}" for p in card.get("prs", [])]),
                        ("Earlier sessions", [f"{x.get('title', '')} ({x.get('id', '')})" for x in card.get("sessions", [])]),
                        ("Children", [str(c.get("status") or c.get("id")) if isinstance(c, dict) else str(c) for c in card.get("children", [])])):
        if rows:
            lines += ["", f"{title}:"] + [f"- {r}" for r in rows if r]
    if card.get("meetings") or task.get("meetings"):
        lines += ["", "Read the meeting transcripts first, and every slide image a transcript names, with the Read tool."]
    lines += ["", f"Desk: add a note only for a fact Maxim must see at a glance: `python3 ~/desk/scripts/task.py note {ref} \"<text>\"`. One short fact, at most 80 characters, no date. Only Maxim closes the item."]
    if message:
        lines += ["", "My message:", message]
    return "\n".join(lines)


def chat_briefing(ref, message):
    """The first message of a chat from the Chat tab: no task, the whole profile is the context."""
    profile_md = PROFILE["root"] / "PROFILE.md"
    lines = [
        f"Desk chat (item task:{ref}, profile {PROFILE['label']}). This chat is about the whole profile, not one task.",
        "",
        "Context, read it before you answer:",
        f"- {REPO}/CLAUDE.md, and the documents it names as the base for decisions.",
        f"- {profile_md}: tags, folders and meeting rules of this desk.",
        "- The open items: `python3 ~/desk/scripts/task.py list`. `task.py show <id>` gives one item in full.",
        "",
        "You may change the desk (DESK_PROFILE is already set):",
        "- Add a task: `python3 ~/desk/scripts/task.py add \"<title>\" --project <TAG> --body \"<context>\"`.",
        "- Edit, note or close a task: `task.py edit`, `task.py note`, `task.py done`. `task.py help` prints the full usage.",
        "- Add only the tasks Maxim asks for or agrees to. Never make tasks from what other people said.",
        f"- After your first answer, give this chat a short topic name (2 to 5 words): `python3 ~/desk/scripts/task.py edit {ref} --title \"<topic>\"`.",
        "",
        "Report in short bullets, in ASD-STE100 Simplified Technical English.",
    ]
    if message:
        lines += ["", "My message:", message]
    return "\n".join(lines)


def show(workspace):
    """Show the workspace in the Superset desktop app. On the devbox the app runs on the Mac: mac.py sends it there."""
    if sys.platform == "darwin":
        run([SUPERSET, "workspaces", "open", workspace, "--local"])
    else:
        subprocess.run([sys.executable, str(Path(__file__).with_name("mac.py")), "workspace", workspace], check=False)


def has_terminal(workspace):
    out = run([SUPERSET, "terminals", "list", "--local", "--workspace", workspace, "--json"])
    return bool(out and json.loads(out).get("sessions"))


def workspace_alive(workspace):
    out = run([SUPERSET, "workspaces", "get", workspace, "--local", "--json"])
    if not out:
        return False
    return not json.loads(out).get("archivedAt")


def run(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        print(res.stderr.strip(), file=sys.stderr)
        return None
    return res.stdout


def quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def save(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
