# desk

One place where everything that waits on me lands: a customer reply I owe, a pull request stuck at changes-requested, a line an agent typed and never sent, a decision somebody needs. Claude Code gathers it, ranks it, prepares each item to the point of one choice, and executes the choice.

This is my working setup, published as a worked example. It runs against my company's tools (Plain, GitHub, Notion, an agent loop, a support bot), so it will not run for you as it is. Read it for the shape, then build your own; `docs/BRIEF.md` is the brief I give an agent for that.

## The idea

- A **card** is one act with its consequence already drafted. The agent does all preparation first ("push right"), so I am asked once, late, with everything ready.
- Every card ends in one of five **verbs**: `do`, `decide`, `delegate`, `defer` (a trigger plus an act), `drop` (a reason).
- The desk is **live**. A pane in Claude Code shows the ranking and reruns the gather script every 10 minutes and after every close. I start a card when it is worth it; nothing opens and nothing closes.
- New customer threads of the day rank first, customer answers of the day second, everything else after.
- Outward actions (a customer reply, a message to a person, a merge) run only on the option that names them.

## What is in the repo

| Path                     | What it is                                                                                                                                                               |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mod/`                   | The Claude Code mod: the pane, the band above the prompt, the status line count, two tools (`desk_card`, `desk_close`), the guards, and the checks that send Claude back |
| `skill/SKILL.md`         | The `/desk` procedure                                                                                                                                                    |
| `skill/CARDS.md`         | Sources, lanes and the recipe for each lane                                                                                                                              |
| `scripts/desk.py`        | Gathers from every source, ranks, writes `cards.json`                                                                                                                    |
| `scripts/desk-close.py`  | Records a verb (the mod calls it through `desk_close`)                                                                                                                   |
| `scripts/desk-decide.py` | Records a pick taken on the pane; `desk-execute.py` runs the queue in the background; `deskanchor.py` and `deskclass.py` are their shared tables                          |
| `scripts/deskhold.py`    | The one hold logic: a card's wait (`key`, `since`), closed / parked / live, the ranking tiers; used by the gather, the close script and the live feed                        |
| `scripts/desk-score.py`  | How often the prepared briefs guessed the user's pick and draft right (`desk-close.py` writes the ledger)                                                                |
| `scripts/desk-events.py` | The client for the support bot's live event feed (see Live events)                                                                                                       |
| `scripts/` (rest)        | Plain numbers for the pane, Notion ticket classes, support memory, the send path, local tasks                                                                            |
| `docs/BRIEF.md`          | The brief for an agent that builds a desk for somebody else                                                                                                              |
| `config.example.json`    | Every company id the code reads                                                                                                                                          |

## How a card runs

1. `w` on the pane (or `/desk`) starts the top card.
2. Claude prepares it per the lane recipe and calls `desk_card`: the pane shows the deciding fact, the draft, and the lane's options with hotkeys `a` to `j`.
3. A key press sends the pick as my own message. No dialog covers the pane.
4. Claude executes the pick and calls `desk_close` with the verb. The tool refuses a close without its caption and a defer without a real trigger.
5. The pane ranks again in the background.

Two checks run when Claude ends a turn. A reply that says a message went out while no send ran is sent back. So is a turn that ends on an answered card without a verb.

## Picks run in the background

Since 2026-10-23 a prepared card shows its options on the pane, and a key press records the pick as a file (`scripts/desk-decide.py`, `decisions/<id>.json`) instead of a prompt. `scripts/desk-execute.py` drains the queue without the session: it reads the brief's anchors again (`scripts/deskanchor.py`: the newest message on the thread, the PR's head and review state) and bounces the pick when the world moved, else it sends, marks done, drops or defers and records the verb. The pane shows the last picks with their state (waiting, running, done, bounced, failed) and lists the picks that need the session under NEEDS YOU; `w` on such a card hands it to Claude with the option already chosen. A handoff, a run card's go and a PR close run as one headless model run (`claude -p` with Bash, Read, Glob and Grep, no MCP) that gets the brief, its raw material, the lane recipe and the one option and answers with a verdict; `desk-execute.py --rehearse` runs that chain without any outward act. `scripts/deskclass.py` is the one table that says which option is a script, a model run or session work. `luna-send.py` refuses a send under a decision record whose option is not a send, so no background run sends on its own. What a brief or a run learned about a tenant or a requester queues in `scripts/desk-harvest.py`; `m` on the pane asks all of it in one memory question. `scripts/desk-score.py` adds the picks per class (ran, bounced, failed, median time from pick to end).

## Plain words on the pane

Every card has a fixed shape. WHY says what put it on the desk, since when, and who waits. DECIDE is the one question. THEN says what the recommended pick sets in motion and what is at stake if nothing happens. The pane shows the three as labelled lines above the fact and the draft, and `desk_card` refuses a card that lacks one. Prepared briefs carry them too.

The pane is read by someone who has not opened the file, often at the end of a long day. A card's fact is one plain sentence under 140 characters. A batch or task draft is at most three numbered lines, each naming the thing in product words, whether it is still true, and the verb. File paths, code names, commit hashes, ids, status values and backticked code go into the file, the close note or the PR. PR numbers, thread refs, ticket ids, dates and amounts stay.

`desk_card` enforces it. It refuses a fact over 140 characters, a draft with more than three numbered items or an item over 220 characters, why, decide or then lines over 160, 160 or 300 characters, and any of these in any of the five fields: a backtick, a file path or a file name with a code extension, a slug path, a UUID, 8 or more hex characters with a digit, camelCase or PascalCase of three or more parts, snake_case, an ALL_CAPS token of six or more letters, kebab-case of three or more parts, a triage status value, or a function call. Links are skipped. The refusal names each word, so Claude rewrites and calls again. A customer reply skips the item cap, not the ban. `desk-prepare.py` gives the same rule to the headless run, whose `checks` and `openChecks` may still carry identifiers.

## Live events

With `"liveEvents": true` in `config.json`, the pane hears about a new Plain thread or a customer message within seconds, not at the next 10-minute gather.

- `desk-events.py poll` asks the support bot's adapter for the events after the stored cursor. It is one `ssh` round trip that runs `curl` on the box, the same path `luna-send.py` takes, so the token never leaves the box. The adapter holds the call up to 20 s and answers as soon as an event lands. Each event goes to `events.jsonl`; `desk-events.py tail` prints the last ones.
- `desk.py --event <json>` puts one event on `cards.json` in well under a second. A new thread gets its `today:<ref>` card at once, ranked like the gather ranks it. A customer message lifts the thread's card. A thread set to DONE loses its `today:` card. A reply or a triage note rewrites the caption.
- The mod runs the loop while the pane is open: poll, apply, redraw, poll again. It waits for a running gather, backs off 60 s after a failed round, and runs in one session per machine. The pane line says `live · last event 12:34Z`, `live · quiet` or `feed off: <reason>`.
- The gather stays the truth. It runs on its own clock and rewrites every card the events touched.
- `desk-events.py poll --stub <file>` reads a feed answer from a file, and `DESK_DIR` points both scripts at a copy of the desk, so a test never touches the live files.

## Pane keys

`1` to `9` select a card, `w` works it, `a` to `j` pick an option, `p` folds the Plain numbers, `h` hides the pane.

## Setup (for a fork)

1. `./install.sh` links `mod/`, `skill/` and `scripts/` into `~/.claude/mods/desk`, `~/.claude/skills/desk` and `~/orbit-scratch/desk`.
2. Copy `config.example.json` to `~/orbit-scratch/desk/config.json` and fill it in.
3. Add `CLAUDE_CODE_PLUGIN_DIRS=$HOME/.claude/mods/desk` to the `env` block of `~/.claude/settings.json`.
4. Set `PLAIN_API_KEY` in your shell environment.

Data (`desk.json`, `cards.json`, caches, `config.json`) lives in `~/orbit-scratch/desk` and never enters the repo.

## What you must replace

The sources in `desk.py` are mine: an agent loop with a `state.json`, a terminal reader, a lane classifier, a support bot with its own memory repo and send path, a weekly error sweep. Each is one block in `desk.py` and one recipe in `CARDS.md`. Keep `add()`, the verbs, the deferral rules and the ranking; swap the sources for yours.

Names of customers and teammates and all ticket numbers in the comments are replaced with neutral ones. The dates and the lessons are real.

## Checks

```sh
claude plugin validate mod
claude plugin test mod
```

## License

MIT
