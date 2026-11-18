export type DeskCard = {
  id: string
  project: string
  status: string
  note: string
  act: string
  url: string
  // Every link of the item, source first; url is the first one
  links: string[]
  mins: number
  impact: string
  age: number
  // The session this item is, for a session without a card of its own.
  session: string
  // Sessions linked to this card, newest first, and the most urgent state among them.
  sessions: DeskLinked[]
  sessionState: string
  // running or todo; also tells an open item from a done one
  turn: string
  // Where the item came from: Todo, Session, PR, Message or Meeting
  kind: string
  // PRs attached to the item
  prs: DeskPr[]
  notes: DeskNote[]
  // Filed meeting transcripts, absolute paths
  meetings: string[]
  // task:<id> of the parent item, or ''
  parent: string
  children: DeskChild[]
  // The linked contact as one line (name · company · kind · stage), or ''
  contact: string
  parked?: string
}

// One desk per profile (~/desk/profiles.json); data is the profile's data folder.
// tabs: always shown, in order; order: sorts the other projects once they have items.
// contacts: the contacts folder, '' when the profile has none.
export type DeskProfile = { name: string; label: string; root: string; data: string; all: string[]; labels: Record<string, string>; tabs: string[]; order: string[]; contacts: string }

// A contact of the profile (contact.py): a record. Its next steps are items, linked by the contact.
export type DeskContact = {
  id: string
  slug: string
  name: string
  // customer, investor, advisor, partner or network
  kind: string
  company: string
  role: string
  email: string
  phone: string
  // lead, contacted, talking, won or lost
  stage: string
  source: string
  since: string
  path: string
  // The free text below the header of the contact file
  notes: string
  mtime: number
  open: { id: string; title: string }[]
  done: { id: string; title: string }[]
}

export type DeskChild = { id: string; title: string; isDone: boolean }

export type DeskPr = {
  num: string
  title: string
  url: string
  // wait, fix, merge or merged
  state: string
}

export type DeskLinked = {
  id: string
  title: string
  state: string
  mtime: number
  // The first characters of Claude's last message
  last: string
}

export type DeskNote = { date: string; text: string }

export type DeskReminder = { at: number; text: string }

export type DeskMessage = { role: string; text: string }

// v: the preview format; a pane with a newer format reads the session again.
export type DeskPreview = { id: string; mtime: number; v?: number; messages: DeskMessage[] }

export type DeskDone = {
  id: string
  title: string
  project: string
  verb: string
  date: string
  note: string
  session: string
  url: string
  links: string[]
  kind: string
  notes: DeskNote[]
  sessions: DeskLinked[]
}

export type DeskView = {
  at: string
  cards: DeskCard[]
  done: DeskDone[]
  parked: number
  closedToday: number
}

export type DeskOption = { label: string; verb: string }

export type DeskBrief = {
  cardId: string
  fact: string
  context: string
  options: DeskOption[]
}

declare module 'claude-code' {
  interface PluginState {
    desk: {
      view: DeskView
      profile: DeskProfile
      contactsOn: boolean
      contactList: DeskContact[]
      contactPicked: string
      contactKind: string
      brief: DeskBrief | null
      picked: string
      sendPass: boolean
      gathering: boolean
      query: string
      isPreview: boolean
      linking: string
      undo: string
      preview: DeskPreview
      page: number
      filter: string
      isHidden: boolean
      editing: string
      clipShot: number
      sentShot: number
      snippets: { name: string; text: string }[]
      snippetQuery: string
      snippetFromWrite: boolean
      snippetAt: number
      writeDraft: string
      writeTyped: string
      // Sessions where Claude works now (live.py) and the spinner frame
      liveIds: string[]
      spin: number
      // The last write of each linked session file (live.py)
      fileTimes: Record<string, number>
      contextUse: Record<string, { tokens: number; window: number }>
      // When Maxim last opened each item with Enter
      seenAt: Record<string, number>
      // Items Maxim marked yellow with y
      marked: string[]
      // z reminders by item id, and the count of due ones
      reminders: Record<string, DeskReminder>
      dueCount: number
      // k/j sort times by item id
      bumps: Record<string, { at: number; touch: number }>
      // The queue: item id to the time it came in; and whether the first check after a load ran
      queued: Record<string, number>
      queueChecked: boolean
      // Sessions the pane just started: item id to the session and the start time, until cards.json links them
      started: Record<string, { session: DeskLinked; at: number }>
    }
  }
}
