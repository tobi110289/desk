#!/usr/bin/env python3
"""contact.py: the contacts of the current profile (a light CRM). One Markdown file per person.

  contact.py add "<name>" [--kind customer] [--company ..] [--role ..] [--email ..] [--phone ..] [--source ..] [--stage lead]
  contact.py list [--json]
  contact.py show <slug>
  contact.py stage <slug> <stage>
  contact.py set <slug> <field> <value>
  contact.py next <slug> "<next step>"   (a task linked to the contact, in the tab of its kind)

The files live in the profile's contacts folder (profiles.json "contacts"). Each file starts with a header:
---
name: ..., kind: ..., company: ..., role: ..., email: ..., phone: ..., stage: ..., source: ...
---
and below it free notes: call logs, facts, what the person said. Claude sessions read and write them directly.
Kinds: customer, investor, advisor, partner, network. Stages, the same for every kind: lead (not reached yet),
contacted, talking, won (a customer, an investment, an advisor on board), lost. A network contact can stay a lead.
Every contact in contacted or talking has one open next step; desk.py adds a "Next step" task when none is open.
The task goes to the tab of the kind: customer and partner to MARKET, investor, advisor and network to COMPANY."""
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_profile import current  # noqa: E402

STAGES = ["lead", "contacted", "talking", "won", "lost"]
ACTIVE = ("contacted", "talking")
KINDS = ["customer", "investor", "advisor", "partner", "network"]
TAB_OF_KIND = {"customer": "MARKET", "partner": "MARKET", "investor": "COMPANY", "advisor": "COMPANY", "network": "COMPANY"}
FIELDS = ["name", "kind", "company", "role", "email", "phone", "stage", "source", "since"]


def folder():
    path = current().get("contacts")
    if not path:
        sys.exit("This profile has no contacts (profiles.json: contacts).")
    path.mkdir(parents=True, exist_ok=True)
    return path


def read(path):
    text = path.read_text()
    head, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                head[k.strip()] = v.strip()
        body = m.group(2)
    head["slug"] = path.stem
    head["path"] = str(path)
    head.setdefault("stage", "lead")
    head.setdefault("kind", "customer")
    return head, body


def write(path, head, body):
    lines = ["---"] + [f"{k}: {head[k]}" for k in FIELDS if head.get(k)] + ["---", ""]
    path.write_text("\n".join(lines) + body.lstrip("\n"))


def all_contacts(base=None):
    base = base or current().get("contacts")
    if not base or not base.exists():
        return []
    return [read(p)[0] for p in sorted(base.glob("*.md"))]


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "contact"


def find(slug):
    path = folder() / f"{slug}.md"
    if not path.exists():
        hits = [p for p in folder().glob("*.md") if p.stem.startswith(slug)]
        if len(hits) != 1:
            sys.exit(f"no contact {slug}")
        path = hits[0]
    return path


def opt(args, name, default=""):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "add":
        name = args[0]
        slug, n = slugify(name), 2
        while (folder() / f"{slug}.md").exists():
            slug, n = f"{slugify(name)}-{n}", n + 1
        stage, kind = opt(args, "--stage", "lead"), opt(args, "--kind", "customer")
        if stage not in STAGES:
            sys.exit(f"stage must be one of {', '.join(STAGES)}")
        if kind not in KINDS:
            sys.exit(f"kind must be one of {', '.join(KINDS)}")
        head = {"name": name, "kind": kind, "company": opt(args, "--company"), "role": opt(args, "--role"), "email": opt(args, "--email"),
                "phone": opt(args, "--phone"), "stage": stage, "source": opt(args, "--source"),
                "since": datetime.date.today().isoformat()}
        write(folder() / f"{slug}.md", head, "")
        print(f"contact:{slug} added")
    elif cmd == "list":
        rows = all_contacts()
        if "--json" in args:
            print(json.dumps(rows, ensure_ascii=False))
            return
        for c in sorted(rows, key=lambda c: (STAGES.index(c["stage"]) if c["stage"] in STAGES else 9, c.get("name", ""))):
            print(f"{c['kind']:<9} {c['stage']:<10} {c.get('name', ''):<28} {c.get('company', ''):<24} {c['slug']}")
    elif cmd == "show":
        print(find(args[0]).read_text())
    elif cmd in ("stage", "set"):
        path = find(args[0])
        head, body = read(path)
        field, value = ("stage", args[1]) if cmd == "stage" else (args[1], " ".join(args[2:]))
        if field == "stage" and value not in STAGES:
            sys.exit(f"stage must be one of {', '.join(STAGES)}")
        if field == "kind" and value not in KINDS:
            sys.exit(f"kind must be one of {', '.join(KINDS)}")
        head[field] = value
        write(path, head, body)
        print(f"contact:{path.stem} {field} {value}")
    elif cmd == "next":
        path = find(args[0])
        head, _ = read(path)
        title = " ".join(args[1:]).strip() or f"Next step: {head.get('name', path.stem)}"
        task = str(Path(__file__).with_name("task.py"))
        tab = TAB_OF_KIND.get(head.get("kind", ""), "MARKET")
        out = subprocess.run([sys.executable, task, "add", title, "--project", tab, "--contact", path.stem],
                             capture_output=True, text=True)
        print((out.stdout or out.stderr).strip())
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
