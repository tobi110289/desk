import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";

import { plainWordsProblems } from "../hooks/plainWords";

const CARDS = JSON.stringify({
  generatedAt: "2026-10-02T10:04:51+00:00",
  cards: [
    {
      id: "loop:T-1",
      ref: "T-1",
      lane: "REPLY",
      mins: 3,
      impact: "P1",
      act: "Send it",
      status: "drafted",
    },
  ],
  parked: [],
});
const DESK = JSON.stringify({ cards: {}, log: [] });
const CONFIG = JSON.stringify({ projects: ["~/app"] });

const world = (on: On, root = "/home/t/app") => {
  mock.env(on, { HOME: "/home/t" });
  mock.clock(on, { now: Date.parse("2026-10-02T10:00:00Z") });
  on("session.root", () => ({ value: root }));
  on("fs.stat", () => ({
    value: { kind: "file" as const, size: 1, mtimeMs: 1, isLink: false },
  }));
  on("fs.read", (_$, e) => ({
    value: e.path.endsWith("cards.json")
      ? CARDS
      : e.path.endsWith("config.json")
        ? CONFIG
        : DESK,
  }));
  const ran: string[] = [];
  on("tool.call", (_$, e) => {
    ran.push(String(e.tool));

    return { result: {} as never };
  });
  on("ui.open", () => ({ value: { id: "desk" } as never }));
  on("ui.status", () => ({ value: undefined as never }));
  const processes: string[][] = [];
  on("process.run", (_$, e) => {
    processes.push([...e.argv]);

    return {
      value: {
        exitCode: 0,
        stdout: "",
        stderr: "",
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    };
  });

  return { ran, processes };
};

test("a Plain reply needs the one-call pass", async ($, on) => {
  const { ran } = world(on);
  const reply = {
    tool: "mcp__plain__reply_to_thread" as const,
    threadId: "th_1",
    text: "hi",
  } as never;

  expect((await $.tool.call(reply)).deny).toBeDefined();
  expect(ran).toEqual([]);

  // The engine sets origin and presentation on a typed command.
  await $.command.run({ command: "desk-reply-once" } as never);
  expect((await $.tool.call(reply)).deny).toBeUndefined();
  expect((await $.tool.call(reply)).deny).toBeDefined();
  expect(ran).toEqual(["mcp__plain__reply_to_thread"]);
});

test("outside a desk project the mod steps aside: no guard, no commands", async ($, on) => {
  const { ran } = world(on, "/home/t/elsewhere");
  const reply = {
    tool: "mcp__plain__reply_to_thread" as const,
    threadId: "th_1",
    text: "hi",
  } as never;

  expect((await $.tool.call(reply)).deny).toBeUndefined();
  expect(ran).toEqual(["mcp__plain__reply_to_thread"]);
});

test("a verb goes through desk_close, never desk-close.py over Bash", async ($, on) => {
  const { ran } = world(on);
  const denied = await $.tool.call({
    tool: "Bash",
    command: 'python3 ~/orbit-scratch/desk/desk-close.py loop:T-1 do "sent"',
  });

  expect(denied.deny).toContain("desk_close");
  // A caption alone still runs: it is not a verb.
  const caption = await $.tool.call({
    tool: "Bash",
    command:
      'python3 ~/orbit-scratch/desk/desk-close.py run:x caption "waiting"',
  });
  expect(caption.deny).toBeUndefined();
  expect(ran).toEqual(["Bash"]);
});

test("desk_close keeps the caption rule and runs desk-close.py", async ($, on) => {
  const { processes } = world(on);
  // The gather command starts the sitting and loads the cards.
  await $.tool.call({
    tool: "Bash",
    command: "python3 ~/orbit-scratch/desk/desk.py",
  });
  const close = (data: Record<string, unknown>) =>
    $.tool.call({ tool: "mcp__desk__desk_close", ...data } as never);

  const bare = await close({ card_id: "loop:T-1", verb: "do", note: "sent" });
  expect(String(bare.result)).toContain("Not closed");
  expect(processes).toEqual([]);

  const later = await close({
    card_id: "loop:T-1",
    verb: "defer",
    trigger: "later",
  });
  expect(String(later.result)).toContain("Not deferred");

  const done = await close({
    card_id: "loop:T-1",
    verb: "do",
    note: "sent as Luna",
    caption: "replied 02.10., waiting on customer",
  });
  expect(String(done.result)).toContain("closed (do)");
  // The desk is live: a close names the count and never a position in a sitting.
  expect(String(done.result)).toContain("Closed today:");
  expect(String(done.result)).not.toContain(" of 6");
  expect(processes.length).toBe(1);
  expect(processes[0]?.slice(2)).toEqual([
    "loop:T-1",
    "do",
    "sent as Luna",
    "--caption",
    "replied 02.10., waiting on customer",
  ]);
});

test("desk_card puts the options on the pane and tells Claude to wait", async ($, on) => {
  world(on);
  await $.tool.call({
    tool: "Bash",
    command: "python3 ~/orbit-scratch/desk/desk.py",
  });
  const shown = await $.tool.call({
    tool: "mcp__desk__desk_card",
    card_id: "loop:T-1",
    why: "Dana wrote at 09:11 today and has had no answer",
    decide: "Send Dana this answer as Luna?",
    then: "Luna sends it now; the thread stays open until she confirms.",
    fact: "customer asked twice",
    draft: "Hallo Dana, ...",
    options: [
      { label: "Send it", verb: "do" },
      { label: "Edit first", verb: "delegate" },
    ],
  } as never);

  expect(String(shown.result)).toContain("a: Send it");
  expect(String(shown.result)).toContain("Stop here and wait");

  const unknown = await $.tool.call({
    tool: "mcp__desk__desk_card",
    card_id: "loop:T-9",
    ...PLAIN_SHAPE,
    fact: "x",
    options: [{ label: "Send it", verb: "do" }],
  } as never);
  expect(String(unknown.result)).toContain("Not shown");
});

// Plain words on the pane (Tobias, 2026-10-14): the draft that landed on the pane, and the rewrite he accepted.
const CODE_DRAFT =
  "Set the Status lines: 01 → ready-for-agent (Remaining: the deferredIntentRef hold exists; missing is the terminal " +
  "path when the rebuild settles with no product: route a held CACHE_MISS to the cache-expired flow and a held failure " +
  "to inline error/Retry, from an explicit rebuild-settled signal, no timeout); 03 → ready-for-agent (decision: record " +
  "the edited product by id in handleSelectionChange, clear only that product at unmount, resolve its index by id; never " +
  "the current index); acme/01 → wontfix (product 12345678 retired 2026-10-06, …)";
const PLAIN_FACT =
  "Three old triage files: two Hub booking bugs are still real, one Acme pricing issue is gone";
// The fixed shape above the draft (Tobias, 2026-10-14): why it is on the desk, what to decide, what then happens.
const PLAIN_SHAPE = {
  why: "These three files have sat untriaged since 26 Aug, nobody waits",
  decide: "Hand the two Hub bugs to agents and close the third file?",
  then: "Two agents start today; left alone the bugs stay live on Hub.",
};
const PLAIN_DRAFT = [
  "1. Hub bug: a resumed booking whose product never comes back stays on the loading screen forever. Still true. Hand it to an agent.",
  "2. Hub bug: edit the schedule of product A, switch to product B, go back: B's schedule gets wiped. Still true. Hand it to an agent; only the product that was edited may be cleared.",
  "3. Acme: the purchasing product this was about was retired on 6 Oct. Close it as wontfix.",
].join("\n");

test("the plain-words check names the code words of a draft nobody can read", () => {
  const problems = plainWordsProblems({ fact: "Three triage files", draft: CODE_DRAFT }).join(" ");

  for (const word of [
    "deferredIntentRef",
    "CACHE_MISS",
    "12345678",
    "handleSelectionChange",
    "ready-for-agent",
    "acme/01",
  ]) {
    expect(problems).toContain(word);
  }
});

test("the plain-words check passes the rewrite Tobias accepted", () => {
  expect(plainWordsProblems({ ...PLAIN_SHAPE, fact: PLAIN_FACT, draft: PLAIN_DRAFT })).toEqual([]);
});

test("a customer reply with a thread ref, a PR number, a date and an amount passes", () => {
  const reply =
    "Hallo Dana,\n\ndanke dir für den Hinweis zu T-120. Der Fix ist mit #1234 seit 14.10. live, die Gutschrift " +
    "über 49,90 € kommt mit der nächsten Rechnung. Mehr dazu: https://support.example.com/articles/booking-schedule-per-product\n\nLG Luna";

  expect(
    plainWordsProblems(
      {
        why: "Dana wrote at 09:11 today and has had no answer",
        decide: "Send Dana this answer as Luna?",
        then: "Luna sends it now; the thread stays open until she confirms on staging.",
        fact: "customer asked twice, fix for ORB-203 is live since 2026-10-14",
        draft: reply,
      },
      true,
    ),
  ).toEqual([]);
});

test("the plain-words check holds the fact length, the item cap and the item length", () => {
  expect(plainWordsProblems({ fact: "x".repeat(141) }).join(" ")).toContain("141 characters");
  const four = ["1. a", "2. b", "3. c", "4. d"].join("\n");
  expect(plainWordsProblems({ draft: four }).join(" ")).toContain("4 numbered items");
  // A reply keeps its own shape.
  expect(plainWordsProblems({ draft: four }, true)).toEqual([]);
  expect(plainWordsProblems({ draft: `1. ${"word ".repeat(50)}` }).join(" ")).toContain("item 1");
  expect(plainWordsProblems({ draft: "run `pnpm test` in mod/hooks/register.tsx at 6ff560d68e" }).join(" ")).toMatch(
    /backtick.*file path.*id or hash/,
  );
});

test("desk_card refuses a brief in code words and names them", async ($, on) => {
  world(on);
  await $.tool.call({
    tool: "Bash",
    command: "python3 ~/orbit-scratch/desk/desk.py",
  });
  const show = (fact: string, draft: string) =>
    $.tool.call({
      tool: "mcp__desk__desk_card",
      card_id: "loop:T-1",
      ...PLAIN_SHAPE,
      fact,
      draft,
      options: [{ label: "Hand it to an agent", verb: "do" }],
    } as never);

  const refused = String((await show("Three triage files", CODE_DRAFT)).result);
  expect(refused).toContain("Not shown");
  expect(refused).toContain("deferredIntentRef");
  expect(refused).toContain("CACHE_MISS");
  expect(refused).toContain("12345678");

  const shown = String((await show(PLAIN_FACT, PLAIN_DRAFT)).result);
  expect(shown).toContain("Stop here and wait");
});

test("desk_card refuses a card without why and names the missing field", async ($, on) => {
  world(on);
  await $.tool.call({
    tool: "Bash",
    command: "python3 ~/orbit-scratch/desk/desk.py",
  });
  const refused = String(
    (
      await $.tool.call({
        tool: "mcp__desk__desk_card",
        card_id: "loop:T-1",
        decide: PLAIN_SHAPE.decide,
        then: PLAIN_SHAPE.then,
        fact: PLAIN_FACT,
        draft: PLAIN_DRAFT,
        options: [{ label: "Hand it to an agent", verb: "do" }],
      } as never)
    ).result,
  );

  expect(refused).toContain("Not shown");
  expect(refused).toContain("no why");
  expect(refused).not.toContain("decide,");
});

test("why, decide and then go through the same plain-words guard", () => {
  const problems = plainWordsProblems({
    why: "the file at needs-triage since 26 Aug",
    decide: "Clear handleSelectionChange at unmount?",
    then: `route a held CACHE_MISS to the expired flow; ${"and more ".repeat(40)}`,
    fact: PLAIN_FACT,
  }).join(" ");

  expect(problems).toContain('why has code words: "needs-triage"');
  expect(problems).toContain('decide has code words: "handleSelectionChange"');
  expect(problems).toContain("then has code words");
  expect(problems).toContain("CACHE_MISS");
  expect(problems).toMatch(/then is \d+ characters/);
});

// The live feed: config.json "liveEvents" switches it on; a round runs desk-events.py poll, then one desk.py --event
// with the round's events, and the loop ends when the pane closes.
const liveWorld = (on: On, liveEvents: boolean) => {
  const config = JSON.stringify({ projects: ["~/app"], liveEvents });
  mock.env(on, { HOME: "/home/t" });
  mock.clock(on, { now: Date.parse("2026-10-02T10:00:00Z") });
  on("session.root", () => ({ value: "/home/t/app" }));
  on("fs.stat", () => ({
    value: { kind: "file" as const, size: 1, mtimeMs: 1, isLink: false },
  }));
  on("fs.read", (_$, e) => ({
    value: e.path.endsWith("cards.json")
      ? CARDS
      : e.path.endsWith("config.json")
        ? config
        : e.path.endsWith("desk.json")
          ? DESK
          : "",
  }));
  on("fs.write", () => ({ value: undefined as never }));
  on("fs.list", () => ({ value: [] as never }));
  on("ui.open", () => ({ value: { id: "desk" } as never }));
  on("ui.status", () => ({ value: undefined as never }));
  const processes: string[][] = [];
  let polls = 0;
  // The pane stays open for one round.
  on("ui.panes", () => ({
    value: (polls < 1 ? [{ id: "desk" }] : []) as never,
  }));
  on("process.run", (_$, e) => {
    processes.push([...e.argv]);
    const isPoll = e.argv.some((arg) => arg.endsWith("desk-events.py"));
    if (isPoll) {
      polls += 1;
    }

    return {
      value: {
        exitCode: 0,
        stdout: isPoll
          ? JSON.stringify([
              { type: "thread.created", ref: "T-101", at: "2026-10-02T09:59:00Z" },
            ])
          : "",
        stderr: "",
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    };
  });

  return processes;
};

// The loop runs in the background of the command; a few runtime ticks let it finish its round.
declare const setTimeout: (fn: () => void, ms: number) => unknown;
const settle = async () => {
  for (let i = 0; i < 50; i += 1) {
    await new Promise<void>((resolve) => setTimeout(resolve, 5));
  }
};

test("with liveEvents on, a feed round applies its events through desk.py --event", async ($, on) => {
  const processes = liveWorld(on, true);
  await $.command.run({ command: "desk-pane" } as never);
  await settle();

  const scripts = processes.map((argv) => argv[1]?.split("/").at(-1));
  expect(scripts).toContain("desk-events.py");
  const applied = processes.find((argv) => argv.includes("--event"));
  expect(applied?.[1]).toEndWith("desk.py");
  expect(JSON.parse(applied?.[3] ?? "[]")[0].ref).toBe("T-101");
});

test("without liveEvents the pane never polls the feed", async ($, on) => {
  const processes = liveWorld(on, false);
  await $.command.run({ command: "desk-pane" } as never);
  await settle();

  expect(processes.some((argv) => argv.includes("poll"))).toBe(false);
  expect(processes.some((argv) => argv.includes("--event"))).toBe(false);
});
