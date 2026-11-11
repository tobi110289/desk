#!/usr/bin/env python3
"""desk.py: gather everything that waits on Maxim, rank it, write data/cards.json, print the top.

  desk.py                 gather, rank, print the top 9
  desk.py --all           print every live card
  desk.py --json          print cards.json
  desk.py --wins          print what got a verb today
  desk.py --focus ACME,GLOBEX   show only these projects (saved in desk.json)
  desk.py --focus all     show every project again

Reads (read-only): data/tasks.json (local todos), Claude Code sessions, GitHub PRs (gh).
The script writes only data/desk.json and data/cards.json.
"""
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_profile import current  # noqa: E402
from contact import ACTIVE, TAB_OF_KIND, all_contacts, read as read_contact  # noqa: E402

HOME = Path.home()
PROFILE = current()
DATA = PROFILE["data"]
PROJECTS = HOME / ".claude" / "projects"
SESSIONS = PROFILE["sessions"] or "-no-work-folder-"
SESSION_DAYS = 7

IMPACT = {"P0": 6, "P1": 4, "P2": 2, "P3": 1}
WAITS = {"customer": 4, "person": 3, "agent": 1, "nobody": 0}

# Session tags as Maxim types them, mapped to one project name (from the profile).
TAGS = PROFILE["tags"]
# Topic tags (cross-project tabs): a title with a project tag and a topic tag goes to the project.
TOPIC_TAGS = tuple(PROFILE.get("topic_tags", []))

PR_RE = re.compile(r"(?:PR\s*#|pull/)(\d{4,5})")
TAG_RE = re.compile(r"^\s*\[([^\]]+)\]")
ASK_RE = re.compile(r"(\?\s*\**\s*$|^(do you want|should i|shall i|want me to)\b)", re.I | re.M)
CLEANUP_RE = re.compile(r"\b(stop|remove|kill|delete|clean)\b.*\b(worktree|server|process|stash|port)s?\b", re.I)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    state = load_state()
    if "--focus" in sys.argv:
        val = sys.argv[sys.argv.index("--focus") + 1]
        state["focus"] = [] if val == "all" else [v.strip().upper() for v in val.split(",") if v.strip()]
        save_state(state)
    if "--wins" in sys.argv:
        print_wins(state)
        return
    if "--json" in sys.argv:
        print((DATA / "cards.json").read_text())
        return

    if len(sys.argv) > 1 and sys.argv[1] == "rename":
        rename(sys.argv[2], " ".join(sys.argv[3:]))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "move":
        move(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "title":
        # desk.py title <item-id> <name>: Maxim's own name for an item that has no file of its own
        state.setdefault("titles", {})[sys.argv[2]] = " ".join(sys.argv[3:]).strip()
        save_state(state)
        print(f"{sys.argv[2]} renamed")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "parent":
        set_parent(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        delete(sys.argv[2], state)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore(sys.argv[2], state)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "link":
        # desk.py link <session-id> <card-id>: the session works on that card
        state.setdefault("links", {})[sys.argv[2]] = sys.argv[3]
        save_state(state)
        print(f"{sys.argv[2][:8]} works on {sys.argv[3]}")
        return
    sessions = read_sessions()
    write_json(DATA / "sessions.json", {"sessions": [
        {k: s[k] for k in ("id", "title", "project", "state", "mtime", "question")}
        for s in sorted(sessions, key=lambda s: -s["mtime"])]})
    prs, merged = fetch_prs()
    doc = load_json(DATA / "tasks.json", {"tasks": []})
    with open(DATA / ".tasks.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_json(DATA / "tasks.json", {"tasks": []})
        sync_items(doc["tasks"], state, sessions, prs, merged)
        next_steps(doc["tasks"], all_contacts(PROFILE["contacts"]))
        write_json(DATA / "tasks.json", doc)
    links = state.get("links", {})
    contacts = {c["slug"]: c for c in all_contacts(PROFILE["contacts"])}
    write_json(DATA / "contacts.json", {"contacts": contact_rows(contacts, doc["tasks"])})
    live, parked = rank(build_cards(doc["tasks"], state, sessions, prs, merged, contacts), state)
    # a parent sorts with its most urgent child, so a child never hides below its group
    for c in live:
        c["sortTurn"] = c["turn"]
    for c in live:
        kid = c.get("parent")
        parent = next((x for x in live if x["id"] == kid), None)
        if parent and STATUS_ORDER[c["turn"]] < STATUS_ORDER[parent["sortTurn"]]:
            parent["sortTurn"] = c["turn"]
    live.sort(key=lambda c: sort_key(c, state.get("order", [])))
    live = place_children(live)
    focus = state.get("focus") or []
    hidden = [c for c in live if focus and c.get("project") not in focus]
    live = [c for c in live if c not in hidden]
    out = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "focus": focus,
        "cards": live,
        "parked": parked,
        "hidden": len(hidden),
        "projects": project_counts(live + hidden),
        "closedToday": closed_today(state),
    }
    write_json(DATA / "cards.json", out)
    write_json(DATA / "done.json", {"done": done_list(state, sessions, links, merged)})
    save_state(state)
    print_desk(out, show_all="--all" in sys.argv)


# ---------- items ----------
# One thing Maxim committed to is one item: a task in tasks.json. Sessions and PRs attach to items.
# A session started outside the desk makes a new item. Old items can still carry a pr, teams or meeting source.

KINDS = {"session": "Session", "pr": "PR", "teams": "Message", "meeting": "Meeting"}


def kind_of(t):
    return KINDS.get((t.get("source") or "").split(":")[0], "Todo")


def new_task(tasks, title, project, source, url=None, body="", since=None):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "item"
    taken = {t["id"] for t in tasks}
    tid, n = slug, 2
    while tid in taken:
        tid, n = f"{slug}-{n}", n + 1
    t = {"id": tid, "project": project or "MISC", "title": title, "steps": [], "body": body, "mins": 15,
         "impact": "P2", "waits": "nobody", "since": since or datetime.date.today().isoformat(), "source": source}
    if url:
        t["url"] = url
    tasks.append(t)
    return t


def auto_close(t, verb, note):
    if t.get("done"):
        return
    t["done"] = datetime.date.today().isoformat()
    t["doneAt"] = time.time()
    t["doneNote"] = note
    t["doneVerb"] = verb
    t["autoDone"] = True


def auto_open(t):
    if t.get("done") and t.get("autoDone"):
        for k in ("done", "doneAt", "doneNote", "doneVerb", "autoDone"):
            t.pop(k, None)


def fetch_prs():
    """Open PRs with the state that decides who acts next, and the PRs merged lately."""
    out = run(["gh", "pr", "list", "--author", "@me", "--state", "open", "-L", "50", "--json",
               "number,title,url,isDraft,reviewDecision,statusCheckRollup,updatedAt,mergeable"])
    prs = {}
    for p in json.loads(out) if out else []:
        checks = p.get("statusCheckRollup") or []
        failed = [c.get("name") or c.get("context") for c in checks
                  if (c.get("conclusion") or c.get("state") or "").upper() in ("FAILURE", "ERROR", "TIMED_OUT")]
        pending = any((c.get("status") or "").upper() in ("IN_PROGRESS", "QUEUED", "PENDING") for c in checks)
        review = p.get("reviewDecision") or ""
        conflict = p.get("mergeable") == "CONFLICTING"
        state, note = "wait", "waits on review or checks"
        if failed or review == "CHANGES_REQUESTED" or conflict:
            state = "fix"
            note = "merge conflicts with main" if conflict else "changes requested"
            if failed:
                note = f"checks failed: {', '.join(failed[:3])}"
        elif review == "APPROVED" and not pending and not p.get("isDraft"):
            state, note = "merge", "approved, checks green"
        num = str(p["number"])
        prs[num] = {"num": num, "title": p["title"], "url": p["url"], "state": state, "note": note,
                    "updated": p["updatedAt"][:10], "bind": f"{review}|{','.join(sorted(failed))}"}
    out = run(["gh", "pr", "list", "--author", "@me", "--state", "merged", "-L", "60", "--json", "number,title,url,mergedAt"])
    merged = {str(p["number"]): {"num": str(p["number"]), "title": p["title"], "url": p["url"], "state": "merged",
                                 "note": "merged", "date": (p.get("mergedAt") or "")[:10]}
              for p in (json.loads(out) if out else [])}
    return prs, merged


def sync_items(tasks, state, sessions, prs, merged):
    """Make an item for every new session, attach sessions and PRs, close and reopen session items."""
    links = state.setdefault("links", {})
    prlinks = state.setdefault("prlinks", {})
    by_id = {t["id"]: t for t in tasks}
    by_source = {t["source"]: t for t in tasks if t.get("source")}

    ignored = set(state.get("ignored", []))

    # A PR that Maxim names in an item (title, body, links, notes) belongs to that item, also a PR of someone else.
    for t in tasks:
        if t.get("done"):
            continue
        for num in mentioned_prs(t):
            prlinks[num] = t["id"]
            if num not in prs and num not in merged:
                p = fetch_pr(num)
                if p:
                    (merged if p["state"] == "merged" else prs)[num] = p

    def item_for(source, title, project, url=None, body="", since=None):
        t = by_source.get(source)
        if not t and source in ignored:
            return None
        if not t:
            t = by_source[source] = new_task(tasks, title, project, source, url, body, since)
            by_id[t["id"]] = t
        return t

    # Links from the old desk named cards (pr:N, meeting:x, session:x). Each now names the item of that source.
    for sid, cid in list(links.items()):
        if cid.startswith("task:"):
            continue
        t = by_source.get(cid) if not cid.startswith("session:") else None
        if cid.startswith("pr:") and cid[3:] in prlinks:
            t = by_id.get(prlinks[cid[3:]])
        if t:
            links[sid] = f"task:{t['id']}"
        else:
            links.pop(sid)

    # Sessions: every session without an item gets its own item. A finished one starts closed (below),
    # so Maxim can still reopen it, rename it or add notes.
    for s in sessions:
        if s["id"] in links:
            continue
        if (s["title"] or "").upper().startswith("[DESK]"):
            continue
        is_new = f"session:{s['id']}" not in by_source
        t = item_for(f"session:{s['id']}", s["title"] or "(no name)", s["project"],
                     since=datetime.date.fromtimestamp(s["mtime"]).isoformat())
        if t and is_new:
            t["fresh"] = True
        if t:
            links[s["id"]] = f"task:{t['id']}"
    # An item that came from a session closes when that session finished, and opens when it asks or runs again.
    # PRs: a new PR joins the item of the one session that mentions it. Other PRs make no item.
    session_item = {s["id"]: links[s["id"]][5:] for s in sessions if links.get(s["id"], "").startswith("task:")}
    for num, p in prs.items():
        if prlinks.get(num) in by_id:
            continue
        owners = pr_owners(num, sessions, session_item)
        if len(owners) == 1:
            prlinks[num] = owners.pop()

    reconcile(tasks, state, sessions, prs)
    by_id = {t["id"]: t for t in tasks}
    # Only Maxim closes items (x). Two exceptions keep the list clean: a session older than SESSION_DAYS gets a
    # closed item when it first shows up, and a closed session item opens again when that session gets new work.
    mtimes = {s["id"]: s["mtime"] for s in sessions}
    for t in tasks:
        source = t.get("source") or ""
        if not source.startswith("session:") or source[8:] not in mtimes:
            continue
        age = time.time() - mtimes[source[8:]]
        if not t.get("done") and age > SESSION_DAYS * 86400 and t.get("fresh"):
            auto_close(t, "do", "older than a week")
        changed = datetime.date.fromtimestamp(mtimes[source[8:]]).isoformat()
        if t.get("done") and t.get("autoDone") and changed > t["done"]:
            auto_open(t)
        t.pop("fresh", None)



def mentioned_prs(t):
    text = " ".join([t.get("title", ""), t.get("body", ""), *links_of(t), json.dumps(t.get("notes", []))])
    return set(PR_RE.findall(text))


def fetch_pr(num):
    """One PR by number, for a PR that is not Maxim's own: he reviews it, so it never waits on a fix from him."""
    out = run(["gh", "pr", "view", num, "--json", "number,title,url,state,updatedAt,mergedAt"])
    if not out:
        return None
    p = json.loads(out)
    state = {"MERGED": "merged", "CLOSED": "closed"}.get(p.get("state"), "review")
    return {"num": num, "title": p["title"], "url": p["url"], "state": state, "note": state,
            "updated": (p.get("updatedAt") or "")[:10], "date": (p.get("mergedAt") or "")[:10], "bind": ""}


def pr_owners(num, sessions, session_item):
    """The items whose sessions work on PR num. A session with "#num" in its name is the strongest sign;
    otherwise every session that mentions the PR counts."""
    named = {session_item[s["id"]] for s in sessions if s["id"] in session_item and f"#{num}" in (s["title"] or "")}
    if named:
        return named
    return {session_item[s["id"]] for s in sessions if num in s["prs"] and s["id"] in session_item}


def reconcile(tasks, state, sessions, prs):
    """One thing, one item. Removes the extra item when a session or PR got two:
    - an item made from a session that is linked to another item;
    - an item made from a PR that the one session which mentions it already covers.
    An item with notes, or one Maxim reopened, always stays."""
    links, prlinks = state.setdefault("links", {}), state.setdefault("prlinks", {})
    linked_items = {cid[5:] for cid in links.values() if cid.startswith("task:")}
    session_item = {sid: cid[5:] for sid, cid in links.items() if cid.startswith("task:")}
    keep = lambda t: t.get("notes") or t.get("manual")
    gone = set()
    for t in tasks:
        source = t.get("source") or ""
        if source.startswith("session:") and not keep(t) and session_item.get(source[8:], t["id"]) != t["id"]:
            gone.add(t["id"])
        if source.startswith("pr:") and t["id"] not in linked_items:
            num = source[3:]
            owners = pr_owners(num, sessions, session_item)
            owners.discard(t["id"])
            if len(owners) != 1:
                continue
            owner = owners.pop()
            if not keep(t):
                prlinks[num] = owner
                gone.add(t["id"])
                continue
            # the PR item has Maxim's notes: the session joins it, and its own item goes if nothing keeps it
            other = next((o for o in tasks if o["id"] == owner), {})
            if (other.get("source") or "").startswith("session:") and not keep(other):
                for sid, cid in list(links.items()):
                    if cid == f"task:{owner}":
                        links[sid] = f"task:{t['id']}"
                gone.add(owner)
    # Sessions with the same name are one piece of work (a resume or a fork): one item, the newest session on top.
    mtime = {x["id"]: x["mtime"] for x in sessions}
    by_title = {}
    for t in tasks:
        source = t.get("source") or ""
        if source.startswith("session:") and t["id"] not in gone and t["title"] not in ("", "(no name)"):
            by_title.setdefault(t["title"], []).append(t)
    for same in by_title.values():
        if len(same) < 2:
            continue
        same.sort(key=lambda t: (not keep(t), not t.get("done") is None, -mtime.get(t["source"][8:], 0)))
        main = same[0]
        for t in same[1:]:
            if keep(t):
                continue
            for sid, cid in list(links.items()):
                if cid == f"task:{t['id']}":
                    links[sid] = f"task:{main['id']}"
            gone.add(t["id"])
    if not gone:
        return
    tasks[:] = [t for t in tasks if t["id"] not in gone]
    state["prlinks"] = {n: tid for n, tid in prlinks.items() if tid not in gone}
    state["order"] = [cid for cid in state.get("order", []) if cid[5:] not in gone]


# ---------- contacts ----------
# A contact (contact.py) is a record, not an item. Its next step is an item in the tab of its kind, linked by "contact".
# Every contact in an active stage has one open next step: when none is open, the gather adds one.

def next_steps(tasks, contacts):
    open_for = {t.get("contact") for t in tasks if t.get("contact") and not t.get("done")}
    for c in contacts:
        if c.get("stage") in ACTIVE and c["slug"] not in open_for:
            t = new_task(tasks, f"Next step: {c.get('name') or c['slug']}" + (f" ({c['company']})" if c.get("company") else ""),
                         TAB_OF_KIND.get(c.get("kind", ""), "MARKET"), f"contact:{c['slug']}")
            t["contact"] = c["slug"]
            t["impact"] = "P1"


def contact_rows(contacts, tasks):
    """contacts.json for the pane: each contact with its open and done items."""
    rows = []
    for c in contacts.values():
        mine = [t for t in tasks if t.get("contact") == c["slug"]]
        rows.append({**c, "id": f"contact:{c['slug']}",
                     "open": [{"id": f"task:{t['id']}", "title": t["title"]} for t in mine if not t.get("done")],
                     "done": [{"id": f"task:{t['id']}", "title": t["title"]} for t in mine if t.get("done")],
                     "notes": read_contact(Path(c["path"]))[1].strip()[:2000],
                     "mtime": os.path.getmtime(c["path"])})
    return rows


def build_cards(tasks, state, sessions, prs, merged, contacts=None):
    links, prlinks = state.get("links", {}), state.get("prlinks", {})
    by_item = {}
    for s in sessions:
        cid = links.get(s["id"], "")
        if cid.startswith("task:"):
            by_item.setdefault(cid[5:], []).append(s)
    cards = []
    for t in tasks:
        if t.get("done"):
            continue
        linked = sorted(by_item.get(t["id"], []), key=lambda s: -s["mtime"])
        attached = [prs.get(n) or merged.get(n) for n, tid in prlinks.items() if tid == t["id"]]
        attached = [p for p in attached if p]
        kind = kind_of(t)
        # ☑️ todo: no work started yet. 🔶 running: work started (a session or a PR).
        # The pane's queue (yellow) says when Maxim must jump in, so the icon shows only this.
        turn = "running" if linked or attached else "todo"
        c = card(id=f"task:{t['id']}", ref=t["id"], kind=kind, project=t.get("project"), note=t.get("body", ""),
                 mins=t.get("mins", 15), impact=t.get("impact", "P2"), waits=t.get("waits", "nobody"), age=days_since(t.get("since")),
                 status=t["title"], act="", url=next(iter(links_of(t)), None) or first_url(t.get("body", "")) or next((p["url"] for p in attached), None),
                 session="", bind="")
        c["sessions"] = [{k: s[k] for k in ("id", "title", "state", "mtime", "last")} for s in linked]
        c["links"] = links_of(t) or ([c["url"]] if c.get("url") else [])
        c["notes"] = t.get("notes", [])
        person = (contacts or {}).get(t.get("contact", ""))
        c["contact"] = " · ".join(x for x in (person.get("name"), person.get("company"), person.get("kind"), person.get("stage")) if x) if person else ""
        c["meetings"] = t.get("meetings", [])
        c["parent"] = f"task:{t['parent']}" if t.get("parent") else ""
        c["children"] = [{"id": f"task:{k['id']}", "title": k["title"], "isDone": bool(k.get("done"))}
                         for k in tasks if k.get("parent") == t["id"]]
        c["sessionState"] = max((s["state"] for s in linked), key=lambda st: STATE_RANK.get(st, 0)) if linked else ""
        c["prs"] = [{k: p[k] for k in ("num", "title", "url", "state")} for p in attached]
        c["turn"] = turn
        cards.append(c)
    return cards


# ---------- sessions ----------

def rename(prefix, title):
    """Append a custom-title line, as /rename does. The newest title line wins."""
    if not title.strip():
        sys.exit('desk.py rename <session-id-prefix> "<[TAG] topic>"')
    hits = [p for p in PROJECTS.glob(f"{SESSIONS}/*.jsonl") if p.stem.startswith(prefix)]
    if len(hits) != 1:
        sys.exit(f"{len(hits)} sessions match {prefix}")
    with hits[0].open("a") as f:
        f.write(json.dumps({"type": "custom-title", "customTitle": title.strip(), "sessionId": hits[0].stem}) + "\n")
    print(f"{hits[0].stem[:8]} renamed: {title.strip()}")


def read_sessions(days=14):
    cutoff = time.time() - days * 86400
    sessions = []
    for project in PROJECTS.glob(SESSIONS):
        for path in project.glob("*.jsonl"):
            if path.stat().st_mtime < cutoff:
                continue
            s = read_session(path)
            if s:
                sessions.append(s)
    return sessions


def read_session(path):
    title = ai_title = first = None
    last_text, last_role, turns, prs = "", None, 0, set()
    # True once Claude ends a turn (stop_reason end_turn); any later message or tool result opens a new turn
    ended = False
    with path.open(errors="ignore") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if ev.get("entrypoint") == "sdk-cli":
                # headless runs (claude -p) are no sessions Maxim works in
                return None
            if kind == "custom-title":
                title = ev.get("customTitle")
            elif kind == "ai-title":
                ai_title = ev.get("aiTitle")
            elif kind == "user" and not ev.get("isMeta"):
                ended = False
                text = user_text(ev)
                if text:
                    turns += 1
                    last_role = "user"
                    first = first or text
                    prs.update(PR_RE.findall(text))
            elif kind == "assistant":
                ended = (ev.get("message") or {}).get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens")
                text = assistant_text(ev)
                if text:
                    last_role, last_text = "assistant", text
                    prs.update(PR_RE.findall(text))
    if turns == 0:
        return None
    mtime = path.stat().st_mtime
    name = title or ai_title or one_line(first, 70)
    tag = TAG_RE.match(name or "")
    project = TAGS.get(tag.group(1).strip().upper()) if tag else None
    if not project:
        words = re.findall(r"[a-z0-9]+", (name or "").lower())
        hits = [TAGS[w.upper()] for w in words if w.upper() in TAGS]
        # a customer name beats a topic tag
        project = next((h for h in hits if h not in TOPIC_TAGS), hits[0] if hits else None)
    question = last_question(last_text) if last_role == "assistant" else None
    # waits: Claude's turn ended, the next move is Maxim's. live: Claude works (a write in the last 10 minutes).
    # stopped: the turn broke off (Esc, a crash); Maxim must look at it too.
    if ended:
        st = "waits"
    elif time.time() - mtime < 600:
        st = "live"
    else:
        st = "stopped"
    return {"id": path.stem, "title": name, "project": project, "mtime": mtime, "state": st,
            "question": question, "prs": prs, "last": re.sub(r"\n{3,}", "\n\n", last_text.strip())[:1500]}


def user_text(ev):
    content = ev.get("message", {}).get("content")
    if isinstance(content, str):
        text = content
    else:
        text = " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
    if text.lstrip().startswith(("<command-", "<local-command", "<task-notification", "<system-reminder")):
        return ""
    return text.strip()


def assistant_text(ev):
    content = ev.get("message", {}).get("content") or []
    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text").strip()


def last_question(text):
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    for i in range(len(lines) - 1, max(len(lines) - 5, -1), -1):
        l = lines[i]
        context = " ".join(lines[max(i - 1, 0):i + 1])
        if ASK_RE.search(l) and not CLEANUP_RE.search(context) and not re.search(r"\b(ports?|servers?) .*\b(run|running)\b", context, re.I):
            return one_line(l.lstrip("*-# "), 140)
    return None


# ---------- ranking and state ----------

def card(**kw):
    kw.setdefault("project", None)
    kw["project"] = kw["project"] or "MISC"
    kw["age"] = round(max(kw.get("age") or 0, 0), 1)
    w = IMPACT.get(kw["impact"], 2) + WAITS.get(kw["waits"], 0) + min(kw["age"] / 3, 2)
    kw["score"] = round(w / max(kw["mins"], 1) * 10, 1)
    return kw


def rank(raw, state):
    today = datetime.date.today().isoformat()
    merged = None
    live, parked = [], []
    for c in raw:
        st = state["cards"].get(c["id"], {})
        closed = st.get("closed")
        if closed and closed.get("bind", "") == c.get("bind", "") and closed["verb"] != "defer":
            continue
        d = st.get("defer")
        if d:
            fired, merged = trigger_fired(d["trigger"], today, merged)
            # A deferred item stays on the desk; the pane shows it with the ⏰ icon until its trigger fires.
            if not fired and d.get("bind", "") == c.get("bind", ""):
                c = {**c, "parked": f"until {d['trigger']}", "deferAct": d.get("act", "")}
            else:
                st.pop("defer", None)
                c["note"] = (f"deferred act: {d.get('act')}. " if d.get("act") else "") + c.get("note", "")
        if c["id"] in state.get("pins", []):
            c["score"] += 1000
        live.append(c)
    live.sort(key=lambda c: -c["score"])
    return live, parked


def trigger_fired(trigger, today, merged):
    t = trigger.strip()
    m = re.match(r"date:(\d{4}-\d{2}-\d{2})", t)
    if m:
        return m.group(1) <= today, merged
    m = re.match(r"pr-merged:#?(\d+)", t)
    if m:
        if merged is None:
            out = run(["gh", "pr", "list", "--author", "@me", "--state", "merged", "-L", "60", "--json", "number"])
            merged = {str(p["number"]) for p in json.loads(out)} if out else set()
        return m.group(1) in merged, merged
    return False, merged


# The links as this run found them; save_state uses them to spot a link written meanwhile.
START_LINKS = {}


def load_state():
    st = load_json(DATA / "desk.json", {})
    START_LINKS.update(st.get("links", {}))
    st.setdefault("cards", {})
    st.setdefault("log", [])
    st.setdefault("pins", [])
    st.setdefault("focus", [])
    return st


def save_state(st):
    with open(DATA / ".desk.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        disk = load_json(DATA / "desk.json", {})
        # desk-close.py may have written while we gathered; its cards and log win
        for k, v in disk.get("cards", {}).items():
            st["cards"][k] = v
        st["log"] = disk.get("log", st["log"])
        st["pins"] = disk.get("pins", st["pins"])
        st["order"] = disk.get("order", st.get("order", []))
        # a link written by `desk.py link` during this gather (changed on disk since the start) wins
        for sid, cid in disk.get("links", {}).items():
            if cid.startswith("task:") and cid != START_LINKS.get(sid):
                st.setdefault("links", {})[sid] = cid
        st["ignored"] = sorted(set(disk.get("ignored", [])) | set(st.get("ignored", [])))
        if "titles" in disk and sys.argv[1:2] != ["title"]:
            st["titles"] = {**st.get("titles", {}), **disk["titles"]}
        if "--focus" not in sys.argv:
            st["focus"] = disk.get("focus", st["focus"])
        write_json(DATA / "desk.json", st)


STATE_RANK = {"live": 3, "waits": 2, "stopped": 1}
STATUS_ORDER = {"running": 0, "todo": 1}


def delete(cid, state):
    """desk.py delete task:<id>: removes an item. Its source never makes a new item,
    and the item is kept in data/deleted.json. Its sessions stay linked to the deleted id,
    so they make no new items either, and a restore gets them back."""
    with open(DATA / ".tasks.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_json(DATA / "tasks.json", {"tasks": []})
        t = next((t for t in doc["tasks"] if f"task:{t['id']}" == cid), None)
        if not t:
            sys.exit(f"no item {cid}")
        doc["tasks"].remove(t)
        write_json(DATA / "tasks.json", doc)
    kept = load_json(DATA / "deleted.json", {"deleted": []})
    kept["deleted"].append({**t, "deleted": datetime.date.today().isoformat()})
    write_json(DATA / "deleted.json", kept)
    if t.get("source"):
        state.setdefault("ignored", []).append(t["source"])
    state["prlinks"] = {n: tid for n, tid in state.get("prlinks", {}).items() if tid != t["id"]}
    save_state(state)
    print(f"{cid} deleted")



def restore(cid, state):
    """desk.py restore task:<id>: puts the last erased copy of an item back on the list (u in the pane)."""
    kept = load_json(DATA / "deleted.json", {"deleted": []})
    at = next((i for i in range(len(kept["deleted"]) - 1, -1, -1) if f"task:{kept['deleted'][i]['id']}" == cid), None)
    if at is None:
        sys.exit(f"no erased item {cid}")
    t = {k: v for k, v in kept["deleted"].pop(at).items() if k != "deleted"}
    with open(DATA / ".tasks.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_json(DATA / "tasks.json", {"tasks": []})
        doc["tasks"].append(t)
        write_json(DATA / "tasks.json", doc)
    write_json(DATA / "deleted.json", kept)
    if t.get("source") in state.get("ignored", []):
        state["ignored"].remove(t["source"])
        save_state(state)
    print(f"{cid} restored")


def sort_key(c, order):
    """Groups first: 🔶 in progress, ☑️ not started (done items follow in the pane).
    Inside a group: an item Maxim never moved comes first, so new work shows; then his k/j order; then the score."""
    group = STATUS_ORDER[c.get("sortTurn", c["turn"])]
    if c["id"] in order:
        return (group, 1, order.index(c["id"]), 0)
    return (group, 0, 0, -c["score"])


def place_children(cards):
    """Each child right under its parent, in their own order. A child whose parent is not open stays in place."""
    shown = {c["id"] for c in cards}
    kids = {}
    for c in cards:
        if c.get("parent") in shown:
            kids.setdefault(c["parent"], []).append(c)
    out = []

    def put(c):
        out.append(c)
        for k in kids.get(c["id"], []):
            put(k)

    for c in cards:
        if c.get("parent") not in shown:
            put(c)
    return out


def set_parent(child, parent):
    """desk.py parent task:<child> task:<parent>|none: makes child a sub-item of parent, or a top-level item again."""
    with open(DATA / ".tasks.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_json(DATA / "tasks.json", {"tasks": []})
        by = {f"task:{t['id']}": t for t in doc["tasks"]}
        if child not in by:
            sys.exit(f"no item {child}")
        if parent == "none":
            by[child].pop("parent", None)
        else:
            if parent not in by or parent == child:
                sys.exit(f"no item {parent}")
            if by[parent].get("parent"):
                sys.exit("That item is a child itself. Pick a top-level item.")
            if any(t.get("parent") == by[child]["id"] for t in doc["tasks"]):
                sys.exit("This item has children. It cannot become a child.")
            by[child]["parent"] = by[parent]["id"]
        write_json(DATA / "tasks.json", doc)
    print(f"{child} parent: {parent}")


def move(cid, way):
    """desk.py move <item-id> up|down: swap the item with its neighbour in the same group.
    Writes the new order to desk.json and cards.json at once, without a new gather."""
    with open(DATA / ".desk.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_json(DATA / "cards.json", {"cards": []})
        cards = data["cards"]
        ids = [c["id"] for c in cards]
        if cid not in ids:
            sys.exit(f"no open item {cid}")
        # an item moves among its siblings: children under one parent, or the top-level items
        shown = set(ids)
        home = lambda c: c.get("parent") if c.get("parent") in shown else ""
        me = cards[ids.index(cid)]
        siblings = [k for k, c in enumerate(cards) if home(c) == home(me)]
        at = siblings.index(ids.index(cid))
        other = at - 1 if way == "up" else at + 1
        if other < 0 or other >= len(siblings):
            sys.exit("This item cannot move further.")
        i, j = siblings[at], siblings[other]
        group = lambda c: c.get("sortTurn", c.get("turn"))
        if home(me) == "" and group(cards[i]) != group(cards[j]):
            sys.exit("An item moves only inside its group: 🔶 or ☑️.")
        cards[i], cards[j] = cards[j], cards[i]
        cards[:] = place_children(cards)
        st = load_json(DATA / "desk.json", {})
        st["order"] = [c["id"] for c in cards]
        write_json(DATA / "desk.json", st)
        write_json(DATA / "cards.json", data)
    print(f"{cid} moved {way}")


def done_list(st, sessions=(), links=None, merged=None):
    """Everything that got do, decide or drop, newest first. A later reopen takes the row out.
    Finished sessions without a card of their own count as done too."""
    tasks = {f"task:{t['id']}": t for t in load_json(DATA / "tasks.json", {"tasks": []})["tasks"]}
    rows, seen = [], set()
    # The time of the last done per item, from the pane's events: items closed before doneAt existed sort by it.
    done_ts = {}
    try:
        for line in open(DATA / "events.jsonl"):
            if '"done"' in line or '"close"' in line:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") in ("done", "close") and e.get("id"):
                    done_ts[e["id"]] = e.get("ts", 0)
    except OSError:
        pass

    def at(r, exact=0):
        day = datetime.datetime.fromisoformat(r["date"][:10]).timestamp() if r.get("date") else 0
        t = exact or done_ts.get(r["id"], 0)
        # An event time from another day belongs to an earlier close; the day of the row wins then.
        return t if t and datetime.date.fromtimestamp(t).isoformat() == r.get("date", "")[:10] else day
    for r in reversed(st["log"]):
        # items live in tasks.json; the log keeps only the rows of the old cards
        if r["id"] in seen or r["id"] in tasks or r["verb"] in ("defer", "pin", "unpin"):
            continue
        seen.add(r["id"])
        if r["verb"] == "reopen":
            continue
        rows.append({"id": r["id"], "verb": r["verb"], "date": r["date"], "note": r.get("note", ""),
                     "kind": KINDS.get(r["id"].split(":")[0], "Todo"),
                     "title": r.get("title") or r["id"], "project": r.get("project") or "MISC"})
    for tid, t in tasks.items():
        if t.get("done"):
            linked = sorted((s for s in sessions if (links or {}).get(s["id"]) == tid), key=lambda s: -s["mtime"])
            rows.append({"notes": t.get("notes", []),
                         "sessions": [{k: s[k] for k in ("id", "title", "state", "mtime", "last")} for s in linked],
                         "id": tid, "verb": t.get("doneVerb", "do"), "date": t["done"], "note": t.get("doneNote", ""), "kind": kind_of(t), "url": t.get("url", ""), "links": links_of(t),
                         "title": t["title"], "project": t.get("project", "MISC"), "doneAt": t.get("doneAt", 0)})
    for s in sessions:
        sid = f"session:{s['id'][:8]}"
        if s["id"] in (links or {}) or sid in seen:
            continue
        rows.append({"sessions": [{k: s[k] for k in ("id", "title", "state", "mtime", "last")}],
                     "id": sid, "verb": "session", "kind": "Session", "date": datetime.date.fromtimestamp(s["mtime"]).isoformat(),
                     "note": "", "title": s["title"], "project": s["project"] or "MISC", "session": s["id"]})
    # merged PRs that never had an item stay findable as history
    attached = set(st.get("prlinks", {}))
    for num, p in (merged or {}).items():
        if num not in attached and f"pr:{num}" not in seen and days_since(p["date"]) <= 14:
            rows.append({"id": f"pr:{num}", "verb": "merged", "kind": "PR", "date": p["date"], "note": "merged on GitHub",
                         "title": f"#{num} {p['title']}", "project": "MISC", "url": p["url"]})
    titles = st.get("titles", {})
    for r in rows:
        r["title"] = titles.get(r["id"], r["title"])
    # Newest close first: the exact close time when known, else the start of the close day.
    for r in rows:
        r["sortAt"] = r["sessions"][0]["mtime"] if r.get("verb") == "session" and r.get("sessions") else at(r, r.pop("doneAt", 0))
    rows.sort(key=lambda r: r["sortAt"], reverse=True)
    return rows[:500]


def closed_today(st):
    today = datetime.date.today().isoformat()
    return sum(1 for r in st["log"] if r["date"] == today)


def project_counts(cards):
    counts = {}
    for c in cards:
        counts[c["project"]] = counts.get(c["project"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# ---------- output ----------

def print_desk(out, show_all):
    cards = out["cards"]
    focus = f" · focus {','.join(out['focus'])} ({out['hidden']} hidden)" if out["focus"] else ""
    print(f"Desk: {len(cards)} waiting, {len(out['parked'])} parked, {out['closedToday']} closed today{focus}")
    print("  " + "  ".join(f"{k} {v}" for k, v in out["projects"].items()))
    print()
    for i, c in enumerate(cards if show_all else cards[:9], 1):
        print(f"{i:>2}. {c['kind']:<8} {c['project']:<8} {one_line(c['status'], 80)}")
        if c.get("note"):
            print(f"              {one_line(c['note'], 100)}")
    if not show_all and len(cards) > 9:
        print(f"    … {len(cards) - 9} more (desk.py --all)")


def print_wins(st):
    today = datetime.date.today().isoformat()
    rows = [r for r in st["log"] if r["date"] == today]
    print(f"Today: {len(rows)} cards got a verb")
    for r in rows:
        print(f"  {r['verb']:<6} {r['id']}  {r.get('note', '')}")


# ---------- helpers ----------

def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=PROFILE["workdir"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def days_since(iso):
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(iso[:10])).days
    except (TypeError, ValueError):
        return 0


def links_of(t):
    """The item's own links: its source url first, then the links Maxim pasted."""
    return list(dict.fromkeys(u for u in [t.get("url"), *t.get("links", [])] if u))


def first_url(text):
    m = re.search(r"https?://\S+", text or "")
    return m.group(0) if m else None


def one_line(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


if __name__ == "__main__":
    main()
