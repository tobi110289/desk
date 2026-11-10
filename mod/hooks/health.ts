import type { DeskDecision, PreparedBrief } from "../types";
import { berlinTime } from "./cardText";

// The health check before a card leads the pane. Eleven times a card on top could not be worked from the pane: no
// options, no brief, or a background pick that failed with no word on the card. Such a card never takes the first row.

// The cards desk-prepare.py never prepares (its LANES and SKIP_ID): the session takes them over with `w`, so the
// missing brief is no fault. Keep these two in step with the script.
const PREPARED_LANES = new Set([
  "REPLY",
  "NOTIFY",
  "PROMISE",
  "FAST",
  "REVIEW",
  "RUN",
  "INBOX",
  "TASK",
  "SWEEP",
  "SEND",
  "TICKET",
]);
const SESSION_ID = /^(batch:|sweep:|sweep-ticket:|prh:|memory:)/;

/** True when the lane recipe hands the card to the session: no prepare pass ever writes a brief for it. */
export const takesSession = (card: { id: string; lane: string }) =>
  SESSION_ID.test(card.id) || !PREPARED_LANES.has(card.lane);

const clip = (line: string, max: number) => {
  const one = line.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max - 1)}…` : one;
};

/**
 * Why a card cannot be worked from the pane, or "" when it can: a prepared brief with options, or a card the session
 * takes over. A desk_card call on the card counts too; rankWorkable takes that from the live brief.
 */
export const healthOf = (
  card: { id: string; lane: string },
  prepared: PreparedBrief | null,
  briefError: string | null,
): string => {
  if (prepared !== null) {
    return prepared.options.length > 0 ? "" : "no options";
  }
  if (takesSession(card)) {
    return "";
  }
  if (briefError !== null) {
    return `brief failed: ${clip(briefError, 60)}`;
  }
  return "no brief yet";
};

/**
 * The pane's order: the gather's ranking, except that an open card that cannot be worked never leads. When the first
 * open card is unworkable, the first workable open card moves up into its place, and the unworkable ones follow it.
 * With no workable card at all the order stays. `liveBrief` is the card a desk_card call put options on.
 */
export const rankWorkable = <
  T extends { id: string; verb: string | null; health: string },
>(
  cards: T[],
  liveBrief: string | null = null,
): T[] => {
  const isOpen = (card: T) => card.verb === null;
  const isWorkable = (card: T) => card.health === "" || card.id === liveBrief;
  const first = cards.findIndex(isOpen);
  if (first < 0 || isWorkable(cards[first] as T)) {
    return cards;
  }
  const lead = cards.findIndex((card) => isOpen(card) && isWorkable(card));
  if (lead < 0) {
    return cards;
  }
  const rest = cards.filter((_, i) => i !== lead);
  return [...rest.slice(0, first), cards[lead] as T, ...rest.slice(first)];
};

/** The card's own line about its newest background pick: a failure or a bounce with its result, or a running one. */
export const pickLine = (decision: DeskDecision | null): string => {
  if (decision === null) {
    return "";
  }
  if (decision.state === "failed" || decision.state === "bounced") {
    const result = clip(decision.result || "no result recorded", 80);
    return `background pick ${decision.state}: ${result}; pick again or work it in the session`;
  }
  if (decision.state === "running") {
    const since = berlinTime(decision.startedAt || decision.pickedAt);
    return since === "" ? "running" : `running since ${since}`;
  }
  return "";
};
