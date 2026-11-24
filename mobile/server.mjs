#!/usr/bin/env node
// The phone desk: a small HTTP server that runs the same desk core as the terminal pane (mod/hooks/core) on its own
// `$`, and serves the phone page (mobile/web).
//
//   node mobile/server.mjs [--port 47600] [--config mobile/config.json] [--home DIR]
//
// It listens on 127.0.0.1 only. Tailscale Serve puts it on the tailnet over HTTPS and names the caller in the
// Tailscale-User-Login header. A request is let in when that login is in config.json "allow". A request with no
// such header comes from this machine; it is let in only when config.json says "local": true.
//
// Rule: desk behaviour lives in the core. This file gives the core files, processes and state, and turns each
// button of the page into one core call.
import "./loader.mjs";

import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { read, update } from "./engine.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.join(HERE, "web");
const CORE = path.join(path.dirname(HERE), "mod", "hooks", "core", "desk.ts");
const PANE = "desk";
// The page asks every few seconds while it is open; a desk nobody looked at for this long does not gather.
const WATCHED_MS = 2 * 60_000;
const BEAT_MS = 5_000;
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".webmanifest": "application/manifest+json",
};

/** Runs a command the way $.process.run does: never throws on a non-zero exit, kills at the time limit. */
export function run(argv, options = {}) {
  return new Promise((resolve) => {
    execFile(
      argv[0],
      argv.slice(1),
      {
        env: { ...process.env, ...(options.env ?? {}) },
        cwd: options.cwd,
        timeout: options.timeoutMs ?? 60_000,
        maxBuffer: 16 * 1024 * 1024,
      },
      (error, stdout, stderr) => {
        const exitCode =
          error === null ? 0 : typeof error.code === "number" ? error.code : 1;
        resolve({ exitCode, stdout: String(stdout), stderr: String(stderr) });
      },
    );
  });
}

/** The `$` of the phone server: the part of the engine interface the core calls. */
export function makeEngine({ home, runProcess }) {
  const $ = {
    state: new Map(),
    version: 0,
    /** Picks that need a Claude session; the phone has none, so the page says where the pick waits. */
    notes: [],
    handed: 0,
    changed() {
      $.version += 1;
    },
    env: {
      get: async (name) => (name === "HOME" ? home : process.env[name]),
    },
    clock: {
      now: async () => Date.now(),
      sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      every: (ms, fn) => {
        const timer = setInterval(() => void fn(), ms);
        return { cancel: () => clearInterval(timer) };
      },
    },
    fs: {
      read: (file) => fs.readFile(file, "utf8"),
      write: async (file, text) => {
        await fs.mkdir(path.dirname(file), { recursive: true });
        const tmp = `${file}.${process.pid}.tmp`;
        await fs.writeFile(tmp, text);
        await fs.rename(tmp, file);
      },
      stat: async (file) => {
        const stat = await fs.stat(file);
        return {
          kind: stat.isDirectory() ? "dir" : "file",
          mtimeMs: stat.mtimeMs,
          size: stat.size,
          isLink: false,
        };
      },
      list: async (dir) => {
        const names = await fs.readdir(dir);
        const entries = await Promise.all(
          names.map(async (name) => {
            const stat = await fs.stat(path.join(dir, name)).catch(() => null);
            return stat === null
              ? null
              : {
                  name,
                  kind: stat.isDirectory() ? "dir" : "file",
                  size: stat.size,
                  mtimeMs: stat.mtimeMs,
                  isLink: false,
                };
          }),
        );
        return entries.filter((entry) => entry !== null);
      },
    },
    process: { run: runProcess },
    // The server is a surface of the desk: for the core's "is a pane open" checks it counts as the open pane.
    ui: {
      panes: async () => [{ id: PANE }],
      open: async () => ({ isPlaced: false, reason: "phone" }),
      status: () => undefined,
      log: (line) => console.error("desk:", line),
    },
    model: { complete: async () => ({ isAnswered: false, text: "" }) },
    tool: {
      list: async () => [],
      call: async () => {
        throw new Error("no tools on the phone server");
      },
    },
    // A pick the core hands to a session has no session here. It stays recorded where the scripts put it.
    prompt: {
      submit: async ({ text }) => {
        $.handed += 1;
        $.notes.push({ at: Date.now(), text: String(text) });
        $.notes = $.notes.slice(-20);
        $.changed();
      },
    },
    session: { id: async () => "phone", root: async () => home },
  };

  return $;
}

/** One desk: a copy of the core with its engine, kept current by a beat. */
export async function startDesk({
  home = os.homedir(),
  gatherAlways = false,
  runProcess = run,
} = {}) {
  const core = await import(
    `${pathToFileURL(CORE).href}?home=${encodeURIComponent(home)}`
  );
  const $ = makeEngine({ home, runProcess });
  const desk = {
    core,
    $,
    home,
    seenAt: 0,
    lock: Promise.resolve(),
    timer: null,
    gatherAlways,
  };
  await core.refresh($, true);
  desk.timer = setInterval(
    () => void beat(desk).catch((error) => console.error("beat", error)),
    BEAT_MS,
  );
  desk.timer.unref();

  return desk;
}

export function stopDesk(desk) {
  clearInterval(desk.timer);
}

/** The pane's timers: show what the scripts wrote, and gather when the desk is due and somebody looks at it. */
export async function beat(desk) {
  const { core, $ } = desk;
  await core.refresh($);
  if (desk.gatherAlways || Date.now() - desk.seenAt < WATCHED_MS) {
    void core.gather($, "due");
  }
}

/** What the page draws. The view is the core's; nothing here ranks or filters a card. */
export async function buildState(desk) {
  const { core, $ } = desk;
  const view = await read($, core.view);
  const stats = await read($, core.plain);

  return {
    version: $.version,
    now: Date.now(),
    isGathering: await read($, core.isGathering),
    recording: await read($, core.recording),
    cards: view.cards,
    decisions: view.decisions,
    parked: view.parked,
    closedToday: view.closedToday,
    generatedAt: view.generatedAt,
    scope: view.scope,
    sso: view.sso,
    plain:
      stats === null
        ? null
        : {
            todo: stats.todo,
            waitingOnUs: stats.waitingOnUs,
            newToday: stats.newToday,
          },
    notes: $.notes,
  };
}

/** Each button of the page is one core call. The card and the option come from the core's view, never from the page. */
export const ACTIONS = {
  refresh: async ({ core, $ }) => {
    await core.refresh($, true);
    return { ok: true };
  },
  gather: async ({ core, $ }) => {
    void core.gather($, "now");
    return { ok: true };
  },
  pick: async ({ core, $ }, body) => {
    const view = await read($, core.view);
    const card = view.cards.find((one) => one.id === body.cardId);
    if (card === undefined || card.verb !== null) {
      return { ok: false, error: "This card is no longer open." };
    }
    const option = (card.prepared?.options ?? []).find(
      (one) => one.label === body.option,
    );
    if (option === undefined) {
      return { ok: false, error: "This option is no longer on the card." };
    }
    if (
      view.decisions.some(
        (one) =>
          one.cardId === card.id && ["queued", "running"].includes(one.state),
      )
    ) {
      return { ok: false, error: "A pick for this card is already recorded." };
    }
    const before = $.handed;
    await core.decide($, card, option);
    // The core marks a card it handed to a session as the one being worked. The phone has no session.
    core.turn.pendingAnswer = null;
    await update($, core.working, () => null);
    await update($, core.picked, () => "");
    const after = await read($, core.view);
    const decision = after.decisions.find((one) => one.cardId === card.id);
    if ($.handed > before) {
      return {
        ok: false,
        error: "This card has no fresh brief. Work it on the laptop.",
      };
    }
    if (decision === undefined) {
      return { ok: false, error: "The pick was not recorded." };
    }
    return { ok: true, kind: decision.kind, state: decision.state };
  },
};

/** One action at a time per desk: two fast taps never record two picks. */
export function act(desk, body) {
  const action = ACTIONS[body?.action];
  if (action === undefined) {
    return Promise.resolve({ ok: false, error: "unknown action" });
  }
  const result = desk.lock.then(() => action(desk, body));
  desk.lock = result.catch(() => undefined);
  return result;
}

export function isAllowed(req, config) {
  const login = String(req.headers["tailscale-user-login"] ?? "").toLowerCase();
  if (login === "") {
    return config.local === true;
  }
  return (config.allow ?? [])
    .map((one) => String(one).toLowerCase())
    .includes(login);
}

function send(res, status, body, type = "application/json") {
  const data = type === "application/json" ? JSON.stringify(body) : body;
  res.writeHead(status, {
    "content-type": type,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  res.end(data);
}

async function readBody(req, limit = 64 * 1024) {
  const parts = [];
  let size = 0;
  for await (const part of req) {
    size += part.length;
    if (size > limit) {
      throw new Error("body too large");
    }
    parts.push(part);
  }
  return Buffer.concat(parts).toString("utf8");
}

export function makeHandler(desk, config) {
  return async function handle(req, res) {
    if (!isAllowed(req, config)) {
      return send(res, 403, { error: "not allowed" });
    }
    const url = new URL(req.url ?? "/", "http://desk");
    if (req.method === "GET" && url.pathname === "/api/state") {
      desk.seenAt = Date.now();
      return send(res, 200, await buildState(desk));
    }
    if (req.method === "POST" && url.pathname === "/api/act") {
      // A page of another site cannot set this header without a preflight, and the server answers none.
      if (req.headers["x-desk"] !== "1") {
        return send(res, 400, { error: "missing header" });
      }
      const body = JSON.parse((await readBody(req)) || "{}");
      return send(res, 200, await act(desk, body));
    }
    if (req.method === "GET") {
      const name =
        url.pathname === "/" ? "index.html" : path.basename(url.pathname);
      const type = TYPES[path.extname(name)];
      const data = type
        ? await fs.readFile(path.join(WEB, name)).catch(() => null)
        : null;
      if (data !== null) {
        return send(res, 200, data, type);
      }
    }
    return send(res, 404, { error: "not found" });
  };
}

export function loadConfig(file) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return {};
  }
}

async function main() {
  const arg = (name) => {
    const at = process.argv.indexOf(name);
    return at > 0 ? process.argv[at + 1] : undefined;
  };
  const config = loadConfig(arg("--config") ?? path.join(HERE, "config.json"));
  const port = Number(arg("--port") ?? config.port ?? 47600);
  // --home points the desk at another home folder (a made-up desk for a look at the page).
  // "gather": "always" in the config: this server is the desk's only gatherer (the desk host), so it gathers
  // whenever the desk is due, with or without a phone on the page. "home" does what --home does.
  const desk = await startDesk({
    home: arg("--home") ?? config.home ?? os.homedir(),
    gatherAlways: config.gather === "always",
  });
  const handle = makeHandler(desk, config);
  http
    .createServer((req, res) => {
      handle(req, res).catch((error) => {
        console.error(req.method, req.url, error);
        if (!res.headersSent) {
          send(res, 500, { error: "server error" });
        } else {
          res.end();
        }
      });
    })
    .listen(port, "127.0.0.1", () => {
      console.log(`desk phone server on http://127.0.0.1:${port}`);
    });
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  await main();
}
