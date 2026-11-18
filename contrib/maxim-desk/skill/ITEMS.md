# Items

## Contacts

- A profile can have contacts (profiles.json "contacts"): one Markdown file per person, managed with `contact.py`.
- A contact has a kind (customer, investor, advisor, partner, network) and a stage (lead, contacted, talking, won, lost).
- A contact is a record, not an item. Its next steps are items, linked with `contact`.
- Every contact in contacted or talking has one open next step. The gather adds one when none is open.

## Profiles

- One engine serves several desks: a profile per project, listed in `~/desk/profiles.json`.
- The scripts find the profile by themselves: `DESK_PROFILE`, else the work folder of the session, else the active profile.
- Each profile has its own rules in PROFILE.md (session tags, meeting routing). Read it before you name a session or file a meeting.

## Items

- One item is one thing Maxim committed to. Every item lives in `tasks.json` in the profile data folder and has the id `task:<id>`.
- Every item works the same way: a title, a status, notes, links, meetings, PRs and Claude sessions.
- A Claude session started outside the desk makes an item by itself.
- A PR attaches to an item when the item names it, or when the one session that mentions it works on the item.
- Only Maxim closes an item, with x.
- Icons: ☑️ not started (no session or PR yet). 🔶 in progress (a session or a PR). ✅ done. Yellow (the queue) says when Maxim must jump in.

## Score

`score = (impact + waits + min(age_days / 3, 2)) / minutes × 10`

- impact: P0 = 6, P1 = 4, P2 = 2, P3 = 1
- waits: customer = 4, person = 3, agent = 1, nobody = 0

A pinned item gets +1000.

## Option set (recommended first)

Recipe: `task.py show <id>`. Search the memory index and recent session titles for the topic. Give the last known state in one line.
- a. Work it here now (verb do)
- b. Work it in a new session (verb do, print the session name and first prompt)
- c. Already done (verb do, note "already done")
- d. Defer (trigger)
- e. Drop (reason)

## Meetings
Claude files the meeting and routes it in one pass, with no choices. Maxim corrects the result in chat.

1. Read the transcript or the notes (Notion page, Granola, the inbox file, or pasted text). A text without a date gets today's date.
2. Route the file as the profile's PROFILE.md says (`python3 ~/desk/scripts/desk_profile.py notes` prints its path).
   - The file holds the full text under a short header: title, date, participants, source. Slack threads and PR links that come with it go into the file too.
3. Read the open items: `task.py list` and `cards.json` in the profile data folder (`desk_profile.py data`). Also read the done items of the last 7 days in tasks.json.
4. Find what the meeting says per item:
   - A topic of an open item: one note on that item.
   - A commitment of Maxim (he says he will do it, or a next step names him): first look for an open item with the same goal, also an older or wider one, best one with a session. Put the commitment there as a note. Make a new task only when no open item fits.
   - A done item that the meeting touches gets only the meeting link.
4b. Contacts (only when the profile has contacts, `contact.py list`):
   - Each external participant who is a contact: append one dated line to the contact file: date, meeting title, file path, the facts the person gave.
   - A new external participant gets a contact: `contact.py add "<name>" --kind <customer|investor|advisor|partner|network> --company <c> --source "<meeting title>"`.
   - Change a stage only on a stated fact (`contact.py stage <slug> <stage>`), for example "we start a pilot" means talking, a signed deal means won.
   - A next step for a contact becomes a task linked to it: `contact.py next <slug> "<step>"`.
5. Write it directly:
   - Notes: `task.py note <id> "<text>"`. One short fact, at most 80 characters, no date, no source prefix. Write no note that repeats a note on the item.
   - New tasks: `task.py add "<his words>" --project <P> --body "<meeting title, date, file>"`.
   - Links: `task.py meeting <id> <file>` on every item from step 4. PRs and URLs from the meeting: `task.py link <id> <url>` on the item they belong to.
   - Other steps that PROFILE.md names, such as history lines.
   - meetings.json: set `filed: true` and `filedTo`. For a file without an entry, add one: `id` = file name stem, `path`, `title`, `date`, `project`. 
6. Report in short bullets: the file path, a table of item and note, the new tasks, the history lines, and the next steps not added (no owner, or another owner).

Rules:
- Keep the terms of the source exactly (a "Pricing Flow" stays a "Pricing Flow").
- Never write open questions, follow-ups, risks or "next steps" that nobody said.
- Never turn a statement or a next step of somebody else into a task for Maxim. List it under "not added".
- Facts only, no summary of opinions.
- When Maxim moves a note to another item or drops a task, do it, and remove the old note or task so nothing repeats.

The `m` key in the pane saves the clipboard to the inbox folder of the profile and asks for this recipe at once.

## Triggers

`date:YYYY-MM-DD`, `tomorrow`, a weekday (`monday`), `pr-merged:<n>`. "Later" is no trigger.
