// What a press does, in a few words, beside each option (2026-11-10: "not clear what the difference is between Start it
// now and Hand it to agents, or between Defer and Drop"). A label names the move; the hint says who does the work,
// where, and what happens to the card. One table for the prepared options and for the ones desk_card puts up.

const HINTS: [RegExp, string][] = [
  [/^Send it$|^Reply with a status$/, "goes to the customer as Luna, now"],
  [/^Send as staged$/, "the line goes to the worker's terminal, now"],
  [/^Edit then send$/, "you change the text first, then it goes out"],
  [/^I sent it already$/, "nothing goes out; records your own reply"],
  [/^Nobody to tell/, "nothing goes out; the thread is marked done"],
  [/^Close the loop/, "nothing goes out; a note says nothing is owed"],
  [
    /^Validate and draft/,
    "background check of the triage brief; returns with a draft",
  ],
  [/^Claim \+ validate now$/, "Claude checks the triage brief here, with you"],
  [
    /^Check the examples/,
    "Claude looks at what the customer named, here, then drafts",
  ],
  [
    /^Draft the reply$/,
    "Claude writes and checks a reply here; you send later",
  ],
  [
    /^Hand (to a worker|it to agents|to an agent|it to a worker)/,
    "a background agent does the work; the card returns with its result",
  ],
  [
    /^Hand the rework to its agent$/,
    "the review goes to the agent that built it",
  ],
  [
    /^Answer the colleague yourself$/,
    "a short answer on your clipboard; the desk sends nothing",
  ],
  [/^Start it now$|^Do it now$/, "you and Claude do it in this session, now"],
  [/^Review it now$/, "you and Claude read the change in this session"],
  [/^Go\b/, "a background agent runs it now"],
  [/^Show me the command first$/, "you see the command; nothing runs yet"],
  [/^Write the notes now$/, "Claude writes them here; you post"],
  [/^Answer( now)?$/, "you answer here; Claude sends your words"],
  [/^Reply with a note$/, "Claude sends a short note in your name"],
  [/^Close as read$/, "no answer; the message is marked read"],
  [/^Close the PR$/, "the PR is closed with a comment; nothing merges"],
  [/^Close \(PR landed\)$/, "the ticket file is closed"],
  [/^Push\b/, "asks the owner to move it"],
  [/^Ticket it in Notion$/, "becomes a board ticket for the triage"],
  [/^Ready for an agent$|^Ready for a person$/, "marks who can take it"],
  [/^Archive$|^Won't fix$/, "closed for good, with a reason"],
  [/^Drop the line$/, "the line is discarded; the worker gets nothing"],
  [/^Drop/, "leaves the desk for good; you give a reason"],
];

/** The hint for an option label; a Defer names the trigger the brief proposed. "" for a label the table lacks. */
export const hintOf = (label: string, deferTrigger = ""): string => {
  if (label.startsWith("Defer")) {
    return deferTrigger !== ""
      ? `leaves the desk and comes back on: ${deferTrigger}`
      : "leaves the desk and comes back on a trigger you name";
  }
  return HINTS.find(([match]) => match.test(label))?.[1] ?? "";
};
