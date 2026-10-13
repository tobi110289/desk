import { atom, read, update } from "claude-code";
import type { EngineInterface, Register } from "claude-code";

import type {
  DeskBrief,
  DeskCard,
  DeskLink,
  DeskOption,
  DeskView,
  PlainStats,
} from "../types";

const PANE = "desk";
const POLL_MS = 15_000;
// The desk is live: the pane reruns desk.py when cards.json is older than this (Tobias, 2026-10-09: no sittings).
const GATHER_MS = 10 * 60_000;
const GATHER_CHECK_MS = 60_000;
// desk.py takes about 45 s; a marker younger than this means another session is running it.
const GATHER_LOCK_MS = 3 * 60_000;
// desk.py's exit code when a close landed while it gathered: nothing was written, run it again.
const STALE_RUN = 75;
const SKILL = "~/.claude/skills/desk";
const PLAIN_MS = 5 * 60_000;
const TICKETS_MS = 30 * 60_000;
const NOTION_TOOL = "mcp__notion__notion-query-data-sources";
// The ids that belong to one company live in ~/orbit-scratch/desk/config.json, outside the repo
// (config.example.json lists the keys). Empty until loadConfig() has read the file.
const config = {
  plainWorkspace: "",
  repo: "",
  notionTasks: "",
  notionUser: "",
};
let isConfigLoaded = false;
// Tickets we file: the Notion user in config only. An integration bot that files for the team stays out
// (Tobias, 2026-10-06).
const ticketsSql = () =>
  'SELECT "Task ID" AS id, "Task name" AS title, "Status" AS status, "Impact Level" AS impact, "Priority" AS priority, ' +
  '"Assignee" AS assignee, "Created time" AS createdAt, "date:Completed time:start" AS completedAt, ' +
  '"GitHub Pull Requests" AS prs, "Sprint" AS sprint, "Created by" AS createdBy, url ' +
  `FROM "${config.notionTasks}" WHERE "Created by" LIKE ? ` +
  'AND datetime("Created time") >= datetime(\'2026-07-01\') ORDER BY "Created time" DESC LIMIT 100';
const plainThread = (id: string) =>
  `https://app.plain.com/workspace/${config.plainWorkspace}/thread/${id}`;
const REPLY_TOOLS = [
  "mcp__plain__reply_to_thread",
  "mcp__mcp-plain__reply_to_thread",
];
// Tools that put a message in front of a person; a reply that claims "sent" needs one of them, or luna-send.py.
const SEND_TOOLS = [
  ...REPLY_TOOLS,
  "mcp__plugin_slack_slack__slack_send_message",
];

// The two tools the mod registers: Claude hands a card's brief to the pane, and records the verb through it.
const CARD_TOOL = /^mcp__desk__desk_card$/;
const CLOSE_TOOL = /^mcp__desk__desk_close$/;
const VERBS = ["do", "decide", "delegate", "defer", "drop"];
// Hotkeys for the options on a card; 1-9, p and w are taken.
const OPTION_KEYS = "abcdefghij";
const SUMMARY_MODEL = "sonnet";

const CARD_DESCRIPTION =
  "Put a desk card's brief on the desk pane: the one fact that decides it, the drafted consequence, and the " +
  "lane's options (recommended first). Call it once per card, after pushing right, instead of AskUserQuestion. " +
  "Then end the turn: the user's pick arrives as their next message, 'Re desk card <id>: <option>'.";
const CARD_SCHEMA = {
  type: "object",
  properties: {
    card_id: {
      type: "string",
      description:
        "The card's id as desk.py prints it, such as loop:T-120 or batch:SEND:all.",
    },
    fact: {
      type: "string",
      description:
        "The single fact that decides the card, under 140 characters, such as 'customer asked twice, fix is on staging since 06.10'.",
    },
    draft: {
      type: "string",
      description:
        "The drafted consequence the recommended option executes: the reply text, the caption, the message. Omit when there is none.",
    },
    options: {
      type: "array",
      description:
        "The lane's fixed option set from CARDS.md, recommended first, at most 10.",
      items: {
        type: "object",
        properties: {
          label: {
            type: "string",
            description:
              "The option as CARDS.md words it, under 50 characters.",
          },
          verb: {
            type: "string",
            enum: VERBS,
            description: "The desk verb this option records.",
          },
        },
        required: ["label", "verb"],
      },
    },
  },
  required: ["card_id", "fact", "options"],
};

const CLOSE_DESCRIPTION =
  "Record a desk card's verb (do, decide, delegate, defer, drop). It runs desk-close.py, so never run that over Bash. " +
  "A loop: or gap: card needs its new caption in the same call. A defer needs a trigger and an act; 'later' is never a trigger.";
const CLOSE_SCHEMA = {
  type: "object",
  properties: {
    card_id: {
      type: "string",
      description: "The card's id, such as loop:T-120.",
    },
    verb: { type: "string", enum: VERBS },
    note: {
      type: "string",
      description: "What happened, under 120 characters; goes to the done log.",
    },
    caption: {
      type: "string",
      description:
        "The loop entry's new status caption, under 50 characters, written in the same command as the close. Required for loop: and gap: cards (unless desk-caption.py already ran this turn).",
    },
    trigger: {
      type: "string",
      description:
        "defer only: the machine-checkable trigger, such as deployed:production:3180, a date, or 'customer replies'.",
    },
    act: {
      type: "string",
      description: "defer only: what to do when the trigger fires.",
    },
  },
  required: ["card_id", "verb"],
};

// `python3 ~/orbit-scratch/desk/desk.py [refs | --tenant x | --today]` ranks the desk again; the report modes do not.
const GATHER = /(^|[\s/])desk\.py(\s|$)(?!.*--(wins|preflight|all)\b)/;
const CLOSE_BASH =
  /desk-close\.py\s+["']?([^\s"']+)["']?\s+(do|decide|delegate|defer|drop)\b/;
const CAPTION = /desk-caption\.py\s+["']?([^\s"']+)/;
const DESK_WRITE = /desk-(close|caption)\.py/;
const LUNA_SEND = /luna-send\.py/;
// A reply that says a message reached a person.
const SENT_CLAIM =
  /\b(reply|antwort|message|note|nachricht|mail)\b[^.\n]{0,60}\b(sent|went out|is out|ist raus|gesendet|abgeschickt)\b|\bsent (it|the reply|the message|the note|the mail)\b|\b(gesendet|abgeschickt|rausgeschickt)\b/i;

const EMPTY: DeskView = {
  cards: [],
  parked: 0,
  closedToday: 0,
  generatedAt: "",
  scope: "",
  scopeArgs: [],
  due: [],
};

const LANE_COLORS: Record<string, string> = {
  SEND: "cyan",
  REVIEW: "magenta",
  REPLY: "green",
  NOTIFY: "green",
  PROMISE: "yellow",
  RUN: "blue",
  STUCK: "red",
  STALE: "gray",
  FAST: "red",
  SWEEP: "yellow",
  INBOX: "blue",
  TICKET: "blue",
  TASK: "cyan",
};

const ago = (minutes: number | null) =>
  minutes === null
    ? "?"
    : minutes < 60
      ? `${minutes}m`
      : minutes < 1440
        ? `${Math.round(minutes / 60)}h`
        : `${Math.round(minutes / 1440)}d`;

const agoH = (hours: number | null) =>
  hours === null ? "?" : ago(Math.round(hours * 60));

const PR_COLORS: Record<string, string> = {
  "changes requested": "red",
  conflict: "red",
  "ci red": "red",
  "human review": "yellow",
  returned: "yellow",
  "waiting on tobias": "yellow",
  "horst queued": "cyan",
  approved: "green",
  draft: "gray",
};

const prColor = (state: string) => PR_COLORS[state.toLowerCase()] ?? "white";

const laneColor = (lane: string) => LANE_COLORS[lane] ?? "white";

const view = atom({ plugin: "desk", key: "view" } as const, EMPTY);
const working = atom({ plugin: "desk", key: "working" } as const, null);
const picked = atom({ plugin: "desk", key: "picked" } as const, "");
const isGathering = atom(
  { plugin: "desk", key: "isGathering" } as const,
  false,
);
// True from turn.start to the main loop's turn.complete: the pane shows it and hides the "Work this card" button,
// so the next card is not started while Claude still works the current one (asked for 2026-10-13).
const isBusy = atom({ plugin: "desk", key: "isBusy" } as const, false);
const hasReplyPass = atom(
  { plugin: "desk", key: "hasReplyPass" } as const,
  false,
);
const plain = atom({ plugin: "desk", key: "plain" } as const, null);
const isPlainOpen = atom({ plugin: "desk", key: "isPlainOpen" } as const, true);
const captioned = atom({ plugin: "desk", key: "captioned" } as const, []);
const brief = atom({ plugin: "desk", key: "brief" } as const, null);
const summaries = atom({ plugin: "desk", key: "summaries" } as const, {});

type Raw = Record<string, unknown>;

const text = (value: unknown) => (typeof value === "string" ? value : "");

// desk-close.py stamps the machine's local date; the desk runs in Berlin.
const berlinDate = (ms: number) =>
  new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Berlin" }).format(
    new Date(ms),
  );

const linksOf = (card: Raw): DeskLink[] => {
  const links: DeskLink[] = [];

  if (text(card.thread)) {
    links.push({ label: "Plain", href: plainThread(text(card.thread)) });
  }

  if (typeof card.pr === "number") {
    const repo = text(card.repo) || config.repo;
    links.push({
      label: `#${card.pr}`,
      href: `https://github.com/${repo}/pull/${card.pr}`,
    });
  }

  if (text(card.url)) {
    links.push({ label: "Link", href: text(card.url) });
  }

  return links;
};

const verbOf = (entry: Raw | undefined, today: string): string | null => {
  const closed = entry?.closed as Raw | undefined;
  const defer = entry?.defer as Raw | undefined;

  if (closed?.date === today) {
    return text(closed.verb) || "do";
  }

  return defer?.since === today ? "defer" : null;
};

const toView = (
  cardsJson: string,
  deskJson: string,
  today: string,
): DeskView => {
  const cache = JSON.parse(cardsJson) as Raw;
  const desk = JSON.parse(deskJson) as Raw;
  const state = (desk.cards ?? {}) as Record<string, Raw>;
  const log = (desk.log ?? []) as Raw[];

  const cards = ((cache.cards ?? []) as Raw[]).map((card): DeskCard => {
    const id = text(card.id);
    const members = ((card.members ?? []) as Raw[]).map(
      (member) =>
        `${text(member.ref)} ${text(member.task)}: ${text(member.status)}`,
    );

    return {
      id,
      ref: text(card.ref),
      lane: text(card.lane),
      mins: typeof card.mins === "number" ? card.mins : 0,
      impact: text(card.impact),
      act: text(card.act),
      status: text(card.status),
      memory: text((card.memory as Raw | undefined)?.summary).replace(
        /^memory: /,
        "",
      ),
      note: text(card.note),
      waits: text(card.waits),
      age: typeof card.age === "number" ? card.age : 0,
      links: linksOf(card),
      members,
      verb: verbOf(state[id], today),
    };
  });

  const preflight = (cache.preflight ?? {}) as Record<string, Raw>;

  return {
    cards,
    parked: ((cache.parked ?? []) as unknown[]).length,
    // The same count desk.py prints: every log row of today.
    closedToday: log.filter((row) => row.date === today).length,
    generatedAt: text(cache.generatedAt),
    scope: text(cache.scope),
    scopeArgs: ((cache.scopeArgs ?? []) as unknown[]).map(text),
    due: Object.entries(preflight)
      .filter(([, one]) => one.due === true)
      .map(([name, one]) => `${name} (${text(one.reason)})`),
  };
};

// The card the pane shows: the picked one while it is on the desk, else the top open card.
const rowOf = (desk: DeskView, id: string) => {
  const row = desk.cards.findIndex((card) => card.id === id);
  if (row >= 0) {
    return row;
  }

  return Math.max(
    0,
    desk.cards.findIndex((card) => card.verb === null),
  );
};

// The files' mtimes at the last load; a reload starts over.
let stamp = "";
// Per turn: what the Stop hook judges a reply against. A press sets pendingAnswer; the turn it starts owns it.
let pendingAnswer: string | null = null;
let answeredCard: string | null = null;
let isSentThisTurn = false;
let isClosedThisTurn = false;
// The desk.py run in flight, so two timers never start two.
let gatherRun: Promise<void> | null = null;

async function isPaneOpen($: EngineInterface) {
  try {
    return (await $.ui.panes()).some((pane) => pane.id === PANE);
  } catch {
    return false;
  }
}

// The status line: the open count, and "working" while a turn runs.
async function showStatus($: EngineInterface) {
  const desk = await read($, view);
  const open = desk.cards.filter((card) => card.verb === null).length;
  const busy = await read($, isBusy);
  const text = `${open > 0 ? `${open} waiting` : ""}${busy ? `${open > 0 ? " · " : ""}working` : ""}`;
  $.ui.status(text === "" ? undefined : text);
}

async function loadConfig($: EngineInterface) {
  if (isConfigLoaded) {
    return;
  }
  const home = (await $.env.get("HOME")) ?? "";
  const json = await $.fs
    .read(`${home}/orbit-scratch/desk/config.json`)
    .catch(() => "");
  try {
    const file = JSON.parse(json) as Raw;
    for (const key of Object.keys(config) as (keyof typeof config)[]) {
      config[key] = text(file[key]);
    }
    isConfigLoaded = true;
  } catch {
    // No config yet: links carry no workspace and the ticket sync stays off.
  }
}

async function refresh($: EngineInterface, isForced = false) {
  await loadConfig($);
  const home = (await $.env.get("HOME")) ?? "";
  const cardsPath = `${home}/orbit-scratch/desk/cards.json`;
  const deskPath = `${home}/orbit-scratch/desk/desk.json`;
  const time = await $.clock.now();

  try {
    const [cardsStat, deskStat] = await Promise.all([
      $.fs.stat(cardsPath),
      $.fs.stat(deskPath),
    ]);
    const statsPath = `${home}/orbit-scratch/desk/plain-stats.json`;
    const statsAt = await $.fs.stat(statsPath).then(
      (stat) => stat.mtimeMs,
      () => 0,
    );
    const next = `${cardsStat.mtimeMs}:${deskStat.mtimeMs}:${statsAt}:${berlinDate(time)}`;

    if (next === stamp && !isForced) {
      return;
    }

    const [cardsJson, deskJson] = await Promise.all([
      $.fs.read(cardsPath),
      $.fs.read(deskPath),
    ]);
    const fresh = toView(cardsJson, deskJson, berlinDate(time));
    stamp = next;
    await update($, view, () => fresh);
    // A card that left the desk, or got its verb elsewhere, is no longer worked and keeps no brief.
    const isOpen = (id: string | null | undefined) =>
      fresh.cards.some((card) => card.id === id && card.verb === null);
    await update($, working, (id) => (isOpen(id) ? id : null));
    await update($, brief, (b) => (isOpen(b?.cardId) ? b : null));

    await showStatus($);

    const statsJson = await $.fs.read(statsPath).catch(() => "");
    if (statsJson !== "") {
      const stats = JSON.parse(statsJson) as PlainStats;
      await update($, plain, () => stats);
    }
  } catch {
    // A half-written file parses on the next poll.
  }
}

// The Notion follow-up tickets we filed, through the Notion MCP (no API key exists locally). At most one read per
// half hour across sessions: the cache's age gates it. tickets.py and desk.py read the file.
async function syncTickets($: EngineInterface, isForced = false) {
  const home = (await $.env.get("HOME")) ?? "";
  const path = `${home}/orbit-scratch/desk/notion-tickets.json`;
  const at = await $.fs.stat(path).then(
    (stat) => stat.mtimeMs,
    () => 0,
  );
  if (!isForced && (await $.clock.now()) - at < TICKETS_MS) {
    return "fresh";
  }
  await loadConfig($);
  if (config.notionTasks === "" || config.notionUser === "") {
    return "no config";
  }
  const tools = await $.tool.list();
  if (!tools.some((tool) => tool.name === NOTION_TOOL)) {
    return "no notion";
  }
  const ran = await $.tool.call({
    tool: NOTION_TOOL,
    data: {
      data_source_urls: [config.notionTasks],
      query: ticketsSql(),
      params: [`%${config.notionUser}%`],
    },
  } as never);
  if (ran.deny !== undefined || ran.isError === true) {
    $.ui.log(
      `tickets sync refused: ${ran.deny ?? ran.text ?? "error"}`.slice(0, 200),
      { to: "debug" },
    );

    return "refused";
  }
  const text = ran.text ?? JSON.stringify(ran.result);
  const start = text.indexOf("{");
  const parsed = JSON.parse(text.slice(start)) as {
    results?: Record<string, unknown>[];
  };
  const tickets = (parsed.results ?? []).map((row) => ({
    id: row.id,
    title: row.title ?? "",
    status: row.status ?? "",
    impact: row.impact ?? "",
    priority: row.priority ?? "",
    assignee:
      typeof row.assignee === "string" && row.assignee !== "[]"
        ? row.assignee
        : "",
    createdAt: row.createdAt ?? "",
    completedAt: row.completedAt ?? "",
    prs:
      typeof row.prs === "string"
        ? (JSON.parse(row.prs) as unknown[]).length
        : 0,
    sprint:
      typeof row.sprint === "string" &&
      row.sprint !== "[]" &&
      row.sprint !== "",
    createdBy: row.createdBy ?? "",
    url: row.url ?? "",
  }));
  const generatedAt = new Date(await $.clock.now()).toISOString();
  await $.fs.write(path, JSON.stringify({ generatedAt, tickets }, null, 1));
  await pollPlain($);

  return `${tickets.length} tickets`;
}

// One Plain read every five minutes; the file it writes is what refresh() shows.
async function pollPlain($: EngineInterface) {
  const home = (await $.env.get("HOME")) ?? "";
  const key = await $.env.get("PLAIN_API_KEY");
  await $.process.run(
    ["python3", `${home}/orbit-scratch/desk/plain-stats.py`],
    {
      env: key ? { PLAIN_API_KEY: key } : undefined,
      timeoutMs: 60_000,
    },
  );
  await refresh($, true);
}

// The live desk: desk.py again when cards.json is old, in the background, while the pane is open and no card is
// mid-work (a card's draft must not lose its row to a new ranking). "now" skips the age check: a close just landed.
function gather($: EngineInterface, when: "due" | "now" = "due") {
  gatherRun ??= runGather($, when)
    .catch((error: unknown) => {
      try {
        $.ui.log(`gather failed: ${String(error)}`.slice(0, 200), {
          to: "debug",
        });
      } catch {
        // Nothing to tell: the next check runs it again.
      }
    })
    .finally(() => {
      gatherRun = null;
    });

  return gatherRun;
}

async function runGather($: EngineInterface, when: "due" | "now") {
  if (!(await isPaneOpen($))) {
    return;
  }
  if ((await read($, working)) !== null || (await read($, brief)) !== null) {
    return;
  }
  const home = (await $.env.get("HOME")) ?? "";
  const dir = `${home}/orbit-scratch/desk`;
  const mtime = (path: string) =>
    $.fs.stat(path).then(
      (stat) => stat.mtimeMs,
      () => 0,
    );
  const time = await $.clock.now();
  if (when === "due" && time - (await mtime(`${dir}/cards.json`)) < GATHER_MS) {
    return;
  }
  // The marker holds the start time of a run in flight and is empty between runs.
  const marker = `${dir}/.gather`;
  const startedAt = Date.parse(await $.fs.read(marker).catch(() => ""));
  if (time - startedAt < GATHER_LOCK_MS) {
    return;
  }
  await $.fs.write(marker, new Date(time).toISOString());
  await update($, isGathering, () => true);
  try {
    const key = await $.env.get("PLAIN_API_KEY");
    const { scopeArgs } = await read($, view);
    const argv = ["python3", `${dir}/desk.py`, ...scopeArgs];
    const init = {
      env: key ? { PLAIN_API_KEY: key } : undefined,
      timeoutMs: 4 * 60_000,
    };
    const ran = await $.process.run(argv, init);
    if (ran.exitCode === STALE_RUN) {
      await $.process.run(argv, init);
    }
  } finally {
    await $.fs.write(marker, "").catch(() => undefined);
    await update($, isGathering, () => false);
    await refresh($, true);
  }
}

// desk_card: the brief goes on the pane, the card becomes the one being worked, and the reply tells Claude to wait.
async function showCard($: EngineInterface, input: Raw): Promise<string> {
  const id = text(input.card_id).trim();
  const desk = await read($, view);
  const row = desk.cards.findIndex((card) => card.id === id);
  if (row < 0) {
    return `Not shown: no card ${id} is on the desk. The cards are: ${desk.cards.map((card) => card.id).join(", ") || "none"}.`;
  }
  const options: DeskOption[] = (
    (Array.isArray(input.options) ? input.options : []) as Raw[]
  )
    .map((option) => ({
      label: text(option.label).trim().slice(0, 60),
      verb: VERBS.includes(text(option.verb)) ? text(option.verb) : "do",
    }))
    .filter((option) => option.label !== "")
    .slice(0, OPTION_KEYS.length);
  if (options.length === 0) {
    return "Not shown: give at least one option with a label and a verb.";
  }
  const at = await $.clock.now();
  await update($, brief, () => ({
    cardId: id,
    fact: text(input.fact).trim().slice(0, 300),
    draft: text(input.draft).trim().slice(0, 1500),
    options,
    at,
  }));
  await update($, picked, () => id);
  await update($, working, () => id);
  void $.ui
    .open({ id: PANE, title: "Desk", columns: 72, rows: 40 })
    .catch(() => undefined);
  const keys = options
    .map((option, i) => `${OPTION_KEYS[i]}: ${option.label}`)
    .join(", ");

  return (
    `Card ${id} is on the desk pane with ${options.length} options (${keys}). ` +
    `Stop here and wait: the user's pick arrives as their next message, "Re desk card ${id}: <option>".`
  );
}

// A press answers as the user. The brief leaves the pane at once, so a second press cannot send twice.
async function choose(
  $: EngineInterface,
  current: DeskBrief,
  option: DeskOption,
) {
  pendingAnswer = current.cardId;
  await update($, brief, (b) => (b?.cardId === current.cardId ? null : b));
  await $.prompt.submit({
    text:
      `Re desk card ${current.cardId}: ${option.label} (${option.verb}). ` +
      "Execute it now per the lane recipe, then record it with desk_close.",
    asUser: true,
  });
}

// desk_close: the caption rule, then desk-close.py, then the restated count.
async function closeCard($: EngineInterface, input: Raw): Promise<string> {
  const id = text(input.card_id).trim();
  const verb = text(input.verb).trim();
  if (!VERBS.includes(verb)) {
    return `Not closed: the verb must be one of ${VERBS.join(", ")}.`;
  }
  const desk = await read($, view);
  const card = desk.cards.find((one) => one.id === id);
  if (!card) {
    return `Not closed: no card ${id} is on the desk. The cards are: ${desk.cards.map((one) => one.id).join(", ") || "none"}.`;
  }
  const ref = id.split(":")[1] ?? "";
  const caption = text(input.caption).trim();
  const trigger = text(input.trigger).trim();
  if (
    /^(loop|gap):/.test(id) &&
    verb !== "defer" &&
    caption === "" &&
    !(await read($, captioned)).includes(ref)
  ) {
    return `Not closed: ${id} is a loop card and needs its new caption (under 50 chars) in this same call. A caption written after the close reopens the card.`;
  }
  if (
    verb === "defer" &&
    /^(|later|später|soon|tomorrow|next week)$/i.test(trigger)
  ) {
    return "Not deferred: a defer needs a trigger (deployed:<env>:<pr>, a date, 'customer replies'). 'later' is never a trigger.";
  }
  const home = (await $.env.get("HOME")) ?? "";
  const script = `${home}/orbit-scratch/desk/desk-close.py`;
  const argv =
    verb === "defer"
      ? [
          "python3",
          script,
          id,
          "defer",
          trigger,
          text(input.act).trim() || card.act,
        ]
      : [
          "python3",
          script,
          id,
          verb,
          text(input.note).trim(),
          ...(caption !== "" ? ["--caption", caption] : []),
        ];
  const ran = await $.process.run(argv, { timeoutMs: 30_000 });
  if (ran.exitCode !== 0) {
    return `Not closed: desk-close.py exited ${ran.exitCode}: ${(ran.stderr || ran.stdout).trim().slice(-400)}`;
  }
  isClosedThisTurn = true;
  if (answeredCard === id) {
    answeredCard = null;
  }
  await update($, brief, (b) => (b?.cardId === id ? null : b));
  await update($, working, (one) => (one === id ? null : one));
  await update($, picked, () => "");
  await refresh($, true);
  // The ranking moves with a close (a batch reopens, a parked card fires); the pane shows it when the run lands.
  void gather($, "now");
  const fresh = await read($, view);
  const upcoming = fresh.cards.find((one) => one.verb === null);
  const tail = upcoming
    ? `Top of the desk: ${upcoming.id} (${upcoming.lane} ${upcoming.ref}, ${upcoming.mins} min). ` +
      "Stop here: the user starts the next card (w on the pane, or by saying so)."
    : "The desk is empty.";

  return `Card ${id} closed (${verb}). Closed today: ${fresh.closedToday}. ${tail}`;
}

// One small model call per reply: the line under the card that says where it stands now.
async function summarize($: EngineInterface, card: DeskCard, answer: string) {
  const res = await $.model.complete({
    model: SUMMARY_MODEL,
    effort: "low",
    maxTokens: 60,
    timeoutMs: 20_000,
    system:
      "You keep a one-line status for a card on a work desk. Answer with the line only: under 90 characters, " +
      "present tense, what was done and what still waits on whom. No quotes, no preamble.",
    prompt:
      `Card ${card.id} (${card.lane} ${card.ref}). Act: ${card.act}\n` +
      `Status before: ${card.status || "none"}\n\nThe assistant's reply this turn:\n${answer.slice(-3000)}`,
  });
  if (!res.isAnswered) {
    return;
  }
  const line = res.text.trim().split("\n")[0]?.slice(0, 120) ?? "";
  if (line !== "") {
    await update($, summaries, (all) => ({ ...all, [card.id]: line }));
  }
}

export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    await Promise.all([
      $.command.register({
        name: "desk-pane",
        description: "Open the live desk pane",
      }),
      $.command.register({
        name: "desk-tickets",
        description:
          "Read the follow-up tickets we filed from Notion now (the mod does it every 30 min)",
      }),
      $.command.register({
        name: "desk-reply-once",
        description:
          "Allow one Plain reply_to_thread call (the fallback after luna-send.py refused)",
      }),
      $.tool.register({
        name: "desk_card",
        description: CARD_DESCRIPTION,
        inputSchema: CARD_SCHEMA,
      }),
      $.tool.register({
        name: "desk_close",
        description: CLOSE_DESCRIPTION,
        inputSchema: CLOSE_SCHEMA,
      }),
    ]);
    // A view kept from before a reload may lack fields the module now reads.
    await update($, view, (kept) => ({ ...EMPTY, ...kept }));
    await refresh($, true);
    $.clock.every(POLL_MS, () => void refresh($));
    $.clock.every(GATHER_CHECK_MS, () => void gather($));
    void pollPlain($);
    $.clock.every(PLAIN_MS, () => void pollPlain($));
    $.clock.after(20_000, () => void syncTickets($));
    $.clock.every(TICKETS_MS, () => void syncTickets($));

    return next(e);
  });

  on("command.run", { command: "desk-pane" }, async ($) => {
    await refresh($, true);
    await $.ui.open({
      id: PANE,
      title: "Desk",
      columns: 72,
      rows: 40,
      focus: true,
    });
    void gather($);

    return { text: "Desk pane opened." };
  });

  on("command.run", { command: "desk-tickets" }, async ($) => {
    const outcome = await syncTickets($, true);

    return { text: `Notion tickets: ${outcome}.` };
  });

  on("command.run", { command: "desk-reply-once" }, async ($) => {
    await update($, hasReplyPass, () => true);

    return {
      text: "The next Plain reply_to_thread call is allowed. The pass ends after one call.",
    };
  });

  // The two desk tools: listed up front, no permission prompt, served here.
  for (const tool of [CARD_TOOL, CLOSE_TOOL]) {
    on("tool.describe", { tool }, async ($, e, next) => ({
      ...(await next(e)),
      isDeferred: false,
    }));
    on("tool.check", { tool }, () => ({ decision: "allow" }));
  }
  on("tool.call", { tool: CARD_TOOL }, async ($, e) => ({
    result: await showCard($, e as unknown as Raw),
  }));
  on("tool.call", { tool: CLOSE_TOOL }, async ($, e) => ({
    result: await closeCard($, e as unknown as Raw),
  }));

  on("tool.call", async ($, e, next) => {
    const tool = String(e.tool);

    if (REPLY_TOOLS.includes(tool)) {
      if (!(await read($, hasReplyPass))) {
        return {
          deny:
            "desk: customer replies go out as Luna (luna-send.py). " +
            "reply_to_thread is the fallback only: the user must type /desk-reply-once first.",
        };
      }

      await update($, hasReplyPass, () => false);
    }

    if (SEND_TOOLS.includes(tool)) {
      const sent = await next(e);
      if (sent.deny === undefined && sent.isError !== true) {
        isSentThisTurn = true;
      }

      return sent;
    }

    if (e.tool !== "Bash") {
      return next(e);
    }

    const command = e.command;

    if (CLOSE_BASH.test(command)) {
      return {
        deny:
          "desk: record a verb with the desk_close tool (card_id, verb, note, and the caption for a loop card), " +
          "not with desk-close.py over Bash.",
      };
    }

    const ran = await next(e);

    if (ran.deny !== undefined || ran.isError === true) {
      return ran;
    }

    if (LUNA_SEND.test(command)) {
      isSentThisTurn = true;
    }

    const caption = CAPTION.exec(command);

    if (caption?.[1]) {
      const done = caption[1];
      await update($, captioned, (list) => [
        ...list.filter((one) => one !== done),
        done,
      ]);
    }

    if (GATHER.test(command)) {
      // refresh() drops the worked card and its brief only when the new ranking no longer carries them.
      await refresh($, true);
      void $.ui.open({ id: PANE, title: "Desk", columns: 72, rows: 40 });
    } else if (DESK_WRITE.test(command)) {
      await refresh($, true);
    }

    return ran;
  });

  on("turn.start", async ($, e, next) => {
    isSentThisTurn = false;
    isClosedThisTurn = false;
    if (pendingAnswer !== null) {
      answeredCard = pendingAnswer;
      pendingAnswer = null;
    }
    await update($, isBusy, () => true);
    await showStatus($);

    return next(e);
  });

  // The desk's state beside every prompt while the pane is open or a card is worked: it survives compaction and
  // tells Claude which tools record the work.
  on("prompt.context", async ($, e, next) => {
    const r = await next(e);
    const id = await read($, working);
    const open = await read($, brief);
    if (id === null && open === null && !(await isPaneOpen($))) {
      return r;
    }
    const desk = await read($, view);
    const card = desk.cards.find((one) => one.id === id);
    const waiting = desk.cards.filter((one) => one.verb === null);
    const lines = [
      `desk: live${desk.scope !== "" ? ` (scope: ${desk.scope})` : ""}, ${waiting.length} waiting, closed today ${desk.closedToday}. ` +
        "No sittings: the user starts each card, and the pane ranks again by itself.",
    ];
    if (card) {
      lines.push(
        `Working card ${card.id} (${card.lane} ${card.ref}, ${card.mins} min, waits on ${card.waits || "you"}): ${card.act}` +
          (card.status !== "" ? ` Status: ${card.status}.` : ""),
      );
    }
    if (open !== null) {
      lines.push(
        `Options on the pane for ${open.cardId}: ${open.options.map((option) => option.label).join(" / ")}. The user's pick arrives as "Re desk card ${open.cardId}: <option>".`,
      );
    }
    lines.push(
      `Top of the desk: ${
        waiting
          .slice(0, 6)
          .map((one) => `${one.id} (${one.lane})`)
          .join(", ") || "nothing"
      }.`,
    );
    if (desk.due.length > 0) {
      lines.push(
        `Pre-flight due: ${desk.due.join("; ")}. Dispatch each once in the background before the next card (Pre-flight table in ${SKILL}/CARDS.md); skip one this conversation already dispatched.`,
      );
    }
    lines.push(
      `To work a card: Step 3 of ${SKILL}/SKILL.md and its lane recipe in CARDS.md. Put its options on the pane with desk_card after pushing right; record the verb with desk_close, then stop.`,
    );

    return {
      ...r,
      blocks: [...r.blocks, { name: "desk", text: lines.join("\n") }],
    };
  });

  // Two claims send Claude back once: a message "sent" with no send this turn, and an answered card with no verb.
  on("classic.Stop", async ($, e, next) => {
    const r = await next(e);
    if (e.agent_id || e.stop_hook_active || r.block) {
      return r;
    }
    const reply = e.last_assistant_message ?? "";
    const isWorking = (await read($, working)) !== null;
    if (
      (isWorking || answeredCard !== null) &&
      !isSentThisTurn &&
      SENT_CLAIM.test(reply)
    ) {
      return {
        ...r,
        block:
          "desk: the reply says a message went out, but neither luna-send.py nor a send tool ran this turn. " +
          "Send it now, or state plainly that nothing was sent.",
      };
    }
    if (answeredCard !== null && !isClosedThisTurn) {
      const desk = await read($, view);
      const card = desk.cards.find((one) => one.id === answeredCard);
      if (card && card.verb === null) {
        return {
          ...r,
          block:
            `desk: the user answered card ${answeredCard}, but it has no verb yet. ` +
            "Call desk_close with the verb (and the caption for a loop card), or say in one line why the card stays open.",
        };
      }
    }

    return r;
  });

  on("turn.complete", async ($, e, next) => {
    const r = await next(e);
    if (e.agentId) {
      return r;
    }
    // every end counts (answer, abort, error): the desk is free for the next card
    await update($, isBusy, () => false);
    await showStatus($);
    const worked = answeredCard;
    answeredCard = null;
    if (e.reason !== "answer" || e.answer.trim() === "") {
      return r;
    }
    const desk = await read($, view);
    const id = worked ?? (await read($, working));
    const card = desk.cards.find((one) => one.id === id);
    if (card) {
      void summarize($, card, e.answer);
    }

    return r;
  });

  // The band names the card being worked; with none, the status line's count is enough.
  on("ui.render", { component: "AbovePrompt" }, async ($, e, next) => {
    const id = await read($, working);
    const desk = await read($, view);
    const card = desk.cards.find((one) => one.id === id);

    if (!card || e.props.hasSurvey) {
      return next(e);
    }

    const { Box, Button, Text } = $.ui.resolve(e);
    const waiting = desk.cards.filter((one) => one.verb === null).length;

    return (
      <Box gap={1}>
        <Text bold>Desk</Text>
        <Text>
          working <Text color={laneColor(card.lane)}>{card.lane}</Text>{" "}
          <Text bold>{card.ref}</Text>
        </Text>
        <Text dimColor>
          {waiting} waiting · closed today {desk.closedToday}
        </Text>
        {(await read($, isBusy)) && (
          <Text bold color="yellow">
            ● working
          </Text>
        )}
        <Button
          key="leave"
          label="Leave card"
          dimColor
          onPress={async () => {
            await update($, working, () => null);
            await update($, brief, () => null);
          }}
        />
      </Box>
    );
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const { Box, Button, Link, Text } = $.ui.resolve(e);
    const desk = await read($, view);
    const index = rowOf(desk, await read($, picked));
    const card = desk.cards[index];
    const isRunning = await read($, isGathering);
    const busy = await read($, isBusy);
    const stats = await read($, plain);
    const isOpen = await read($, isPlainOpen);
    const shown = await read($, brief);
    const current = card && shown?.cardId === card.id ? shown : null;
    const summary = card ? (await read($, summaries))[card.id] : undefined;
    const open = desk.cards.filter((one) => one.verb === null).length;
    const width = Math.max(20, e.props.bodyColumns - 2);
    const rule = "─".repeat(width);
    const refWidth = Math.max(5, ...desk.cards.map((one) => one.ref.length));

    return (
      <Box flexDirection="column" paddingX={1}>
        <Box justifyContent="space-between">
          <Text wrap="wrap">
            <Text bold color="cyan">
              DESK
            </Text>{" "}
            <Text bold>{open}</Text>
            <Text dimColor> waiting · </Text>
            {desk.parked}
            <Text dimColor> parked · </Text>
            <Text color="green">{desk.closedToday}</Text>
            <Text dimColor> closed today</Text>
            {desk.scope !== "" && <Text color="yellow"> · {desk.scope}</Text>}
          </Text>
          <Box gap={1}>
            {busy && (
              <Text bold color="yellow">
                ● CLAUDE WORKING
              </Text>
            )}
            <Text dimColor>
              {isRunning ? "ranking… " : ""}
              {desk.generatedAt.slice(11, 16)}Z
            </Text>
            {/* The pane goes away until /desk-pane, the next gather, or the next desk_card opens it again. */}
            <Button
              key="hide"
              plain
              hotkey="h"
              dimColor
              label="h hide"
              onPress={() =>
                void $.ui.close({ id: PANE }).catch(() => undefined)
              }
            />
          </Box>
        </Box>
        <Text dimColor>{rule}</Text>
        {desk.due.length > 0 && (
          <Text wrap="wrap" color="yellow">
            due: {desk.due.join(" · ")}
          </Text>
        )}
        {card && (
          <Box flexDirection="column" marginBottom={1}>
            <Box gap={1}>
              <Text bold color={laneColor(card.lane)}>
                {card.lane}
              </Text>
              <Text bold>{card.ref}</Text>
              <Text dimColor>
                · {card.impact} · {card.mins} min · waits on{" "}
                {card.waits || "you"}
                {card.age > 0 ? ` · ${card.age}d old` : ""}
              </Text>
            </Box>
            {card.note !== "" && (
              <Text wrap="wrap" dimColor>
                {card.note}
              </Text>
            )}
            <Box marginTop={1} flexDirection="column">
              <Text dimColor>DO</Text>
              <Text wrap="wrap" bold>
                {card.act}
              </Text>
            </Box>
            {card.status !== "" && (
              <Box marginTop={1} flexDirection="column">
                <Text dimColor>STATUS</Text>
                <Text wrap="wrap">{card.status}</Text>
              </Box>
            )}
            {summary !== undefined && (
              <Box marginTop={1} flexDirection="column">
                <Text dimColor>NOW</Text>
                <Text wrap="wrap" color="cyan">
                  {summary}
                </Text>
              </Box>
            )}
            {card.members.length > 0 && (
              <Box marginTop={1} flexDirection="column">
                <Text dimColor>MEMBERS</Text>
                {card.members.map((member) => (
                  <Text wrap="wrap">· {member}</Text>
                ))}
              </Box>
            )}
            {card.memory !== "" && (
              <Box marginTop={1} flexDirection="column">
                <Text dimColor>MEMORY</Text>
                <Text wrap="wrap" italic>
                  {card.memory}
                </Text>
              </Box>
            )}
            {current && (
              <Box marginTop={1} flexDirection="column">
                <Text dimColor>FACT</Text>
                <Text wrap="wrap" bold color="yellow">
                  {current.fact}
                </Text>
              </Box>
            )}
            {current && current.draft !== "" && (
              <Box marginTop={1} flexDirection="column">
                <Text dimColor>DRAFT</Text>
                <Text wrap="wrap">{current.draft}</Text>
              </Box>
            )}
            {current && (
              <Box marginTop={1} flexDirection="column">
                <Text dimColor>CHOOSE</Text>
                {current.options.map((option, i) => (
                  <Button
                    key={`opt-${i}`}
                    hotkey={OPTION_KEYS[i]}
                    variant={i === 0 ? "primary" : undefined}
                    label={`${OPTION_KEYS[i]}  ${option.label}${i === 0 ? "  ★" : ""}`}
                    onPress={() => void choose($, current, option)}
                  />
                ))}
              </Box>
            )}
            <Box marginTop={1} gap={1} alignItems="center">
              {busy && card.verb === null && current === null && (
                <Text color="yellow">
                  Claude is working, wait for the turn to end before the next
                  card
                </Text>
              )}
              {!busy && card.verb === null && current === null && (
                <Button
                  key="work"
                  variant="primary"
                  hotkey="w"
                  label="Work this card"
                  onPress={async () => {
                    await update($, picked, () => card.id);
                    await update($, working, () => card.id);
                    await $.prompt.submit({
                      text:
                        `Work desk card ${card.id} now. Follow Step 3 of ` +
                        `${SKILL}/SKILL.md and the lane recipe in CARDS.md for this one card.`,
                    });
                  }}
                />
              )}
              {card.links.map((link) => (
                <Link href={link.href} label={`↗ ${link.label}`} />
              ))}
            </Box>
          </Box>
        )}
        <Text dimColor>{rule}</Text>
        {desk.cards.length === 0 && (
          <Box marginY={1}>
            <Text dimColor>Nothing waits on you.</Text>
          </Box>
        )}
        {desk.cards.map((one, row) => {
          const isSelected = row === index;
          const isDone = one.verb !== null;

          return (
            <Box key={`row-${row}`} gap={1}>
              <Text color={isSelected ? "cyan" : undefined}>
                {isSelected ? "▌" : " "}
              </Text>
              <Button
                key={`pick-${row}`}
                plain
                hotkey={row < 9 ? String(row + 1) : undefined}
                dimColor={isDone}
                label={`${row < 9 ? "" : "   "}${one.lane.padEnd(7)} ${one.ref.padEnd(refWidth)}`}
                onPress={() => update($, picked, () => one.id)}
              />
              <Text
                wrap="truncate-end"
                bold={isSelected && !isDone}
                dimColor={isDone}
                color={isDone ? undefined : laneColor(one.lane)}
              >
                {isDone ? (
                  <Text color="green">✓ {one.verb} · </Text>
                ) : (
                  <Text dimColor>{one.mins}m · </Text>
                )}
                {stats?.titles[one.ref] ?? (one.note || one.act)}
              </Text>
            </Box>
          );
        })}
        <Text dimColor>{rule}</Text>
        {stats && (
          <Box flexDirection="column" marginTop={0}>
            <Box gap={1}>
              <Button
                key="plain-toggle"
                plain
                hotkey="p"
                label={isOpen ? "▾ PLAIN" : "▸ PLAIN"}
                onPress={() => update($, isPlainOpen, (open) => !open)}
              />
              <Text wrap="wrap">
                <Text bold>{stats.todo}</Text>
                <Text dimColor> open · </Text>
                {stats.snoozed}
                <Text dimColor> snoozed · </Text>
                <Text color={stats.newToday > 0 ? "yellow" : undefined}>
                  {stats.newToday}
                </Text>
                <Text dimColor> new today · </Text>
                {stats.newWeek}
                <Text dimColor> this week · </Text>
                <Text
                  color={stats.customerRepliesToday > 0 ? "yellow" : undefined}
                >
                  {stats.customerRepliesToday}
                </Text>
                <Text dimColor> customer replies today</Text>
                {!isOpen && (
                  <Text dimColor>
                    {" · "}
                    <Text color={stats.waitingOnUs > 0 ? "red" : "green"}>
                      {stats.waitingOnUs}
                    </Text>{" "}
                    wait on us ·{" "}
                    <Text color={stats.sync.uncovered > 0 ? "yellow" : "green"}>
                      {stats.sync.uncovered}
                    </Text>{" "}
                    uncovered
                  </Text>
                )}
              </Text>
            </Box>
            {isOpen && (
              <Box flexDirection="column">
                <Text wrap="wrap">
                  {"        "}
                  <Text bold color={stats.waitingOnUs > 0 ? "red" : "green"}>
                    {stats.waitingOnUs}
                  </Text>
                  <Text dimColor>
                    {" "}
                    wait on us (oldest {agoH(stats.oldestUnansweredH)}) ·{" "}
                  </Text>
                  {stats.waitingOnCustomer}
                  <Text dimColor> wait on customer · </Text>
                  <Text dimColor>
                    {Object.entries(stats.readiness)
                      .sort((a, b) => b[1] - a[1])
                      .map(([k, n]) => `${n} ${k}`)
                      .join(" · ")}
                  </Text>
                </Text>
                <Text wrap="wrap">
                  <Text bold color="magenta">
                    SYNC
                  </Text>
                  <Text dimColor> loop </Text>
                  {stats.sync.loopLive} live
                  <Text
                    color={
                      stats.sync.loopDoneStillTodo > 0 ? "yellow" : undefined
                    }
                  >
                    {" "}
                    {stats.sync.loopDoneStillTodo} done-but-open
                  </Text>
                  <Text dimColor> · desk </Text>
                  {stats.sync.onDesk} live {stats.sync.parked} parked
                  <Text dimColor> · </Text>
                  <Text color={stats.sync.uncovered > 0 ? "yellow" : "green"}>
                    {stats.sync.uncovered} uncovered
                  </Text>{" "}
                  <Text color={stats.sync.unswept > 0 ? "red" : undefined}>
                    {stats.sync.unswept} unswept
                  </Text>
                  <Text dimColor>
                    {" "}
                    · loop {ago(stats.sync.loopStateAgeMin)} · sweep{" "}
                    {ago(stats.sync.sweepCacheAgeMin)} · desk{" "}
                    {ago(stats.sync.deskAgeMin)} · plain{" "}
                    {stats.generatedAt.slice(11, 16)}Z
                  </Text>
                </Text>
                {stats.tickets && (
                  <Text wrap="wrap">
                    <Text bold color="magenta">
                      TICKETS
                    </Text>
                    <Text dimColor> filed by us </Text>
                    <Text bold>{stats.tickets.total}</Text>
                    <Text dimColor> · open </Text>
                    {stats.tickets.open}
                    <Text dimColor> · running </Text>
                    {stats.tickets.running}
                    <Text dimColor> · shipped </Text>
                    {stats.tickets.shipped}
                    <Text dimColor> · </Text>
                    <Text color={stats.tickets.idle > 0 ? "yellow" : "green"}>
                      {stats.tickets.idle} idle (oldest{" "}
                      {stats.tickets.oldestIdleDays}d)
                    </Text>
                    <Text dimColor> · </Text>
                    <Text
                      color={stats.tickets.shippedUntold > 0 ? "red" : "green"}
                    >
                      {stats.tickets.shippedUntold} shipped, customer not told
                    </Text>
                    <Text dimColor>
                      {" "}
                      · notion {ago(stats.tickets.cacheAgeMin)}
                    </Text>
                  </Text>
                )}
                {stats.sync.uncoveredRefs.length > 0 && (
                  <Text dimColor wrap="wrap">
                    {"        "}nobody owns:{" "}
                    {stats.sync.uncoveredRefs.join(" ")}
                    {stats.sync.uncovered > stats.sync.uncoveredRefs.length
                      ? " …"
                      : ""}
                  </Text>
                )}
                <Box flexDirection="column" marginTop={1}>
                  <Text>
                    <Text bold color="magenta">
                      NEWEST
                    </Text>
                    <Text dimColor> last activity on Plain</Text>
                  </Text>
                  {stats.newest.slice(0, 5).map((t) => (
                    <Text key={`new-${t.ref}`} wrap="truncate-end">
                      {"        "}
                      <Text
                        bold={t.isNew}
                        color={t.isNew ? "yellow" : undefined}
                      >
                        {t.ref.padEnd(7)}
                      </Text>
                      <Text dimColor>
                        {(t.status === "SNOOZED"
                          ? "zz"
                          : /^P\d$/.test(t.impact)
                            ? t.impact
                            : "–"
                        ).padEnd(3)}
                      </Text>
                      <Text color={t.onDesk ? "green" : "yellow"}>
                        {(t.onDesk ? "desk" : "nobody").padEnd(7)}
                      </Text>
                      <Text
                        color={t.lastSpoke === "customer" ? "red" : undefined}
                      >
                        {t.lastSpoke.padEnd(9)}
                      </Text>
                      <Text dimColor>{agoH(t.ageH).padEnd(4)}</Text>
                      {t.title}
                      <Text dimColor> · {t.who}</Text>
                    </Text>
                  ))}
                </Box>
                {stats.prs && (
                  <Box flexDirection="column" marginTop={1}>
                    <Text wrap="wrap">
                      <Text bold color="magenta">
                        PRS
                      </Text>
                      <Text dimColor> our open pull requests · </Text>
                      <Text bold>{stats.prs.list.length}</Text>
                      <Text dimColor>
                        {Object.entries(stats.prs.byState)
                          .sort((a, b) => b[1] - a[1])
                          .map(([k, n]) => ` · ${n} ${k}`)
                          .join("")}
                        {" · github "}
                        {ago(stats.prs.cacheAgeMin)}
                      </Text>
                    </Text>
                    {stats.prs.list.map((p) => (
                      <Text key={`pr-${p.repo}-${p.pr}`} wrap="truncate-end">
                        {"        "}
                        <Text bold>
                          {`${p.repo === "orbot" ? "orbot#" : "#"}${p.pr}`.padEnd(
                            10,
                          )}
                        </Text>
                        <Text color={prColor(p.state)}>
                          {p.state.padEnd(18)}
                        </Text>
                        <Text dimColor>
                          {(p.rounds > 0 ? `${p.rounds}r` : "").padEnd(4)}
                          {(p.commitDays === null
                            ? ""
                            : `${p.commitDays}d`
                          ).padEnd(4)}
                        </Text>
                        {p.title}
                        {p.loop !== null && <Text dimColor> · {p.loop}</Text>}
                      </Text>
                    ))}
                  </Box>
                )}
              </Box>
            )}
          </Box>
        )}
      </Box>
    );
  });
};
