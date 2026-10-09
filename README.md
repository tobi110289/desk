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

| Path                    | What it is                                                                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mod/`                  | The Claude Code mod: the pane, the band above the prompt, the status line count, two tools (`desk_card`, `desk_close`), the guards, and the checks that send Claude back |
| `skill/SKILL.md`        | The `/desk` procedure                                                                                                                                                    |
| `skill/CARDS.md`        | Sources, lanes and the recipe for each lane                                                                                                                              |
| `scripts/desk.py`       | Gathers from every source, ranks, writes `cards.json`                                                                                                                    |
| `scripts/desk-close.py` | Records a verb (the mod calls it through `desk_close`)                                                                                                                   |
| `scripts/` (rest)       | Plain numbers for the pane, Notion ticket classes, support memory, the send path, local tasks                                                                            |
| `docs/BRIEF.md`         | The brief for an agent that builds a desk for somebody else                                                                                                              |
| `config.example.json`   | Every company id the code reads                                                                                                                                          |

## How a card runs

1. `w` on the pane (or `/desk`) starts the top card.
2. Claude prepares it per the lane recipe and calls `desk_card`: the pane shows the deciding fact, the draft, and the lane's options with hotkeys `a` to `j`.
3. A key press sends the pick as my own message. No dialog covers the pane.
4. Claude executes the pick and calls `desk_close` with the verb. The tool refuses a close without its caption and a defer without a real trigger.
5. The pane ranks again in the background.

Two checks run when Claude ends a turn. A reply that says a message went out while no send ran is sent back. So is a turn that ends on an answered card without a verb.

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
