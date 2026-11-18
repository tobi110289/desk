#!/usr/bin/env python3
"""log-event.py '<json>': appends one pane action to data/events.jsonl, with a timestamp."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_profile import current  # noqa: E402

EVENTS = current()["data"] / "events.jsonl"


def main():
    try:
        row = json.loads(sys.argv[1])
    except (IndexError, json.JSONDecodeError):
        sys.exit("usage: log-event.py '<json>'")
    row = {"ts": round(time.time(), 3), **row}
    with open(EVENTS, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
