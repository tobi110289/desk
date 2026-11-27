// The phone page of the desk. It draws the state the server sends and turns each tap into one action.
// Nothing here ranks or filters a card by a rule of its own: order, briefs and options come from the desk core.
"use strict";

const POLL_MS = 4000;
const WAIT_POLL_MS = 1500;
const ARM_MS = 4000;
const UNDO_MS = 5000;
// An option that sends text to a customer: two taps on the card, never a swipe on the list, no delayed send.
const READ_FIRST = /^(Send|Reply)\b/;
const PICTURE_EDGE = 1600;

const $ = (id) => document.getElementById(id);
const scrollBox = $("scroll");
const textBox = $("text");
const sendButton = $("send");
const micButton = $("mic");
const searchBox = $("search");

let state = null;
let tag = "";
// Server clock minus phone clock, so "Claude works 12s" counts from the server's time of the message.
let skew = 0;
let page = route();
let search = "";
let armed = "";
let armTimer = 0;
let drawn = "";
let drawnPage = "";
// A pick drawn before the server has it: { cardId, label, option, timer }. Undo takes it back within UNDO_MS.
let pending = null;
// Messages on their way: { key, text, cardId, no, at, file, picture, state: "going" | "failed" }.
const outbox = [];
// The picture picked for the next message: { blob, url }.
let attached = null;

const el = (tagName, className, text) => {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function route() {
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash === "") return { kind: "list", id: "" };
  if (hash === "chat") return { kind: "chat", id: "" };
  return { kind: "card", id: hash };
}

// The open card is in the address (#<card id>), so the back gesture of the phone closes it and a link opens it.
function open(hash) {
  history.pushState({ desk: 1 }, "", `#${encodeURIComponent(hash)}`);
  page = route();
  armed = "";
  draw(true);
}

function goList() {
  if (history.state && history.state.desk) {
    history.back();
    return;
  }
  history.replaceState(null, "", location.pathname);
  page = route();
  armed = "";
  draw(true);
}

// ---------------------------------------------------------------- a short tick under the finger

// iOS has no vibration for a web page. A switch control ticks when it flips (iOS 18 and later), so the page flips a
// hidden one. iOS lets the tick through only while the last touch still counts. Other phones use navigator.vibrate.
const isIos = /iP(hone|ad|od)/.test(navigator.userAgent);
function haptic() {
  try {
    if (!isIos && navigator.vibrate) {
      navigator.vibrate(12);
      return;
    }
    const label = el("label");
    label.style.display = "none";
    const input = el("input");
    input.type = "checkbox";
    input.setAttribute("switch", "");
    label.append(input);
    document.head.append(label);
    label.click();
    label.remove();
  } catch {
    // no tick
  }
}

// ---------------------------------------------------------------- the busy line and the toast

let busyCount = 0;
function busy(step, isOk = true) {
  const line = $("busy");
  busyCount = Math.max(0, busyCount + step);
  if (busyCount > 0) {
    line.className = "run";
    return;
  }
  line.className = isOk ? "ok" : "fail";
  requestAnimationFrame(() => line.classList.add("fade"));
}

// One toast at a time, on its own line over the keys. A note goes after 2 s, an Undo offer when its time is up;
// an error stays until a tap.
let toastTimer = 0;
function toast(text, { kind = "", action = "", run = null, ms = 2000 } = {}) {
  const box = $("toast");
  clearTimeout(toastTimer);
  box.replaceChildren(el("span", "", text));
  box.className = kind;
  if (action) {
    const button = el("button", "", action);
    button.type = "button";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      closeToast();
      run();
    });
    box.append(button);
  }
  box.hidden = false;
  if (kind !== "err") toastTimer = setTimeout(closeToast, ms);
}
function closeToast() {
  clearTimeout(toastTimer);
  $("toast").hidden = true;
}
$("toast").addEventListener("click", closeToast);

// ---------------------------------------------------------------- the server

let isLoading = false;
async function load(isForced = false) {
  if (isLoading) return;
  isLoading = true;
  try {
    const res = await fetch(`/api/state?tag=${isForced ? "" : tag}`, {
      cache: "no-store",
    });
    if (!res.ok)
      throw new Error(
        res.status === 403
          ? "This login is not allowed on the desk."
          : `The desk answered ${res.status}.`,
      );
    const got = await res.json();
    loadError = "";
    if (!got.same) {
      state = got;
      tag = got.tag || "";
      skew = got.now - Date.now();
      // A message leaves the outbox when the conversation holds it.
      const held = new Set((state.chat || []).map((row) => row.key));
      for (let i = outbox.length - 1; i >= 0; i--)
        if (held.has(outbox[i].key)) outbox.splice(i, 1);
    }
    draw();
  } catch (error) {
    loadError = error.message || "The desk does not answer.";
    draw(true);
  } finally {
    isLoading = false;
  }
}
let loadError = "";

async function post(body, isLast = false) {
  const res = await fetch("/api/act", {
    method: "POST",
    headers: { "content-type": "application/json", "x-desk": "1" },
    body: JSON.stringify(body),
    // The page is about to go: the browser still delivers the request.
    keepalive: isLast,
  });
  return res.json();
}

// One action: the busy line while the desk works, then green or red. An error shows as a toast that stays.
async function act(body, { isQuiet = false, isLast = false } = {}) {
  if (!isQuiet) busy(1);
  let result = { ok: false };
  try {
    result = await post(body, isLast);
    if (!result.ok && !isQuiet)
      toast(result.error || "The desk refused this.", { kind: "err" });
  } catch {
    if (!isQuiet)
      toast("The desk does not answer. Nothing was recorded.", { kind: "err" });
  }
  if (!isQuiet) busy(-1, result.ok);
  await load(true);
  return result;
}

// A card without a brief: ask the desk to write one, at most every 30 seconds per card.
const asked = {};
function askBrief(id) {
  if (Date.now() - (asked[id] || 0) < 30000) return;
  asked[id] = Date.now();
  post({ action: "prepare", cardId: id }).catch(() => undefined);
}

// ---------------------------------------------------------------- picks: drawn at once, sent when Undo has passed

function pick(card, option) {
  flushPick();
  haptic();
  pending = {
    cardId: card.id,
    label: numberOf(card) || card.ref,
    option,
    timer: setTimeout(flushPick, UNDO_MS),
  };
  toast(`${pending.label}: ${option.label}`, {
    action: "Undo",
    ms: UNDO_MS,
    run: () => {
      if (pending === null) return;
      clearTimeout(pending.timer);
      pending = null;
      draw(true);
    },
  });
  if (page.kind === "card" && page.id === card.id) goList();
  else draw(true);
}

async function flushPick(isLast = false) {
  if (pending === null) return;
  const one = pending;
  clearTimeout(one.timer);
  sending = one;
  pending = null;
  closeToast();
  await act(
    { action: "pick", cardId: one.cardId, option: one.option.label },
    { isLast },
  );
  sending = null;
  draw(true);
}
// The pick on its way to the server: its card keeps the line "Picked …" until the state carries the decision.
let sending = null;

// ---------------------------------------------------------------- words for a card

const numberOf = (card) => (card.no > 0 ? `D-${card.no}` : "");
const ago = (iso) => {
  const mins = Math.round((Date.now() + skew - Date.parse(iso)) / 60000);
  if (Number.isNaN(mins)) return "never";
  return mins < 1
    ? "just now"
    : mins < 60
      ? `${mins} min ago`
      : `${Math.round(mins / 60)} h ago`;
};
const clock = (iso) => {
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? ""
    : at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};
const took = (iso) => {
  const s = Math.max(
    0,
    Math.round((Date.now() + skew - Date.parse(iso)) / 1000),
  );
  if (Number.isNaN(s)) return "";
  if (s < 60) return `${s}s`;
  if (s < 600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 60)}m`;
};
const decisionOf = (id) =>
  (state.decisions || []).find((one) => one.cardId === id);

function pickText(card) {
  const mine = [pending, sending].find((one) => one && one.cardId === card.id);
  if (mine) return `Picked "${mine.option.label}".`;
  const recording = (state.recording || {})[card.id];
  if (recording) return `Recording: ${recording}`;
  const one = decisionOf(card.id);
  if (!one) return "";
  if (one.state === "queued" && one.kind === "session")
    return `Picked "${one.option}". It waits for a session on the laptop.`;
  if (one.state === "queued" || one.state === "running")
    return `Picked "${one.option}". It runs in the background.`;
  if (one.state === "done") return `Done: ${one.option}. ${one.result || ""}`;
  return `${one.state}: ${one.option}. ${one.result || ""}`;
}

// A pick that ran and did not do its work: the card shows why and offers its options again.
function failedPick(card) {
  if ([pending, sending].some((one) => one && one.cardId === card.id))
    return null;
  if ((state.recording || {})[card.id]) return null;
  const one = decisionOf(card.id);
  return one && !["queued", "running", "done"].includes(one.state) ? one : null;
}

// What a swipe to the right does on a card: its first option, when the card has a brief, no pick, and the option
// sends nothing to a customer. Otherwise the swipe opens the card.
function swipeOption(card) {
  if (card.verb !== null || !card.prepared || pickText(card)) return null;
  const first = card.prepared.options[0];
  return first && !READ_FIRST.test(first.label) ? first : null;
}

// ---------------------------------------------------------------- Markdown, as DOM nodes (no HTML from a message)

function inline(node, text) {
  const pattern =
    /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|\[([^\]\n]+)\]\((https:\/\/[^)\s]+)\)|(https:\/\/[^\s<>)\]]+)/g;
  let at = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > at) node.append(text.slice(at, match.index));
    if (match[1]) node.append(el("code", "", match[1].slice(1, -1)));
    else if (match[2]) node.append(el("strong", "", match[2].slice(2, -2)));
    else {
      const link = el("a", "", match[3] || match[5]);
      link.href = match[4] || match[5];
      link.target = "_blank";
      link.rel = "noopener";
      node.append(link);
    }
    at = match.index + match[0].length;
  }
  if (at < text.length) node.append(text.slice(at));
}

// A message as one run of plain words, for the line on the list.
const plain = (source) =>
  source
    .replace(/```[^\n]*/g, " ")
    .replace(/\*\*|`/g, "")
    .replace(/\[([^\]\n]+)\]\(https:\/\/[^)\s]+\)/g, "$1")
    .replace(/^\s*(?:[-*•]|#{1,6})\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();

function md(source) {
  const out = el("div", "body");
  source.split(/^```[^\n]*\n?/m).forEach((part, index) => {
    if (index % 2 === 1) {
      const box = el("div", "code");
      const pre = el("pre");
      const code = part.replace(/\n$/, "");
      pre.append(el("code", "", code));
      const copy = el("button", "copy", "Copy");
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(code);
          copy.textContent = "Copied";
        } catch {
          copy.textContent = "No access";
        }
        setTimeout(() => (copy.textContent = "Copy"), 1500);
      });
      box.append(pre, copy);
      out.append(box);
      return;
    }
    let para = null;
    let list = null;
    for (const line of part.split("\n")) {
      const item = /^\s*(?:([-*•])|(\d+)[.)])\s+(.*)$/.exec(line);
      const head = /^#{1,6}\s+(.*)$/.exec(line);
      if (line.trim() === "") {
        para = list = null;
      } else if (item) {
        const kind = item[1] ? "UL" : "OL";
        if (list === null || list.tagName !== kind) {
          list = el(kind.toLowerCase());
          out.append(list);
        }
        const li = el("li");
        inline(li, item[3]);
        list.append(li);
        para = null;
      } else if (head) {
        const p = el("p");
        const strong = el("strong");
        inline(strong, head[1]);
        p.append(strong);
        out.append(p);
        para = list = null;
      } else {
        if (para === null) {
          para = el("p");
          out.append(para);
        } else para.append("\n");
        inline(para, line);
        list = null;
      }
    }
  });
  return out;
}

// ---------------------------------------------------------------- the conversation

// The messages of a card, or all of them: what the conversation holds, then the ones on their way.
function thread(cardId) {
  const held = (state.chat || []).filter(
    (row) => !cardId || row.card === cardId,
  );
  const going = outbox.filter((one) => !cardId || one.cardId === cardId);
  return { held, going };
}

// Since when Claude works on a message that has no answer, or "" when it works on none.
function worksSince(cardId) {
  const { held, going } = thread(cardId);
  const answered = new Set(held.map((row) => row.re));
  // A run ends after ten minutes at most: an older message without an answer never got one, and no run works on it.
  const open = held.filter(
    (row) =>
      row.from === "you" &&
      !answered.has(row.id) &&
      Date.now() + skew - Date.parse(row.at) < 10 * 60000,
  );
  if (open.length > 0 && state.chatRuns > 0) return open[0].at;
  const first = going.find((one) => one.state === "going");
  return first ? first.at : "";
}

function messageNode(row, isAll) {
  const kind = row.state ? `you ${row.state}` : row.from;
  const node = el("div", `msg ${kind}`);
  const who =
    row.state === "failed"
      ? "Not sent. Tap to send again"
      : row.from === "claude"
        ? "Claude"
        : row.from === "desk"
          ? "Desk"
          : "You";
  const bits = [who, clock(row.at)];
  if (isAll && row.no > 0) bits.push(`D-${row.no}`);
  if (row.hasPicture || row.file || row.picture) bits.push("with a picture");
  node.append(el("span", "who", bits.filter(Boolean).join(" · ")));
  node.append(
    row.from === "claude" ? md(row.text) : el("div", "body", row.text),
  );
  if (row.state === "failed")
    node.addEventListener("click", () => void deliver(row));
  return node;
}

function drawThread(cardId, limit) {
  const { held, going } = thread(cardId);
  const nodes = held
    .slice(-limit)
    .map((row) => messageNode(row, !cardId))
    .concat(going.map((one) => messageNode({ ...one, from: "you" }, !cardId)));
  const since = worksSince(cardId);
  if (since) {
    const works = el("div", "works");
    const time = el("span", "", took(since));
    time.dataset.since = since;
    works.append(el("span", "dot"), "Claude works", time);
    nodes.push(works);
  }
  return nodes;
}

async function scaleDown(file) {
  try {
    const bitmap = await createImageBitmap(file);
    const ratio = Math.min(
      1,
      PICTURE_EDGE / Math.max(bitmap.width, bitmap.height),
    );
    const canvas = el("canvas");
    canvas.width = Math.round(bitmap.width * ratio);
    canvas.height = Math.round(bitmap.height * ratio);
    canvas
      .getContext("2d")
      .drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.85),
    );
    if (blob) return blob;
  } catch {
    // the phone cannot decode it here: the file goes as it is, when the desk can read its kind
  }
  return /^image\/(jpeg|png|webp)$/.test(file.type) ? file : null;
}

async function attach(file) {
  if (!file) return;
  const blob = await scaleDown(file);
  if (blob === null) {
    toast("The phone cannot read this picture.", { kind: "err" });
    return;
  }
  clearAttached();
  attached = { blob, url: URL.createObjectURL(blob) };
  drawAttached();
}
function clearAttached() {
  if (attached) URL.revokeObjectURL(attached.url);
  attached = null;
  drawAttached();
}
function drawAttached() {
  const box = $("attach");
  box.replaceChildren();
  box.hidden = attached === null;
  if (attached) {
    const img = el("img");
    img.src = attached.url;
    img.alt = "";
    const remove = el("button", "", "Remove");
    remove.type = "button";
    remove.addEventListener("click", clearAttached);
    box.append(img, el("span", "", "Goes with the next message."), remove);
  }
  setSendable();
}
const setSendable = () => {
  sendButton.disabled = textBox.value.trim() === "" && attached === null;
};

// Sends one outbox message: the picture first, then the text with the picture's name.
async function deliver(one) {
  one.state = "going";
  draw(true);
  try {
    if (one.file) {
      const res = await fetch("/api/upload", {
        method: "POST",
        headers: { "content-type": "application/octet-stream", "x-desk": "1" },
        body: one.file,
      });
      const kept = await res.json();
      if (!kept.ok)
        throw new Error(kept.error || "The picture did not reach the desk.");
      one.picture = kept.name;
      one.file = null;
    }
    const result = await post({
      action: "say",
      text: one.text,
      cardId: one.cardId || undefined,
      key: one.key,
      picture: one.picture || undefined,
    });
    if (!result.ok)
      throw new Error(result.error || "The desk refused the message.");
  } catch (error) {
    one.state = "failed";
    toast(error.message || "The desk does not answer.", { kind: "err" });
  }
  await load(true);
  draw(true);
}

function sendMessage() {
  const text = textBox.value.trim();
  if (text === "" && attached === null) return;
  haptic();
  const card =
    page.kind === "card" ? state.cards.find((one) => one.id === page.id) : null;
  const one = {
    key: `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
    text: text || "See the picture.",
    cardId: card ? card.id : "",
    no: card ? card.no : 0,
    at: new Date(Date.now() + skew).toISOString(),
    file: attached ? attached.blob : null,
    picture: "",
    state: "going",
  };
  outbox.push(one);
  textBox.value = "";
  textBox.style.height = "auto";
  clearAttached();
  stopDictation();
  toEnd = true;
  // A message from the list opens the conversation, as a messenger does.
  if (page.kind === "list") open("chat");
  void deliver(one);
}

// ---------------------------------------------------------------- dictation

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let listening = null;
function stopDictation() {
  if (listening === null) return;
  const one = listening;
  listening = null;
  try {
    one.stop();
  } catch {
    // already stopped
  }
  micButton.classList.remove("on");
}
function dictate() {
  if (listening !== null) {
    stopDictation();
    return;
  }
  const base = textBox.value ? `${textBox.value.trimEnd()} ` : "";
  const one = new Recognition();
  one.lang = navigator.language || "en-US";
  one.continuous = true;
  one.interimResults = true;
  one.onresult = (event) => {
    let said = "";
    for (const result of event.results) said += result[0].transcript;
    textBox.value = base + said;
    grow();
    setSendable();
  };
  one.onerror = (event) => {
    if (event.error === "not-allowed" || event.error === "service-not-allowed")
      toast("The phone does not let this page use the microphone.", {
        kind: "err",
      });
  };
  one.onend = () => {
    if (listening === one) stopDictation();
  };
  listening = one;
  micButton.classList.add("on");
  try {
    one.start();
  } catch {
    stopDictation();
  }
}

// ---------------------------------------------------------------- the list

function matches(card) {
  if (search === "") return true;
  const hay =
    `${numberOf(card)} ${card.ref} ${card.lane} ${card.impact || ""} ${card.act} ${card.status || ""}`.toLowerCase();
  return search
    .toLowerCase()
    .split(/\s+/)
    .every((word) => hay.includes(word));
}

function rowNode(card) {
  const wrap = el("div", "rowwrap");
  wrap.dataset.id = card.id;
  const option = swipeOption(card);
  const under = el("div", "swipe");
  under.append(
    el("span", "l", option ? option.label : "Open"),
    el("span", "r", "Ask Claude"),
  );
  const row = el("button", card.verb === null ? "row" : "row closed");
  row.type = "button";
  const top = el("div", "top");
  top.append(
    el("span", "no", numberOf(card)),
    el("span", card.impact === "P0" ? "p0" : "", card.impact || ""),
    el("span", "", card.lane),
    el("span", "", card.ref),
    el("span", "end", card.verb === null ? `${card.mins} min` : card.verb),
  );
  row.append(top, el("div", "act", card.act));
  const picked = pickText(card);
  const line = picked || card.health;
  if (line)
    row.append(el("div", card.health && !picked ? "line warn" : "line", line));
  wrap.append(under, row);
  return wrap;
}

function drawList() {
  const shown = state.cards.filter(matches);
  const open = shown.filter((card) => card.verb === null);
  const closed = shown.filter((card) => card.verb !== null);
  // The box is drawn bottom-up: the first card of the desk is the lowest row.
  for (const card of open) scrollBox.append(rowNode(card));
  if (closed.length > 0) {
    scrollBox.append(el("div", "group", "Closed today"));
    for (const card of closed) scrollBox.append(rowNode(card));
  }
  if (shown.length === 0)
    scrollBox.append(
      el("p", "empty", search ? "No card matches." : "The desk is empty."),
    );
}

// ---------------------------------------------------------------- a card

function drawCard(card) {
  const box = el("section", "brief");
  const add = (label, text, className = "text") => {
    if (!text) return;
    box.append(el("div", "label", label), el("div", className, text));
  };
  add("Card", card.act);
  const brief = card.prepared;
  const picked = pickText(card);
  if (brief) {
    add("Why", brief.why);
    add("Decide", brief.decide);
    add("Then", brief.then);
    add("Fact", brief.fact);
    add("Draft", brief.draft, "text draft");
    if (brief.openChecks.length > 0) {
      const list = el("ul", "checks");
      for (const check of brief.openChecks) list.append(el("li", "", check));
      box.append(el("div", "label", "Open checks"), list);
    }
  } else {
    const isOpen = card.verb === null && !picked;
    add(
      "State",
      card.health ||
        (isOpen
          ? "The desk writes the brief for this card now. The options come in about a minute."
          : "No brief."),
    );
    add("Status", card.status);
    if (isOpen) askBrief(card.id);
  }
  const links = el("div", "links");
  for (const link of card.links || []) {
    if (!/^https:\/\//.test(link.href)) continue;
    const a = el("a", "", link.label);
    a.href = link.href;
    a.rel = "noopener";
    a.target = "_blank";
    links.append(a);
  }
  if (links.children.length > 0) box.append(links);
  scrollBox.append(box);

  const failed = failedPick(card);
  if (failed) {
    const note = el("section", "note");
    note.append(
      el("div", "label", `Not done · ${failed.option}`),
      el(
        "div",
        "text",
        String(failed.result || "The desk gave no reason.").replace(
          /^not done:\s*/i,
          "",
        ),
      ),
    );
    scrollBox.append(note);
  }
  if (picked && !failed) scrollBox.append(el("p", "said", picked));
  else if (card.verb !== null)
    scrollBox.append(el("p", "said", `Closed today: ${card.verb}.`));
  else if (brief)
    brief.options.forEach((option, index) => {
      const key = `${card.id}|${option.label}`;
      const isTwoTaps = READ_FIRST.test(option.label);
      const button = el(
        "button",
        index === 0 ? "option lead" : "option",
        armed === key ? `Tap again: ${option.label}` : option.label,
      );
      button.type = "button";
      if (armed === key) button.classList.add("armed");
      button.addEventListener("click", () => {
        // An option that sends to a customer takes two taps and goes at once. Every other pick takes one tap and
        // waits for Undo.
        if (isTwoTaps && armed !== key) {
          armed = key;
          clearTimeout(armTimer);
          armTimer = setTimeout(() => {
            armed = "";
            draw(true);
          }, ARM_MS);
          haptic();
          draw(true);
          return;
        }
        armed = "";
        clearTimeout(armTimer);
        pick(card, option);
        if (isTwoTaps) void flushPick();
      });
      scrollBox.append(button);
    });

  const messages = drawThread(card.id, 12);
  if (messages.length > 0)
    scrollBox.append(el("div", "label", "Claude"), ...messages);
}

// ---------------------------------------------------------------- the page

const keyButton = (text, run, isOff = false) => {
  const button = el("button", "", text);
  button.type = "button";
  button.disabled = isOff;
  button.addEventListener("click", run);
  return button;
};

function drawDock(card) {
  const keys = $("keys");
  keys.replaceChildren();
  if (page.kind === "list") {
    keys.append(
      keyButton("Search", () => {
        $("searchbar").hidden = false;
        searchBox.focus();
      }),
      keyButton(
        state.isGathering ? "Gathering" : "Gather",
        () => {
          haptic();
          void act({ action: "gather" });
        },
        state.isGathering,
      ),
      el("span", "grow"),
      keyButton("Claude", () => open("chat")),
    );
  } else {
    keys.append(
      keyButton("‹ Back", goList),
      el("span", "grow", card ? "" : "The conversation with Claude"),
    );
  }
  $("searchbar").hidden =
    page.kind !== "list" ||
    (search === "" && document.activeElement !== searchBox);

  // The list shows the newest line of the conversation; a tap opens all of it.
  const strip = $("strip");
  strip.replaceChildren();
  const last = (state.chat || []).filter((row) => row.from !== "you").at(-1);
  const since = worksSince("");
  strip.hidden = page.kind !== "list" || (!last && !since);
  if (!strip.hidden) {
    if (since) {
      const time = el("span", "", took(since));
      time.dataset.since = since;
      const who = el("span", "who", "Claude works · ");
      who.append(time);
      strip.append(who);
    } else {
      strip.append(
        el(
          "span",
          "who",
          `${last.from === "claude" ? "Claude" : "Desk"} · ${clock(last.at)}${last.no > 0 ? ` · D-${last.no}` : ""}`,
        ),
        el("span", "body", plain(last.text)),
      );
    }
  }
  textBox.placeholder = card
    ? `Write to Claude about ${numberOf(card) || card.ref}`
    : "Write to Claude";
}

// The conversation shows its end after the person's own message and when a new answer comes while the end is in view.
let toEnd = false;

function draw(isForced = false) {
  if (state === null) {
    $("meta").textContent = loadError;
    $("meta").className = loadError ? "bad" : "";
    return;
  }
  let card = null;
  if (page.kind === "card") {
    card = state.cards.find((one) => one.id === page.id) || null;
    if (card === null) {
      history.replaceState(null, "", location.pathname);
      page = route();
    }
  }
  // A poll that changed nothing does not rebuild the page: a button keeps its armed state, a text its selection.
  const key = `${tag}|${page.kind}|${page.id}|${search}|${loadError}`;
  if (!isForced && key === drawn) return;
  drawn = key;

  const openCount = state.cards.filter((one) => one.verb === null).length;
  $("title").textContent =
    page.kind === "list"
      ? `Desk · ${openCount}`
      : page.kind === "chat"
        ? "Claude"
        : `${numberOf(card)} ${card.ref}`.trim();
  const parts = card
    ? [card.lane, card.impact].filter(Boolean)
    : [
        `gathered ${ago(state.generatedAt)}`,
        `${state.closedToday} closed today`,
      ];
  // A card page names the card; the counts of the desk belong to the list.
  if (!card) {
    if (state.parked) parts.push(`${state.parked} parked`);
    if (state.plain) parts.push(`${state.plain.waitingOnUs} wait on us`);
    if (state.scope) parts.push(state.scope);
  }
  $("meta").textContent = loadError || parts.join(" · ");
  $("meta").className = loadError ? "bad" : "";

  const pageKey = `${page.kind}|${page.id}`;
  const isSamePage = pageKey === drawnPage;
  const wasAt = scrollBox.scrollTop;
  const wasAtEnd =
    scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight < 60;
  drawnPage = pageKey;
  scrollBox.replaceChildren();
  scrollBox.className = page.kind;
  if (page.kind === "card") drawCard(card);
  else if (page.kind === "chat") {
    const messages = drawThread("", 30);
    scrollBox.append(
      ...(messages.length > 0
        ? messages
        : [
            el(
              "p",
              "empty",
              "Write to Claude in the field below. It reads the desk and answers here.",
            ),
          ]),
    );
  } else drawList();
  drawDock(card);

  if (page.kind === "list") scrollBox.scrollTop = isSamePage ? wasAt : 0;
  else if (toEnd || (page.kind === "chat" && (!isSamePage || wasAtEnd)))
    scrollBox.scrollTop = scrollBox.scrollHeight;
  else scrollBox.scrollTop = isSamePage ? wasAt : 0;
  toEnd = false;
}

// ---------------------------------------------------------------- swipe on a row: right takes the first option, left asks Claude

const swipe = {
  el: null,
  wrap: null,
  x0: 0,
  y0: 0,
  dx: 0,
  locked: "",
  isActive: false,
};

scrollBox.addEventListener(
  "touchstart",
  (event) => {
    const row = event.target.closest(".row");
    if (page.kind !== "list" || !row || event.touches.length !== 1) return;
    Object.assign(swipe, {
      el: row,
      wrap: row.parentElement,
      x0: event.touches[0].clientX,
      y0: event.touches[0].clientY,
      dx: 0,
      locked: "",
      isActive: false,
    });
  },
  { passive: true },
);
scrollBox.addEventListener(
  "touchmove",
  (event) => {
    if (!swipe.el) return;
    const dx = event.touches[0].clientX - swipe.x0;
    const dy = event.touches[0].clientY - swipe.y0;
    if (swipe.locked === "") {
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      swipe.locked = Math.abs(dx) > Math.abs(dy) * 1.4 ? "x" : "y";
      swipe.isActive = swipe.locked === "x";
      swipe.el.classList.remove("back");
    }
    if (swipe.locked !== "x") return;
    event.preventDefault();
    swipe.dx = dx;
    swipe.el.style.transform = `translateX(${dx}px)`;
    const under = swipe.wrap.querySelector(".swipe");
    under.classList.toggle("right", dx > 0);
    under.classList.toggle("left", dx < 0);
    under.style.opacity = Math.min(1, Math.abs(dx) / 90);
  },
  { passive: false },
);
function swipeEnd() {
  const { el: row, wrap, dx, locked } = swipe;
  swipe.el = null;
  if (!row || locked !== "x") {
    swipe.isActive = false;
    return;
  }
  // The row goes back to its place; the tap that ends the touch must not open the card.
  row.classList.add("back");
  row.style.transform = "";
  wrap.querySelector(".swipe").style.opacity = 0;
  setTimeout(() => (swipe.isActive = false), 250);
  if (Math.abs(dx) <= Math.min(120, wrap.offsetWidth * 0.32)) return;
  const card = state.cards.find((one) => one.id === wrap.dataset.id);
  if (!card) return;
  const option = dx > 0 ? swipeOption(card) : null;
  if (option) {
    setTimeout(() => pick(card, option), 200);
    return;
  }
  haptic();
  open(card.id);
  if (dx < 0) textBox.focus();
}
scrollBox.addEventListener("touchend", swipeEnd);
scrollBox.addEventListener("touchcancel", swipeEnd);

scrollBox.addEventListener("click", (event) => {
  const wrap = event.target.closest(".rowwrap");
  if (!wrap || swipe.isActive) return;
  open(wrap.dataset.id);
});

// ---------------------------------------------------------------- the keyboard

// The keyboard of the phone covers the bottom of the page: the page takes the height that is left, so the message
// field sits on top of the keyboard.
function fitKeyboard() {
  const view = window.visualViewport;
  if (!view) return;
  // The page takes the height the window reports: as a home-screen app, the CSS units name a height that is not
  // the one on the screen.
  const full = window.innerHeight;
  const isUp = full - view.height > 120;
  document.documentElement.style.setProperty(
    "--vh",
    `${isUp ? view.height : full}px`,
  );
  document.documentElement.classList.toggle("kb", isUp);
  if (isUp) {
    window.scrollTo(0, 0);
    if (page.kind !== "list") scrollBox.scrollTop = scrollBox.scrollHeight;
  }
}
window.addEventListener("resize", fitKeyboard);
window.addEventListener("pageshow", fitKeyboard);
fitKeyboard();
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", fitKeyboard);
  window.visualViewport.addEventListener("scroll", fitKeyboard);
}

// ---------------------------------------------------------------- events

function grow() {
  textBox.style.height = "auto";
  textBox.style.height = `${textBox.scrollHeight}px`;
}
textBox.addEventListener("input", () => {
  grow();
  setSendable();
});
// A picture from the clipboard, pasted into the field, goes with the message like a picked one.
textBox.addEventListener("paste", (event) => {
  const file = [...(event.clipboardData?.files || [])].find((one) =>
    one.type.startsWith("image/"),
  );
  if (file) {
    event.preventDefault();
    void attach(file);
  }
});
// A tap on Send keeps the focus in the field, as in a messenger: the keyboard stays up and the page does not jump.
sendButton.addEventListener("pointerdown", (event) => event.preventDefault());
$("say").addEventListener("submit", (event) => {
  event.preventDefault();
  if (state !== null) sendMessage();
});
$("pic").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (event) => {
  void attach(event.target.files[0]);
  event.target.value = "";
});
micButton.hidden = !Recognition;
micButton.addEventListener("click", dictate);
$("strip").addEventListener("click", () => open("chat"));

searchBox.addEventListener("input", () => {
  search = searchBox.value.trim();
  draw(true);
});
searchBox.addEventListener("blur", () => {
  if (search === "") $("searchbar").hidden = true;
});
$("search-end").addEventListener("click", () => {
  searchBox.value = "";
  search = "";
  searchBox.blur();
  draw(true);
});

window.addEventListener("popstate", () => {
  page = route();
  armed = "";
  draw(true);
});
window.addEventListener("hashchange", () => {
  page = route();
  draw(true);
});
// A pick that still waits for Undo goes to the desk when the page goes away.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) void flushPick(true);
  else void load();
});
window.addEventListener("pagehide", () => void flushPick(true));

// While a message waits for Claude, the page asks more often, so the answer shows soon after it is there.
let lastPoll = 0;
setInterval(() => {
  const isWaiting =
    state !== null && (outbox.length > 0 || worksSince("") !== "");
  const every = isWaiting ? WAIT_POLL_MS : POLL_MS;
  if (document.hidden || Date.now() - lastPoll < every - 50) return;
  lastPoll = Date.now();
  void load();
}, 500);
// "Claude works 12s" and the times in the header move on without new state.
setInterval(() => {
  for (const node of document.querySelectorAll("[data-since]"))
    node.textContent = took(node.dataset.since);
}, 1000);
void load();
