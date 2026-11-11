#!/usr/bin/env python3
"""desk-close.py <card-id> <verb> [note]

Verbs: do, decide, defer, drop.
  defer: desk-close.py <id> defer "<trigger>" "<act>"
  triggers: date:YYYY-MM-DD, tomorrow, monday..sunday, pr-merged:<number>
  pin / unpin: desk-close.py <id> pin   (keeps the card on top until it gets a verb)
A closure binds to the card's current state (session mtime, PR review state). New activity reopens the card.
A verb on a task: card marks the task done in tasks.json; defer keeps it open."""
import datetime, fcntl, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desk_profile import current  # noqa: E402
DATA = str(current()["data"])
P = os.path.join(DATA, "desk.json")
VERBS = ("do", "decide", "defer", "drop", "pin", "unpin", "reopen")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def trigger(raw):
    t = re.sub(r"^(until\s+)+", "", raw.strip().lower())
    today = datetime.date.today()
    if t == "tomorrow":
        return f"date:{today + datetime.timedelta(days=1)}"
    if t in DAYS:
        ahead = (DAYS.index(t) - today.weekday()) % 7 or 7
        return f"date:{today + datetime.timedelta(days=ahead)}"
    if re.match(r"\d{4}-\d{2}-\d{2}$", t):
        return f"date:{t}"
    if re.match(r"(date:\d{4}-\d{2}-\d{2}|pr-merged:#?\d+)$", t):
        return t
    sys.exit(f"no real trigger: '{raw}'. Use date:YYYY-MM-DD, tomorrow, a weekday, or pr-merged:<n>. 'later' is no trigger.")


def main():
    if len(sys.argv) < 3 or sys.argv[2] not in VERBS:
        sys.exit(__doc__)
    cid, verb, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    lock = open(os.path.join(DATA, ".desk.lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        d = json.load(open(P))
    except (OSError, json.JSONDecodeError):
        d = {}
    d.setdefault("cards", {}); d.setdefault("log", []); d.setdefault("pins", [])
    bind, title, project = "", cid, ""
    try:
        cards = json.load(open(os.path.join(DATA, "cards.json")))
        for c in cards["cards"] + cards["parked"]:
            if c["id"] == cid:
                bind, title, project = c.get("bind", ""), c.get("status", cid), c.get("project", "")
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    today = datetime.date.today().isoformat()
    c = d["cards"].setdefault(cid, {})
    if verb == "pin":
        d["pins"] = [p for p in d["pins"] if p != cid] + [cid]
        print(f"{cid} pinned")
    elif verb == "unpin":
        d["pins"] = [p for p in d["pins"] if p != cid]
        print(f"{cid} unpinned")
    elif verb == "reopen":
        # brings a closed card back; a done todo becomes open again
        c.pop("closed", None)
        if cid.startswith("task:"):
            sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
            import task
            task.reopen(cid)
        d["log"].append({"date": today, "id": cid, "verb": verb, "note": ""})
        print(f"{cid} reopened")
    elif verb == "defer":
        if not rest:
            sys.exit("defer needs a trigger")
        trig = trigger(rest[0])
        c["defer"] = {"trigger": trig, "act": " ".join(rest[1:]), "since": today, "bind": bind}
        c.pop("closed", None)
        d["log"].append({"date": today, "id": cid, "verb": verb, "note": f"until {trig}"})
        print(f"{cid} → defer until {trig}")
    else:
        note = " ".join(rest)
        c["closed"] = {"verb": verb, "date": today, "note": note, "bind": bind}
        c.pop("defer", None)
        d["pins"] = [p for p in d["pins"] if p != cid]
        if cid.startswith("task:"):
            sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
            import task
            task.mark_done(cid, f"{verb}: {note}")
        d["log"].append({"date": today, "id": cid, "verb": verb, "note": note, "title": title, "project": project})
        print(f"{cid} → {verb}")
    tmp = P + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, P)
    print(f"Closed today: {sum(1 for r in d['log'] if r['date'] == today)}")


if __name__ == "__main__":
    main()
