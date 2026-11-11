#!/usr/bin/env python3
"""desk_profile.py: the desk profile. One engine (scripts, pane, skill, shadow code), one profile per project.

  desk_profile.py              print the current profile name
  desk_profile.py list         print every profile, the active one marked
  desk_profile.py use <name>   make <name> the active profile (the pane does this on v)
  desk_profile.py next         make the next profile the active one, print its name
  desk_profile.py json         print the current profile as JSON (the pane reads it)
  desk_profile.py root|data|workdir|notes   print one path of the current profile

~/desk/profiles.json lists the profiles. A profile has a root folder with data/, inbox/, meetings/,
shadow/ and PROFILE.md (its own rules: session tags, meeting routing), a work folder where its sessions start,
and optionally a contacts folder (contact.py).
The current profile is DESK_PROFILE, else the profile whose work folder holds the current folder, else the active one."""
import json
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
REGISTRY = HOME / "desk" / "profiles.json"


def registry():
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": "work", "profiles": {}}


def expand(path):
    return Path(os.path.expanduser(path)) if path else None


def resolve(name, conf):
    root = expand(conf.get("root")) or HOME / "desk" / "profiles" / name
    workdir = expand(conf.get("workdir"))
    return {
        "name": name,
        "label": conf.get("label") or name,
        "root": root,
        "data": Path(os.environ["DESK_DIR"]) if os.environ.get("DESK_DIR") else root / "data",
        "workdir": workdir,
        # Claude Code keeps the sessions of a folder in ~/.claude/projects/<the path, each other character a dash>.
        "sessions": re.sub(r"[^A-Za-z0-9]", "-", str(workdir)) + "*" if workdir else None,
        "superset_project": conf.get("superset_project", ""),
        "tags": conf.get("tags", {}),
        "topic_tags": conf.get("topic_tags", []),
        "folders": conf.get("folders", {}),
        # The pane's tabs: tabs always show, in this order; order sorts the other projects once they have items.
        # The contacts folder (contact.py), or None: a profile without contacts has no contact view.
        "contacts": expand(conf.get("contacts")),
        "tabs": conf.get("tabs", []),
        "order": conf.get("order", []),
    }


def current():
    reg = registry()
    profiles = reg.get("profiles", {})
    name = os.environ.get("DESK_PROFILE", "")
    if name not in profiles:
        here = Path.cwd().resolve()
        name = next((n for n, c in profiles.items() if expand(c.get("workdir")) and
                     (here == expand(c["workdir"]).resolve() or expand(c["workdir"]).resolve() in here.parents)), "")
    if name not in profiles:
        name = reg.get("active", "work")
    return resolve(name, profiles.get(name, {}))


def use(name):
    reg = registry()
    if name not in reg.get("profiles", {}):
        sys.exit(f"no profile {name}; known: {', '.join(reg.get('profiles', {}))}")
    reg["active"] = name
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, REGISTRY)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    p = current()
    if cmd == "list":
        active = registry().get("active")
        for n in registry().get("profiles", {}):
            print(f"{'*' if n == active else ' '} {n}")
    elif cmd == "use":
        use(sys.argv[2])
        print(sys.argv[2])
    elif cmd == "next":
        names = list(registry().get("profiles", {}))
        name = names[(names.index(p["name"]) + 1) % len(names)] if p["name"] in names else names[0]
        use(name)
        print(name)
    elif cmd in ("json", "active"):
        if cmd == "active":
            reg = registry()
            p = resolve(reg.get("active", "work"), reg.get("profiles", {}).get(reg.get("active", "work"), {}))
        print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in p.items()
                          if k in ("name", "label", "root", "data", "workdir", "tabs", "order", "contacts")} | {"all": list(registry().get("profiles", {})),
                             "labels": {n: c.get("label") or n for n, c in registry().get("profiles", {}).items()}}))
    elif cmd in ("root", "data", "workdir"):
        print(p[cmd])
    elif cmd == "notes":
        print(p["root"] / "PROFILE.md")
    else:
        print(p["name"])


if __name__ == "__main__":
    main()
