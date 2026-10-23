export type DeskLink = { label: string; href: string };

/** A brief desk-prepare.py wrote ahead of time for this card (briefs/<id>.json), shown only while its caption still matches. */
export type PreparedBrief = {
  file: string;
  at: string;
  why: string;
  decide: string;
  then: string;
  fact: string;
  draft: string;
  options: DeskOption[];
  openChecks: string[];
  resolved: boolean;
};

export type DeskCard = {
  id: string;
  ref: string;
  lane: string;
  mins: number;
  impact: string;
  act: string;
  status: string;
  memory: string;
  note: string;
  waits: string;
  age: number;
  links: DeskLink[];
  members: string[];
  /** The verb the card got today, or null while it is open. */
  verb: string | null;
  prepared: PreparedBrief | null;
};

export type DeskView = {
  cards: DeskCard[];
  parked: number;
  closedToday: number;
  generatedAt: string;
  /** "tenant acme" or "threads since 2026-10-09" while desk.py ran scoped, else "". */
  scope: string;
  /** The arguments the background run repeats so a scoped desk stays scoped. */
  scopeArgs: string[];
  /** Executors the pre-flight names as due, each with its reason. */
  due: string[];
  /** The picks the pane recorded (decisions/<id>.json), newest first. */
  decisions: DeskDecision[];
  /** Learned lines (harvest.json) not yet asked; `m` asks them in one memory question. */
  harvestPending: number;
};

/**
 * A pick taken on the pane from a prepared brief (desk-decide.py). `script` runs in the background without the
 * session, `model` is a headless run, `session` waits under "needs you" until `w` hands it to the session.
 */
export type DeskDecision = {
  cardId: string;
  ref: string;
  lane: string;
  option: string;
  verb: string;
  kind: "script" | "model" | "session";
  state: "queued" | "running" | "done" | "bounced" | "failed";
  pickedAt: string;
  finishedAt: string;
  result: string;
};

export type DeskOption = { label: string; verb: string };

/** What Claude handed to the pane with desk_card: the deciding fact, the drafted consequence, the options. */
export type DeskBrief = {
  cardId: string;
  /** What put the card on the desk, since when, who waits. */
  why: string;
  /** The one question. */
  decide: string;
  /** What the recommended pick sets in motion, and what is at stake if nothing happens. */
  then: string;
  fact: string;
  draft: string;
  options: DeskOption[];
  at: number;
};

export type PlainNewest = {
  ref: string;
  title: string;
  status: string;
  impact: string;
  who: string;
  lastSpoke: string;
  ageH: number | null;
  isNew: boolean;
  onDesk: boolean;
};

export type PlainStats = {
  generatedAt: string;
  todo: number;
  snoozed: number;
  newToday: number;
  newWeek: number;
  customerRepliesToday: number;
  waitingOnUs: number;
  oldestUnansweredH: number;
  waitingOnCustomer: number;
  oldestOpenDays: number;
  readiness: Record<string, number>;
  sync: {
    loopLive: number;
    loopDoneStillTodo: number;
    onDesk: number;
    parked: number;
    uncovered: number;
    uncoveredRefs: string[];
    unswept: number;
    loopStateAgeMin: number | null;
    sweepCacheAgeMin: number | null;
    deskAgeMin: number | null;
  };
  newest: PlainNewest[];
  titles: Record<string, string>;
  prs: {
    generatedAt: string;
    cacheAgeMin: number | null;
    byState: Record<string, number>;
    list: {
      pr: number;
      repo: "orbit" | "orbot";
      title: string;
      state: string;
      rounds: number;
      commitDays: number | null;
      ageDays: number | null;
      loop: string | null;
      stuck: boolean;
      url: string;
    }[];
  } | null;
  tickets: {
    total: number;
    open: number;
    running: number;
    shipped: number;
    idle: number;
    shippedUntold: number;
    withThread: number;
    cacheAgeMin: number | null;
    oldestIdleDays: number;
    untoldRefs: string[];
  } | null;
  waitingOnUsRefs: [string, number][];
};

/** The live feed's state for the pane line: "" while config.json liveEvents is off, else live or off with a reason. */
export type DeskFeed = {
  state: "" | "live" | "off";
  reason: string;
  /** ISO time of the newest event applied this session, "" before the first. */
  lastEventAt: string;
};

declare module "claude-code" {
  interface PluginState {
    desk: {
      view: DeskView;
      /** The id of the card being worked, from its start (w, desk_card) to its verb. */
      working: string | null;
      /** The id of the card the pane shows; "" follows the top open card. */
      picked: string;
      /** True while desk.py runs in the background. */
      isGathering: boolean;
      /** The live event loop (desk-events.py), shown as one pane line. */
      feed: DeskFeed;
      /** True from turn.start to the main loop's turn.complete: Claude is working, the pane hides "Work this card". */
      isBusy: boolean;
      /** Counts up ten times a second while Claude works; drives the wave next to WORKING. */
      tick: number;
      hasReplyPass: boolean;
      captioned: string[];
      /** The option label the user pressed per card id, until desk_close hands it to desk-close.py as --pick. */
      picks: Record<string, string>;
      isPlainOpen: boolean;
      /** False while the open-cards list is folded to its header line (`l`). */
      isListOpen: boolean;
      /** False while the pull-request list is folded (`o`). */
      isPrsOpen: boolean;
      /** True from the pane's close to its next open; the band then offers it back on ctrl+k. */
      isPaneHidden: boolean;
      /** Auto mode (`t`, file desk/.auto): the session takes queued session-class picks by itself when idle. */
      isAuto: boolean;
      /** null until checked; then whether the session root lies inside a desk project (config `projects` or `repo`). */
      isHome: boolean | null;
      plain: PlainStats | null;
      brief: DeskBrief | null;
      /** One line per card, where it stands after the last reply (a small model writes it). */
      summaries: Record<string, string>;
    };
  }
}
