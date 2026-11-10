// What a card says in plain words before any brief exists. The gather's `note` is written for the next gather and for
// the agent: a reopened card carries its last close and both wait keys there. The pane shows a title, who waits and
// since when; the raw lines stay one key away.

const clip = (line: string, max: number) => {
  const one = line.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max - 1)}…` : one;
};

const REOPENED = /^reopened \((\w+) (\d{4}-\d{2}-\d{2}):.*?\): /;
const DAY = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  day: "numeric",
  month: "short",
});

/** "6 Nov" for 2026-11-06. */
const dayOf = (date: string) => {
  const ms = Date.parse(`${date}T12:00:00Z`);
  return Number.isNaN(ms) ? date : DAY.format(new Date(ms));
};

/** A note that is a working line for the agent and no title: a brief's body, a caption stamp, a command. */
const isRaw = (line: string) =>
  line === "" ||
  line.length > 110 ||
  /^(##|caption only|desk \d{4}-|After pnpm|Re-read|Read ~|Decide:|Draft a PR)/.test(
    line,
  ) ||
  /screen idle|n_01[A-Z0-9]{8}/.test(line);

/**
 * The card's title: the Plain thread title when the pane knows it, a task's own title, else the first part of the
 * note without its reopened stamp, else the status or the act.
 */
export const titleOf = (
  card: {
    lane: string;
    note: string;
    since: string;
    status: string;
    act: string;
  },
  plainTitle: string | undefined,
): string => {
  if (plainTitle) {
    return clip(plainTitle, 90);
  }
  if (card.lane === "TASK" && card.since !== "") {
    return clip(card.since, 90);
  }
  const note = card.note.replace(REOPENED, "");
  // "Title · ready-for-human; owner nobody": the title ends at the first separator.
  const head = (note.split(" · ")[0] ?? "").trim();
  if (!isRaw(head)) {
    return clip(head, 90);
  }
  return clip(card.status || card.act, 90);
};

/** "back on the desk: closed 6 Nov (do), new activity since" for a card the gather reopened, else "". */
export const reopenedOf = (note: string): string => {
  const found = REOPENED.exec(note);
  if (found === null) {
    return "";
  }
  return `back on the desk: closed ${dayOf(found[2] ?? "")} (${found[1]}), new activity since`;
};

const BERLIN = new Intl.DateTimeFormat("de-DE", {
  timeZone: "Europe/Berlin",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** An ISO time as the Berlin wall clock, "14:34"; "" for no time. The desk's person reads Berlin time. */
export const berlinTime = (iso: string): string => {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "" : BERLIN.format(new Date(ms));
};

/** "since today", "for 1 day", "for 12 days". */
export const waitOf = (age: number): string =>
  age <= 0 ? "since today" : age === 1 ? "for 1 day" : `for ${age} days`;

/** A pre-flight line without its instruction to the agent: "harvest (4 open entries, …)" → "harvest". */
export const dueName = (line: string): string =>
  (line.split(" (")[0] ?? line).trim();
