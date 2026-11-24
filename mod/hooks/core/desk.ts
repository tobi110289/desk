// The desk core with state: every action of the desk (refresh, gather, prepare, pick, close). It takes the engine
// interface `$` and draws nothing, so the terminal pane and any other surface run the same desk.
import { atom, read, update } from "claude-code";
import type { Raw } from "./pure";
import type { EngineInterface } from "claude-code";
import type {
  DeskBrief,
  DeskCard,
  DeskFeed,
  DeskOption,
  PlainStats,
} from "../../types";
import { rankWorkable, takesSession } from "../health";
import { BRIEF_FIELDS, plainWordsProblems } from "../plainWords";
import {
  EMPTY,
  EVENTS_BACKOFF_MS,
  EVENTS_GATHER_WAIT_MS,
  EVENTS_LOCK_MS,
  EVENTS_WAIT_S,
  GATHER_LOCK_MS,
  GATHER_MS,
  NOTION_TOOL,
  OPTION_KEYS,
  PANE,
  REPLY_LANES,
  STALE_RUN,
  SUMMARY_MODEL,
  TICKETS_MS,
  VERBS,
  berlinDate,
  config,
  rowOf,
  text,
  toView,
} from "./pure";

// Per turn: what the Stop hook judges a reply against. A press sets pendingAnswer; the turn it starts owns it.
export const turn = {
  pendingAnswer: null as string | null,
  answeredCard: null as string | null,
  isSentThisTurn: false,
  isClosedThisTurn: false,
};

// What a surface adds to the core: its reading of the config file (the pane takes its theme from it).
export const surface = {
  onConfig: (_file: Raw): void => undefined,
};
// The wave the status line draws while Claude works; the pane's theme sets it.
export const look = {
  wave: ["⠁⠂⠄⡀", "⠂⠄⡀⢀", "⠄⡀⢀⠠", "⡀⢀⠠⠐", "⢀⠠⠐⠈", "⠠⠐⠈⠁", "⠐⠈⠁⠂", "⠈⠁⠂⠄"],
  waveMs: 100,
};

export let isConfigLoaded = false;
// Directory prefixes (config.json `projects`, `~` allowed) where the desk belongs; empty means "any checkout of `repo`".
export let projectRoots: string[] = [];
// null until the first hook asked; then whether this session sits inside a desk project. Outside, every hook steps aside.
export const isHome = atom(
  { plugin: "desk", key: "isHome" } as const,
  null as boolean | null,
);

/** Once per session: the root sits under a configured project prefix, or its origin remote is the configured repo. */
export async function atHome($: EngineInterface): Promise<boolean> {
  const known = await read($, isHome);
  if (known !== null) {
    return known;
  }
  await loadConfig($);
  const is = await findHome($);
  await update($, isHome, () => is);
  return is;
}

export async function findHome($: EngineInterface) {
  const home = (await $.env.get("HOME")) ?? "";
  const root = await $.session.root().catch(() => "");
  if (root === "") {
    return false;
  }
  for (const prefix of projectRoots) {
    const dir = prefix.replace(/^~/, home).replace(/\/$/, "");
    if (root === dir || root.startsWith(`${dir}/`)) {
      return true;
    }
  }
  if (config.repo === "") {
    return false;
  }
  const ran = await $.process
    .run(["git", "-C", root, "remote", "get-url", "origin"], {
      timeoutMs: 5_000,
    })
    .catch(() => null);
  return ran !== null && ran.exitCode === 0 && ran.stdout.includes(config.repo);
}
// Tickets we file: the Notion user in config only. An integration bot that files for the team stays out
// (Tobias, 2026-10-06).
export const ticketsSql = () =>
  'SELECT "Task ID" AS id, "Task name" AS title, "Status" AS status, "Impact Level" AS impact, "Priority" AS priority, ' +
  '"Assignee" AS assignee, "Created time" AS createdAt, "date:Completed time:start" AS completedAt, ' +
  '"GitHub Pull Requests" AS prs, "Sprint" AS sprint, "Created by" AS createdBy, url ' +
  `FROM "${config.notionTasks}" WHERE "Created by" LIKE ? ` +
  'AND datetime("Created time") >= datetime(\'2026-07-01\') ORDER BY "Created time" DESC LIMIT 100';

export const view = atom({ plugin: "desk", key: "view" } as const, EMPTY);
export const working = atom({ plugin: "desk", key: "working" } as const, null);
export const picked = atom({ plugin: "desk", key: "picked" } as const, "");
export const isGathering = atom(
  { plugin: "desk", key: "isGathering" } as const,
  false,
);
export const NO_FEED: DeskFeed = { state: "", reason: "", lastEventAt: "" };
export const feed = atom({ plugin: "desk", key: "feed" } as const, NO_FEED);
// True from turn.start to the main loop's turn.complete: the pane shows it and hides the "Work this card" button,
// so the next card is not started while Claude still works the current one (asked for 2026-10-13).
export const isBusy = atom({ plugin: "desk", key: "isBusy" } as const, false);
// Counts up every theme.waveMs while Claude works; the pane and the band draw one wave frame per count.
export const tick = atom({ plugin: "desk", key: "tick" } as const, 0);

export const hasReplyPass = atom(
  { plugin: "desk", key: "hasReplyPass" } as const,
  false,
);
export const plain = atom({ plugin: "desk", key: "plain" } as const, null);
export const isPlainOpen = atom(
  { plugin: "desk", key: "isPlainOpen" } as const,
  true,
);
/** The open-cards list folds to its header line with `l`, like the Plain drawer with `p`. */
export const isListOpen = atom(
  { plugin: "desk", key: "isListOpen" } as const,
  true,
);
/** The pull-request list folds with `o`, apart from the Plain drawer (`p`). */
export const isPrsOpen = atom(
  { plugin: "desk", key: "isPrsOpen" } as const,
  true,
);
/** True from the pane's close (`h`, ctrl+x x) to its next open: the band offers it back on ctrl+k. */
export const isPaneHidden = atom(
  { plugin: "desk", key: "isPaneHidden" } as const,
  false,
);
/** The raw lines of a card (status, memory, the gather's note) fold out with `s`; a brief tells the story without them. */
export const isDetailsOpen = atom(
  { plugin: "desk", key: "isDetailsOpen" } as const,
  false,
);
/** The card whose whole option list is folded out with `v`; a brief shows three by default. */
export const moreFor = atom({ plugin: "desk", key: "moreFor" } as const, "");
export const captioned = atom(
  { plugin: "desk", key: "captioned" } as const,
  [],
);
/**
 * 2026-10-23: auto mode. Off: a session-class pick waits under NEEDS YOU for `w`. On: when the session is idle, it takes
 * the oldest queued pick by itself, and the next one as soon as a card closes, until the queue is empty. The switch is
 * the file desk/.auto ("on …" / "off …"), so every session and the pane in each of them see the same state.
 */
export const isAuto = atom({ plugin: "desk", key: "isAuto" } as const, false);
// 2026-11-11: the switch file names the one session that owns auto mode. Before, every session read "on" and sent
// itself the next queued pick at the end of its own turn, so picks landed in sessions that had nothing to do with the desk.
export const isAutoElsewhere = atom(
  { plugin: "desk", key: "isAutoElsewhere" } as const,
  false,
);
export let ownId: string | null = null;
export async function sessionId($: EngineInterface) {
  ownId ??= await $.session.id().catch(() => null);
  return ownId;
}
/** The option label the user pressed per card, until its close: desk-close.py scores it against the prepared brief. */
export const picks = atom({ plugin: "desk", key: "picks" } as const, {});
// 2026-11-11: a pressed option shows as picked at once, while desk-decide.py still records it (up to 20 s).
export const recording = atom(
  { plugin: "desk", key: "recording" } as const,
  {},
);
export const brief = atom({ plugin: "desk", key: "brief" } as const, null);
export const summaries = atom(
  { plugin: "desk", key: "summaries" } as const,
  {},
);

/** Move the selection one row up or down the list (wrapping); ctrl+↑/↓ from the prompt press these (2026-10-23). */
export async function moveSelection($: EngineInterface, step: 1 | -1) {
  const desk = await read($, view);
  if (desk.cards.length === 0) {
    return;
  }
  const row = rowOf(desk, await read($, picked));
  const next = desk.cards[(row + step + desk.cards.length) % desk.cards.length];
  if (next !== undefined) {
    await update($, picked, () => next.id);
  }
}

// The files' mtimes at the last load; a reload starts over.
export let stamp = "";
// Per turn: what the Stop hook judges a reply against. A press sets turn.pendingAnswer; the turn it starts owns it.
// The desk.py run in flight, so two timers never start two.
export let gatherRun: Promise<void> | null = null;
// The desk-prepare.py pass in flight.
export let prepareRun: Promise<void> | null = null;
export let prepareFull = false;
export let prepareIsPartial = false;
// The desk-execute.py drain in flight, and whether a pick landed while it ran.
export let executeRun: Promise<void> | null = null;
export let executeAgain = false;
// The live event loop in flight, and its desk.py --event step: the gather waits for the step, the step for the gather.
export let liveRun: Promise<void> | null = null;
export let applyRun: Promise<void> | null = null;
// Who owns the .events marker: this module instance (a reload or another session gets its own).
export const LIVE_OWNER = Math.random().toString(36).slice(2, 10);

export async function isPaneOpen($: EngineInterface) {
  try {
    return (await $.ui.panes()).some((pane) => pane.id === PANE);
  } catch {
    return false;
  }
}

let ticker: ReturnType<EngineInterface["clock"]["every"]> | null = null;
/** The wave frame for a count, dimmed on every second cycle so the eye also sees a slow 1.6 s beat. */
export function wave(count: number) {
  return {
    frame: look.wave[count % look.wave.length],
    isDim: count % (look.wave.length * 2) >= look.wave.length,
  };
}

// The status line: the open count, and "working" while a turn runs.
/** The status line under the prompt: the open count and, while Claude works, the wave. */
export async function showStatus($: EngineInterface) {
  if (!(await atHome($))) {
    $.ui.status(undefined);
    return;
  }
  const desk = await read($, view);
  const open = desk.cards.filter((card) => card.verb === null).length;
  const parts: string[] = [];
  if (desk.sso.state === "expired") {
    parts.push("⚠ SSO EXPIRED · pnpm sso");
  } else if (desk.sso.state === "expiring") {
    parts.push(`sso ↻ ${desk.sso.minutes}m`);
  }
  if (open > 0) {
    parts.push(`${open} open`);
  }
  if (await read($, isBusy)) {
    startTicker($);
    parts.push(`${wave(await read($, tick)).frame} working`);
  }
  $.ui.status(parts.length === 0 ? undefined : parts.join(" · "));
}

/** The wave clock, started once per busy stretch; a hot reload drops the old timer, so a later call restarts it. */
export function startTicker($: EngineInterface) {
  ticker ??= $.clock.every(
    look.waveMs,
    () => void update($, tick, (count) => count + 1).then(() => showStatus($)),
  );
}

/** The turn ended: the wave stops. */
export function stopTicker() {
  ticker?.cancel();
  ticker = null;
}

export async function loadConfig($: EngineInterface) {
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
    surface.onConfig(file);
    projectRoots = Array.isArray(file.projects)
      ? file.projects.map((one) => text(one)).filter((one) => one !== "")
      : [];
    isConfigLoaded = true;
  } catch {
    // No config yet: links carry no workspace and the ticket sync stays off.
  }
}

export async function refresh($: EngineInterface, isForced = false) {
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
    // briefs/<id>.json, written by desk-prepare.py; the newest file's mtime is part of the stamp
    const briefsDir = `${home}/orbit-scratch/desk/briefs`;
    const briefFiles = (await $.fs.list(briefsDir).catch(() => [])).filter(
      (entry) => entry.kind === "file" && entry.name.endsWith(".json"),
    );
    const briefsAt = Math.max(0, ...briefFiles.map((entry) => entry.mtimeMs));
    // decisions/<id>.json, written by desk-decide.py and desk-execute.py
    const decisionsDir = `${home}/orbit-scratch/desk/decisions`;
    const decisionFiles = (
      await $.fs.list(decisionsDir).catch(() => [])
    ).filter((entry) => entry.kind === "file" && entry.name.endsWith(".json"));
    const decisionsAt = Math.max(
      0,
      ...decisionFiles.map((entry) => entry.mtimeMs),
    );
    // the auto switch follows the file, so a toggle in another session shows here within a poll
    // "on <time> <session id>": auto runs only in the session the file names; a file without an id counts as off
    const [autoState, , autoOwner = ""] = (
      await $.fs.read(`${home}/orbit-scratch/desk/.auto`).catch(() => "")
    )
      .trim()
      .split(/\s+/);
    const me = await sessionId($);
    const autoHere = autoState === "on" && me !== null && autoOwner === me;
    await update($, isAuto, () => autoHere);
    await update(
      $,
      isAutoElsewhere,
      () => autoState === "on" && autoOwner !== "" && !autoHere,
    );
    const harvestPath = `${home}/orbit-scratch/desk/harvest.json`;
    const harvestAt = await $.fs.stat(harvestPath).then(
      (stat) => stat.mtimeMs,
      () => 0,
    );
    // 2026-11-10: the SSO login state, so an expired login shows on the pane and the status line, not in a failed run.
    const ssoPath = `${home}/orbit-scratch/desk/cache/sso-state.json`;
    const ssoAt = await $.fs.stat(ssoPath).then(
      (stat) => stat.mtimeMs,
      () => 0,
    );
    const next = `${cardsStat.mtimeMs}:${deskStat.mtimeMs}:${statsAt}:${briefsAt}:${decisionsAt}:${decisionFiles.length}:${harvestAt}:${ssoAt}:${berlinDate(time)}`;

    if (next === stamp && !isForced) {
      return;
    }

    const [cardsJson, deskJson] = await Promise.all([
      $.fs.read(cardsPath),
      $.fs.read(deskPath),
    ]);
    let sso = { state: "none", minutes: 0 };
    try {
      const raw = JSON.parse(await $.fs.read(ssoPath)) as Raw;
      sso = {
        state: text(raw.state) || "none",
        minutes: typeof raw.minutes === "number" ? raw.minutes : 0,
      };
    } catch {
      // no state file yet: the next poll writes it
    }
    const briefs: Record<string, Raw> = {};
    for (const entry of briefFiles) {
      const file = `${briefsDir}/${entry.name}`;
      try {
        const b = JSON.parse(await $.fs.read(file)) as Raw;
        briefs[text(b.cardId)] = { ...b, file };
      } catch {
        // a half-written brief: the next refresh reads it
      }
    }
    const decisions: Raw[] = [];
    for (const entry of decisionFiles) {
      try {
        decisions.push(
          JSON.parse(await $.fs.read(`${decisionsDir}/${entry.name}`)) as Raw,
        );
      } catch {
        // a half-written record: the next refresh reads it
      }
    }
    let harvestPending = 0;
    try {
      const items = (JSON.parse(await $.fs.read(harvestPath)) as Raw).items;
      harvestPending = ((items ?? []) as Raw[]).filter(
        (one) => one.asked !== true,
      ).length;
    } catch {
      // no harvest file yet
    }
    const fresh = toView(
      cardsJson,
      deskJson,
      berlinDate(time),
      briefs,
      decisions,
      harvestPending,
      sso,
    );
    stamp = next;
    // The health check: a card the pane cannot work never leads, and the gather's own top card, when it is such a
    // card and a prepare pass can fix it, gets one at once.
    const live = (await read($, brief))?.cardId ?? null;
    const top = fresh.cards.find((card) => card.verb === null);
    fresh.cards = rankWorkable(fresh.cards, live);
    await update($, view, () => fresh);
    if (
      top !== undefined &&
      top.health !== "" &&
      top.id !== live &&
      !takesSession(top)
    ) {
      askPrepare($, top.id, time);
    }
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
export async function syncTickets($: EngineInterface, isForced = false) {
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
  // Built apart from the call: inline, the compiler tries the object against every connected tool's input first.
  const query = {
    tool: NOTION_TOOL,
    data: {
      data_source_urls: [config.notionTasks],
      query: ticketsSql(),
      params: [`%${config.notionUser}%`],
    },
  } as never;
  const ran = await $.tool.call(query);
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
/** Writes cache/sso-state.json from the AWS SSO token cache, then refreshes so the pane and the status line show it. */
export async function pollSso($: EngineInterface) {
  const home = (await $.env.get("HOME")) ?? "";
  try {
    await $.process.run(
      ["python3", `${home}/orbit-scratch/desk/sso-state.py`],
      { timeoutMs: 10_000 },
    );
  } catch {
    // a missing python or cache: the state file keeps its last value
  }
  await refresh($, true);
}

export async function pollPlain($: EngineInterface) {
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

// The live desk: desk.py again when cards.json is old, in the background, while the pane is open. "now" skips the
// age check: a close just landed, or the person pressed r.
// 2026-11-12: a press on r while a card was open did nothing and left no trace. The press is kept until a run starts.
export let isFreshWanted = false;

export function gather($: EngineInterface, when: "due" | "now" = "due") {
  if (when === "now") {
    isFreshWanted = true;
  }
  gatherRun ??= runGather($, isFreshWanted ? "now" : when)
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

export async function runGather($: EngineInterface, when: "due" | "now") {
  if (!(await isPaneOpen($))) {
    return;
  }
  // A gather runs with a card open too (2026-11-17: a card stood open for hours and two new P0s never showed).
  // The open card keeps its place: the pane finds it by id (picked), and refresh() ranks around the live brief.
  const home = (await $.env.get("HOME")) ?? "";
  const dir = `${home}/orbit-scratch/desk`;
  const mtime = (path: string) =>
    $.fs.stat(path).then(
      (stat) => stat.mtimeMs,
      () => 0,
    );
  const time = await $.clock.now();
  // The gather's own clock: an event write (desk.py --event) touches cards.json but keeps generatedAt.
  const generatedAt = Date.parse((await read($, view)).generatedAt);
  const gatheredAt = Number.isNaN(generatedAt)
    ? await mtime(`${dir}/cards.json`)
    : generatedAt;
  if (when === "due" && time - gatheredAt < GATHER_MS) {
    return;
  }
  // An event step writes cards.json in well under a second; the gather starts after it.
  if (applyRun !== null) {
    await applyRun;
  }
  // The marker holds the start time of a run in flight and is empty between runs.
  const marker = `${dir}/.gather`;
  const startedAt = Date.parse(await $.fs.read(marker).catch(() => ""));
  if (time - startedAt < GATHER_LOCK_MS) {
    return;
  }
  await $.fs.write(marker, new Date(time).toISOString());
  isFreshWanted = false;
  await update($, isGathering, () => true);
  try {
    const key = await $.env.get("PLAIN_API_KEY");
    const { scopeArgs } = await read($, view);
    // a forced gather reads every source afresh; a due one may reuse the per-source caches (desk.py --fresh)
    const argv = [
      "python3",
      `${dir}/desk.py`,
      ...(when === "now" ? ["--fresh"] : []),
      ...scopeArgs,
    ];
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
    prepare($);
  }
}

// After every gather: desk-prepare.py drafts the top cards in the background (one headless sonnet run per card,
// scripts collect the raw material), so `w` shows the options at once. Fire and forget; the briefs land in
// briefs/<id>.json and refresh() picks them up. A child never starts children (DESK_PREPARE=1 is set inside).
export function prepare($: EngineInterface, ids: string[] = []) {
  if (prepareRun !== null) {
    // a full pass asked while a one-card pass runs follows it, so the top five are not skipped
    prepareFull ||= ids.length === 0 && prepareIsPartial;
    return;
  }
  prepareIsPartial = ids.length > 0;
  prepareRun = (async () => {
    const home = (await $.env.get("HOME")) ?? "";
    const key = await $.env.get("PLAIN_API_KEY");
    await $.process.run(
      ["python3", `${home}/orbit-scratch/desk/desk-prepare.py`, ...ids],
      { env: key ? { PLAIN_API_KEY: key } : undefined, timeoutMs: 6 * 60_000 },
    );
    await refresh($, true);
  })()
    .catch((error: unknown) => {
      try {
        $.ui.log(`prepare failed: ${String(error)}`.slice(0, 200), {
          to: "debug",
        });
      } catch {
        // the next gather runs it again
      }
    })
    .finally(() => {
      prepareRun = null;
      if (prepareFull) {
        prepareFull = false;
        prepare($);
      }
    });
}

// The gather's top card has no workable brief: one prepare pass for that card alone, at most every two minutes per
// card. desk-prepare.py keeps its own back-off after a failed brief, so a card that keeps failing is not hammered.
export const PREPARE_ASK_MS = 2 * 60_000;
export let lastAsk = { id: "", at: 0 };
export function askPrepare($: EngineInterface, id: string, now: number) {
  if (prepareRun !== null) {
    return;
  }
  if (lastAsk.id === id && now - lastAsk.at < PREPARE_ASK_MS) {
    return;
  }
  lastAsk = { id, at: now };
  prepare($, [id]);
}

// 2026-10-23: a pick on a prepared card is a decision record, not a prompt. desk-execute.py drains the script-class
// records in the background; a bounced one loses its brief, so the prepare pass that follows writes a new one.
export function execute($: EngineInterface) {
  if (executeRun !== null) {
    executeAgain = true;
    return;
  }
  executeRun = (async () => {
    const home = (await $.env.get("HOME")) ?? "";
    const key = await $.env.get("PLAIN_API_KEY");
    await $.process.run(
      ["python3", `${home}/orbit-scratch/desk/desk-execute.py`],
      // $.process.run allows ten minutes at most; a larger value is refused and the drain never starts (2026-10-23).
      { env: key ? { PLAIN_API_KEY: key } : undefined, timeoutMs: 10 * 60_000 },
    );
    await refresh($, true);
    prepare($);
  })()
    .catch((error: unknown) => {
      try {
        $.ui.log(`execute failed: ${String(error)}`.slice(0, 200), {
          to: "debug",
        });
      } catch {
        // the next pick runs it again
      }
    })
    .finally(() => {
      executeRun = null;
      if (executeAgain) {
        executeAgain = false;
        execute($);
      }
    });
}

/** The prompt that hands one card with a chosen option to the session (today's path, and the session-class queue). */
export async function handToSession(
  $: EngineInterface,
  cardId: string,
  option: DeskOption,
) {
  turn.pendingAnswer = cardId;
  await update($, picked, () => cardId);
  await update($, working, () => cardId);
  await update($, picks, (p) => ({ ...p, [cardId]: option.label }));
  await $.prompt.submit({
    text:
      `Re desk card ${cardId}: ${option.label} (${option.verb}). ` +
      "Execute it now per the lane recipe, then record it with desk_close.",
    asUser: true,
  });
}

/** Auto mode: hand the oldest queued session-class pick to the session when it is idle. */
export async function autoNext($: EngineInterface) {
  if (!(await read($, isAuto))) {
    return;
  }
  // isAuto is true only in the session the switch file names, and that session takes the queue only with its pane
  if (!(await isPaneOpen($))) {
    return;
  }
  if (
    (await read($, isBusy)) ||
    (await read($, working)) !== null ||
    turn.pendingAnswer !== null
  ) {
    return;
  }
  const desk = await read($, view);
  const next = [...desk.decisions]
    .reverse() // oldest pick first
    .find(
      (d) =>
        d.kind === "session" &&
        d.state === "queued" &&
        desk.cards.some((card) => card.id === d.cardId && card.verb === null),
    );
  if (next === undefined) {
    return;
  }
  await handToSession($, next.cardId, { label: next.option, verb: next.verb });
}

export async function setAuto($: EngineInterface, on: boolean) {
  const home = (await $.env.get("HOME")) ?? "";
  const path = `${home}/orbit-scratch/desk/.auto`;
  // the file holds "on <time> <session id>" or "off <time>": "on" names this session as the only one that runs auto
  const me = await sessionId($);
  if (on && me === null) {
    return;
  }
  await $.fs
    .write(
      path,
      on
        ? `on ${new Date().toISOString()} ${me}`
        : `off ${new Date().toISOString()}`,
    )
    .catch(() => undefined);
  await update($, isAuto, () => on);
  await update($, isAutoElsewhere, () => false);
  if (on) {
    await autoNext($);
  }
}

/**
 * A pick on a prepared card, from the pane: desk-decide.py records it and names its class. A script-class pick
 * starts the background drain; a session-class pick waits under "needs you" for `w`; a pick the script refuses
 * (no fresh brief, exit 3) goes to the session at once like a `w` would.
 */
export async function decide(
  $: EngineInterface,
  card: DeskCard,
  option: DeskOption,
) {
  const home = (await $.env.get("HOME")) ?? "";
  const settle = () =>
    update(
      $,
      recording,
      ({ [card.id]: _gone, ...rest }: Record<string, string>) => rest,
    );
  await update($, recording, (r) => ({ ...r, [card.id]: option.label }));
  const ran = await $.process
    .run(
      [
        "python3",
        `${home}/orbit-scratch/desk/desk-decide.py`,
        card.id,
        option.label,
      ],
      { timeoutMs: 20_000 },
    )
    .catch(() => ({
      exitCode: 1,
      stdout: "",
      stderr: "desk-decide did not run",
    }));
  const kind = ran.stdout.trim();
  if (ran.exitCode === 0) {
    await refresh($, true);
    await settle();
    if (kind === "script" || kind === "model") {
      execute($);
    } else {
      await autoNext($);
    }
    return;
  }
  await settle();
  if (ran.exitCode === 3) {
    await handToSession($, card.id, option);
    return;
  }
  try {
    $.ui.log(`decide: ${(ran.stdout + ran.stderr).trim()}`.slice(0, 200), {
      to: "debug",
    });
  } catch {
    // nothing recorded; the pane still shows the options
  }
}

export async function isLiveOn($: EngineInterface) {
  const home = (await $.env.get("HOME")) ?? "";
  const json = await $.fs
    .read(`${home}/orbit-scratch/desk/config.json`)
    .catch(() => "");
  try {
    return (JSON.parse(json) as Raw).liveEvents === true;
  } catch {
    return false;
  }
}

/** A full gather runs here (gatherRun) or in another session (a .gather marker younger than GATHER_LOCK_MS). */
export async function isGatherBusy($: EngineInterface, dir: string) {
  if (gatherRun !== null) {
    return true;
  }
  const startedAt = Date.parse(
    await $.fs.read(`${dir}/.gather`).catch(() => ""),
  );

  return (await $.clock.now()) - startedAt < GATHER_LOCK_MS;
}

export function setFeed(
  $: EngineInterface,
  state: DeskFeed["state"],
  reason = "",
) {
  return update($, feed, (kept) => ({ ...kept, state, reason }));
}

// The live desk between two gathers: desk-events.py long-polls the adapter's feed (one round returns within ~20 s),
// desk.py --event applies the events to cards.json, refresh() redraws the pane. Opt-in through config.json
// "liveEvents": true. It runs while the pane is open, one loop per machine (the .events marker), never in parallel
// with a full gather, and the 10-minute gather stays the truth.
export function live($: EngineInterface) {
  liveRun ??= runLive($)
    .catch(async (error: unknown) => {
      await setFeed($, "off", String(error).slice(0, 80)).catch(
        () => undefined,
      );
    })
    .finally(() => {
      liveRun = null;
    });

  return liveRun;
}

export async function runLive($: EngineInterface) {
  const home = (await $.env.get("HOME")) ?? "";
  const dir = `${home}/orbit-scratch/desk`;
  const marker = `${dir}/.events`;
  const key = await $.env.get("PLAIN_API_KEY");
  const env = key ? { PLAIN_API_KEY: key } : undefined;
  try {
    for (;;) {
      if (!(await isLiveOn($))) {
        await update($, feed, () => NO_FEED);
        return;
      }
      if (!(await isPaneOpen($))) {
        return;
      }
      const time = await $.clock.now();
      const [heldAt = "", owner = ""] = (
        await $.fs.read(marker).catch(() => "")
      ).split(" ");
      if (owner !== LIVE_OWNER && time - Date.parse(heldAt) < EVENTS_LOCK_MS) {
        await setFeed($, "off", "another session holds the feed");
        return;
      }
      await $.fs.write(marker, `${new Date(time).toISOString()} ${LIVE_OWNER}`);
      // A gather in flight first: the events wait in the feed (the cursor stays) until it has written cards.json.
      if (await isGatherBusy($, dir)) {
        await $.clock.sleep(EVENTS_GATHER_WAIT_MS);
        continue;
      }
      const ran = await $.process
        .run(
          [
            "python3",
            `${dir}/desk-events.py`,
            "poll",
            "--wait",
            String(EVENTS_WAIT_S),
          ],
          { env, timeoutMs: (EVENTS_WAIT_S + 45) * 1000 },
        )
        .catch((error: unknown) => ({
          exitCode: 4,
          stdout: "",
          stderr: String(error),
        }));
      if (ran.exitCode !== 0) {
        const why = ran.stderr.trim().split("\n")[0] ?? "";
        await setFeed(
          $,
          "off",
          why.replace(/^desk-events: /, "").slice(0, 60) ||
            `exit ${ran.exitCode}`,
        );
        await $.clock.sleep(EVENTS_BACKOFF_MS);
        continue;
      }
      let events: Raw[] = [];
      try {
        const parsed = JSON.parse(ran.stdout) as unknown;
        events = Array.isArray(parsed) ? (parsed as Raw[]) : [];
      } catch {
        // an unreadable answer counts as a quiet round; the events are in events.jsonl
      }
      await setFeed($, "live");
      if (events.length > 0) {
        applyRun = applyEvents($, dir, events, env).finally(() => {
          applyRun = null;
        });
        await applyRun;
        const newest = events
          .map((one) => text(one.at))
          .filter((at) => at !== "")
          .sort()
          .at(-1);
        const at = newest ?? new Date(await $.clock.now()).toISOString();
        await update($, feed, (kept) => ({ ...kept, lastEventAt: at }));
        await refresh($, true);
      }
    }
  } finally {
    // Hand the marker back so the next open (here or in another session) starts at once.
    const [, owner = ""] = (await $.fs.read(marker).catch(() => "")).split(" ");
    if (owner === LIVE_OWNER) {
      await $.fs.write(marker, "").catch(() => undefined);
    }
  }
}

/** One desk.py --event call for the round's events; exit 75 means a gather started meanwhile, so wait and try again. */
export async function applyEvents(
  $: EngineInterface,
  dir: string,
  events: Raw[],
  env: Record<string, string> | undefined,
) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    while (await isGatherBusy($, dir)) {
      await $.clock.sleep(EVENTS_GATHER_WAIT_MS);
    }
    const ran = await $.process.run(
      ["python3", `${dir}/desk.py`, "--event", JSON.stringify(events)],
      { env, timeoutMs: 30_000 },
    );
    if (ran.exitCode !== STALE_RUN) {
      return;
    }
    await $.clock.sleep(EVENTS_GATHER_WAIT_MS);
  }
}

// desk_card: the brief goes on the pane, the card becomes the one being worked, and the reply tells Claude to wait.
export async function showCard(
  $: EngineInterface,
  input: Raw,
): Promise<string> {
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
  const [why, decide, then, fact, draft] = BRIEF_FIELDS.map((field) =>
    text(input[field]).trim(),
  ) as [string, string, string, string, string];
  // The fixed shape: WHY the card is on the desk, what to DECIDE, what THEN happens. Each is required.
  const missing = (["why", "decide", "then", "fact"] as const).filter(
    (field) => text(input[field]).trim() === "",
  );
  if (missing.length > 0) {
    return (
      `Not shown: the brief has no ${missing.join(", ")}. Every card carries why (what put it on the desk, since when, who waits), ` +
      "decide (the one question), then (what the recommended pick sets in motion and what is at stake if nothing happens) " +
      "and fact (the deciding fact), each in plain words. Then call desk_card again."
    );
  }
  const problems = plainWordsProblems(
    { why, decide, then, fact, draft },
    REPLY_LANES.includes(desk.cards[row]?.lane ?? ""),
  );
  if (problems.length > 0) {
    return (
      `Not shown: the brief is not in plain words. ${problems.join(". ")}. ` +
      "Rewrite it for someone who has not opened the file: name each thing in product words (what a user sees or cannot do), " +
      "say whether it is still true, end with the verb. Paths, code names, ids, hashes and status values go into the file, " +
      "the close note or the PR. Then call desk_card again."
    );
  }
  const at = await $.clock.now();
  await update($, brief, () => ({
    cardId: id,
    why,
    decide,
    then,
    fact,
    draft: draft.slice(0, 1500),
    options,
    at,
  }));
  // options on the card make it workable: it may lead again
  await update($, view, (d) => ({ ...d, cards: rankWorkable(d.cards, id) }));
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
export async function choose(
  $: EngineInterface,
  current: DeskBrief,
  option: DeskOption,
) {
  turn.pendingAnswer = current.cardId;
  await update($, brief, (b) => (b?.cardId === current.cardId ? null : b));
  await update($, picks, (p) => ({ ...p, [current.cardId]: option.label }));
  await $.prompt.submit({
    text:
      `Re desk card ${current.cardId}: ${option.label} (${option.verb}). ` +
      "Execute it now per the lane recipe, then record it with desk_close.",
    asUser: true,
  });
}

// desk_close: the caption rule, then desk-close.py, then the restated count.
export async function closeCard(
  $: EngineInterface,
  input: Raw,
): Promise<string> {
  const id = text(input.card_id).trim();
  const verb = text(input.verb).trim();
  if (!VERBS.includes(verb)) {
    return `Not closed: the verb must be one of ${VERBS.join(", ")}.`;
  }
  const desk = await read($, view);
  // A member of a batch or of the ageing sweep is no card of its own, but CARDS.md closes members one by one
  // (2026-11-18: eight drops of sweep:ageing were refused here, and the Bash guard refused desk-close.py).
  const card =
    desk.cards.find((one) => one.id === id) ??
    // A view stored before this field existed has cards without it.
    desk.cards.find((one) => (one.memberIds ?? []).includes(id));
  if (!card) {
    return `Not closed: no card ${id} is on the desk, and no card there has it as a member. The cards are: ${desk.cards.map((one) => one.id).join(", ") || "none"}.`;
  }
  const isMember = card.id !== id;
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
  const pick = (await read($, picks))[id];
  const pickArgs = pick === undefined ? [] : ["--pick", pick];
  const argv =
    verb === "defer"
      ? [
          "python3",
          script,
          id,
          "defer",
          trigger,
          text(input.act).trim() || (isMember ? "" : card.act),
          ...pickArgs,
        ]
      : [
          "python3",
          script,
          id,
          verb,
          text(input.note).trim(),
          ...(caption !== "" ? ["--caption", caption] : []),
          ...pickArgs,
        ];
  const ran = await $.process.run(argv, { timeoutMs: 30_000 });
  if (ran.exitCode !== 0) {
    return `Not closed: desk-close.py exited ${ran.exitCode}: ${(ran.stderr || ran.stdout).trim().slice(-400)}`;
  }
  await update($, picks, ({ [id]: _gone, ...rest }) => rest);
  turn.isClosedThisTurn = true;
  if (turn.answeredCard === id) {
    turn.answeredCard = null;
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
export async function summarize(
  $: EngineInterface,
  card: DeskCard,
  answer: string,
) {
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
