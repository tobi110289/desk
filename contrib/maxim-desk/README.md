# maxim-desk: a fork in spirit, for picking

Maxim's desk started from this repo on 2026-10-14 (after our call) and then grew in its own direction: less "one prepared card at a time", more "every task, session and PR in one keyboard list, with each session in its own Superset workspace". This folder is a snapshot of that code, cleaned of company data. It is not meant to merge as is. Take what you like.

Layout: `mod/` is the Claude Code mod (the pane), `scripts/` the Python side, `skill/` the `/desk` skill.

## What may be worth stealing

**One list for everything**
- Every item is a todo, a Claude session, a PR, a meeting or a contact follow-up. Sources create and close items; sessions and PRs attach to items. A session without an item gets one; sessions with the same name share one.
- Project tabs from session title tags (`[ACME] fix login`), with an open count and a color per project. Number keys jump to a tab.
- One level of sub-items: `l` nests an item under a parent; the list draws tree marks, the body shows Parent and Children.
- `k`/`j` reorder by hand; the order sticks until newer activity moves an item up.
- Done items stay in the list below the open ones, newest close first, with `x` to reopen.

**The queue (`q`)**
- An item enters the queue when its session goes idle (Claude finished a turn) or a reminder comes due; a short sound plays on arrival. Opening or answering takes it out.
- A moon spinner on rows whose session works right now (`scripts/live.py` reads only the tail of each transcript, every few seconds).
- Yellow marks the item whose session went idle in the last minutes; `y` marks or unmarks by hand.

**Sessions as first-class rows**
- Enter opens the item's session in its own Superset workspace (`scripts/open-session.py`), tagged by project, or starts a new one with the item as context. A lock stops a double Enter from starting two sessions.
- `w` writes a message into the session's terminal without leaving the desk (`scripts/send-message.py`); a session that does not run is resumed with the message as its first prompt.
- `w` attaches the clipboard screenshot when there is a new one; backspace drops it for this message only.
- The body shows the session's last messages in a small terminal-like inset; `p` expands it. `c` copies Claude's last message as plain text.

**Typing**
- One command bar for all text: search (`s`, filters on every key), new todo (`t`), rename (`r`), note (`n`), remind (`z`), write (`w`).
- `z` reminders in plain language ("tomorrow 9 check the API fields", "in 2 hours"); the item shows ⏰ until then.
- Snippets (`f`, or `;;` while writing): saved texts from `snippets.json`, filtered as you type.
- `u` undoes the last done, erase, move or nest.

**Profiles**
- `scripts/desk_profile.py`: one engine, several desks (for example work and a side project), each with its own data, tags, tabs and work folder; `v` switches. A profile can have a contacts view (`i`) backed by markdown files (`scripts/contact.py`).

**Running the desk on a remote Linux box**
- `scripts/mac.py` routes the Mac-only acts (open links, VS Code, clipboard, sound, show a Superset workspace) and `scripts/clip-image.py` reads clipboard screenshots. On macOS they run locally; elsewhere they write one JSON line to `~/desk/bridge/outbox.jsonl`.
- `scripts/desk-bridge.py` runs on the Mac (a launchd agent), follows that file over SSH and does the act. The connection goes only from the Mac to the box, and the bridge accepts only a fixed set of acts and URL schemes.

**Small things**
- Deferred items stay visible with ⏰ instead of disappearing.
- The done list sorts by the exact close time (`doneAt`), with the event log as fallback for older items.
- Meetings: `m` files a pasted transcript to the selected meeting item, or starts a new one.

## Setup notes

- Profiles live in `~/desk/profiles.json`: `tags` maps title tags to projects, `topic_tags` lists cross-project tags that yield to a project tag, `folders` maps projects to folders in the work repo, `superset_project` is the Superset project id, `workdir` the repo where sessions start.
- The mod loads through `CLAUDE_CODE_PLUGIN_DIRS`; `/desk-pane` opens the pane.
