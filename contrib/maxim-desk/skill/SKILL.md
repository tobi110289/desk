---
name: desk
description: Work Maxim's desk, everything that waits on him (his tasks, each with its Claude session, notes, meetings and PRs), ranked, one item at a time. Use when Maxim types /desk, says "work this item", "put X on my desk", or asks what waits on him.
argument-hint: "[next | wins | focus ACME,GLOBEX | focus all | add <title> | <item-id>]"
---

# Desk

The desk is the one place where everything that waits on Maxim lands. Each item ends in exactly one **verb**: `do`, `decide`, `defer` (with a real trigger), or `drop` (with a reason).

Scripts live in `~/desk/scripts`. Each profile (for example work and a side project) has its own data and its own PROFILE.md; see Profiles in ITEMS.md. The option set and the meeting recipe live in [ITEMS.md](ITEMS.md).

## Hard rules

- No outward actions. The agent never sends a message, never merges, never publishes an SOP or instruction, never writes to production. Maxim does those himself.
- No drafts to humans. A item for a reply shows the facts. Maxim writes the text.
- Never stop local servers, processes or worktrees.
- Never use the AskUserQuestion tool. Present options with the `desk_choices` tool. Without the tool, use a plain numbered list.
- Maxim has ADHD. Every desk output follows these rules, always:
  - At most five bullets. One fact per bullet. Each bullet has 12 words or fewer.
  - ASD-STE100 Simplified Technical English: active voice, present tense, one meaning per word, no idioms, no metaphors.
  - No paragraphs, no rendered tables, no em dashes, no code names in prose.
  - The item `fact` has 15 words or fewer. Each `context` line has 12 words or fewer. Each option label has 6 words or fewer.

## Arguments

- none: Step 1 to Step 3 for the top item.
- `next`: Step 3 for the top item. No new gather.
- `wins`: run `python3 ~/desk/scripts/desk.py --wins`, print it, stop.
- `focus ACME,GLOBEX`: run `desk.py --focus ACME,GLOBEX`. Only these projects show. `focus all` shows every project again.
- `add <title>`: run `task.py add "<title>" --project <P>`. Guess the project from the title (customer name or topic). Say the id in one line. Stop.
- a item id (`task:…`, `pr:5643`, `session:2883c8db`): Step 3 for that item.

## Meetings

A pasted transcript in the chat goes to `<profile root>/inbox/<YYYY-MM-DD>-<topic>.md` as is (`desk_profile.py root`). Then file it with the Meetings recipe in ITEMS.md.

## Step 1: Gather

Run `python3 ~/desk/scripts/desk.py`. Print the output verbatim in a code block. The ranking is the script's. Do not reorder.

## Step 2: One line

`Desk: N waiting, K closed today.` Then work the top item.

## Step 3: Work one item

1. Read the item from `cards.json` in the profile data folder. Fetch what the recipe in ITEMS.md asks for. Keep it under two minutes.
2. Present it with the `desk_choices` tool: `item_id`, `fact` (the one fact that decides it), `context` (at most three short lines), `options` from the option set in ITEMS.md, recommended first. End the turn.
3. Maxim's pick arrives as `Re desk item <id>: <option>`. Execute it per the recipe.
   His own words can arrive in place of an option, such as `Re desk item <id>: idc`. Map them to a verb. "idc", "not mine", "skip" mean drop. "next week" means defer with a date. When the words are unclear, ask one short question.
4. Record it with the `desk_close` tool (`item_id`, `verb`, `note`; for defer also `trigger` and `act`). Without the tool, run `python3 ~/desk/scripts/desk-close.py <id> <verb> "<note>"`.
5. Say one line: `Item <id> closed (<verb>). Closed today: K.` Name the item now on top. Stop.

## Working a item in a new session

When the pick is "Work it in a new session", give the session name in Maxim's form: `[PROJECT] short topic`. The project tags are in the profile's PROFILE.md. The topic has two to four words.

Print the first prompt for that session in a code block: the item title, the context, the links. Maxim opens it in a new Superset workspace or terminal.

## Putting things on the desk

When Maxim says "put X on my desk" or pastes todos, add each open item with `task.py add`. One task per open checkbox. Skip checked items. A checked item with an open child item becomes one task for the child. Keep Maxim's words. Do not invent follow-ups or open questions.

## Sessions

- A session works on one item. Enter on an item opens its newest session, or a new linked one, in its own Superset workspace.
- A session started outside the desk gets an item of its own. It closes by itself when the session finishes.
- To rename a session, run `python3 ~/desk/scripts/desk.py rename <id> "[TAG] topic"`.
- A name always has Maxim's form: `[TAG] topic`, topic two to four words.

## Words

- Say "item", never "card". An item is one row in the pane list.
- Say "the choices in the pane", never "the card on the pane".
