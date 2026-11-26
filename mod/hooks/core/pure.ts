// The desk core without state: constants, the tool contracts and the rules that turn the gather's files into the view.
// No engine, no drawing: the pane and any other surface read the same rules from here.
import type {
  DeskCard,
  DeskDecision,
  DeskFeed,
  DeskLink,
  DeskView,
  PreparedBrief,
} from "../../types";
import { berlinTime } from "../cardText";
import { healthOf, pickLine } from "../health";

export const PANE = "desk";
export const PANE_SIZE = { id: PANE, title: "Desk", columns: 72, rows: 40 };
export const POLL_MS = 15_000;
// The desk is live: the pane reruns desk.py when cards.json is older than this (Tobias, 2026-10-09: no sittings).
export const GATHER_MS = 10 * 60_000;
export const GATHER_CHECK_MS = 60_000;
// desk.py takes about 45 s; a marker younger than this means another session is running it.
export const GATHER_LOCK_MS = 3 * 60_000;
// desk.py's exit code when a close landed while it gathered: nothing was written, run it again.
export const STALE_RUN = 75;
export const SKILL = "~/.claude/skills/desk";
// The live feed (config.json "liveEvents": true): one long-poll round takes up to this many seconds.
export const EVENTS_WAIT_S = 20;
// After a failed round (desk-events.py exit 4) the loop waits this long before the next.
export const EVENTS_BACKOFF_MS = 60_000;
// The .events marker holds the time of the newest round and its owner; younger than this means another session runs it.
export const EVENTS_LOCK_MS = 90_000;
// While a full gather runs, the loop checks again after this long.
export const EVENTS_GATHER_WAIT_MS = 5_000;
export const PLAIN_MS = 5 * 60_000;
// The SSO login check: the token cache only, no network (2026-11-10).
export const SSO_MS = 2 * 60_000;
export const TICKETS_MS = 30 * 60_000;
export const NOTION_TOOL = "mcp__notion__notion-query-data-sources";

export const config = {
  plainWorkspace: "",
  repo: "",
  notionTasks: "",
  notionUser: "",
  /** The ssh host that holds the desk; "" when the desk is on this machine. */
  deskHost: "",
};

export const plainThread = (id: string) =>
  `https://app.plain.com/workspace/${config.plainWorkspace}/thread/${id}`;

export const REPLY_TOOLS = [
  "mcp__plain__reply_to_thread",
  "mcp__mcp-plain__reply_to_thread",
];
// Tools that put a message in front of a person; a reply that claims "sent" needs one of them, or luna-send.py.
export const SEND_TOOLS = [
  ...REPLY_TOOLS,
  "mcp__plugin_slack_slack__slack_send_message",
];

// The two tools the mod registers: Claude hands a card's brief to the pane, and records the verb through it.
export const CARD_TOOL = /^mcp__desk__desk_card$/;
export const CLOSE_TOOL = /^mcp__desk__desk_close$/;
export const VERBS = ["do", "decide", "delegate", "defer", "drop"];
// Hotkeys for the options on a card; 1-9, p and w are taken.
export const OPTION_KEYS = "abcdefghij";
/** A pick's state on the pane: waiting, running, done, bounced (the world moved, nothing ran), failed. */
export const STATE_GLYPH: Record<DeskDecision["state"], string> = {
  queued: "◌",
  running: "◍",
  done: "✓",
  bounced: "↩",
  failed: "✗",
};
export const SUMMARY_MODEL = "sonnet";
// Lanes whose draft is customer text: the plain-words ban holds, the cap on numbered items does not.
export const REPLY_LANES = ["REPLY", "NOTIFY", "PROMISE", "FAST"];

export const CARD_DESCRIPTION =
  "Put a desk card's brief on the desk pane in its fixed shape: WHY the card is on the desk, what to DECIDE, what THEN " +
  "happens, the one fact that decides it, the drafted consequence, and the lane's options (recommended first). " +
  "Every text field is plain words for a reader who has not opened the file: no paths, code names, ids, hashes, " +
  "status values or backticks; the tool refuses them and names each one. " +
  "Call it once per card, after pushing right, instead of AskUserQuestion. " +
  "Then end the turn: the user's pick arrives as their next message, 'Re desk card <id>: <option>'.";
export const CARD_SCHEMA = {
  type: "object",
  properties: {
    card_id: {
      type: "string",
      description:
        "The card's id as desk.py prints it, such as loop:T-120 or batch:SEND:all.",
    },
    why: {
      type: "string",
      description:
        "One plain sentence: what put the card on the desk, since when, and who waits, such as " +
        "'Dana wrote at 09:11 today and has had no answer' or 'this file has sat untriaged since 26 Aug, nobody waits'.",
    },
    decide: {
      type: "string",
      description:
        "The one question, in one sentence, such as 'Send Dana this answer as Luna?' or " +
        "'Hand the two Hub bugs to agents and close the third file?'.",
    },
    then: {
      type: "string",
      description:
        "One or two sentences: what the recommended pick sets in motion and what is at stake if nothing happens, such as " +
        "'Luna sends it now; the thread stays open until she confirms on staging' or 'two agents start today; left alone the bugs stay live on Hub'.",
    },
    fact: {
      type: "string",
      description:
        "The single fact that decides the card: one plain sentence under 140 characters, what the case is and what changed, " +
        "such as 'customer asked twice, fix is on staging since 06.10'. No paths, code names, ids, hashes, status values or backticks.",
    },
    draft: {
      type: "string",
      description:
        "The drafted consequence the recommended option executes: the reply text, the caption, the message. Omit when there is none. " +
        "A batch or task card: at most three numbered lines, each naming the thing in product words, whether it is still true, and the verb. " +
        "No paths, code names, ids, hashes, status values or backticks: the tool refuses them.",
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
  required: ["card_id", "why", "decide", "then", "fact", "options"],
};

export const CLOSE_DESCRIPTION =
  "Record a desk card's verb (do, decide, delegate, defer, drop). It runs desk-close.py, so never run that over Bash. " +
  "A loop: or gap: card needs its new caption in the same call. A defer needs a trigger and an act; 'later' is never a trigger.";
export const CLOSE_SCHEMA = {
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
export const GATHER = /(^|[\s/])desk\.py(\s|$)(?!.*--(wins|preflight|all)\b)/;
export const CLOSE_BASH =
  /desk-close\.py\s+["']?([^\s"']+)["']?\s+(do|decide|delegate|defer|drop)\b/;
export const CAPTION = /desk-caption\.py\s+["']?([^\s"']+)/;
export const DESK_WRITE = /desk-(close|caption)\.py/;
export const LUNA_SEND = /luna-send\.py/;
// A reply that says a message reached a person.
export const SENT_CLAIM =
  /\b(reply|antwort|message|note|nachricht|mail)\b[^.\n]{0,60}\b(sent|went out|is out|ist raus|gesendet|abgeschickt)\b|\bsent (it|the reply|the message|the note|the mail)\b|\b(gesendet|abgeschickt|rausgeschickt)\b/i;

export const EMPTY: DeskView = {
  cards: [],
  parked: 0,
  closedToday: 0,
  generatedAt: "",
  scope: "",
  scopeArgs: [],
  due: [],
  decisions: [],
  harvestPending: 0,
  sso: { state: "none", minutes: 0 },
};

export type Raw = Record<string, unknown>;

export const text = (value: unknown) =>
  typeof value === "string" ? value : "";

// desk-close.py stamps the machine's local date; the desk runs in Berlin.
export const berlinDate = (ms: number) =>
  new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Berlin" }).format(
    new Date(ms),
  );

export const linksOf = (card: Raw): DeskLink[] => {
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

// 2026-10-22: the pane holds no copy of the hold logic. desk-close.py writes the verb into the card in cards.json, and
// the next gather removes the card; until then the card shows its verb. (Before this the pane recomputed the closure
// from desk.json and drifted from the gather: 2026-10-19 a reopened card still showed ✓ do.)
export const verbOf = (card: Raw): string | null => {
  const verb = text(card.verb);
  return verb === "" ? null : verb;
};

// 2026-11-12: desk-prepare.py marks a card whose question is gone (a REVIEW card whose PR merged or closed after the
// gather read it). Such a card leaves the pane at once; the next gather drops it from cards.json.
export const isGone = (card: Raw, briefs: Record<string, Raw>): boolean => {
  const b = briefs[text(card.id)];
  return (
    b !== undefined &&
    text(b.gone) !== "" &&
    text(b.key) === text(card.key) &&
    text(b.since) === text(card.since)
  );
};

// A prepared brief counts only while the card's wait (key + since) is the one it was written for; a brief from before
// the wait keys binds to the caption (status + act) as it did then.
export const preparedOf = (
  card: Raw,
  briefs: Record<string, Raw>,
): PreparedBrief | null => {
  const b = briefs[text(card.id)];
  if (b === undefined || b.error !== undefined || isGone(card, briefs)) {
    return null;
  }
  // 2026-10-23: a card with a wait key binds only a brief written for that key; a brief from before the keys
  // (status + act) is stale on such a card, and a pick on it would bounce as soon as a pass rewrote it.
  const bound =
    text(card.key) !== ""
      ? text(b.key) === text(card.key) && text(b.since) === text(card.since)
      : text(b.status) === text(card.status) && text(b.act) === text(card.act);
  if (!bound) {
    return null;
  }

  return {
    file: text(b.file),
    at: text(b.preparedAt),
    why: text(b.why),
    decide: text(b.decide),
    then: text(b.then),
    fact: text(b.fact),
    draft: text(b.draft),
    options: ((b.options ?? []) as Raw[]).map((o) => ({
      label: text(o.label),
      verb: text(o.verb),
    })),
    deferTrigger: text(b.deferTrigger),
    // A brief from before 2026-11-10 has no count: it shows its whole list, as it always did.
    shown: typeof b.shown === "number" && b.shown > 0 ? b.shown : 99,
    openChecks: ((b.openChecks ?? []) as unknown[]).map(text),
    resolved: b.resolved === true,
    causeOpen: b.causeOpen === true,
  };
};

export const decisionOf = (d: Raw): DeskDecision => ({
  cardId: text(d.cardId),
  ref: text(d.ref),
  lane: text(d.lane),
  option: text(d.option),
  verb: text(d.verb),
  kind: (["script", "model", "session"].includes(text(d.class))
    ? text(d.class)
    : "session") as DeskDecision["kind"],
  state: (["queued", "running", "done", "bounced", "failed"].includes(
    text(d.state),
  )
    ? text(d.state)
    : "failed") as DeskDecision["state"],
  pickedAt: text(d.pickedAt),
  startedAt: text(d.startedAt),
  finishedAt: text(d.finishedAt),
  result: text(d.result),
});

export const toView = (
  cardsJson: string,
  deskJson: string,
  today: string,
  briefs: Record<string, Raw> = {},
  decisions: Raw[] = [],
  harvestPending = 0,
  sso: DeskView["sso"] = { state: "none", minutes: 0 },
): DeskView => {
  const cache = JSON.parse(cardsJson) as Raw;
  const desk = JSON.parse(deskJson) as Raw;
  const log = (desk.log ?? []) as Raw[];
  const picks = decisions
    .map(decisionOf)
    .sort((a, b) => (a.pickedAt < b.pickedAt ? 1 : -1));

  const cards = ((cache.cards ?? []) as Raw[])
    .filter((card) => !isGone(card, briefs))
    .map((card): DeskCard => {
      const id = text(card.id);
      const prepared = preparedOf(card, briefs);
      const failed = briefs[id]?.error;
      const members = ((card.members ?? []) as Raw[]).map(
        (member) =>
          `${text(member.ref)} ${text(member.task)}: ${text(member.status)}`,
      );

      return {
        prepared,
        health: healthOf(
          { id, lane: text(card.lane) },
          prepared,
          failed === undefined ? null : text(failed) || String(failed),
        ),
        pickLine: pickLine(picks.find((d) => d.cardId === id) ?? null),
        id,
        no: typeof card.no === "number" ? card.no : 0,
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
        since: text(card.since),
        waits: text(card.waits),
        age: typeof card.age === "number" ? card.age : 0,
        links: linksOf(card),
        members,
        memberIds: ((card.members ?? []) as Raw[])
          .map((member) => text(member.id))
          .filter((memberId) => memberId !== ""),
        verb: verbOf(card),
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
    decisions: picks,
    harvestPending,
    sso,
  };
};

/** The open pick of a card: queued or running, whatever its class. */
export const pendingOf = (desk: DeskView, id: string) =>
  desk.decisions.find(
    (d) => d.cardId === id && (d.state === "queued" || d.state === "running"),
  ) ?? null;

// The card the pane shows: the picked one while it is on the desk, else the top open card.
export const rowOf = (desk: DeskView, id: string) => {
  const row = desk.cards.findIndex((card) => card.id === id);
  if (row >= 0) {
    return row;
  }

  return Math.max(
    0,
    desk.cards.findIndex((card) => card.verb === null),
  );
};

/** The pane's feed line: `live · last event 12:34`, `live · quiet`, `feed off: <reason>`. */
export const feedLine = (one: DeskFeed) =>
  one.state === "off"
    ? `feed off: ${one.reason || "no answer"}`
    : one.lastEventAt === ""
      ? "live · quiet"
      : `live · last event ${berlinTime(one.lastEventAt)}`;
