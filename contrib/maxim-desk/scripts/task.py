#!/usr/bin/env python3
"""task.py: local todos for the desk. Stored in tasks.json of the current profile (desk_profile.py).

  task.py add "<title>" [--project ACME, default: guessed from the title] [--body "<context>"] [--mins N] [--impact P1|P2|P3] [--waits customer|person|nobody] [--id <slug>] [--contact <slug>]
  task.py contact <id> <slug>   (links the item to a contact of contact.py; "none" unlinks it)
  task.py list [PROJECT]
  task.py show <id>
  task.py done <id> [note]
  task.py edit <id> [--title ..] [--project ..] [--body ..] [--impact ..]
  task.py drop <id>
  task.py note <id> "<text>"   (adds a dated note line; links in the text go to the item's links.
                                The pane shows the date, so the text holds no date. One short fact, at most 80 characters.
                                --mine: a note Maxim typed in the pane; no length limit)
  task.py link <id> <url>...   (adds links to the item)
  task.py meeting <id> <path>...   (adds filed meeting transcripts to the item; an item keeps all of them)"""
import datetime, fcntl, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desk_profile import current  # noqa: E402
DATA = str(current()["data"])
P = os.path.join(DATA, "tasks.json")


# The lock of desk.py. A write here between its read and its write was lost before.
# It stays held until the process ends, so load and save are one step for other writers.
held = None


def load():
    global held
    if held is None:
        held = open(os.path.join(DATA, ".tasks.lock"), "w")
        fcntl.flock(held, fcntl.LOCK_EX)
    try:
        return json.load(open(P))
    except (OSError, json.JSONDecodeError):
        return {"tasks": []}


def save(d):
    tmp = P + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, P)


def add_links(t, urls):
    """Adds new links to the item, in order, without duplicates. Returns the links it added."""
    have = t.setdefault("links", [])
    new = [u for u in dict.fromkeys(urls) if u not in have and u != t.get("url")]
    have.extend(new)
    return new


def find(d, tid):
    tid = tid.removeprefix("task:")
    return next((t for t in d["tasks"] if t["id"] == tid), None)


def opt(name, default=None):
    if name not in sys.argv:
        return default
    i = sys.argv.index(name)
    v = sys.argv[i + 1]
    del sys.argv[i:i + 2]
    return v


def guess_project(title):
    """A [TAG] or a customer word in the title names the project, as desk.py reads session names."""
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from desk import TAGS, TOPIC_TAGS
    tag = re.match(r"\s*\[([^\]]+)\]", title)
    if tag and tag.group(1).strip().upper() in TAGS:
        return TAGS[tag.group(1).strip().upper()]
    hits = [TAGS[w.upper()] for w in re.findall(r"[A-Za-z0-9]+", title) if w.upper() in TAGS]
    return next((h for h in hits if h not in TOPIC_TAGS), hits[0] if hits else "MISC")


def reopen(tid):
    d = load()
    t = find(d, tid)
    if not t:
        return False
    for k in ("done", "doneNote", "doneVerb", "autoDone"):
        t.pop(k, None)
    # Maxim opened it himself: its source never closes it again
    t["manual"] = True
    save(d)
    return True


def mark_done(tid, note=""):
    d = load()
    t = find(d, tid)
    if not t:
        return False
    t["done"] = datetime.date.today().isoformat()
    t["doneAt"] = time.time()
    t["doneNote"] = note
    t["doneVerb"] = "drop" if note.startswith("drop") else "do"
    t.pop("autoDone", None)
    save(d)
    return True


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    d = load()
    if cmd == "add":
        project = (opt("--project", "auto") or "auto").upper()
        given_id = opt("--id")
        body, mins, impact, waits = opt("--body", ""), int(opt("--mins", "15")), opt("--impact", "P2"), opt("--waits", "nobody")
        contact = opt("--contact")
        title = " ".join(sys.argv[2:]).strip() or sys.exit(__doc__)
        if project == "AUTO":
            project = guess_project(title)
        s = given_id or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "task"
        taken = {t["id"] for t in d["tasks"]}
        base, n = s, 2
        while s in taken:
            s, n = f"{base}-{n}", n + 1
        d["tasks"].append({"id": s, "project": project, "title": title, "steps": [], "body": body, "mins": mins,
                           "impact": impact, "waits": waits, "since": datetime.date.today().isoformat()})
        if contact:
            d["tasks"][-1]["contact"] = contact.removeprefix("contact:")
        save(d)
        print(f"task:{s} added ({project})")
    elif cmd == "list":
        only = sys.argv[2].upper() if len(sys.argv) > 2 else None
        for t in d["tasks"]:
            if not t.get("done") and (not only or t["project"] == only):
                print(f"task:{t['id']:<42} {t['project']:<8} {t['impact']}  {t['title']}")
    elif cmd == "show":
        t = find(d, sys.argv[2]) or sys.exit(f"no task {sys.argv[2]}")
        print(json.dumps(t, indent=1, ensure_ascii=False))
    elif cmd == "edit":
        t = find(d, sys.argv[2]) or sys.exit(f"no task {sys.argv[2]}")
        for k in ("title", "project", "body", "impact", "waits"):
            v = opt(f"--{k}")
            if v is not None:
                t[k] = v.upper() if k == "project" else v
        save(d)
        print(f"task:{t['id']} edited")
    elif cmd == "done":
        print("done" if mark_done(sys.argv[2], " ".join(sys.argv[3:])) else f"no task {sys.argv[2]}")
    elif cmd == "note":
        mine = "--mine" in sys.argv
        args = [a for a in sys.argv if a != "--mine"]
        t = find(d, args[2]) or sys.exit(f"no task {args[2]}")
        text = " ".join(args[3:]).strip() or sys.exit("empty note")
        urls = add_links(t, re.findall(r"https?://\S+", text))
        text = " ".join(re.sub(r"https?://\S+", " ", text).split())
        # The pane shows the date of each note, so a date in the text only repeats it.
        text = re.sub(r"^(?:[\w-]+ )?(?:call |meeting )?\d{4}-\d{2}-\d{2}:?\s*", "", text)
        if not mine and len(text) > 80:
            sys.exit(f"note not saved: {len(text)} characters. Write one short fact for Maxim, at most 80 characters, no date. Put details in the item's session, not in a note.")
        if text:
            t.setdefault("notes", []).append({"date": datetime.date.today().isoformat(), "text": text})
        save(d)
        print(f"task:{t['id']} note added" if text else f"task:{t['id']} {len(urls)} link(s) added")
    elif cmd == "link":
        t = find(d, sys.argv[2]) or sys.exit(f"no task {sys.argv[2]}")
        add_links(t, sys.argv[3:]) or sys.exit("no link")
        save(d)
        print(f"task:{t['id']} link added")
    elif cmd == "meeting":
        t = find(d, sys.argv[2]) or sys.exit(f"no task {sys.argv[2]}")
        have = t.setdefault("meetings", [])
        new = [os.path.abspath(os.path.expanduser(m)) for m in sys.argv[3:]]
        new = [m for m in dict.fromkeys(new) if m not in have]
        new or sys.exit("no new meeting")
        missing = [m for m in new if not os.path.exists(m)]
        missing and sys.exit(f"no file: {missing[0]}")
        have.extend(new)
        save(d)
        print(f"task:{t['id']} meeting added")
    elif cmd == "contact":
        t = find(d, sys.argv[2]) or sys.exit(f"no task {sys.argv[2]}")
        slug = sys.argv[3].removeprefix("contact:") if len(sys.argv) > 3 else "none"
        if slug == "none":
            t.pop("contact", None)
        else:
            t["contact"] = slug
        save(d)
        print(f"task:{t['id']} contact {slug}")
    elif cmd == "drop":
        t = find(d, sys.argv[2]) or sys.exit(f"no task {sys.argv[2]}")
        d["tasks"].remove(t)
        save(d)
        print(f"task:{t['id']} dropped")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
