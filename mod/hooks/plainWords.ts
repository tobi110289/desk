// Plain words on the pane (Tobias, 2026-10-14): the brief is read by someone who has not opened the file, often at the
// end of a long day. Code words go into the file, the close note or the PR, never into `fact` or `draft`.
// The checks lean towards rejecting: a false reject costs one rewrite, a missed one costs the reader.

export const FACT_MAX = 140;
export const ITEMS_MAX = 3;
export const ITEM_MAX = 220;

// A link is how the pane and a customer reply point at things; its slugs and ids are not words the reader has to read.
const URL = /\bhttps?:\/\/\S+/g;

type Rule = { what: string; pattern: RegExp; keep?: (hit: string) => boolean };

const RULES: Rule[] = [
  { what: "backtick", pattern: /`[^`\n]*`?/g },
  // a path with a slash whose last part has a file extension: mod/hooks/register.tsx, ~/orbit-scratch/desk/cards.json
  {
    what: "file path",
    pattern: /(?:[\w.~-]+\/)+[\w-]+\.[a-z][a-z0-9]{0,4}\b/gi,
  },
  // a bare file name with a code or data extension: SKILL.md, desk.py
  {
    what: "file name",
    pattern:
      /\b[\w-]+\.(?:tsx?|jsx?|mjs|py|json|jsonl|md|ya?ml|sql|sh|toml)\b/gi,
  },
  // a slug path such as a ticket folder: acme/01, hub-booking/03-resume
  {
    what: "slug path",
    pattern: /\b[a-z][a-z0-9-]*\/[a-z0-9][a-z0-9-]*\b/g,
    keep: (hit) => /[\d-]/.test(hit),
  },
  {
    what: "UUID",
    pattern:
      /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi,
  },
  // 8+ hex characters with at least one digit: commit hashes, entity ids, 12345678
  {
    what: "id or hash",
    pattern: /\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b/gi,
  },
  // camelCase of 3+ parts: deferredIntentRef, handleSelectionChange
  {
    what: "code name",
    pattern: /\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*){2,}\b/g,
  },
  // PascalCase of 3+ parts: TourDetailsPage (GitHub and PostHog stay)
  { what: "code name", pattern: /\b(?:[A-Z][a-z0-9]+){3,}\b/g },
  // snake_case: agent_readiness, th_01J…
  { what: "code name", pattern: /\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b/g },
  // an ALL_CAPS token of 6+ letters: CACHE_MISS, FIX_DEPLOYED, BLOCKED
  {
    what: "enum value",
    pattern: /\b[A-Z][A-Z0-9_]*[A-Z0-9]\b/g,
    keep: (hit) => hit.replace(/[^A-Z]/g, "").length >= 6,
  },
  // kebab-case of 3+ parts with a letter in it: ready-for-agent, waiting-for-customer (2026-10-14 stays)
  {
    what: "status or slug",
    pattern: /\b[a-z0-9]+(?:-[a-z0-9]+){2,}\b/g,
    keep: (hit) => /[a-z]/.test(hit),
  },
  { what: "status value", pattern: /\b(?:needs-triage|needs-info)\b/g },
  // a call: handleSelectionChange(), refresh()
  { what: "function call", pattern: /\b[A-Za-z_]\w*\(\)/g },
];

/** The code words in a text, each once, in the order they appear. */
const codeWords = (value: string): { what: string; hit: string }[] => {
  const prose = value.replace(URL, " ");
  const found: { what: string; hit: string; at: number }[] = [];
  for (const rule of RULES) {
    for (const match of prose.matchAll(rule.pattern)) {
      const hit = match[0];
      if (rule.keep && !rule.keep(hit)) {
        continue;
      }
      if (!found.some((one) => one.hit === hit)) {
        found.push({ what: rule.what, hit, at: match.index ?? 0 });
      }
    }
  }

  return found.sort((a, b) => a.at - b.at);
};

/** The numbered items of a draft: each line that starts with `1.`, `2)` and so on, with the lines under it. */
const itemsOf = (draft: string): string[] => {
  const items: string[] = [];
  for (const line of draft.split("\n")) {
    if (/^\s*\d+[.)]\s/.test(line)) {
      items.push(line.trim());
    } else if (items.length > 0 && line.trim() !== "") {
      items[items.length - 1] += ` ${line.trim()}`;
    }
  }

  return items;
};

/**
 * What keeps a brief off the pane, one line per problem; empty when the brief may go on.
 * `isReply` marks customer text: it keeps the ban but not the cap on numbered items.
 */
export function plainWordsProblems(
  fact: string,
  draft: string,
  isReply = false,
): string[] {
  const problems: string[] = [];
  if (fact.length > FACT_MAX) {
    problems.push(
      `fact is ${fact.length} characters; keep it to one sentence under ${FACT_MAX}`,
    );
  }
  for (const [field, value] of [
    ["fact", fact],
    ["draft", draft],
  ] as const) {
    const words = codeWords(value);
    if (words.length > 0) {
      problems.push(
        `${field} has code words: ${words.map((one) => `"${one.hit}" (${one.what})`).join(", ")}`,
      );
    }
  }
  // A customer reply keeps its own shape: no item cap, no item length.
  const items = isReply ? [] : itemsOf(draft);
  if (items.length > ITEMS_MAX) {
    problems.push(
      `draft has ${items.length} numbered items; keep it to ${ITEMS_MAX} (group the rest, or split the card)`,
    );
  }
  items.forEach((item, i) => {
    if (item.length > ITEM_MAX) {
      problems.push(
        `draft item ${i + 1} is ${item.length} characters; keep each under ${ITEM_MAX}`,
      );
    }
  });

  return problems;
}
