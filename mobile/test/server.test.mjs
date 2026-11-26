// The phone server on a made-up desk in a temp folder. No real script runs: the process runner is a stub that
// records each call and plays desk-decide.py.
//
//   node --test mobile/test/server.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  act,
  buildState,
  isAllowed,
  makeHandler,
  startDesk,
  stopDesk,
} from "../server.mjs";

async function world({ decideExit = 0, decideClass = "script" } = {}) {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), "desk-phone-"));
  const dir = path.join(home, "orbit-scratch", "desk");
  await fs.mkdir(path.join(dir, "briefs"), { recursive: true });
  await fs.mkdir(path.join(dir, "decisions"), { recursive: true });
  const card = (n, status, act) => ({
    id: `loop:T-${n}`,
    ref: `T-${n}`,
    lane: "REPLY",
    mins: 3,
    impact: "P2",
    act,
    status,
  });
  await fs.writeFile(
    path.join(dir, "cards.json"),
    JSON.stringify({
      // a fresh gather, so the timed gather is not due
      generatedAt: new Date().toISOString(),
      cards: [
        card(1, "new", "Answer the customer"),
        card(2, "drafted", "Send it"),
      ],
      parked: [],
    }),
  );
  await fs.writeFile(
    path.join(dir, "desk.json"),
    JSON.stringify({ cards: {}, log: [] }),
  );
  await fs.writeFile(
    path.join(dir, "briefs", "loop_T-2.json"),
    JSON.stringify({
      cardId: "loop:T-2",
      status: "drafted",
      act: "Send it",
      preparedAt: new Date().toISOString(),
      why: "The customer wrote this morning and has had no answer",
      decide: "Send the answer?",
      then: "The answer goes out now.",
      fact: "customer asked once",
      draft: "The fix is live.",
      options: [
        { label: "Send it", verb: "do" },
        { label: "Drop", verb: "drop" },
      ],
    }),
  );
  const calls = [];
  const runProcess = async (argv) => {
    const script = path.basename(argv[1] ?? argv[0]);
    calls.push([script, ...argv.slice(2)]);
    if (script === "desk-decide.py") {
      if (decideExit !== 0) {
        return { exitCode: decideExit, stdout: "", stderr: "no fresh brief" };
      }
      await fs.writeFile(
        path.join(dir, "decisions", "loop_T-2.json"),
        JSON.stringify({
          cardId: argv[2],
          ref: "T-2",
          lane: "REPLY",
          option: argv[3],
          verb: "do",
          class: decideClass,
          state: "queued",
          pickedAt: new Date().toISOString(),
        }),
      );
      return { exitCode: 0, stdout: `${decideClass}\n`, stderr: "" };
    }
    return { exitCode: 0, stdout: "", stderr: "" };
  };
  const desk = await startDesk({ home, runProcess });
  const ran = (script) => calls.filter((call) => call[0] === script);

  return { desk, dir, calls, ran, end: () => stopDesk(desk) };
}

/** Lets the background runs the core started (execute, prepare) reach the stub. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

test("the state shows the core's cards with the prepared options", async () => {
  const { desk, end } = await world();
  const state = await buildState(desk);
  const card = state.cards.find((one) => one.id === "loop:T-2");
  assert.deepEqual(
    card.prepared.options.map((one) => one.label),
    ["Send it", "Drop"],
  );
  assert.equal(card.prepared.draft, "The fix is live.");
  assert.equal(state.cards.find((one) => one.id === "loop:T-1").prepared, null);
  end();
});

test("a pick records the decision once and starts the background run", async () => {
  const { desk, ran, end } = await world();
  const first = await act(desk, {
    action: "pick",
    cardId: "loop:T-2",
    option: "Send it",
  });
  await settle();
  assert.deepEqual(first, { ok: true, kind: "script", state: "queued" });
  assert.deepEqual(ran("desk-decide.py"), [
    ["desk-decide.py", "loop:T-2", "Send it"],
  ]);
  assert.equal(ran("desk-execute.py").length, 1);
  const second = await act(desk, {
    action: "pick",
    cardId: "loop:T-2",
    option: "Send it",
  });
  assert.equal(second.ok, false);
  assert.equal(ran("desk-decide.py").length, 1);
  end();
});

test("two taps at the same moment record one pick", async () => {
  const { desk, ran, end } = await world();
  const both = await Promise.all([
    act(desk, { action: "pick", cardId: "loop:T-2", option: "Send it" }),
    act(desk, { action: "pick", cardId: "loop:T-2", option: "Send it" }),
  ]);
  assert.deepEqual(
    both.map((one) => one.ok),
    [true, false],
  );
  assert.equal(ran("desk-decide.py").length, 1);
  end();
});

test("an option the card does not carry is refused and nothing runs", async () => {
  const { desk, ran, end } = await world();
  const made = await act(desk, {
    action: "pick",
    cardId: "loop:T-2",
    option: "Send it to everyone",
  });
  const noBrief = await act(desk, {
    action: "pick",
    cardId: "loop:T-1",
    option: "Send it",
  });
  assert.equal(made.ok, false);
  assert.equal(noBrief.ok, false);
  assert.equal(ran("desk-decide.py").length, 0);
  end();
});

test("opening a card without a brief asks the desk to write one, for that card alone", async () => {
  const { desk, ran, end } = await world();
  const asked = await act(desk, { action: "prepare", cardId: "loop:T-1" });
  const hasOne = await act(desk, { action: "prepare", cardId: "loop:T-2" });
  await settle();
  assert.equal(asked.ok, true);
  assert.equal(hasOne.ok, false);
  assert.deepEqual(
    ran("desk-prepare.py").filter((call) => call.length > 1),
    [["desk-prepare.py", "loop:T-1"]],
  );
  end();
});

test("a typed message goes to Claude once, with the card it was typed on, and the page gets the answer", async () => {
  const { desk, dir, ran, end } = await world();
  const empty = await act(desk, { action: "say", text: "   " });
  const sent = await act(desk, {
    action: "say",
    text: "Why is this one still open?",
    cardId: "loop:T-2",
  });
  const general = await act(desk, { action: "say", text: "What is next?", cardId: "loop:T-3" });
  await settle();
  assert.equal(empty.ok, false);
  assert.equal(sent.ok, true);
  assert.equal(general.ok, true);
  assert.deepEqual(ran("desk-chat.py"), [
    ["desk-chat.py", "Why is this one still open?", "--card", "loop:T-2"],
    ["desk-chat.py", "What is next?"],
  ]);
  await fs.writeFile(
    path.join(dir, "chat.jsonl"),
    `${JSON.stringify({ at: "2026-01-01T10:00:00Z", from: "you", text: "Why is this one still open?", id: "a1", card: "loop:T-2", no: 7 })}\n` +
      `${JSON.stringify({ at: "2026-01-01T10:00:20Z", from: "claude", text: "It waits for your send.", re: "a1", card: "loop:T-2", no: 7 })}\n`,
  );
  const state = await buildState(desk);
  assert.deepEqual(
    state.chat.map((row) => [row.from, row.text, row.no]),
    [
      ["you", "Why is this one still open?", 7],
      ["claude", "It waits for your send.", 7],
    ],
  );
  assert.equal(state.chatRuns, 0);
  end();
});

test("a pick that needs a session waits for the laptop and runs nothing in the background", async () => {
  const { desk, ran, end } = await world({ decideClass: "session" });
  const result = await act(desk, {
    action: "pick",
    cardId: "loop:T-2",
    option: "Send it",
  });
  await settle();
  assert.deepEqual(result, { ok: true, kind: "session", state: "queued" });
  assert.equal(ran("desk-execute.py").length, 0);
  end();
});

test("a pick the script refuses for a stale brief says so and leaves no card marked as worked", async () => {
  const { desk, ran, end } = await world({ decideExit: 3 });
  const result = await act(desk, {
    action: "pick",
    cardId: "loop:T-2",
    option: "Send it",
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /laptop/);
  assert.equal(ran("desk-execute.py").length, 0);
  assert.equal(desk.core.turn.pendingAnswer, null);
  end();
});

test("only an allowed Tailscale login gets in; a local request needs local: true", () => {
  const from = (login) => ({
    headers: login ? { "tailscale-user-login": login } : {},
  });
  assert.equal(
    isAllowed(from("Owner-Login"), { allow: ["owner-login"] }),
    true,
  );
  assert.equal(
    isAllowed(from("other-login"), { allow: ["owner-login"] }),
    false,
  );
  assert.equal(isAllowed(from(""), { allow: ["owner-login"] }), false);
  assert.equal(isAllowed(from(""), { local: true }), true);
  assert.equal(isAllowed(from("other-login"), { local: true }), false);
});

test("over HTTP: the state needs an allowed caller, and a pick needs the desk header", async (t) => {
  const { desk, ran, end } = await world();
  const server = http.createServer(
    makeHandler(desk, { allow: ["owner-login"] }),
  );
  // a failed assert must not leave the socket open: the run would never end
  t.after(() => {
    server.closeAllConnections();
    server.close();
    end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const me = { "tailscale-user-login": "owner-login" };
  const pick = JSON.stringify({
    action: "pick",
    cardId: "loop:T-2",
    option: "Send it",
  });

  assert.equal((await fetch(`${base}/api/state`)).status, 403);
  const state = await (
    await fetch(`${base}/api/state`, { headers: me })
  ).json();
  assert.equal(state.cards.length, 2);
  assert.equal(
    (
      await fetch(`${base}/api/act`, {
        method: "POST",
        headers: me,
        body: pick,
      })
    ).status,
    400,
  );
  assert.equal(ran("desk-decide.py").length, 0);
  const done = await (
    await fetch(`${base}/api/act`, {
      method: "POST",
      headers: { ...me, "x-desk": "1" },
      body: pick,
    })
  ).json();
  assert.equal(done.ok, true);
  assert.equal((await fetch(`${base}/`, { headers: me })).status, 200);
  assert.equal(
    (await fetch(`${base}/../server.mjs`, { headers: me })).status,
    404,
  );
});
