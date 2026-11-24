import { atom, read, update } from "claude-code";
import type { Raw } from "./core/pure";
import type { EngineInterface, Register } from "claude-code";

import { berlinTime, dueName, reopenedOf, titleOf, waitOf } from "./cardText";
import { hintOf } from "./optionHints";
import {
  NO_FEED,
  atHome,
  autoNext,
  choose,
  closeCard,
  decide,
  gather,
  handToSession,
  isPaneOpen,
  live,
  look,
  moveSelection,
  pollPlain,
  pollSso,
  prepare,
  refresh,
  setAuto,
  showCard,
  showStatus,
  startTicker,
  stopTicker,
  summarize,
  surface,
  syncTickets,
  turn,
  wave,
} from "./core/desk";
import {
  CAPTION,
  CARD_DESCRIPTION,
  CARD_SCHEMA,
  CARD_TOOL,
  CLOSE_BASH,
  CLOSE_DESCRIPTION,
  CLOSE_SCHEMA,
  CLOSE_TOOL,
  DESK_WRITE,
  GATHER,
  GATHER_CHECK_MS,
  LUNA_SEND,
  OPTION_KEYS,
  PANE,
  PLAIN_MS,
  POLL_MS,
  REPLY_TOOLS,
  SEND_TOOLS,
  SENT_CLAIM,
  SKILL,
  SSO_MS,
  STATE_GLYPH,
  TICKETS_MS,
  config,
  feedLine,
  pendingOf,
  rowOf,
  text,
  EMPTY,
  PANE_SIZE,
} from "./core/pure";

/**
 * A theme: a palette over the colour names the pane is written in, plus its glyphs and borders.
 * config.json `theme` picks one ("default" when unset or unknown); `/desk-theme` cycles and saves it.
 */
type Theme = {
  palette: Record<string, string>;
  wave: string[];
  waveMs: number;
  title: string;
  selected: string;
  prepared: string;
  rule: string;
  border: {
    focused: string;
    idle: string;
    focusedColor: string;
    idleColor: string;
  };
};

const DEFAULT_THEME: Theme = {
  palette: {},
  // braillewave (unicode-animations, MIT): one dot runs down and across four cells.
  wave: ["⠁⠂⠄⡀", "⠂⠄⡀⢀", "⠄⡀⢀⠠", "⡀⢀⠠⠐", "⢀⠠⠐⠈", "⠠⠐⠈⠁", "⠐⠈⠁⠂", "⠈⠁⠂⠄"],
  waveMs: 100,
  title: "DESK",
  selected: "▌",
  prepared: "◆",
  rule: "─",
  border: {
    focused: "double",
    idle: "round",
    focusedColor: "cyan",
    idleColor: "gray",
  },
};

const THEMES: Record<string, Theme> = {
  default: DEFAULT_THEME,
  cyberpunk: {
    // Neon on night: hot pink for trouble, acid yellow for waits, cyan for the active thing.
    palette: {
      cyan: "#05d9e8",
      yellow: "#fcee0c",
      green: "#00ff9f",
      red: "#ff2a6d",
      magenta: "#d300c5",
      blue: "#7b61ff",
      gray: "#5a5f80",
      white: "#d1f7ff",
    },
    // Own frames: a lit cell scans a four-cell bar and back, like a data bus.
    wave: ["▰▱▱▱", "▱▰▱▱", "▱▱▰▱", "▱▱▱▰", "▱▱▰▱", "▱▰▱▱"],
    waveMs: 90,
    title: "//DESK",
    selected: "▶",
    prepared: "◈",
    rule: "━",
    border: {
      focused: "bold",
      idle: "single",
      focusedColor: "#ff2a6d",
      idleColor: "#5a5f80",
    },
  },
};

let theme: Theme = DEFAULT_THEME;

/** A colour name as the active theme paints it; undefined stays undefined. */
const C = (name: string | undefined) =>
  name === undefined ? undefined : (theme.palette[name] ?? name);

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

const prColor = (state: string) => C(PR_COLORS[state.toLowerCase()] ?? "white");

const laneColor = (lane: string) => C(LANE_COLORS[lane] ?? "white");

// The desk's values this file reads and writes. The core declares the same ones: the engine lists a file's values from
// the atoms written in that file, so each file names its own, with the same plugin and key.
const brief = atom({ plugin: "desk", key: "brief" } as const, null);
const captioned = atom({ plugin: "desk", key: "captioned" } as const, []);
const isAuto = atom({ plugin: "desk", key: "isAuto" } as const, false);
const isAutoElsewhere = atom(
  { plugin: "desk", key: "isAutoElsewhere" } as const,
  false,
);
const isDetailsOpen = atom(
  { plugin: "desk", key: "isDetailsOpen" } as const,
  false,
);
const isHome = atom(
  { plugin: "desk", key: "isHome" } as const,
  null as boolean | null,
);
const isListOpen = atom({ plugin: "desk", key: "isListOpen" } as const, true);
const isPaneHidden = atom(
  { plugin: "desk", key: "isPaneHidden" } as const,
  false,
);
const isPrsOpen = atom({ plugin: "desk", key: "isPrsOpen" } as const, true);
const moreFor = atom({ plugin: "desk", key: "moreFor" } as const, "");
const picks = atom({ plugin: "desk", key: "picks" } as const, {});
const recording = atom({ plugin: "desk", key: "recording" } as const, {});
const summaries = atom({ plugin: "desk", key: "summaries" } as const, {});

const feed = atom({ plugin: "desk", key: "feed" } as const, NO_FEED);
const hasReplyPass = atom(
  { plugin: "desk", key: "hasReplyPass" } as const,
  false,
);
const isBusy = atom({ plugin: "desk", key: "isBusy" } as const, false);
const isGathering = atom(
  { plugin: "desk", key: "isGathering" } as const,
  false,
);
const isPlainOpen = atom({ plugin: "desk", key: "isPlainOpen" } as const, true);
const picked = atom({ plugin: "desk", key: "picked" } as const, "");
const plain = atom({ plugin: "desk", key: "plain" } as const, null);
const tick = atom({ plugin: "desk", key: "tick" } as const, 0);
const view = atom({ plugin: "desk", key: "view" } as const, EMPTY);
const working = atom({ plugin: "desk", key: "working" } as const, null);

// True once the person opened the pane in this session (/desk-pane, ctrl+k). Only such a session gets it back by itself.
let askedForPane = false;

// The pane's part in the core: its theme from the config file, and the wave the status line draws.
surface.onConfig = (file) => {
  theme = THEMES[text(file.theme)] ?? DEFAULT_THEME;
  look.wave = theme.wave;
  look.waveMs = theme.waveMs;
};

export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    if (!(await atHome($))) {
      // Another project: no commands, no tools, no pollers, no pane. The other hooks check isHome.
      return next(e);
    }
    await Promise.all([
      $.command.register({
        name: "desk-pane",
        description: "Open the live desk pane",
      }),
      $.command.register({
        name: "desk-theme",
        description: `Switch the desk theme (${Object.keys(THEMES).join(", ")}); no name cycles to the next`,
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
    $.clock.every(GATHER_CHECK_MS, () => {
      void gather($);
      void live($);
    });
    void pollPlain($);
    $.clock.every(PLAIN_MS, () => void pollPlain($));
    void pollSso($);
    $.clock.every(SSO_MS, () => void pollSso($));
    $.clock.after(20_000, () => void syncTickets($));
    $.clock.every(TICKETS_MS, () => void syncTickets($));

    return next(e);
  });

  on("command.run", { command: "desk-pane" }, async ($) => {
    askedForPane = true;
    await refresh($, true);
    await $.ui.open({
      id: PANE,
      title: "Desk",
      columns: 72,
      rows: 40,
      focus: true,
    });
    void gather($);
    void live($);

    return { text: "Desk pane opened." };
  });

  on("command.run", { command: "desk-theme" }, async ($, e) => {
    const home = (await $.env.get("HOME")) ?? "";
    const path = `${home}/orbit-scratch/desk/config.json`;
    const file = JSON.parse(await $.fs.read(path).catch(() => "{}")) as Raw;
    const names = Object.keys(THEMES);
    const asked = e.args.trim();
    const next =
      asked !== ""
        ? asked
        : (names[
            (names.indexOf(text(file.theme) || "default") + 1) % names.length
          ] ?? "default");
    if (!(next in THEMES)) {
      return { text: `No theme "${next}". Themes: ${names.join(", ")}.` };
    }
    await $.fs.write(
      path,
      `${JSON.stringify({ ...file, theme: next }, null, 2)}\n`,
    );
    theme = THEMES[next] ?? DEFAULT_THEME;
    $.ui.invalidate("ui.render");

    return { text: `Desk theme: ${next}.` };
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
    if (!(await atHome($))) {
      return next(e);
    }
    const tool = String(e.tool);

    if (REPLY_TOOLS.includes(tool)) {
      if (!(await read($, hasReplyPass))) {
        return {
          deny:
            "desk: customer replies go out as Luna (luna-send.py). " +
            "reply_to_thread is the fallback only: the user must type /desk-reply-once first.",
        };
      }

      // The pass ends with the call that reached Plain. A call the harness
      // rejected before sending (a wrong parameter name, a schema error) keeps
      // it: on 2026-10-19 such a call burned the pass on a thread and the user
      // had to type the command twice.
      const replied = await next(e);
      if (replied.deny === undefined && replied.isError !== true) {
        await update($, hasReplyPass, () => false);
        turn.isSentThisTurn = true;
      }
      return replied;
    }

    if (SEND_TOOLS.includes(tool)) {
      const sent = await next(e);
      if (sent.deny === undefined && sent.isError !== true) {
        turn.isSentThisTurn = true;
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
      turn.isSentThisTurn = true;
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
      // 2026-11-10: a gather run in any session opened the pane there. The pane belongs to the session that asked for
      // it with /desk-pane; a gather brings it back only where the person had it and hid it.
      if (askedForPane && (await read($, isPaneHidden))) {
        void $.ui.open({ id: PANE, title: "Desk", columns: 72, rows: 40 });
      }
    } else if (DESK_WRITE.test(command)) {
      await refresh($, true);
    }

    return ran;
  });

  on("turn.start", async ($, e, next) => {
    if (!(await atHome($))) {
      return next(e);
    }
    turn.isSentThisTurn = false;
    turn.isClosedThisTurn = false;
    if (turn.pendingAnswer !== null) {
      turn.answeredCard = turn.pendingAnswer;
      turn.pendingAnswer = null;
    }
    await update($, isBusy, () => true);
    startTicker($);
    await showStatus($);

    return next(e);
  });

  // The desk's state beside every prompt while the pane is open or a card is worked: it survives compaction and
  // tells Claude which tools record the work.
  on("prompt.context", async ($, e, next) => {
    const r = await next(e);
    if (!(await atHome($))) {
      return r;
    }
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
      if (card.prepared !== null) {
        lines.push(
          `A prepared brief for this card waits at ${card.prepared.file} (desk-prepare.py, ${card.prepared.at}, caption unchanged since). ` +
            (card.prepared.causeOpen
              ? "The cause of what the customer reports is open: find it first with live reads (data, logs, replay, code), as far as the tools reach, and draft only after that. Never draft a holding reply (we do not know yet, we are still checking, we will write once we know more). "
              : "") +
            "Read it and start from it: verify each of its openChecks live, confirm no customer-visible message is newer than preparedAt, " +
            "then call desk_card with its why, decide, then, fact, draft and options (edit what the checks changed). Skip the raw reads the brief already did; " +
            "its context file (.ctx.md beside it) holds what it saw.",
        );
      }
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
    if (e.agent_id || e.stop_hook_active || r.block || !(await atHome($))) {
      return r;
    }
    const reply = e.last_assistant_message ?? "";
    const isWorking = (await read($, working)) !== null;
    if (
      (isWorking || turn.answeredCard !== null) &&
      !turn.isSentThisTurn &&
      SENT_CLAIM.test(reply)
    ) {
      return {
        ...r,
        block:
          "desk: the reply says a message went out, but neither luna-send.py nor a send tool ran this turn. " +
          "Send it now, or state plainly that nothing was sent.",
      };
    }
    if (turn.answeredCard !== null && !turn.isClosedThisTurn) {
      const desk = await read($, view);
      const card = desk.cards.find((one) => one.id === turn.answeredCard);
      if (card && card.verb === null) {
        return {
          ...r,
          block:
            `desk: the user answered card ${turn.answeredCard}, but it has no verb yet. ` +
            "Call desk_close with the verb (and the caption for a loop card), or say in one line why the card stays open.",
        };
      }
    }

    return r;
  });

  on("turn.complete", async ($, e, next) => {
    const r = await next(e);
    if (e.agentId || !(await atHome($))) {
      return r;
    }
    // every end counts (answer, abort, error): the desk is free for the next card
    await update($, isBusy, () => false);
    stopTicker();
    await showStatus($);
    const worked = turn.answeredCard;
    turn.answeredCard = null;
    if (e.reason !== "answer" || e.answer.trim() === "") {
      return r;
    }
    const desk = await read($, view);
    const id = worked ?? (await read($, working));
    const card = desk.cards.find((one) => one.id === id);
    if (card) {
      void summarize($, card, e.answer);
    }
    // auto mode: the next queued pick, once this card's close is on disk
    await refresh($, true);
    await autoNext($);

    return r;
  });

  on("ui.open", async ($, e, next) => {
    const r = await next(e);
    if (e.id === PANE) {
      await update($, isPaneHidden, () => false);
      void live($);
    }

    return r;
  });

  // The person's close (ctrl+x x); the mod's own $.ui.close does not come back here, so `h` sets the flag itself.
  on("ui.close", async ($, e, next) => {
    const r = await next(e);
    if (e.id === PANE) {
      await update($, isPaneHidden, () => true);
    }

    return r;
  });

  // ctrl+k (abovePrompt:focus) gives the band the keyboard; its autoFocus "show" Button takes the ring and opens the pane.
  on("ui.focus", async ($, e, next) => {
    const r = await next(e);
    if (
      e.component === "AbovePrompt" &&
      e.element === "show" &&
      (await read($, isPaneHidden))
    ) {
      askedForPane = true;
      await $.ui.open({ ...PANE_SIZE, focus: true });
    }

    return r;
  });

  // The band names the card being worked; with none, the status line's count is enough.
  // While the pane is hidden it also offers it back.
  on("ui.render", { component: "AbovePrompt" }, async ($, e, next) => {
    // Drawing is pure: atHome() stores its answer on the first call, so the draw reads what session.start stored.
    if ((await read($, isHome)) !== true) {
      return next(e);
    }
    const id = await read($, working);
    const desk = await read($, view);
    const card = desk.cards.find((one) => one.id === id);
    // The band offers the pane back only in the session that asked for it.
    const hidden = askedForPane && (await read($, isPaneHidden));

    if ((!card && !hidden) || e.props.hasSurvey) {
      return next(e);
    }

    const { Box, Button, Text } = $.ui.resolve(e);
    const waiting = desk.cards.filter((one) => one.verb === null).length;
    const show = hidden && (
      <Button
        key="show"
        autoFocus
        dimColor
        label="ctrl+k show desk"
        onPress={() => void $.ui.open({ ...PANE_SIZE, focus: true })}
      />
    );

    if (!card) {
      return (
        <Box gap={1}>
          <Text bold>Desk</Text>
          <Text dimColor>{waiting} open</Text>
          {show}
        </Box>
      );
    }

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
        {desk.sso.state === "expired" && (
          <Text bold color={C("red")}>
            ⚠ SSO EXPIRED · run pnpm sso
          </Text>
        )}
        {desk.sso.state === "expiring" && (
          <Text bold color={C("yellow")}>
            sso ↻ {desk.sso.minutes}m
          </Text>
        )}
        {(await read($, isBusy)) && (
          <Text bold color={C("yellow")}>
            <Text dimColor={wave(await read($, tick)).isDim}>
              {wave(await read($, tick)).frame}
            </Text>{" "}
            working
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
        {show}
      </Box>
    );
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const { Box, Button, Link, Text } = $.ui.resolve(e);
    const desk = await read($, view);
    const index = rowOf(desk, await read($, picked));
    const card = desk.cards[index];
    const isRunning = await read($, isGathering);
    const liveFeed = await read($, feed);
    const busy = await read($, isBusy);
    const auto = await read($, isAuto);
    const autoElsewhere = await read($, isAutoElsewhere);
    const beat = wave(await read($, tick));
    const stats = await read($, plain);
    const isOpen = await read($, isPlainOpen);
    const listOpen = await read($, isListOpen);
    const prsOpen = await read($, isPrsOpen);
    const detailsOpen = await read($, isDetailsOpen);
    const moreOpen = card !== undefined && (await read($, moreFor)) === card.id;
    const shown = await read($, brief);
    const current = card && shown?.cardId === card.id ? shown : null;
    const summary = card ? (await read($, summaries))[card.id] : undefined;
    const open = desk.cards.filter((one) => one.verb === null).length;
    // 2026-11-11: a card shows one state. Open: the question and its options. Picked: the pick and where it runs.
    // In the session: what Claude works on. The options never stay up beside a pick or a running session.
    const workingId = (await read($, working)) as string | null;
    const workingCard = desk.cards.find((one) => one.id === workingId);
    const workingName =
      workingCard === undefined
        ? ""
        : workingCard.lane === "TASK"
          ? "a task"
          : workingCard.ref;
    const chosen = (await read($, picks)) as Record<string, string>;
    const beingRecorded = card
      ? ((await read($, recording)) as Record<string, string>)[card.id]
      : undefined;
    const inSession =
      card !== undefined && workingId === card.id && current === null;
    // The engine's own frame has one color; this border is the one the eye finds when the desk holds the keyboard.
    const focused = e.props.isFocused;
    const width = Math.max(20, e.props.bodyColumns - 4);
    const rule = theme.rule.repeat(width);
    const refWidth = Math.max(
      5,
      ...desk.cards.map((one) => (one.lane === "TASK" ? 4 : one.ref.length)),
    );

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle={focused ? theme.border.focused : theme.border.idle}
        borderColor={
          focused ? theme.border.focusedColor : theme.border.idleColor
        }
        borderDimColor={!focused}
      >
        <Box justifyContent="space-between">
          <Text wrap="wrap">
            <Text bold color={C("cyan")} inverse={focused}>
              {focused ? ` ${theme.title} ` : theme.title}
            </Text>{" "}
            <Text bold>{open}</Text>
            <Text dimColor> waiting · </Text>
            {desk.parked}
            <Text dimColor> parked · </Text>
            <Text color={C("green")}>{desk.closedToday}</Text>
            <Text dimColor> closed today</Text>
            {desk.scope !== "" && (
              <Text color={C("yellow")}> · {desk.scope}</Text>
            )}
          </Text>
          <Box gap={1}>
            {busy && (
              <Text bold color={C("yellow")}>
                <Text dimColor={beat.isDim}>{beat.frame}</Text>{" "}
                {workingName === ""
                  ? "CLAUDE WORKING"
                  : `CLAUDE WORKS ${workingName}`}
              </Text>
            )}
            <Text dimColor>
              {isRunning ? "ranking… " : ""}
              {berlinTime(desk.generatedAt)}
            </Text>
            {/* 2026-11-12: r reads every source afresh now (new Plain threads included) and ranks again, also while
                a card is open. */}
            <Button
              key="refresh"
              plain
              hotkey="r"
              dimColor={isRunning}
              label={isRunning ? "refreshing…" : "refresh"}
              onPress={() => {
                void gather($, "now");
                void pollPlain($).catch(() => undefined);
              }}
            />
            <Button
              key="auto"
              plain
              hotkey="t"
              dimColor={!auto}
              label={
                auto
                  ? "auto on"
                  : autoElsewhere
                    ? "auto runs in another session · t moves it here"
                    : "auto off"
              }
              onPress={() => void setAuto($, !auto)}
            />
            {/* 2026-11-10: what the cards taught, walked one line at a time when the person presses m */}
            {desk.harvestPending > 0 && !busy && (
              <Button
                key="harvest"
                plain
                hotkey="m"
                label={`harvest ${desk.harvestPending}`}
                onPress={async () => {
                  await $.prompt.submit({
                    text:
                      "Harvest the desk's learned lines: follow the Harvest step of " +
                      `${SKILL}/SKILL.md. Walk the queue one line at a time, oldest first: desk-harvest.py next, one MEMORY question for that line, memory.py propose on a store, desk-harvest.py done <id> stored|skipped, then the next line, until the queue is empty or I say stop.`,
                    asUser: true,
                  });
                }}
              />
            )}
            {/* The pane goes away until ctrl+k, /desk-pane, the next gather or the next desk_card opens it again. */}
            <Button
              key="hide"
              plain
              hotkey="h"
              dimColor
              label="hide"
              onPress={async () => {
                await update($, isPaneHidden, () => true);
                void $.ui.close({ id: PANE }).catch(() => undefined);
              }}
            />
          </Box>
        </Box>
        <Text dimColor>{rule}</Text>
        {desk.due.length > 0 && (
          <Text wrap="wrap" color={C("yellow")}>
            due: {desk.due.map(dueName).join(" · ")}
          </Text>
        )}
        {liveFeed.state !== "" && (
          <Text
            wrap="truncate"
            dimColor={liveFeed.state === "live"}
            color={liveFeed.state === "off" ? C("yellow") : undefined}
          >
            {feedLine(liveFeed)}
          </Text>
        )}
        {card &&
          (() => {
            const pending = pendingOf(desk, card.id);
            // settled: the person chose already, so the question, its options and the old result leave the card
            const settled =
              pending !== null || inSession || beingRecorded !== undefined;
            const hasBrief = card.prepared !== null || current !== null;
            const reopened = reopenedOf(card.note);
            // A brief tells the story; without one the status is all the card has, so it shows.
            const showsRaw = detailsOpen || !hasBrief;

            return (
              <Box flexDirection="column" marginBottom={1} marginTop={1}>
                <Text wrap="wrap">
                  <Text bold color={laneColor(card.lane)}>
                    {card.lane}
                  </Text>{" "}
                  {card.lane !== "TASK" && <Text bold>{card.ref} </Text>}
                  <Text bold>{titleOf(card, stats?.titles[card.ref])}</Text>
                </Text>
                <Text wrap="wrap" dimColor>
                  {card.waits === "" || card.waits === "nobody"
                    ? "nobody waits"
                    : `${card.waits === "person" ? "someone" : card.waits} waits`}{" "}
                  {waitOf(card.age)}
                  {card.impact !== "" && card.impact !== "-"
                    ? ` · ${card.impact}`
                    : ""}{" "}
                  · about {card.mins} min
                  {reopened !== "" ? ` · ${reopened}` : ""}
                </Text>
                {/* the health check: why the pane has no options for this card, and what a failed pick left behind */}
                {card.verb === null &&
                  card.health !== "" &&
                  current === null && (
                    <Text key="health" wrap="wrap" color={C("yellow")}>
                      {`not ready: ${card.health} · w works it in the session`}
                    </Text>
                  )}
                {card.verb === null && card.pickLine !== "" && !settled && (
                  <Text key="pick-line" wrap="wrap" color={C("red")}>
                    {card.pickLine}
                  </Text>
                )}
                {!hasBrief && (
                  <Box marginTop={1} flexDirection="column">
                    <Text dimColor>DO</Text>
                    <Text wrap="wrap" bold>
                      {card.act}
                    </Text>
                  </Box>
                )}
                {card.prepared !== null && current === null && (
                  <Box marginTop={1} flexDirection="column">
                    {(
                      [
                        ["WHY", card.prepared.why],
                        // A pick is taken: the question and its consequence are history, the pick line follows.
                        ["DECIDE", settled ? "" : card.prepared.decide],
                        ["THEN", settled ? "" : card.prepared.then],
                      ] as const
                    )
                      .filter(([, line]) => Boolean(line))
                      .map(([label, line]) => (
                        <Text wrap="wrap" bold={label === "DECIDE"}>
                          <Text dimColor bold={false}>
                            {label.padEnd(7)}
                          </Text>
                          {line}
                        </Text>
                      ))}
                    {!settled && (
                      <Text wrap="wrap" color={C("yellow")}>
                        <Text dimColor>{"FACT".padEnd(7)}</Text>
                        {card.prepared.fact}
                      </Text>
                    )}
                    {!settled && card.prepared.draft !== "" && (
                      <Box marginTop={1} flexDirection="column">
                        <Text dimColor>DRAFT</Text>
                        <Text wrap="wrap">
                          {card.prepared.draft.length > 600
                            ? `${card.prepared.draft.slice(0, 600)}…`
                            : card.prepared.draft}
                        </Text>
                      </Box>
                    )}
                    {/* 2026-10-23: the brief's options are the pane's; a press records the pick without the session */}
                    {card.verb === null && !settled && (
                      <Box marginTop={1} flexDirection="column">
                        <Text dimColor>
                          {card.pickLine !== "" ? "PICK AGAIN" : "PICK"} · runs
                          in the background · prepared{" "}
                          {berlinTime(card.prepared.at)}
                          {card.prepared.openChecks.length > 0
                            ? ` · ${card.prepared.openChecks.length} checks open`
                            : ""}
                          {card.prepared.resolved ? " · already resolved" : ""}
                          {card.prepared.causeOpen
                            ? " · cause open: the check comes before any draft"
                            : ""}
                        </Text>
                        {card.prepared.options
                          .slice(0, moreOpen ? undefined : card.prepared.shown)
                          .map((option, i) => (
                            <Box key={`prepared-opt-row-${i}`} gap={1}>
                              <Box flexShrink={0}>
                                <Button
                                  key={`prepared-opt-${i}`}
                                  hotkey={OPTION_KEYS[i]}
                                  variant={i === 0 ? "primary" : undefined}
                                  label={`${OPTION_KEYS[i]}  ${option.label}${i === 0 ? "  ★" : ""}`}
                                  onPress={() => void decide($, card, option)}
                                />
                              </Box>
                              <Text dimColor wrap="truncate-end">
                                {hintOf(
                                  option.label,
                                  card.prepared?.deferTrigger ?? "",
                                )}
                              </Text>
                            </Box>
                          ))}
                        {/* 2026-11-10: the pick was one of the first three options 101 of 117 times; the rest fold out */}
                        {card.prepared.options.length > card.prepared.shown && (
                          <Button
                            key="more-options"
                            plain
                            dimColor
                            hotkey="v"
                            label={
                              moreOpen
                                ? "fewer options"
                                : `${card.prepared.options.length - card.prepared.shown} more options`
                            }
                            onPress={() =>
                              update($, moreFor, (id) =>
                                id === card.id ? "" : card.id,
                              )
                            }
                          />
                        )}
                      </Box>
                    )}
                  </Box>
                )}
                {beingRecorded !== undefined &&
                  pending === null &&
                  !inSession && (
                    <Box key="recording" marginTop={1} flexDirection="column">
                      <Text dimColor>PICKED</Text>
                      <Text wrap="wrap" color={C("cyan")}>
                        {STATE_GLYPH.queued} {beingRecorded} · recording your
                        pick
                      </Text>
                    </Box>
                  )}
                {inSession && card.verb === null && (
                  <Box key="in-session" marginTop={1} flexDirection="column">
                    <Text dimColor>IN THE SESSION</Text>
                    <Text wrap="wrap" color={C("yellow")}>
                      {busy ? `${beat.frame} ` : ""}
                      {chosen[card.id] !== undefined
                        ? `You chose: ${chosen[card.id]}. `
                        : ""}
                      {busy
                        ? "Claude works this card now. A question from Claude shows here with new options."
                        : "Claude stopped without a question. Answer in the prompt, press w to continue, or leave the card on the band."}
                    </Text>
                  </Box>
                )}
                {pending !== null && !inSession && (
                  <Box marginTop={1} flexDirection="column">
                    <Text dimColor>PICKED</Text>
                    <Text wrap="wrap" color={C("cyan")}>
                      {STATE_GLYPH[pending.state]} {pending.option}
                      {pending.kind === "session"
                        ? " · needs you: press w to hand it to Claude"
                        : pending.state === "running"
                          ? ` · ${card.pickLine || "running"} in the background`
                          : " · queued for the background"}
                    </Text>
                  </Box>
                )}
                {showsRaw && card.status !== "" && (
                  <Box marginTop={1} flexDirection="column">
                    <Text dimColor>STATUS</Text>
                    <Text wrap="wrap">{card.status}</Text>
                  </Box>
                )}
                {detailsOpen && card.note !== "" && (
                  <Box marginTop={1} flexDirection="column">
                    <Text dimColor>NOTE</Text>
                    <Text wrap="wrap" dimColor>
                      {card.note}
                    </Text>
                  </Box>
                )}
                {detailsOpen && card.memory !== "" && (
                  <Box marginTop={1} flexDirection="column">
                    <Text dimColor>MEMORY</Text>
                    <Text wrap="wrap" italic>
                      {card.memory}
                    </Text>
                  </Box>
                )}
                {summary !== undefined && (
                  <Box marginTop={1} flexDirection="column">
                    <Text dimColor>NOW</Text>
                    <Text wrap="wrap" color={C("cyan")}>
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
                {current &&
                  (
                    [
                      ["WHY", current.why],
                      ["DECIDE", current.decide],
                      ["THEN", current.then],
                    ] as const
                  )
                    .filter(([, line]) => Boolean(line))
                    .map(([label, line]) => (
                      <Box marginTop={1} flexDirection="column">
                        <Text dimColor>{label}</Text>
                        <Text wrap="wrap" bold={label === "DECIDE"}>
                          {line}
                        </Text>
                      </Box>
                    ))}
                {current && (
                  <Box marginTop={1} flexDirection="column">
                    <Text dimColor>FACT</Text>
                    <Text wrap="wrap" bold color={C("yellow")}>
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
                      <Box key={`opt-row-${i}`} gap={1}>
                        <Box flexShrink={0}>
                          <Button
                            key={`opt-${i}`}
                            hotkey={OPTION_KEYS[i]}
                            variant={i === 0 ? "primary" : undefined}
                            label={`${OPTION_KEYS[i]}  ${option.label}${i === 0 ? "  ★" : ""}`}
                            onPress={() => void choose($, current, option)}
                          />
                        </Box>
                        <Text dimColor wrap="truncate-end">
                          {hintOf(option.label)}
                        </Text>
                      </Box>
                    ))}
                  </Box>
                )}
                <Box marginTop={1} gap={1} alignItems="center">
                  {card.verb !== null && (
                    <Text dimColor>
                      {`closed (${card.verb}) · the next gather drops it · pick another row (ctrl+↑/↓)`}
                    </Text>
                  )}
                  {busy &&
                    card.verb === null &&
                    current === null &&
                    !inSession && (
                      <Text dimColor>
                        {`Claude works ${workingName === "" ? "another task" : workingName} · `}
                        {card.prepared !== null && !settled
                          ? "a pick here still runs in the background"
                          : "w is free when the turn ends"}
                      </Text>
                    )}
                  {!busy && card.verb === null && current === null && (
                    <Button
                      key="work"
                      variant="primary"
                      hotkey="w"
                      label={
                        pendingOf(desk, card.id)?.kind === "session"
                          ? "Hand the pick to Claude"
                          : inSession
                            ? "Continue this card"
                            : "Work this card"
                      }
                      onPress={async () => {
                        const pending = pendingOf(desk, card.id);
                        if (pending !== null && pending.kind === "session") {
                          await handToSession($, card.id, {
                            label: pending.option,
                            verb: pending.verb,
                          });
                          return;
                        }
                        await update($, picked, () => card.id);
                        await update($, working, () => card.id);
                        await $.prompt.submit({
                          text:
                            `Work desk card ${card.id} now. Follow Step 3 of ` +
                            `${SKILL}/SKILL.md and the lane recipe in CARDS.md for this one card.` +
                            (card.prepared !== null
                              ? ` A prepared brief waits at ${card.prepared.file}: read it first, verify its openChecks live, then desk_card.`
                              : ""),
                        });
                      }}
                    />
                  )}
                  {card.links.map((link) => (
                    <Link href={link.href} label={`↗ ${link.label}`} />
                  ))}
                  <Button
                    key="details"
                    plain
                    dimColor
                    hotkey="s"
                    label={detailsOpen ? "▾ status" : "▸ status"}
                    onPress={() => update($, isDetailsOpen, (isUp) => !isUp)}
                  />
                </Box>
              </Box>
            );
          })()}
        <Text dimColor>{rule}</Text>
        <Box gap={1}>
          <Button
            key="list-toggle"
            plain
            hotkey="l"
            label={listOpen ? "▾ CARDS" : "▸ CARDS"}
            onPress={() => update($, isListOpen, (isUp) => !isUp)}
          />
          <Text wrap="wrap">
            <Text bold>{open}</Text>
            <Text dimColor> open · </Text>
            <Text color={C("green")}>
              {
                desk.cards.filter(
                  (one) => one.prepared !== null && one.verb === null,
                ).length
              }
            </Text>
            <Text dimColor> ◆ prepared · </Text>
            {desk.cards.length - open}
            <Text dimColor> done</Text>
            {/* ctrl+↑ / ctrl+↓ (the engine's diff-list chords, free while no diff panel is up) walk the list */}
            <Text dimColor> · </Text>
          </Text>
          <Button
            key="select-up"
            plain
            dimColor
            action="app:diffFileListUp"
            label="▲"
            onPress={() => void moveSelection($, -1)}
          />
          <Button
            key="select-down"
            plain
            dimColor
            action="app:diffFileListDown"
            label="▼"
            onPress={() => void moveSelection($, 1)}
          />
          <Text dimColor>ctrl+↑↓</Text>
          <Text wrap="wrap">
            {desk.decisions.some(
              (d) => d.state === "queued" || d.state === "running",
            ) && (
              <Text>
                <Text dimColor> · </Text>
                <Text color={C("cyan")}>
                  {
                    desk.decisions.filter(
                      (d) => d.state === "queued" || d.state === "running",
                    ).length
                  }
                </Text>
                <Text dimColor> picked</Text>
              </Text>
            )}
          </Text>
        </Box>
        {listOpen && desk.cards.length === 0 && (
          <Box marginY={1}>
            <Text dimColor>Nothing waits on you.</Text>
          </Box>
        )}
        {listOpen &&
          desk.cards.map((one, row) => {
            const isSelected = row === index;
            const isDone = one.verb !== null;

            return (
              <Box key={`row-${row}`} gap={1}>
                {/* the marker, the glyph and the key column keep their width when a long title is cut */}
                <Box flexShrink={0} gap={1}>
                  <Text color={C(isSelected ? "cyan" : undefined)}>
                    {isSelected ? theme.selected : " "}
                  </Text>
                  {/* ◆ = a prepared brief waits for this card; ◌ ◍ = a pick waits or runs; · = not prepared */}
                  {(() => {
                    const pending = pendingOf(desk, one.id);
                    if (one.id === workingId && !isDone) {
                      return <Text color={C("yellow")}>▶</Text>;
                    }
                    if (pending !== null && !isDone) {
                      return (
                        <Text color={C("cyan")}>
                          {STATE_GLYPH[pending.state]}
                        </Text>
                      );
                    }
                    return (
                      <Text
                        color={C(
                          one.prepared !== null && !isDone
                            ? "green"
                            : undefined,
                        )}
                        dimColor={one.prepared === null || isDone}
                      >
                        {one.prepared !== null && !isDone
                          ? theme.prepared
                          : "·"}
                      </Text>
                    );
                  })()}
                  <Button
                    key={`pick-${row}`}
                    plain
                    hotkey={row < 9 ? String(row + 1) : undefined}
                    dimColor={isDone}
                    label={`${row < 9 ? "" : "   "}${one.lane.padEnd(7)} ${(one.lane === "TASK" ? "task" : one.ref).padEnd(refWidth)}`}
                    onPress={() => update($, picked, () => one.id)}
                  />
                </Box>
                <Text
                  wrap="truncate-end"
                  bold={isSelected && !isDone}
                  dimColor={isDone}
                  color={isDone ? undefined : laneColor(one.lane)}
                >
                  {isDone ? (
                    <Text color={C("green")}>✓ {one.verb} · </Text>
                  ) : (
                    <Text dimColor>{one.mins}m · </Text>
                  )}
                  {!isDone && one.health !== "" && one.id !== shown?.cardId && (
                    <Text color={C("yellow")}>{one.health} · </Text>
                  )}
                  {titleOf(one, stats?.titles[one.ref])}
                </Text>
              </Box>
            );
          })}
        {(() => {
          const needsYou = desk.decisions.filter(
            (d) => d.kind === "session" && d.state === "queued",
          );
          const results = desk.decisions
            .filter((d) => !(d.kind === "session" && d.state === "queued"))
            .slice(0, 8);
          if (needsYou.length === 0 && results.length === 0) {
            return null;
          }
          return (
            <Box flexDirection="column" marginTop={1}>
              {needsYou.length > 0 && (
                <Text dimColor>
                  NEEDS YOU · {needsYou.length} pick
                  {needsYou.length === 1 ? "" : "s"}{" "}
                  {auto
                    ? "for the session · auto on: Claude takes them one by one"
                    : "wait for the session (select the card, then w · t for auto)"}
                </Text>
              )}
              {needsYou.map((d) => (
                <Text wrap="truncate-end" key={`needs-${d.cardId}`}>
                  <Text color={C("yellow")}>▸ </Text>
                  <Text bold>{d.ref}</Text>
                  <Text dimColor> · {d.option}</Text>
                </Text>
              ))}
              {results.length > 0 && <Text dimColor>PICKS</Text>}
              {results.map((d) => (
                <Text wrap="truncate-end" key={`result-${d.cardId}`}>
                  <Text
                    color={C(
                      d.state === "done"
                        ? "green"
                        : d.state === "bounced"
                          ? "yellow"
                          : d.state === "failed"
                            ? "red"
                            : "cyan",
                    )}
                  >
                    {STATE_GLYPH[d.state]}{" "}
                  </Text>
                  <Text bold>{d.ref}</Text>
                  <Text dimColor>
                    {" "}
                    {d.option}
                    {d.result !== "" ? ` · ${d.result}` : ""}
                  </Text>
                </Text>
              ))}
            </Box>
          );
        })()}
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
                <Text color={C(stats.newToday > 0 ? "yellow" : undefined)}>
                  {stats.newToday}
                </Text>
                <Text dimColor> new today · </Text>
                {stats.newWeek}
                <Text dimColor> this week · </Text>
                <Text
                  color={C(
                    stats.customerRepliesToday > 0 ? "yellow" : undefined,
                  )}
                >
                  {stats.customerRepliesToday}
                </Text>
                <Text dimColor> customer replies today</Text>
                {!isOpen && (
                  <Text dimColor>
                    {" · "}
                    <Text color={C(stats.waitingOnUs > 0 ? "red" : "green")}>
                      {stats.waitingOnUs}
                    </Text>{" "}
                    wait on us ·{" "}
                    <Text
                      color={C(stats.sync.uncovered > 0 ? "yellow" : "green")}
                    >
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
                  <Text bold color={C(stats.waitingOnUs > 0 ? "red" : "green")}>
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
                  <Text bold color={C("magenta")}>
                    SYNC
                  </Text>
                  <Text dimColor> loop </Text>
                  {stats.sync.loopLive} live
                  <Text
                    color={C(
                      stats.sync.loopDoneStillTodo > 0 ? "yellow" : undefined,
                    )}
                  >
                    {" "}
                    {stats.sync.loopDoneStillTodo} done-but-open
                  </Text>
                  <Text dimColor> · desk </Text>
                  {stats.sync.onDesk} live {stats.sync.parked} parked
                  <Text dimColor> · </Text>
                  <Text
                    color={C(stats.sync.uncovered > 0 ? "yellow" : "green")}
                  >
                    {stats.sync.uncovered} uncovered
                  </Text>{" "}
                  <Text color={C(stats.sync.unswept > 0 ? "red" : undefined)}>
                    {stats.sync.unswept} unswept
                  </Text>
                  <Text dimColor>
                    {" "}
                    · loop {ago(stats.sync.loopStateAgeMin)} · sweep{" "}
                    {ago(stats.sync.sweepCacheAgeMin)} · desk{" "}
                    {ago(stats.sync.deskAgeMin)} · plain{" "}
                    {berlinTime(stats.generatedAt)}
                  </Text>
                </Text>
                {stats.tickets && (
                  <Text wrap="wrap">
                    <Text bold color={C("magenta")}>
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
                    <Text
                      color={C(stats.tickets.idle > 0 ? "yellow" : "green")}
                    >
                      {stats.tickets.idle} idle (oldest{" "}
                      {stats.tickets.oldestIdleDays}d)
                    </Text>
                    <Text dimColor> · </Text>
                    <Text
                      color={C(
                        stats.tickets.shippedUntold > 0 ? "red" : "green",
                      )}
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
                    <Text bold color={C("magenta")}>
                      NEWEST
                    </Text>
                    <Text dimColor> last activity on Plain</Text>
                  </Text>
                  {stats.newest.slice(0, 5).map((t) => (
                    <Text key={`new-${t.ref}`} wrap="truncate-end">
                      {"        "}
                      <Text
                        bold={t.isNew}
                        color={C(t.isNew ? "yellow" : undefined)}
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
                      <Text color={C(t.onDesk ? "green" : "yellow")}>
                        {(t.onDesk ? "desk" : "nobody").padEnd(7)}
                      </Text>
                      <Text
                        color={C(
                          t.lastSpoke === "customer" ? "red" : undefined,
                        )}
                      >
                        {t.lastSpoke.padEnd(9)}
                      </Text>
                      <Text dimColor>{agoH(t.ageH).padEnd(4)}</Text>
                      {t.title}
                      <Text dimColor> · {t.who}</Text>
                    </Text>
                  ))}
                </Box>
              </Box>
            )}
            {stats.prs && (
              <Box flexDirection="column" marginTop={1}>
                <Box gap={1}>
                  <Button
                    key="prs-toggle"
                    plain
                    hotkey="o"
                    label={prsOpen ? "▾ PRS" : "▸ PRS"}
                    onPress={() => update($, isPrsOpen, (open) => !open)}
                  />
                  <Text wrap="wrap">
                    <Text dimColor>our open pull requests · </Text>
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
                </Box>
                {prsOpen &&
                  stats.prs.list.map((p) => (
                    <Text key={`pr-${p.repo}-${p.pr}`} wrap="truncate-end">
                      {"        "}
                      <Text bold>
                        {`${p.repo === "orbot" ? "orbot#" : "#"}${p.pr}`.padEnd(
                          10,
                        )}
                      </Text>
                      <Text color={prColor(p.state)}>{p.state.padEnd(18)}</Text>
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
    );
  });
};
