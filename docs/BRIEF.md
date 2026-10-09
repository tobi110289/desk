# Build me a "desk": one place where everything that waits on me lands

Hi agent. Tobias runs a system called the desk with his Claude Code. I want the same shape for my own loops. This document is his full brief: the idea, the parts, the rules that came out of five weeks of use, and the traps. Read all of it, then start with the interview in section 1. Do not build before the interview is done.

> Written 2026-10-08, when the desk still ran in sittings (six cards, 25 minutes). One day later the sitting went away: the pane stays open, ranks again by itself, and each card starts when its owner says so. Read "sitting" below as history; `skill/SKILL.md` has the current procedure.

## 0. The idea in five sentences

The desk is the one place where everything that waits on me lands: a reply I owe, a decision somebody needs, a document to sign off, an agent run that only needs a "go". A sitting works the desk for a bounded stretch: six cards, about 25 minutes. Each card is a brief: one physical act with its consequence already drafted, and it ends in exactly one verb. The agent pushes right: it does every bit of preparation before the checkpoint, so I am asked once, late, with everything ready, and then it executes. I only choose.

The verbs are fixed: `do` (agent finishes it now), `decide` (one closed question, then the agent acts), `delegate` (agent drafts, I say send), `defer` (a trigger plus an act), `drop` (with a reason).

## 1. Interview me first (one question at a time, with options)

Ask me these in order. Give me numbered options and a recommended one. Write my answers to a file (`~/desk/CONTEXT.md`) before you build anything.

1. Which loops wait on me? Candidates: customer threads I own (which tool: Plain, email, Slack), Slack DMs and mentions I never answered, Notion tickets or pages that wait for my decision or sign-off, the sprint board items marked for me, sales or partner conversations (which CRM or inbox), investor and board items, hiring steps, contracts and invoices waiting for signature, calendar follow-ups (meetings without notes or next steps), agent tasks and PRs waiting for my "go". Ask me to pick, and ask what I forgot.
2. For every loop I picked: where does the truth live (tool, API, export, local file), is there read access from my machine (API key, MCP server, CLI), and what makes an item "waiting on me" in that tool (a status, a label, who spoke last, a due date).
3. Which outward actions may the agent ever run on its own? Default: none. A customer reply, a Slack message to a person, a signature, a payment: only on the explicit option that names it.
4. How long is a sitting (default six cards, 25 minutes), and how many sittings a day?
5. Who else must be able to see the desk (a board page for the team), or is it mine alone?
6. Which language for customer text, and the address form rules (du/Sie per contact).

## 2. The parts, in build order

Build in this order. Each step is useful on its own. Do not start the next before the previous one runs.

### 2.1 The gather script (`desk.py`)

One script reads every source and prints the ranked list. It is read-only against every source and writes only its own two files:

- `desk.json`: my state. Closures (`cards[id].closed = {verb, date, note, status}`), deferrals (`cards[id].defer = {trigger, act, since, status}`), seen counts, captions I wrote, pins, and the done log (one row per verb).
- `cards.json`: the output of the last run: the live cards (ranked), the parked cards, and the pre-flight block.

A card is a dict: `id`, `ref` (the thing's own id), `lane`, `mins`, `impact` (P0 to P2), `age` in days, `waits` (customer, person, agent, nobody), links (`thread`, `pr`, `url`), `status` (the fact the card is built on, under 60 chars), `note`, `act` (the imperative sentence), `score`.

Score = (impact weight + wait weight + min(age/3, 2)) / minutes × 10. Who waits counts more than how old. A card that waits on a customer outranks one that waits on nobody.

Lanes are fixed per source. Each lane has one unit (one card per item, or one batch card with members), fixed minutes, and a fixed option set with the recommended option first. Write them in a table in `CARDS.md`. Examples from Tobias's desk: SEND (a line I typed into an agent chat but never submitted), REVIEW (a PR needs a human call), REPLY (a drafted reply waits for "send"), NOTIFY (a fix is live, the customer has not heard), PROMISE (we wrote last and nobody works it), RUN (an agent action that only needs a go), STALE (an entry with no caption), INBOX (a decision somebody put to me), TICKET (a follow-up ticket we filed that nobody moves).

### 2.2 The skill (`SKILL.md` + `CARDS.md`)

A slash command `/desk` with four steps. Keep the steps short. Everything per lane goes to `CARDS.md`.

- Step 0, pre-flight (silent, under 30 s): run the gather script; if an executor is due (an agent loop, a harvest), start it in the background and print one line. Never wait for it; its results are the next sitting's cards.
- Step 1, gather and rank (silent): print the header and the ranked six verbatim. The ranking is fixed for the sitting; a card can be deferred but never reordered.
- Step 2, open the sitting (one line): `Sitting: 6 cards, ~N min. Closed today so far: K.` Then reveal card 1 only.
- Step 3, work one card: push right (fetch what the verb needs, draft the default), present (one question, header = lane, options = the lane's fixed set, recommended first), execute the chosen verb, record (`desk-close.py <id> <verb> "<note>"`), restate (`Card 3 of 6 closed (<verb>). Closed today: K.`), reveal the next card.
- Step 4, close the sitting: after six cards, 25 minutes, or an empty desk, print the wins as links and ONE next action.

Arguments that paid off: `next` (top card only), `wins` (what closed today), `<ref>` to pin an item to the top until it gets a verb, a scope (one customer, one project, `today`).

### 2.3 The close script (`desk-close.py`)

Records the verb, stores the member set of a batch, stores the caption the card was closed with, appends to the log. A `defer` stores a trigger and an act; "later" is never a trigger.

### 2.4 The pane (a Claude Code mod)

Claude Code has mods: plugins of function hooks that draw a live pane, a band above the prompt, a status line entry, and can block tool calls. The `plugin-authoring` skill writes them. Tobias's desk mod does: a pane with the ranked cards (hotkeys 1 to 9 select, `w` sends "work this card" to the session), a drawer with the live numbers of each source, a band during a sitting (card N of 6, minutes left), a status line entry (`desk: N waiting`), and two guards (an outward tool call is denied unless I typed a one-shot allow command; a close without a caption is denied). Load it in every session through `CLAUDE_CODE_PLUGIN_DIRS` in the `env` block of `~/.claude/settings.json`.

Build the pane last. The scripts and the skill are the desk; the pane is the window.

## 3. Rules that came out of use (keep all of them)

- Touch once. Every card ends in one verb. No card is read twice without a verb.
- A brief, not a draft. The card arrives with the consequence drafted and the deciding fact on top. I confirm or edit, never compose from scratch, never read raw material.
- One card on screen. The next card stays hidden until this one has its verb.
- Appetite over willpower. A card that overruns its minutes goes back with `defer` and a re-bet note; the sitting ends on count or clock, never on "one more".
- Expiry. A card seen in three sittings without a verb is marked EXPIRING and its options collapse to defer or drop.
- Outward actions run only on the explicit option that names them. Everything else the agent does at once.
- Every claim in a customer draft is checked against live data before the draft is offered. Otherwise the card carries the open checks, not a draft.
- Context informs, never speaks. The agent reads what it knows about the contact (role, address form, history) and the customer text never quotes it.
- A deferral is bound to the fact it was written for. When the fact changes (a new caption, a new message), the wait is over and the card comes back.
- A closed batch reopens on new members. A batch closed today comes back as soon as a member appears that was not in the closed set.
- Caption first, then close. When the close writes a status back to a source, write the status in the same command as the close. The other order reopened every card the same sitting.
- Never write the liveness field of another system. The desk writes a status caption and a note marker, nothing else. The other system's `updated` is its own signal.
- The desk never runs an executor inside a sitting. Code work, long agent runs and validation belong to their own loops; the desk dispatches them at pre-flight and clears what waits on a person.
- A shipped thing waits for its release, not for a person. Defer a customer note until a deploy with a machine-checkable trigger (`deployed:<env>:<pr>`), so the desk fires it by itself.
- Questions to teammates go out on one channel. A card that waits on a teammate's decision is no card for me; it is a message to them with a trigger.
- The board reads the desk. If a team page exists, it groups rows by the desk's state (waiting on me, waiting on others, parked with trigger), never by its own guesses.
- The desk writes its own files only. Every source is read-only. A production write is never automatic.

## 4. Traps we hit (so you do not)

- A gather that rewrites shared files in a scoped run (one customer, one day) shrinks the board for everybody. Scoped runs never write the shared files.
- A closure recorded before captions existed hid a card for six days. Every closure stores the status it was closed with.
- A batch closed on day one hid a new member on day two. Batch closures expire at midnight.
- Relative dates in a status ("5d") change every day and voided every deferral bound to them. Compare without the age.
- The agent's own idle chats were full of lines typed but never submitted. Read the worker screens before any caption rule; a staged line is a card.
- A regex cannot catch every caption. A small classifier (Tobias uses TypeSafe Jev) lifts the rows the regex leaves, at a confidence threshold, and can be turned off with an env var.
- The question dialog hides the pane. The pane is for between questions; the next step is to put the options on the card as buttons.
- Mods: the first session after a new mod may refuse it ("rollout switch saved off"); two headless `claude -p` runs refresh it. Superset's terminal eats ctrl+x, bind `ctrl+k` to `abovePrompt:focus` in `~/.claude/keybindings.json`. A pane opened by a command may take the keyboard (`focus: true`), one opened by a timer waits for 144 columns. Project MCP servers must be approved in every session, or a mod that reads through them says "no server".
- Write each mod change as one save. An open session hot-reloads between two half-edits and throws.
- Test the drawing in tmux: `tmux new-session -d -s desk -x 190 -y 60 "claude"`, send `/desk-pane`, `capture-pane -p`.
- A formatter hook rewrites files after every write; string replacements on freshly written code then miss silently. Read the file before you edit it again.

## 5. What to deliver first

1. `~/desk/CONTEXT.md` with my interview answers and the lane table.
2. `desk.py` with two or three sources working and the ranked print. Show me a run.
3. `/desk` with Steps 0 to 4 and `desk-close.py`. Run one sitting with me.
4. Then the next sources, one at a time, each with its lane row in `CARDS.md`.
5. The pane last.

Ask me whenever a lane's option set or a trigger is unclear. Do not invent a source I did not name.
