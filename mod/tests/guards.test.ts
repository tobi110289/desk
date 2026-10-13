import type { On } from "claude-code";
import { expect, mock, test } from "claude-code/testing";

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
    fact: "x",
    options: [{ label: "Send it", verb: "do" }],
  } as never);
  expect(String(unknown.result)).toContain("Not shown");
});
