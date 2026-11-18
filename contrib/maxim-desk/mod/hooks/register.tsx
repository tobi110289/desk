import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { DeskBrief, DeskCard, DeskContact, DeskProfile, DeskLinked, DeskReminder, DeskDone, DeskNote, DeskOption, DeskPreview, DeskView } from '../types'

// Maxim's desk. Scripts live in ~/desk/scripts (desk.py gathers, desk-close.py records).
// One engine, one desk per profile (~/desk/profiles.json): v switches the profile, and each profile has its own data.
// One list: open items on top in Maxim's order, done items below with ✅.
// Keys: ↑↓ select, Enter open, 1-9 item options, x done or reopen,
// k/j move up or down, r rename, o open link, t new todo, m meeting from the clipboard, e erase, l link to a parent, n note,
// p session preview, s search, v next profile, i contacts (a profile with contacts).

const PANE = 'desk'
// rows: inline (a narrow terminal, the phone) the pane asks for all the height the layout spares.
// columns: as wide as the dock allows (Claude Code caps it at 45% of the terminal).
const PANE_SIZE = { id: PANE, title: 'Desk', columns: 999, rows: 200 }
const VERBS = ['do', 'decide', 'defer', 'drop']
// A card's options answer to digits; the list moves with the arrows, so digits are free.
const OPTION_KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9']
// Rows shown at once. The window scrolls when the selection reaches its edge.
const WINDOW = 10
// The detail block shows the newest notes only, so the session box keeps its room. Sessions get all notes.
const MAX_NOTES = 5
// w with a screenshot: the field starts with this mark. Backspace into it (or a leading ~) drops the screenshot.
const SHOT_MARK = '[screenshot] '
const REFRESH_MS = 30_000
const GATHER_MS = 10 * 60_000

// Outward actions Maxim does himself. A call is denied unless he typed /desk-allow-send first.
const SEND_TOOLS =
  /^mcp__claude_ai_(Slack__slack_(send_message|schedule_message)|Microsoft_365__(teams_send_chat_message|teams_send_channel_message|teams_reply_channel_message|outlook_send_mail|outlook_send_draft|outlook_forward_mail)|Gmail__(send_message|reply|forward))$/
const SEND_COMMANDS = /\bgh\s+pr\s+merge\b/

// The kind of an item: what it is. The status icon says who acts next.
// Colored icons only: a white one vanishes in the white highlight of the selected row.
// Moon phases: two cells wide like the icons, so the spinner sits in their place. A row whose session works shows it.
const SPINNER = ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘']
const SPIN_MS = 400
const LIVE_MS = 4_000
// A tab stays yellow this long after a session of its items idles.
const IDLE_FRESH_S = 600
const TURN_ICONS: Record<string, string> = { running: '🔶', todo: '☑️' }
const TURN_ORDER: Record<string, number> = { running: 0, todo: 1 }
// Contacts (contact.py): a kind for the relation, a stage for the pipeline. The same words as contact.py.
const CONTACT_KINDS = ['customer', 'investor', 'advisor', 'partner', 'network']
const CONTACT_STAGES = ['lead', 'contacted', 'talking', 'won', 'lost']
const STAGE_ICONS: Record<string, string> = { lead: '⚪', contacted: '🔵', talking: '🟡', won: '🟢', lost: '⚫' }
const PR_STATES: Record<string, string> = { wait: 'waits on review', fix: 'needs a fix', merge: 'ready to merge', merged: 'merged', review: 'waits on your review', closed: 'closed' }

const EMPTY: DeskView = { at: '', cards: [], done: [], parked: 0, closedToday: 0 }

const view = atom({ plugin: 'desk', key: 'view' } as const, EMPTY)
// The profile on screen; data '' until desk_profile.py answered.
const NO_PROFILE: DeskProfile = { name: '', label: '', root: '', data: '', all: [], labels: {}, tabs: [], order: [], contacts: '' }
const profile = atom({ plugin: 'desk', key: 'profile' } as const, NO_PROFILE)
// i: the contacts view; its list (contacts.json), its selected contact and its kind tab ('' = all).
const contactsOn = atom({ plugin: 'desk', key: 'contactsOn' } as const, false)
const contactList = atom({ plugin: 'desk', key: 'contactList' } as const, [] as DeskContact[])
const contactPicked = atom({ plugin: 'desk', key: 'contactPicked' } as const, '')
const contactKind = atom({ plugin: 'desk', key: 'contactKind' } as const, '')
const brief = atom({ plugin: 'desk', key: 'brief' } as const, null as DeskBrief | null)
const picked = atom({ plugin: 'desk', key: 'picked' } as const, '')
// Sessions where Claude works now, from live.py, and the spinner frame.
const liveIds = atom({ plugin: 'desk', key: 'liveIds' } as const, [] as string[])
const spin = atom({ plugin: 'desk', key: 'spin' } as const, 0)
// The last write of each linked session file, from live.py. cards.json holds the time of the last gather only.
const fileTimes = atom({ plugin: 'desk', key: 'fileTimes' } as const, {} as Record<string, number>)
// The context of each linked session now, from live.py: tokens of the newest call and the model's window.
const contextUse = atom({ plugin: 'desk', key: 'contextUse' } as const, {} as Record<string, { tokens: number; window: number }>)
// When Maxim last pushed each item out of the queue. A session write after that brings it back.
const seenAt = atom({ plugin: 'desk', key: 'seenAt' } as const, {} as Record<string, number>)
// Items Maxim marked yellow with y. Saved in data/marks.json, so a restart keeps them.
const marked = atom({ plugin: 'desk', key: 'marked' } as const, [] as string[])
// z: reminders by item id. Due, the item turns yellow and shows ⏰ until Enter or y. Saved in data/marks.json too.
const reminders = atom({ plugin: 'desk', key: 'reminders' } as const, {} as Record<string, DeskReminder>)
// The count of due reminders; a change draws the pane again when a reminder comes due.
const dueCount = atom({ plugin: 'desk', key: 'dueCount' } as const, 0)
// The queue (q): yellow items. An item comes in when its session idles or when its reminder is due.
// It stays until y pushes it out. Saved in data/marks.json.
const queued = atom({ plugin: 'desk', key: 'queued' } as const, {} as Record<string, number>)
// False until the first queue check after a load: that check only fills the queue and plays no sound.
const queueChecked = atom({ plugin: 'desk', key: 'queueChecked' } as const, false)
// A session the pane just started stays on its item until cards.json links it (at most STARTED_SECS),
// and shows as running until its session file exists.
const started = atom({ plugin: 'desk', key: 'started' } as const, {} as Record<string, { session: DeskLinked; at: number }>)
const STARTED_SECS = 180
// The filter value of the Queue tab.
const QUEUE = '@queue'
// The Chat tab: talks with Claude about the whole profile, not one task. t there starts a chat at once.
const CHAT = 'CHAT'
// k/j on a top-level item: its sort time, until it gets new activity (touch = its last touch at the move).
const bumps = atom({ plugin: 'desk', key: 'bumps' } as const, {} as Record<string, { at: number; touch: number }>)
const sendPass = atom({ plugin: 'desk', key: 'sendPass' } as const, false)
const gathering = atom({ plugin: 'desk', key: 'gathering' } as const, false)
// p shows the newest session of the selected item in the body; preview holds its last messages.
const previewOn = atom({ plugin: 'desk', key: 'isPreview' } as const, false)
const preview = atom({ plugin: 'desk', key: 'preview' } as const, { id: '', mtime: 0, messages: [] } as DeskPreview)
// Bump when session-preview.py changes its output, so a cached preview is read again.
const PREVIEW_V = 3
// l on an item: the id of the item that waits for its parent; Enter on a row makes that row its parent.
const linking = atom({ plugin: 'desk', key: 'linking' } as const, '')
// The last x, e or y, so u can take it back: 'done:<id>', 'erase:<id>', 'mark:<id>', or 'unmark:<queued at>:<seen at>:<id>'.
// Empty when there is nothing to undo.
const UNDO_LABELS: Record<string, string> = { done: 'Done', erase: 'Erase', mark: 'Mark', unmark: 'Unmark' }
const lastUndo = atom({ plugin: 'desk', key: 'undo' } as const, '')
// The search text; the list shows only rows that contain it.
const query = atom({ plugin: 'desk', key: 'query' } as const, '')
// The index of the first row in the window.
const offset = atom({ plugin: 'desk', key: 'page' } as const, 0)
const filter = atom({ plugin: 'desk', key: 'filter' } as const, '')
// Hidden until /desk-pane: session workspaces load the mod too, and must not get a desk pane of their own.
const isHidden = atom({ plugin: 'desk', key: 'isHidden' } as const, true)
// '' when no field shows, 'new' for a new todo, 'rename' for a new name of the selected item, 'search' for the search text.
const editing = atom({ plugin: 'desk', key: 'editing' } as const, '')
// w: the clipboard's change count when it holds a screenshot not sent yet (0: none). sentShot: the last one sent.
const clipShot = atom({ plugin: 'desk', key: 'clipShot' } as const, 0)
const sentShot = atom({ plugin: 'desk', key: 'sentShot' } as const, 0)
// Snippets: saved texts from ~/desk/snippets.json. f (or Tab while writing) opens the search; Enter puts the top match into w.
const snippets = atom({ plugin: 'desk', key: 'snippets' } as const, [] as { name: string; text: string }[])
const snippetQuery = atom({ plugin: 'desk', key: 'snippetQuery' } as const, '')
// True when the snippet search came from w: leaving it goes back to the draft, else it closes.
const snippetFromWrite = atom({ plugin: 'desk', key: 'snippetFromWrite' } as const, false)
// Where in the draft the snippet goes: where Maxim typed ;; (-1: at the end).
const snippetAt = atom({ plugin: 'desk', key: 'snippetAt' } as const, -1)
// w: the text the field starts with (a snippet), and what Maxim typed since, so a snippet adds to the draft.
const writeDraft = atom({ plugin: 'desk', key: 'writeDraft' } as const, '')
const writeTyped = atom({ plugin: 'desk', key: 'writeTyped' } as const, '')

type Raw = Record<string, unknown>

const CARD_SCHEMA = {
  type: 'object',
  properties: {
    item_id: { type: 'string', description: 'The item id from cards.json, such as task:cut-down-costs or pr:5643.' },
    fact: { type: 'string', description: 'The one fact that decides the card. 15 words or fewer, ASD-STE100 Simplified Technical English.' },
    context: { type: 'string', description: 'At most two lines, 12 words or fewer each, ASD-STE100. No file paths or routes. Never a draft message to a human.' },
    options: {
      type: 'array',
      description: "The option set from ITEMS.md, recommended first, at most 10.",
      items: {
        type: 'object',
        properties: {
          label: { type: 'string', description: '6 words or fewer, imperative.' },
          verb: { type: 'string', enum: VERBS },
        },
        required: ['label', 'verb'],
      },
    },
  },
  required: ['item_id', 'fact', 'options'],
}

const CLOSE_SCHEMA = {
  type: 'object',
  properties: {
    item_id: { type: 'string' },
    verb: { type: 'string', enum: VERBS },
    note: { type: 'string', description: 'What happened, one line.' },
    trigger: { type: 'string', description: 'defer only: date:YYYY-MM-DD, tomorrow, a weekday, or pr-merged:<n>.' },
    act: { type: 'string', description: 'defer only: what to do when the trigger fires.' },
  },
  required: ['item_id', 'verb'],
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await Promise.all([
      $.command.register({ name: 'desk-pane', description: 'Show the desk pane again' }),
      $.command.register({ name: 'desk-hide', description: 'Hide the desk pane' }),
      $.command.register({ name: 'desk-allow-send', description: 'Allow one outward send (Slack, Teams, Outlook, Gmail, gh pr merge)' }),
      $.tool.register({
        name: 'desk_choices',
        description:
          'Show the choices for one desk item in the pane: its deciding fact and numbered options. Call it an item, never a card. ' +
          "Maxim's pick arrives as his next message, 'Re desk item <id>: <option or his own words>'. End the turn after this call.",
        inputSchema: CARD_SCHEMA,
        isDeferred: false,
      }),
      $.tool.register({
        name: 'desk_close',
        description: 'Record the verb for a desk item (runs ~/desk/scripts/desk-close.py) and rank the desk again.',
        inputSchema: CLOSE_SCHEMA,
        isDeferred: false,
      }),
    ])
    // A reload can cut off a gather; its flag would then stop every refresh.
    await update($, gathering, () => false)
    await refresh($)
    await loadMarks($)
    // While a gather runs, cards.json is older than the list on screen; the timer waits for the gather.
    // Every Claude session loads the desk; only the one that shows the pane refreshes and gathers on a timer.
    $.clock.every(REFRESH_MS, async () => {
      if (!(await read($, gathering)) && !(await read($, isHidden))) {
        await refresh($)
      }
    })
    $.clock.every(GATHER_MS, async () => {
      if (!(await read($, isHidden))) {
        await gather($)
      }
    })
    $.clock.every(LIVE_MS, () => void checkLive($))
    $.clock.every(REFRESH_MS, async () => {
      const now = Date.now() / 1000
      const due = Object.values(await read($, reminders)).filter(r => r.at <= now).length
      if (due !== (await read($, dueCount))) {
        await update($, dueCount, () => due)
      }
    })
    // The frame moves only while a session works, so an idle pane does not draw again.
    $.clock.every(SPIN_MS, async () => {
      if ((await read($, liveIds)).length > 0) {
        // A count, not a frame: the moon reads it modulo its frames, a tab underline modulo its name length.
        await update($, spin, n => (n + 1) % 5040)
      }
    })

    return next(e)
  })

  on('command.run', { command: 'desk-pane' }, async $ => {
    await update($, isHidden, () => false)
    const opened = await $.ui.open({ ...PANE_SIZE, focus: true })
    // Where the session draws, and why a surface does not place the pane (the phone app).
    const surfaces = (await $.session.surfaces()).join(', ') || 'none'
    const placed = opened.isPlaced ? 'placed' : `not placed: ${opened.reason}`
    // /desk-pane always reads cards.json at once, even when a stale gather flag is on.
    await update($, gathering, () => false)
    await refresh($)
    void gather($)

    return { text: `Desk pane shown (${placed}; surfaces: ${surfaces}).` }
  })

  on('command.run', { command: 'desk-allow-send' }, async $ => {
    await update($, sendPass, () => true)

    return { text: 'The next send or merge is allowed. The pass ends after one call.' }
  })

  // Any close by Maxim hides the pane until /desk-pane. The timer never reopens a hidden pane.
  on('ui.close', async ($, e, next) => {
    if (e.id === PANE && e.origin.kind === 'person') {
      await update($, isHidden, () => true)
    }

    return next(e)
  })

  on('command.run', { command: 'desk-hide' }, async $ => {
    await update($, isHidden, () => true)
    await $.ui.close({ id: PANE }).catch(() => undefined)

    return { text: 'Desk pane hidden. /desk-pane shows it again.' }
  })

  // Arrow keys move the focus ring. A row that takes the ring is selected.
  // The ring never leaves the list: a move onto a control below the list is one row down,
  // a move onto anything above it is one row up, and the ends of the list hold the selection.
  on('ui.focus', async ($, e, next) => {
    if (e.requestId !== PANE) {
      return next(e)
    }
    const element = e.element ?? ''
    // A text field (search, new todo, rename) shows only after its key, and only that key puts the cursor in it.
    // An arrow press while it shows closes it and gives the ring back to the list.
    if (e.origin.kind === 'person' && (await read($, editing)) !== '') {
      await update($, editing, () => '')
      await keepOrPickFirst($)
      return next({ ...e, element: 'list' })
    }
    if (element === 'list' || ((element === 'edit' || element === 'snip') && e.origin.kind === 'plugin')) {
      return next(e)
    }
    // The ring lives only on the selected row. Nothing else in the pane ever takes it:
    // choices and actions answer to their keys alone.
    if (e.origin.kind === 'person' && (await read($, contactsOn))) {
      const by = BELOW_LIST.includes(element) || CONTACT_BELOW_LIST.includes(element) ? -1 : 1
      const people = shownContacts(await read($, contactList), await read($, contactKind))
      const pickedNow = await read($, contactPicked)
      const at = people.findIndex(c => c.id === pickedNow)
      const target = people[at < 0 ? 0 : Math.max(0, Math.min(people.length - 1, at + by))]
      if (target) {
        await update($, contactPicked, () => target.id)
      }
      return next({ ...e, element: 'list' })
    }
    if (e.origin.kind === 'person') {
      // An arrow press only tells the direction. The pane is drawn bottom-up, so in the tree the six
      // actions come before the list: Up lands on one of them, Down on anything after the list.
      const by = BELOW_LIST.includes(element) ? -1 : 1
      const { rows } = await listRows($)
      const pickedId = await read($, picked)
      const from = rows.findIndex(row => row.id === pickedId)
      const target = rows[from < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, from + by))]
      if (target) {
        await update($, picked, () => target.id)
        await scrollTo($, target.id)
      }
    }

    return next({ ...e, element: 'list' })
  })

  // While a card waits for a pick, Maxim's own words answer that card.
  on('prompt.submit', async ($, e, next) => {
    const shown = await read($, brief)
    const typed = e.text.trim()
    if (!shown || e.isMeta || typed === '' || typed.startsWith('/') || typed.startsWith('Re desk item')) {
      return next(e)
    }
    await update($, brief, () => null)

    return next({ ...e, text: `Re desk item ${shown.cardId}: ${typed}` })
  })

  for (const tool of ['mcp__desk__desk_choices', 'mcp__desk__desk_close']) {
    on('tool.check', { tool }, () => ({ decision: 'allow' }))
  }
  on('tool.call', { tool: 'mcp__desk__desk_choices' }, async ($, e) => ({ result: await showCard($, e as unknown as Raw) }))
  on('tool.call', { tool: 'mcp__desk__desk_close' }, async ($, e) => ({ result: await closeCard($, e as unknown as Raw) }))

  on('tool.call', async ($, e, next) => {
    const tool = String(e.tool)
    const command = tool === 'Bash' ? String((e as unknown as Raw).command ?? '') : ''
    if (!SEND_TOOLS.test(tool) && !SEND_COMMANDS.test(command)) {
      return next(e)
    }
    if (!(await read($, sendPass))) {
      return {
        deny:
          'desk: Maxim sends, posts and merges himself. Give him the facts or the link. ' +
          'If he asked for this send in his own words, he types /desk-allow-send first.',
      }
    }
    await update($, sendPass, () => false)

    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const table = $.ui.resolve(e)
    const { Box, Button, Link, Markdown, Text } = table
    // The project column of a list row in its brand color; the rest of the row keeps the row color.
    const paintProject = (label: string, project: string, rowColor: string | undefined) => {
      const at = label.indexOf(` ${project.padEnd(8)} `)
      const brand = PROJECT_COLORS[project]
      if (at < 0 || brand === undefined) {
        return <Text color={rowColor}>{label}</Text>
      }
      const end = at + 1 + project.length

      return (
        <Text color={rowColor}>
          {label.slice(0, at + 1)}
          <Text color={brand}>{project}</Text>
          {label.slice(end)}
        </Text>
      )
    }

    // The phone surface has no text field; there the chat adds and renames.
    const Input = 'Input' in table ? table.Input : undefined
    const desk = await read($, view)
    if (desk.at === '') {
      // A resumed session starts with empty state; fill it from the files.
      void refresh($)
    }
    const only = await read($, filter)
    const search = await read($, query)
    const shown = await read($, brief)
    const pickedId = await read($, picked)
    const width = Math.max(20, e.props.bodyColumns - 4)
    const isNarrow = width < 70
    const isDock = e.props.placement === 'dock'
    const rule = '─'.repeat(width)
    const focused = e.props.isFocused
    if (!focused && (await read($, editing)) !== '') {
      void update($, editing, () => '')
    }

    const rows = visibleRows(desk, only, search, await yellowTest($), await sortTimeTest($), await read($, queued), await wakeTest($))
    const at = (await read($, editing)) === 'search' ? 0 : Math.max(0, Math.min(await read($, offset), rows.length - WINDOW))
    // While Maxim types a search, the picked item stays as the first row: the body keeps it, and its row keeps the
    // keyboard in the pane. Enter or an arrow then picks a real result.
    const editNow = await read($, editing)
    const pickedRow = [...desk.cards, ...desk.done].find(row => row.id === pickedId)
    const live = await read($, liveIds)
    const times = await read($, fileTimes)
    const isYellow = await yellowTest($)
    const isWaking = await wakeTest($)
    const inQueueNow = await read($, queued)
    const tick = await read($, spin)
    const frame = SPINNER[tick % SPINNER.length] ?? '  '
    // The picked row is always the first row while the search field is open. Its button then never leaves the screen
    // or comes back: a list button that mounts again takes the focus (autoFocus) out of the search field.
    const shownRows = editNow === 'search' && pickedRow ? [pickedRow, ...rows.filter(row => row.id !== pickedId)] : rows
    // A search never changes the selected item: it stays in the body until an arrow picks a result.
    const selected = rows.find(row => row.id === pickedId)
      ?? [...desk.cards, ...desk.done].find(row => row.id === pickedId)
      ?? rows[at]
    const card = selected && 'turn' in selected ? selected : undefined
    const closed = selected && !('turn' in selected) ? selected : undefined
    const answer = card && shown?.cardId === card.id ? shown : null
    const edit = await read($, editing)
    const shot = await read($, clipShot)
    const openCount = rows.filter(row => 'turn' in row).length
    let editValue = ''
    if (edit === 'rename' && selected) {
      editValue = card ? card.status : (closed?.title ?? '')
    }
    if (edit === 'search') {
      editValue = search
    }
    if (edit === 'write') {
      editValue = await read($, writeDraft)
    }
    const snipQuery = await read($, snippetQuery)
    const snipMatches = edit === 'snippet' ? matchSnippets(await read($, snippets), snipQuery) : []
    // A profile saved by an older pane can lack fields; the defaults fill them.
    const shownProfile = { ...NO_PROFILE, ...(await read($, profile)) }
    // v: every profile in one segmented switch. The one on screen is inverse in its own color, the others dim,
    // so the switch says where Maxim is and where v goes.
    const profileSwitch = shownProfile.all.length < 2 ? null : (
      <Text>
        <Text color="suggestion">v: </Text>
        {shownProfile.all.map((name, i) => (name === shownProfile.name
          ? <Text bold inverse color={PROFILE_COLORS[i % PROFILE_COLORS.length]}>{` ${shownProfile.labels[name] ?? name} `}</Text>
          : <Text dimColor>{` ${shownProfile.labels[name] ?? name} `}</Text>))}
      </Text>
    )
    // One fixed project order, so a name never moves: the profile's tabs always, then its order, then the rest.
    const seen = new Set([...desk.cards, ...desk.done].map(row => row.project))
    const order = [...shownProfile.tabs, ...shownProfile.order.filter(p => !shownProfile.tabs.includes(p))]
    const queueCount = desk.cards.filter(c => inQueueNow[c.id] !== undefined).length
    const projects = [
      QUEUE,
      '',
      ...order.filter(p => p !== CHAT && (shownProfile.tabs.includes(p) || seen.has(p))),
      ...[...seen].filter(p => !order.includes(p) && p !== CHAT).sort(),
      CHAT,
    ]

    // One labeled row of the detail block. `lines` fixes its height, so the rows below never move.
    const field = (label: string, value: string, lines: number, color?: string, isBold?: boolean, labelColor?: string) => (
      <Box height={lines} overflow="hidden">
        <Box width={10} flexShrink={0}>
          <Text dimColor={labelColor === undefined} color={labelColor}>{label}</Text>
        </Box>
        <Text wrap={lines === 1 ? "truncate" : "wrap"} color={color} bold={isBold}>
          {value}
        </Text>
      </Box>
    )

    // i: the contacts view. The same frame as the items: tabs, detail, bar, list, keys; drawn bottom-up the same way.
    if (await read($, contactsOn)) {
      const kindNow = await read($, contactKind)
      const everyone = await read($, contactList)
      const people = shownContacts(everyone, kindNow)
      const pickedContactId = await read($, contactPicked)
      const person = people.find(c => c.id === pickedContactId) ?? people[0]
      const from = Math.max(0, Math.min(people.findIndex(c => c.id === person?.id) - WINDOW + 2, people.length - WINDOW))
      const kinds = ['', ...CONTACT_KINDS]
      const contactTabs = (
        <Box justifyContent="space-between" flexShrink={0}>
          <Box flexWrap="wrap" flexShrink={1} columnGap={2}>
            {kinds.map((kind, i) => (
              <Text>
                <Text color="suggestion">{`${i + 1}:`}</Text>
                <Text bold={kind === kindNow} inverse={kind === kindNow} dimColor={kind !== kindNow}>
                  {`${kind === '' ? 'All' : `${kind[0]?.toUpperCase()}${kind.slice(1)}s`} (${everyone.filter(c => kind === '' || c.kind === kind).length})`}
                </Text>
              </Text>
            ))}
            <Box width={0} height={1} overflow="hidden">
              {kinds.map((kind, i) => (
                <Button key={`kind-${i}`} plain hotkey={String(i + 1)} label={kind || 'All'} onPress={() => void pickContactKind($, kind)} />
              ))}
              <Button key="contacts" plain hotkey="i" label="Items" onPress={() => void toggleContacts($)} />
              <Button key="profile" plain hotkey="v" label="Profile" onPress={() => void switchProfile($)} />
            </Box>
          </Box>
          <Box gap={2} flexShrink={0}>
            <Text>
              <Text color="suggestion">i:</Text>
              <Text dimColor>Items</Text>
            </Text>
            {profileSwitch}
          </Box>
        </Box>
      )
      const steps = person?.open ?? []
      const contactBody = (
        <Box flexDirection="column" flexGrow={1} overflow="hidden">
          {person && (
            <Box flexDirection="column" flexGrow={1} overflow="hidden">
              {field('Name', person.name || person.slug, 1, undefined, true)}
              {field('Stage', `${STAGE_ICONS[person.stage] ?? '  '} ${person.stage} · ${person.kind}`, 1)}
              {field('Company', [person.company, person.role].filter(Boolean).join(' · ') || '–', 1)}
              {field('Email', person.email || '–', 1)}
              {field('Phone', person.phone || '–', 1)}
              {field('Source', [person.source, person.since].filter(Boolean).join(' · ') || '–', 1)}
              <Text> </Text>
              <Box flexShrink={0}>
                <Box width={10} flexShrink={0}>
                  <Text dimColor>Next</Text>
                </Box>
                <Box flexDirection="column" flexShrink={1}>
                  {steps.length === 0 && <Text color={CONTACT_STAGES.indexOf(person.stage) === 1 || CONTACT_STAGES.indexOf(person.stage) === 2 ? 'yellow' : undefined}>–</Text>}
                  {steps.map(step => <Text wrap="truncate">{`☑️ ${step.title}`}</Text>)}
                </Box>
              </Box>
              {field('Done', person.done.length === 0 ? '–' : `${person.done.length}: ${person.done.map(d => d.title).join(', ')}`, 1)}
              <Text> </Text>
              <Box flexGrow={1} overflow="hidden">
                <Box width={10} flexShrink={0}>
                  <Text dimColor>Notes</Text>
                </Box>
                <Box flexDirection="column" flexGrow={1} backgroundColor={INSET} paddingX={1} overflow="hidden">
                  {person.notes === '' ? <Text dimColor>No notes yet. ⏎ opens the file.</Text> : <Markdown text={person.notes} />}
                </Box>
              </Box>
            </Box>
          )}
        </Box>
      )
      const contactBar = (
        <Box borderStyle="round" borderColor="gray" paddingX={1} justifyContent="space-between" flexShrink={0}>
          <Box flexGrow={1} flexShrink={1}>
            {edit.startsWith('contact-') && Input && (
              <Input
                key="edit"
                label={EDIT_LABELS[edit] ?? 'Text'}
                placeholder={edit === 'contact-new' ? 'Anna Weber, Acme Logistics, customer' : ''}
                value=""
                submitLabel=""
                onSubmit={(value: string) => void saveEdit($, value, edit, undefined)}
              />
            )}
            {(!edit.startsWith('contact-') || !Input) && (
              <Box columnGap={2}>
                <Button key="contact-new" plain hotkey="a" label="Add contact" onPress={() => void startEdit($, 'contact-new')} />
                <Button key="contact-step" plain hotkey="t" label="Next step" dimColor={!person} onPress={() => (person ? void startEdit($, 'contact-step') : undefined)} />
                <Button key="contact-stage" plain hotkey="g" label="Stage" dimColor={!person} onPress={() => (person ? void startEdit($, 'contact-stage') : undefined)} />
              </Box>
            )}
          </Box>
          <Box flexShrink={0} marginLeft={2}>
            <Text dimColor>{`${people.length} contacts · ${people.filter(c => c.stage === 'contacted' || c.stage === 'talking').length} active`}</Text>
          </Box>
        </Box>
      )
      const contactRows = (
        <Box flexDirection="column" flexShrink={0}>
          {people.length === 0 && (
            <Button key="list" plain autoFocus label="No contacts yet. a adds one." onPress={() => undefined} />
          )}
          {people.slice(from, from + WINDOW).map(one => {
            // A contact in contacted or talking without an open next step is yellow: it needs one.
            const needsStep = (one.stage === 'contacted' || one.stage === 'talking') && one.open.length === 0
            const label = `${STAGE_ICONS[one.stage] ?? '  '} ${one.stage.padEnd(9)} ${one.kind.padEnd(8)} ${one.name || one.slug}${one.company ? ` · ${one.company}` : ''}`
            if (one.id === person?.id) {
              return <Button key="list" plain autoFocus label={label} onPress={() => void openMeetings($, [one.path])} />
            }
            return <Text wrap="truncate" color={needsStep ? 'yellow' : undefined} dimColor={one.stage === 'lost'}>{label}</Text>
          })}
        </Box>
      )
      const contactKeys = (
        <Box columnGap={2} flexShrink={0}>
          {/* A control before the list in the tree, so Up lands on it and the arrow handler reads the direction. */}
          <Button key="contact-open" plain label="⏎: Open file" onPress={() => (person ? void openMeetings($, [person.path]) : undefined)} />
        </Box>
      )

      return (
        <Box
          flexDirection="column-reverse"
          paddingX={1}
          borderStyle="round"
          borderColor={focused ? 'red' : 'gray'}
          borderDimColor={!focused}
          height={isDock ? Math.max(20, e.props.scroll.bodyRows) : e.props.scroll.bodyRows}
        >
          {contactKeys}
          <Text dimColor>{rule}</Text>
          {contactRows}
          {contactBar}
          {contactBody}
          <Text dimColor>{rule}</Text>
          {contactTabs}
        </Box>
      )
    }

    // The detail block: the same rows in the same place for every item, open or done.
    const isPreview = await read($, previewOn)
    const undo = await read($, lastUndo)
    const sessions = card?.sessions ?? closed?.sessions ?? []
    const newest = sessions[0]
    const notes = card?.notes ?? closed?.notes ?? []
    const title = card?.status ?? closed?.title ?? ''
    const reminds = await read($, reminders)
    const isDue = (id: string) => (reminds[id]?.at ?? Infinity) <= Date.now() / 1000
    // The right column of a row: ⏰ for a due reminder, else the last touch.
    const rowTime = (c: DeskCard) => (isDue(c.id) ? '⏰' : shortAgo(touchedAt(c, times)))
    let statusIcon = ''
    let statusRest = ''
    if (card) {
      statusIcon = card.sessions.some(x => live.includes(x.id)) ? frame : isWaking(card) ? '⏰' : TURN_ICONS[card.turn] ?? ''
      const remind = reminds[card.id]
      const pending = remind && !isDue(card.id) ? ` · ⏰ ${whenLabel(remind.at)}: ${remind.text}` : ''
      statusRest = ` · ${days(card.age).trim()} · ${rowTime(card)}${pending}`
    }
    if (closed) {
      statusIcon = closed.verb === 'drop' ? '❌' : '✅'
      statusRest = ` · ${daysSince(closed.date).trim()} · ${closed.date}`
    }
    const statusProject = card?.project ?? closed?.project ?? ''
    const statusColor = card && isYellow(card) ? 'yellow' : undefined
    // The newest session's share of its context window, in brackets after the session title.
    const ctx = newest ? (await read($, contextUse))[newest.id] : undefined
    const contextNote = ctx ? ` (context ${Math.round((ctx.tokens / ctx.window) * 100)}%)` : ''
    const sessionLine = newest
      ? `${newest.title}${contextNote} · ${ago(times[newest.id] ?? newest.mtime)}${sessions.length > 1 ? `  +${sessions.length - 1} more` : ''}`
      : '–'
    // PRs by number and state only: the title is often the item's own title.
    const prLines = (card?.prs ?? []).length === 0 ? '–' : (card?.prs ?? []).map(pr => `#${pr.num} ${PR_STATES[pr.state] ?? pr.state}`).join(' · ')
    // The rows left for the preview: the pane height less the list rows on screen, the bars and the title rows.
    // A short list (the queue) takes only its own rows, so the session gets the rest.
    const listRowCount = Math.max(1, Math.min(WINDOW, shownRows.length - at))
    const previewRows = Math.max(5, e.props.scroll.bodyRows - listRowCount - 12)
    const loaded = await read($, preview)
    const shownPreview = newest && loaded.id === newest.id && loaded.v === PREVIEW_V ? loaded.messages : undefined
    // The preview needs a session; an item without one shows its details.
    const showPreview = isPreview && newest !== undefined
    // p: the session over the whole body, and back to the details.
    const previewKey = <Button key="preview" plain hotkey="p" label={showPreview ? 'Details' : 'Expand'} onPress={() => void update($, previewOn, shown => !shown)} />
    // The preview and the Session box both read the session file. They read it again when live.py sees a newer write.
    const newestTime = newest ? (times[newest.id] ?? newest.mtime) : 0
    if (newest && (shownPreview === undefined || loaded.mtime < newestTime)) {
      void loadPreview($, newest.id, newestTime)
    }
    // Claude's last message from the session file; cards.json can be up to one gather old.
    const lastClaude = shownPreview?.filter(m => m.role === 'claude').at(-1)?.text ?? newest?.last ?? ''
    // The session renderer of p and of the Session box: the newest messages that fit in `rows`, the last one at the bottom.
    const transcript = (rows: number, room: number) => {
      if (newest === undefined) {
        return <Text dimColor>This item has no session.</Text>
      }
      if (shownPreview === undefined) {
        return lastClaude !== '' ? markdown(lastClaude) : <Text dimColor>Loading the session…</Text>
      }

      // Claude's own Markdown renderer draws each message: tables, code fences and links as in a reply.
      // The newest messages that fill `rows` by the line estimate; the box keeps the bottom and clips the top.
      // What Claude runs now (a tool call after its last text), one dim line at the bottom while the session is live.
      const step = shownPreview.at(-1)?.role === 'activity' && live.includes(newest.id) ? shownPreview.at(-1)?.text ?? '' : ''
      const messages = shownPreview.filter(m => m.role !== 'activity')
      const room2 = step !== '' ? rows - 2 : rows
      const picked: { role: string; text: string }[] = []
      let used = 0
      for (const message of [...messages].reverse()) {
        if (used >= room2) {
          break
        }
        picked.unshift(message)
        used += previewLines([message], room).length
      }

      return (
        <Box flexDirection="column" height={rows} overflow="hidden" justifyContent="flex-end">
          {picked.map(message => {
            const who = message.role === 'you' ? 'You' : 'Claude'
            const color = who === 'You' ? 'blue' : 'magenta'

            return (
              <Box flexDirection="column" flexShrink={0}>
                <Text wrap="truncate">
                  <Text bold color={color}>{who}</Text>
                  <Text color={color}>{` ${'─'.repeat(Math.max(1, room - who.length - 1))}`}</Text>
                </Text>
                <Markdown text={message.text} />
              </Box>
            )
          })}
          {step !== '' && (
            <Box height={1} flexShrink={0} marginTop={1}>
              <Text dimColor wrap="truncate">{`${frame} ${step}`}</Text>
            </Box>
          )}
        </Box>
      )
    }

    // Parent and children: one level only. A parent cannot become a child.
    const pending = await read($, linking)
    const parentTitle = card?.parent ? (desk.cards.find(c => c.id === card.parent)?.status ?? desk.done.find(d => d.id === card.parent)?.title ?? card.parent) : '–'
    const kids = card?.children ?? []
    const childLines = kids.length === 0
      ? '–'
      : `${kids.filter(k => !k.isDone).length} open · ${kids.filter(k => k.isDone).length} done: ${kids.filter(k => !k.isDone).map(k => k.title).join(', ') || '–'}`
    const canLink = card !== undefined && kids.length === 0
    // Notes live on items in tasks.json; old rows without an item take none.
    const canNote = selected !== undefined && selected.id.startsWith('task:')
    // Delete is for items without a session; an item with a session is finished with x.
    const canDelete = canNote && sessions.length === 0
    // A small Markdown view for Claude's text: bullets, headings, **bold**, `code` and [links](url).
    const markdown = (source: string) => {
      const lines = source.split('\n').filter((line, i, all) => line.trim() !== '' || (all[i - 1] ?? '').trim() !== '')

      return lines.map(raw => {
        let line = raw.replace(/\s+$/, '')
        const heading = /^#{1,6}\s+/.test(line)
        line = line.replace(/^#{1,6}\s+/, '').replace(/^(\s*)[-*]\s+/, '$1• ')
        const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).filter(part => part !== '')

        return (
          <Text wrap="wrap" bold={heading}>
            {line === '' ? ' ' : ''}
            {parts.map(part => {
              if (part.startsWith('**') && part.endsWith('**')) {
                return <Text bold>{part.slice(2, -2)}</Text>
              }
              if (part.startsWith('`') && part.endsWith('`')) {
                return <Text color="cyan">{part.slice(1, -1)}</Text>
              }
              const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part)
              if (link) {
                return <Text color="blue">{link[1]}</Text>
              }

              return part
            })}
          </Text>
        )
      })
    }

    const canReopen = closed !== undefined && closed.verb !== 'session' && closed.verb !== 'merged'
    const doneEnabled = card !== undefined || canReopen
    const links = (card?.links ?? closed?.links ?? []).slice(0, MAX_LINKS)
    const meetings = (card?.meetings ?? []).slice(0, MAX_LINKS)
    // The Session box gets the rows the detail rows above it leave: notes, choices, links, meetings, PRs, parent,
    // children, the blank rows, the session title and the blank row under it.
    const usedRows = Math.max(1, Math.min(notes.length, MAX_NOTES + 1)) + (answer ? 1 + answer.options.length : 1) + Math.max(1, links.length) + Math.max(1, meetings.length) + 9 + (card && card.contact !== '' ? 1 : 0)
    const sessionRows = Math.max(3, previewRows - usedRows)
    // A search shows a subset, so the order cannot change while it is on.
    const canMove = card !== undefined && search === ''

    const projectRow = (
      <Box justifyContent="space-between" flexShrink={0}>
        <Box flexWrap="wrap" flexShrink={1} columnGap={2}>
          {projects.map((name, i) => {
            // A project tab shows the moon while a session of its items works, and is yellow while a session works or one of its items is fresh.
            // The All tab shows neither. "2:ACME": the digit jumps to the tab (the hidden buttons below take the keys).
            const tabCards = desk.cards.filter(c => name !== '' && c.project === name)
            const isWorking = tabCards.some(c => c.sessions.some(x => live.includes(x.id)))
            const isFresh = tabCards.some(isYellow)
            return (
              <Text>
                {/* A bar after Queue and All sets them apart from the project tabs. */}
                {(i === 2 || name === CHAT) && <Text dimColor>{'|  '}</Text>}
                {/* The theme's accent, the color the engine gives every button key. */}
                {name === QUEUE ? <Text color="suggestion">q:</Text> : name === CHAT ? <Text color="suggestion">h:</Text> : i < 10 && <Text color="suggestion">{`${i}:`}</Text>}
                <Text bold={name === only} inverse={name === only} dimColor={name !== only && !isFresh && !isWorking} color={isFresh || isWorking ? 'yellow' : undefined}>
                  {/* While a session of the tab works, an underline runs along the name: one letter on names up to
                      three letters, up to three on longer ones, and one empty step before it starts again. */}
                  {[...(name === QUEUE ? `Queue (${queueCount})` : name === '' ? 'All' : name)].map((ch, k, all) => {
                    const span = all.length <= 3 ? 1 : Math.min(3, all.length - 2)
                    const at = tick % (all.length - span + 2)
                    return <Text underline={isWorking && at <= all.length - span && k >= at && k < at + span}>{ch}</Text>
                  })}
                </Text>
              </Text>
            )
          })}
          {/* The digit keys of the tabs: real buttons in a box of no width, so the engine's "1: " label never shows. */}
          <Box width={0} height={1} overflow="hidden">
            <Button key="tab-queue" plain hotkey="q" label="Queue" onPress={() => void jumpProject($, QUEUE)} />
            <Button key="tab-chat" plain hotkey="h" label="Chat" onPress={() => void jumpProject($, CHAT)} />
            <Button key="profile" plain hotkey="v" label="Profile" onPress={() => void switchProfile($)} />
            {shownProfile.contacts !== '' && <Button key="contacts" plain hotkey="i" label="Contacts" onPress={() => void toggleContacts($)} />}
            {/* While choices show, the digits pick a choice, so the tab digits step aside. */}
            {!answer && projects.slice(1, 10).filter(name => name !== CHAT).map((name, i) => (
              <Button key={`tab-${i}`} plain hotkey={String(i + 1)} label={name === '' ? 'All' : name} onPress={() => void jumpProject($, name)} />
            ))}
          </Box>
        </Box>
        <Box gap={2} flexShrink={0}>
          {/* i and v: the hidden buttons above take the keys. */}
          {shownProfile.contacts !== '' && (
            <Text>
              <Text color="suggestion">i:</Text>
              <Text dimColor>Contacts</Text>
            </Text>
          )}
          {profileSwitch}
        </Box>
      </Box>
    )

    // The command bar: the one place where Maxim types. Idle, it lists the typing keys; each key turns it into its field.
    const searchBar = (
      <Box borderStyle="round" borderColor="gray" paddingX={1} justifyContent="space-between" flexShrink={0}>
        <Box flexGrow={1} flexShrink={1}>
          {pending !== '' && (
            <Text color="yellow" wrap="truncate">{`Pick the parent for "${desk.cards.find(c => c.id === pending)?.status ?? pending}": ↑↓ then ⏎. l cancels.`}</Text>
          )}
          {pending === '' && edit !== '' && Input && (
            <Input
              key={edit === 'snippet' ? 'snip' : 'edit'}
              label={edit === 'write' && shot !== 0 ? 'Message (screenshot: ⌫ to drop)' : edit === 'new' && only === CHAT ? 'Message to a new chat' : EDIT_LABELS[edit] ?? 'Text'}
              placeholder={edit === 'remind' ? 'tomorrow 9 check the API fields' : edit === 'write' ? ';; opens the snippets' : ''}
              value={edit === 'search' ? search : edit === 'snippet' ? snipQuery : editValue}
              submitLabel=""
              onInput={(value: string) => (edit === 'search' ? void liveSearch($, value) : edit === 'write' ? void typedInWrite($, value) : edit === 'snippet' ? void update($, snippetQuery, () => value) : undefined)}
              onSubmit={(value: string) => void saveEdit($, value, edit, selected)}
            />
          )}
          {pending === '' && (edit === '' || !Input) && search === '' && (
            <Box columnGap={2}>
              <Button key="search" plain hotkey="s" label="Search" onPress={() => void startEdit($, 'search')} />
              <Button key="new" plain hotkey="t" label={only === CHAT ? 'New chat' : 'Todo'} onPress={() => void startEdit($, 'new')} />
              <Button key="rename" plain hotkey="r" label="Rename" dimColor={!selected} onPress={() => (selected ? void startEdit($, 'rename') : undefined)} />
              <Button key="note" plain hotkey="n" label="Note" dimColor={!canNote} onPress={() => (canNote ? void startEdit($, 'note') : undefined)} />
              <Button key="remind" plain hotkey="z" label="Remind" dimColor={!card} onPress={() => (card ? void startEdit($, 'remind') : undefined)} />
              <Button key="write" plain hotkey="w" label="Write" dimColor={!card} onPress={() => (card ? void startEdit($, 'write') : undefined)} />
              <Button key="snippets" plain hotkey="f" label="Snippets" dimColor={!card} onPress={() => (card ? void openSnippets($, true) : undefined)} />
            </Box>
          )}
          {/* While a search is on, the bar shows the search first and its two keys, then the keys for the picked item.
              A key with no button here goes out of the pane and takes the focus with it. */}
          {pending === '' && (edit === '' || !Input) && search !== '' && (
            <Box columnGap={2}>
              <Text>
                <Text dimColor>Search:</Text> {search}
              </Text>
              <Box>
                <Text dimColor>(</Text>
                <Button key="search" plain hotkey="s" label="Edit" onPress={() => void startEdit($, 'search')} />
                <Text>  </Text>
                <Button key="clear" plain hotkey="c" label="Clear" onPress={() => void saveEdit($, '', 'search', selected)} />
                <Text dimColor>)</Text>
              </Box>
              <Button key="rename" plain hotkey="r" label="Rename" dimColor={!selected} onPress={() => (selected ? void startEdit($, 'rename') : undefined)} />
              <Button key="note" plain hotkey="n" label="Note" dimColor={!canNote} onPress={() => (canNote ? void startEdit($, 'note') : undefined)} />
              <Button key="remind" plain hotkey="z" label="Remind" dimColor={!card} onPress={() => (card ? void startEdit($, 'remind') : undefined)} />
              <Button key="write" plain hotkey="w" label="Write" dimColor={!card} onPress={() => (card ? void startEdit($, 'write') : undefined)} />
              <Button key="snippets" plain hotkey="f" label="Snippets" dimColor={!card} onPress={() => (card ? void openSnippets($, true) : undefined)} />
            </Box>
          )}
        </Box>
        <Box flexShrink={0} marginLeft={2}>
          <Text dimColor>{`${openCount} open · ${rows.length - openCount} done`}</Text>
        </Box>
      </Box>
    )

    // The selected item: fixed rows in a fixed order, so the eye finds each fact in the same place.
    const body = (
      <Box flexDirection="column" flexGrow={1} overflow="hidden">
        {selected && (
          <Box flexDirection="column" flexGrow={1} overflow="hidden">
            {field('Title', title, 1, undefined, true)}
            {/* The same facts as the list row, in the same colors: icon, project, age, last touch; a pending reminder after them. */}
            <Box height={1} overflow="hidden">
              <Box width={10} flexShrink={0}>
                <Text dimColor>Status</Text>
              </Box>
              <Text wrap="truncate" color={statusColor} dimColor={closed !== undefined}>
                {`${statusIcon} `}
                <Text color={PROJECT_COLORS[statusProject]}>{statusProject}</Text>
                {statusRest}
              </Text>
            </Box>
            {card && card.contact !== '' && field('Contact', card.contact, 1)}
            {showPreview ? (
              // One text block of exactly the rows that fit, so the newest line always sits at the bottom.
              <Box flexDirection="column" height={previewRows} overflow="hidden" backgroundColor={INSET} paddingX={1}>
                {/* The session title and its context share, as in the Session box; p sits at the top right. */}
                <Box justifyContent="space-between" columnGap={2} flexShrink={0}>
                  <Text wrap="truncate" bold>{sessionLine}</Text>
                  <Box flexShrink={0}>{previewKey}</Box>
                </Box>
                {transcript(previewRows - 1, width - 4)}
              </Box>
            ) : (
              <Box flexDirection="column" flexGrow={1} overflow="hidden">
                {/* One line per note, cut with an ellipsis at the edge, so long notes never push the session down. */}
                <Box flexShrink={0}>
                  <Box width={10} flexShrink={0}>
                    <Text dimColor>Notes</Text>
                  </Box>
                  <Box flexDirection="column" flexShrink={1}>
                    {notes.length === 0 && <Text>–</Text>}
                    {notes.length > MAX_NOTES && <Text dimColor>{`+${notes.length - MAX_NOTES} older`}</Text>}
                    {notes.slice(-MAX_NOTES).map(note => (
                      <Text wrap="truncate">
                        <Text dimColor>{note.date.slice(5)}</Text>
                        {` ${clip(oneLine(note.text), Math.max(10, width - 18))}`}
                      </Text>
                    ))}
                  </Box>
                </Box>
                {/* A pick that waits, with a yellow label. */}
                <Box flexShrink={0}>
                  <Box width={10} flexShrink={0}>
                    <Text color={answer ? 'yellow' : undefined} dimColor={!answer}>Choices</Text>
                  </Box>
                  {!answer && <Text>–</Text>}
                  {answer && (
                    <Box flexDirection="column">
                      <Text wrap="wrap" bold>{answer.fact}</Text>
                      {answer.options.map((option, i) => (
                        <Button key={`opt-${i}`} plain hotkey={OPTION_KEYS[i]} label={option.label} onPress={() => void choose($, answer, option)} />
                      ))}
                    </Box>
                  )}
                </Box>
                <Text> </Text>
                {/* One short line per link; o sits on the first line and opens every link. */}
                <Box height={Math.max(1, links.length)} overflow="hidden">
                  <Box width={10} flexShrink={0}>
                    <Text dimColor>{links.length > 1 ? 'Links' : 'Link'}</Text>
                  </Box>
                  {links.length === 0 && <Text>–</Text>}
                  {links.length > 0 && (
                    <Box columnGap={2}>
                      <Box flexDirection="column" flexShrink={1}>
                        {links.map(url => (
                          <Text wrap="truncate">{clip(linkLabel(url), Math.max(10, width - 24))}</Text>
                        ))}
                      </Box>
                      <Box>
                        <Text>(</Text>
                        <Button key="open" plain hotkey="o" label={links.length > 1 ? 'Open all' : 'Open'} onPress={() => void openUrls($, links)} />
                        <Text>)</Text>
                      </Box>
                    </Box>
                  )}
                </Box>
                {/* One short line per filed meeting; g sits on the first line and opens every transcript in VS Code. */}
                <Box height={Math.max(1, meetings.length)} overflow="hidden">
                  <Box width={10} flexShrink={0}>
                    <Text dimColor>Meetings</Text>
                  </Box>
                  {meetings.length === 0 && <Text>–</Text>}
                  {meetings.length > 0 && (
                    <Box columnGap={2}>
                      <Box flexDirection="column" flexShrink={1}>
                        {meetings.map(path => (
                          <Text wrap="truncate">{clip(meetingLabel(path), Math.max(10, width - 24))}</Text>
                        ))}
                      </Box>
                      <Box>
                        <Text>(</Text>
                        <Button key="meetings" plain hotkey="g" label={meetings.length > 1 ? 'Open all' : 'Open'} onPress={() => void openMeetings($, meetings)} />
                        <Text>)</Text>
                      </Box>
                    </Box>
                  )}
                </Box>
                {field('PRs', prLines, 1)}
                <Text> </Text>
                {field('Parent', parentTitle, 1)}
                {field('Children', childLines, 1)}
                <Text> </Text>
                {/* Activity last: the session and Claude's last message in a darker inset, like a small terminal. */}
                <Box flexGrow={1} overflow="hidden">
                  <Box width={10} flexShrink={0}>
                    <Text dimColor>Session</Text>
                  </Box>
                  <Box flexDirection="column" flexGrow={1} backgroundColor={INSET} paddingX={1}>
                    <Box justifyContent="space-between" columnGap={2}>
                      <Text wrap="truncate" bold>{sessionLine}</Text>
                      {newest !== undefined && <Box flexShrink={0}>{previewKey}</Box>}
                    </Box>
                    {newest !== undefined && <Text> </Text>}
                    {newest !== undefined && transcript(sessionRows, width - 16)}
                  </Box>
                </Box>
              </Box>
            )}
          </Box>
        )}
      </Box>
    )

    // Only the selected row is a control (key "list"), so the focus ring always sits on it. Done rows are dim.
    const list = (
      <Box flexDirection="column" flexShrink={0}>
        {/* An empty list still holds the keyboard, so c, s and the other keys keep working. */}
        {shownRows.length === 0 && (
          <Button key="list" plain autoFocus label={search !== '' ? 'Nothing found. c clears the search.' : only === QUEUE ? 'The queue is empty.' : 'Nothing here.'} onPress={() => undefined} />
        )}
        {shownRows.slice(at, at + WINDOW).map((one, i) => {
          const isDone = !('turn' in one)
          const label = 'turn' in one
            ? listRow({ n: at + i + 1, icon: one.sessions.some(x => live.includes(x.id)) ? frame : isWaking(one) ? '⏰' : TURN_ICONS[one.turn] ?? '  ', project: one.project, age: days(one.age), touched: rowTime(one), title: `${treeMark(one, shownRows[at + i + 1])}${one.status}`, width, isNarrow })
            : listRow({ n: at + i + 1, icon: one.verb === 'drop' ? '❌' : '✅', project: one.project, age: daysSince(one.date), touched: '', title: one.title, width, isNarrow })
          // Yellow: the item whose session idled in the last 10 minutes, the same one that turns its tab yellow.
          const rowColor = 'turn' in one && isYellow(one) ? 'yellow' : undefined
          if (one.id === selected?.id) {
            return (
              <Button key="list" plain autoFocus label={label} onPress={() => void (pending !== '' ? pickParent($, pending, one.id) : openSession($, one))}>
                {paintProject(label, one.project, rowColor)}
              </Button>
            )
          }

          return <Text wrap="truncate" dimColor={isDone}>{paintProject(label, one.project, rowColor)}</Text>
        })}
      </Box>
    )

    // Actions: always the same keys in the same place. A key that does not apply is dim and does nothing.
    const actions = (
      <Box columnGap={2} rowGap={0} flexWrap="wrap" flexShrink={0}>
        <Text>
          <Text color="blue">⏎</Text>: Open
        </Text>
        <Button
          key="done"
          plain
          hotkey="x"
          label={closed ? 'Reopen' : 'Done'}
          dimColor={!doneEnabled}
          onPress={() => {
            if (card) {
              void markDone($, card.id)
              return
            }
            if (closed && canReopen) {
              void reopen($, closed.id)
            }
          }}
        />
        <Button key="up" plain hotkey="k" label="Up" dimColor={!canMove} onPress={() => (canMove && card ? void moveItem($, card.id, 'up') : undefined)} />
        <Button key="down" plain hotkey="j" label="Down" dimColor={!canMove} onPress={() => (canMove && card ? void moveItem($, card.id, 'down') : undefined)} />
        <Button key="meeting" plain hotkey="m" label="Meeting" onPress={() => void meetingFromClipboard($, selected)} />
        {/* c copies Claude's last message as plain text, without the pane border. While a search is on, c clears it.
            The button always stays in the tree: a button that comes and goes here moves the focus out of the search field. */}
        <Button key="copy" plain hotkey={search === '' ? 'c' : undefined} label="Copy" dimColor={lastClaude === '' || search !== ''} onPress={() => (search === '' ? void copyText($, lastClaude) : undefined)} />
        <Button key="delete" plain hotkey="e" label="Erase" dimColor={!canDelete} onPress={() => (canDelete && selected ? void deleteItem($, selected.id) : undefined)} />
        <Button key="link" plain hotkey="l" label={card?.parent ? 'Unnest' : 'Nest'} dimColor={!canLink} onPress={() => (canLink && card ? void startLink($, card) : undefined)} />
        <Button key="yellow" plain hotkey="y" label={card && isYellow(card) ? 'Unmark' : 'Mark'} dimColor={!card} onPress={() => (card ? void toggleYellow($, card.id, isYellow(card)) : undefined)} />
        <Button key="undo" plain hotkey="u" label={undo === '' ? 'Undo' : `Undo ${UNDO_LABELS[undo.split(':')[0] ?? ''] ?? ''}`} dimColor={undo === ''} onPress={() => void undoLast($)} />
      </Box>
    )

    // Drawn bottom-up: the controls before the selected row in the tree (the actions) never change,
    // so choices that appear in the body can never take the highlight from the list.
    return (
      <Box
        flexDirection="column-reverse"
        paddingX={1}
        borderStyle="round"
        borderColor={focused ? 'red' : 'gray'}
        borderDimColor={!focused}
        height={isDock ? Math.max(20, e.props.scroll.bodyRows) : e.props.scroll.bodyRows}
      >
        {actions}
        <Text dimColor>{rule}</Text>
        {list}
        {searchBar}
        {edit === 'snippet' && (
          <Box flexDirection="column" flexShrink={0} paddingX={2}>
            {snipMatches.length === 0 && <Text dimColor>No snippet matches. Snippets live in ~/desk/snippets.json.</Text>}
            {snipMatches.slice(0, 8).map((one, k) => (
              <Text wrap="truncate" inverse={k === 0}>{oneLine(one.text)}</Text>
            ))}
          </Box>
        )}
        {body}
        <Text dimColor>{rule}</Text>
        {projectRow}
      </Box>
    )
  })
}

const WORD_COLORS: Record<string, string> = { code: 'cyan', link: 'blue', you: 'blue', claude: 'magenta' }

// The background of the session inset: a shade darker than the pane.
const INSET = '#1b1b1b'


// The action controls. In the tree they come right before the list, so Up moves the ring onto them.
const BELOW_LIST = ['done', 'up', 'down', 'meeting', 'delete', 'link', 'yellow', 'undo']
const EDIT_LABELS: Record<string, string> = { 'contact-new': 'New contact: name, company, kind', 'contact-step': 'Next step', 'contact-stage': 'Stage (lead, contacted, talking, won, lost) or kind', new: 'New todo', rename: 'Rename', search: 'Search', note: 'Note', remind: 'Remind (when, then why)', write: 'Message to the session', snippet: 'Snippets: type to filter, ⏎ inserts, ↑↓ leaves' }
// One list: open items in desk order, then done items, newest first. A done row that is open again shows once.
function visibleRows(desk: DeskView, only: string, search: string, isYellow: (c: DeskCard) => boolean, sortTime: (c: DeskCard) => number, inQueue: Record<string, number>, isWaking: (c: DeskCard) => boolean): (DeskCard | DeskDone)[] {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean)
  // The Queue tab: only queued items, the newest arrival first, no done rows.
  if (only === QUEUE) {
    return desk.cards
      .filter(c => inQueue[c.id] !== undefined && words.every(word => `${c.status} ${c.project} ${c.kind}`.toLowerCase().includes(word)))
      .sort((a, b) => (inQueue[b.id] ?? 0) - (inQueue[a.id] ?? 0))
  }
  const keep = (row: { project: string }, title: string, kind: string) => {
    if (only !== '' && row.project !== only) {
      return false
    }
    const hay = `${title} ${row.project} ${kind}`.toLowerCase()

    return words.every(word => hay.includes(word))
  }
  const openIds = new Set(desk.cards.map(c => c.id))
  const open = yellowFirst(desk.cards.filter(c => keep(c, c.status, c.kind)), isYellow, sortTime, isWaking)
  const done = desk.done.filter(d => !openIds.has(d.id) && keep(d, d.title, d.kind))

  return [...open, ...done]
}

// The rows of the list on screen, after the project filter and the search.
// Asks live.py which linked sessions of the last day work now. Cheap: it reads only the end of each file.
async function checkLive($: EngineInterface) {
  // Every Claude session loads the desk; only the one that shows the pane checks, so the queue and its sound happen once.
  if (await read($, isHidden)) {
    return
  }
  // A todo added from a chat (task.py) changes tasks.json; a gather then shows it at once.
  const home = (await $.env.get('HOME')) ?? ''
  const tasksTime = (await $.fs.stat(`${(await deskProfile($)).data}/tasks.json`).catch(() => undefined))?.mtimeMs ?? 0
  if (tasksTime > tasksSeen && !(await read($, gathering))) {
    void gather($)
  }
  const desk = await read($, view)
  const since = Date.now() / 1000 - 86_400
  const ids = [...new Set(desk.cards.flatMap(c => c.sessions).filter(x => x.mtime > since).map(x => x.id))]
  let live: string[] = []
  if (ids.length > 0) {
    const home = (await $.env.get('HOME')) ?? ''
    const ran = await runProc($, ['python3', `${home}/desk/scripts/live.py`, ...ids], { timeoutMs: 10_000 }).catch(() => undefined)
    if (!ran || ran.exitCode !== 0) {
      return
    }
    const out = JSON.parse(ran.stdout || '{}') as Raw
    live = (Array.isArray(out.live) ? out.live : []).map(text)
    const times: Record<string, number> = {}
    for (const [id, t] of Object.entries((out.mtimes ?? {}) as Raw)) {
      times[id] = Number(t) || 0
    }
    const use: Record<string, { tokens: number; window: number }> = {}
    for (const [id, one] of Object.entries((out.context ?? {}) as Record<string, Raw>)) {
      use[id] = { tokens: Number(one.tokens) || 0, window: Number(one.window) || 1 }
    }
    if (JSON.stringify(await read($, contextUse)) !== JSON.stringify(use)) {
      await update($, contextUse, () => use)
    }
    const beforeTimes = await read($, fileTimes)
    if (JSON.stringify(beforeTimes) !== JSON.stringify(times)) {
      await update($, fileTimes, () => times)
    }
  }
  // A session the pane just started counts as running until its file exists.
  for (const [, one] of Object.entries(pendingStarted(await read($, started)))) {
    if (!live.includes(one.session.id) && (await read($, fileTimes))[one.session.id] === undefined) {
      live = [...live, one.session.id]
    }
  }
  const before = await read($, liveIds)
  if (before.join() !== live.join()) {
    await update($, liveIds, () => live)
  }
  await syncQueue($)
}

// The started sessions still in their window.
function pendingStarted(all: Record<string, { session: DeskLinked; at: number }>): Record<string, { session: DeskLinked; at: number }> {
  const now = Date.now() / 1000
  return Object.fromEntries(Object.entries(all).filter(([, one]) => now - one.at < STARTED_SECS))
}

// Puts each started session on its item while cards.json does not link it yet.
function withStarted(next: DeskView, pending: Record<string, { session: DeskLinked; at: number }>): DeskView {
  if (Object.keys(pending).length === 0) {
    return next
  }
  return {
    ...next,
    cards: next.cards.map(c => {
      const one = pending[c.id]
      if (!one || c.sessions.some(x => x.id === one.session.id)) {
        return c
      }
      return { ...c, sessions: [one.session, ...c.sessions], sessionState: 'live', turn: 'running' }
    }),
  }
}

async function listRows($: EngineInterface): Promise<{ rows: readonly (DeskCard | DeskDone)[] }> {
  return { rows: visibleRows(await read($, view), await read($, filter), await read($, query), await yellowTest($), await sortTimeTest($), await read($, queued), await wakeTest($)) }
}

// One highlight only: a number key moves the focus ring too, and the ring decides the selected row.
async function select($: EngineInterface, id: string) {
  await update($, picked, () => id)
  await scrollTo($, id)
  await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
}

// Snippets whose name or text has every typed word, in file order.
function matchSnippets(all: { name: string; text: string }[], query: string): { name: string; text: string }[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  return all.filter(one => words.every(word => `${one.name} ${one.text}`.toLowerCase().includes(word)))
}

// f, or Tab in w: the snippet search. From w, the text typed so far stays as the draft.
async function openSnippets($: EngineInterface, fresh = false) {
  const home = (await $.env.get('HOME')) ?? ''
  const raw = await $.fs.read(`${home}/desk/snippets.json`).catch(() => '{}')
  try {
    const list = ((JSON.parse(raw) as Raw).snippets ?? []) as Raw[]
    await update($, snippets, () => list.map(one => ({ name: text(one.name), text: text(one.text) })).filter(one => one.text !== ''))
  } catch {
    $.ui.toast('~/desk/snippets.json is not valid JSON.')
  }
  if (fresh) {
    await update($, writeTyped, () => '')
  }
  await update($, snippetAt, () => -1)
  await update($, snippetFromWrite, () => !fresh)
  // The w field opens again with the draft, so what Maxim typed survives the trip to the snippets.
  const typed = await read($, writeTyped)
  await update($, writeDraft, () => typed)
  await update($, snippetQuery, () => '')
  await update($, editing, () => 'snippet')
  await $.ui.focus({ requestId: PANE, key: 'snip' }).catch(() => undefined)
}

// w: typing ;; opens the snippet search; the text before it stays as the draft.
async function typedInWrite($: EngineInterface, value: string) {
  // A backspace into the screenshot mark drops the screenshot and the rest of the mark.
  if ((await read($, clipShot)) !== 0 && !value.startsWith(SHOT_MARK)) {
    await update($, writeTyped, () => value)
    await dropShot($)
    return
  }
  const at = value.indexOf(';;')
  if (at >= 0) {
    await update($, writeTyped, () => value.slice(0, at) + value.slice(at + 2))
    await openSnippets($)
    await update($, snippetAt, () => at)
    return
  }
  await update($, writeTyped, () => value)
}

// Enter in the snippet search: the highlighted (top) match goes after the draft, and the w field opens with both.
async function insertSnippet($: EngineInterface) {
  const query = await read($, snippetQuery)
  const top = matchSnippets(await read($, snippets), query)[0]
  const draft = await read($, writeTyped)
  const at = await read($, snippetAt)
  const cut = at < 0 || at > draft.length ? draft.length : at
  // A space sets the snippet apart from a word right before it.
  const before = draft.slice(0, cut)
  const glue = before !== '' && !/\s$/.test(before) ? ' ' : ''
  const next = top ? `${before}${glue}${top.text}${draft.slice(cut)}` : draft
  if (!top) {
    $.ui.toast('No snippet matches.')
  } else {
    logEvent($, { type: 'snippet', name: top.name })
  }
  await update($, writeDraft, () => next)
  await update($, writeTyped, () => next)
  await update($, editing, () => 'write')
  await $.ui.focus({ requestId: PANE, key: 'edit' }).catch(() => undefined)
}

// Markdown to plain text for the clipboard: no ** or __, no backticks, no # before headings, no code fences.
// A link keeps its text and its URL. Bullets stay as "- ".
function plainText(source: string): string {
  return source
    .split('\n')
    .filter(line => !/^\s*```/.test(line))
    .map(line => line
      .replace(/^(\s*)#{1,6}\s+/, '$1')
      .replace(/^(\s*)[*+]\s+/, '$1- ')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label: string, url: string) => (label === url ? url : `${label} (${url})`))
      .replace(/(\*\*|__)(.+?)\1/g, '$2')
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, '$1$2')
      .replace(/`([^`]+)`/g, '$1'))
    .join('\n')
}

// c: Claude's last message to the clipboard, as plain text.
async function copyText($: EngineInterface, body: string) {
  if (body === '') {
    $.ui.toast('No message to copy.')
    return
  }
  // mac.py copies on the Mac; on the devbox it sends the text to the Mac through desk-bridge.py.
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await runProc($, ['python3', `${home}/desk/scripts/mac.py`, 'copy', plainText(body)], { timeoutMs: 10_000 })
  $.ui.toast(ran.exitCode === 0 ? "Copied Claude's last message." : `Not copied: ${ran.stderr}`.slice(0, 160))
}

// Every pane action as one line in data/events.jsonl. Quiet: it never shows or waits.
function logEvent($: EngineInterface, row: Record<string, unknown>) {
  void (async () => {
    const home = (await $.env.get('HOME')) ?? ''
    await runProc($, ['python3', `${home}/desk/scripts/log-event.py`, JSON.stringify(row)], { timeoutMs: 10_000 })
  })().catch(() => undefined)
}

async function jumpProject($: EngineInterface, name: string) {
  logEvent($, { type: 'tab', name: name === QUEUE ? 'queue' : name || 'all' })
  await update($, filter, () => name)
  await selectFirst($)
}

// Keeps one row above and below the selection drawn, so the next arrow press always has a row to land on.
async function scrollTo($: EngineInterface, id: string): Promise<boolean> {
  const { rows } = await listRows($)
  const index = rows.findIndex(row => row.id === id)
  if (index < 0) {
    return false
  }
  const before = await read($, offset)
  const last = Math.max(0, rows.length - WINDOW)
  await update($, offset, at => {
    if (index <= at && at > 0) {
      return Math.max(0, index - 1)
    }
    if (index >= at + WINDOW - 1) {
      return Math.min(last, index - WINDOW + 2)
    }

    return at
  })

  return (await read($, offset)) !== before
}

// After a project change or a search, the first row takes the selection and the focus ring.
async function selectFirst($: EngineInterface) {
  const { rows } = await listRows($)
  await update($, offset, () => 0)
  const first = rows[0]
  if (!first) {
    // No row: the empty-list row takes the keyboard, so the keys keep working.
    await update($, picked, () => '')
    await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
    return
  }
  await select($, first.id)
}

async function startEdit($: EngineInterface, kind: string) {
  await update($, clipShot, () => 0)
  if (kind === 'write') {
    void checkClipboardShot($)
    await update($, writeDraft, () => '')
    await update($, writeTyped, () => '')
  }
  await update($, editing, () => kind)
  await $.ui.focus({ requestId: PANE, key: 'edit' }).catch(() => undefined)
}

// Leaving the search: the picked item stays when it is among the results; otherwise the first result is picked,
// so the selected row always holds the keyboard.
async function keepOrPickFirst($: EngineInterface) {
  const { rows } = await listRows($)
  const pickedId = await read($, picked)
  if (rows.some(row => row.id === pickedId)) {
    await select($, pickedId)
    return
  }
  await selectFirst($)
}

// Every key typed in the search bar filters the list at once; the first match takes the selection.
async function liveSearch($: EngineInterface, value: string) {
  await update($, query, () => value.trim())
  await update($, offset, () => 0)
}

// m: the transcript Maxim copied goes to the selected meeting item, or to ~/desk/inbox as a new meeting.
// This session then files it and routes notes, links and tasks to the items.
async function meetingFromClipboard($: EngineInterface, row: DeskCard | DeskDone | undefined) {
  const home = (await $.env.get('HOME')) ?? ''
  const pasted = await runProc($, ['pbpaste'], { timeoutMs: 10_000 })
  const transcript = pasted.stdout.trim()
  if (transcript.length < 200) {
    $.ui.toast('Copy the meeting transcript first, then press m.')
    return
  }
  const stamp = new Date().toISOString().slice(0, 16).replace('T', '-').replace(':', '')
  // A selected meeting item takes the transcript; the file then goes outside the inbox, so it makes no second item.
  const meeting = row && 'turn' in row && row.kind === 'Meeting' ? row : undefined
  if (meeting) {
    const path = `${(await deskProfile($)).data}/transcripts/${stamp}-meeting.md`
    await runProc($, ['mkdir', '-p', `${(await deskProfile($)).data}/transcripts`], { timeoutMs: 5_000 })
    await $.fs.write(path, `${transcript}\n`)
    $.ui.toast('Transcript added to this meeting. Claude files it now.')
    await $.prompt.submit({
      text: `File the meeting of desk item ${meeting.id} ("${meeting.status}") with the Meetings recipe in ITEMS.md. Its transcript is in ${path}; use that file as the transcript. Route the notes, meeting links and tasks to existing items first, write them directly, then report.`,
      asUser: true,
    })
    return
  }
  const path = `${(await deskProfile($)).root}/inbox/${stamp}-meeting.md`
  await $.fs.write(path, `${transcript}\n`)
  $.ui.toast('Meeting saved. Claude files it now.')
  await gather($)
  await $.prompt.submit({
    text: `File the meeting in ${path} with the Meetings recipe in ITEMS.md. Route the notes, meeting links and tasks to existing items first, write them directly, then report.`,
    asUser: true,
  })
}

// k and j: the item swaps places with its neighbour. desk.py keeps the order across gathers.
async function moveItem($: EngineInterface, id: string, way: string) {
  logEvent($, { type: 'move', id, way })
  const { rows } = await listRows($)
  const card = rows.find((r): r is DeskCard => r.id === id && 'turn' in r)
  // A top-level item swaps places with its top-level neighbour by sort time; children keep desk.py's order.
  if (card && card.parent === '') {
    const tops = rows.filter((r): r is DeskCard => 'turn' in r && r.parent === '')
    const index = tops.findIndex(r => r.id === id)
    const other = tops[way === 'up' ? index - 1 : index + 1]
    const beyond = tops[way === 'up' ? index - 2 : index + 2]
    const isYellow = await yellowTest($)
    const sortTime = await sortTimeTest($)
    const isWaking = await wakeTest($)
    // sortTime takes off a tiny step by desk position; the saved time adds it back, so the shown time is exact.
    const deskOrder = (await read($, view)).cards.map(c => c.id)
    const position = (c: DeskCard) => Math.max(0, deskOrder.indexOf(c.id))
    const isNew = (c: DeskCard) => c.kind === 'Todo' && c.age < 1 && c.sessions.length === 0
    const place = (c: DeskCard) => (isYellow(c) ? 0 : isWaking(c) ? 3 : isNew(c) ? 1 : 2)
    if (!other || (TURN_ORDER[other.turn] ?? 9) !== (TURN_ORDER[card.turn] ?? 9) || place(other) !== place(card)) {
      $.ui.toast('This item cannot move further in its group.')
      return
    }
    const times = await read($, fileTimes)
    // The item lands between its neighbour and the row after it, so one press moves it exactly one row.
    const room = beyond && (TURN_ORDER[beyond.turn] ?? 9) === (TURN_ORDER[card.turn] ?? 9) && place(beyond) === place(card)
    const step = room ? (sortTime(beyond) - sortTime(other)) / 2 : way === 'up' ? 1 : -1
    const at = sortTime(other) + step + (position(card) * 0.001)
    await update($, bumps, all => ({ ...all, [id]: { at, touch: touchedAt(card, times) } }))
    await saveMarks($)
    await select($, id)
    return
  }
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await runProc($, ['python3', `${home}/desk/scripts/desk.py`, 'move', id, way], { timeoutMs: 15_000 })
  if (ran.exitCode !== 0) {
    $.ui.toast(`${ran.stderr || ran.stdout}`.trim().slice(0, 160))
    return
  }
  await refresh($)
  await select($, id)
}

// Search filters the list. A new todo goes to tasks.json with the project guessed from its words.
// A rename writes the todo, the session, or Maxim's own name for any other item.
async function saveEdit($: EngineInterface, value: string, kind: string, row: DeskCard | DeskDone | undefined) {
  if (kind === 'snippet') {
    await insertSnippet($)
    return
  }
  await update($, editing, () => '')
  let name = oneLine(value)
  if (kind === 'search') {
    await update($, query, () => name)
    await keepOrPickFirst($)
    return
  }
  await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
  // w with a screenshot: Enter alone sends the screenshot without text.
  const shot = await read($, clipShot)
  if (kind === 'write') {
    name = name.replace(/^\[screenshot\]\s*/, '')
  }
  if (name === '' && !(kind === 'write' && shot !== 0)) {
    return
  }
  if (kind.startsWith('contact-')) {
    await saveContactEdit($, kind, name)
    return
  }
  logEvent($, { type: kind === 'new' ? 'todo' : kind, id: row?.id ?? '', text: name, screenshot: kind === 'write' && shot !== 0 })
  if (kind === 'note' && row) {
    await addNote($, row, name)
    return
  }
  if (kind === 'write' && row && 'turn' in row) {
    await update($, clipShot, () => 0)
    const text = await withShot($, name, shot)
    // No session yet: a new session starts the normal way, with the message after the item's context.
    if (!row.sessions[0]) {
      await openSession($, row, text)
      return
    }
    await sendToSession($, row.sessions[0].id, text)
    return
  }
  if (kind === 'remind' && row) {
    await setReminder($, row.id, name)
    return
  }
  if (kind === 'new' && (await read($, filter)) === CHAT) {
    await startChat($, name)
    return
  }
  if (kind === 'new') {
    await addTodo($, name)
    return
  }
  const home = (await $.env.get('HOME')) ?? ''
  const scripts = `${home}/desk/scripts`
  let argv: string[] = []
  if (kind === 'rename' && row) {
    argv = ['python3', `${scripts}/desk.py`, 'title', row.id, name]
    if (row.id.startsWith('task:')) {
      argv = ['python3', `${scripts}/task.py`, 'edit', row.id, '--title', name]
    } else if (row.session) {
      argv = ['python3', `${scripts}/desk.py`, 'rename', row.session, name]
    }
  }
  if (argv.length === 0) {
    return
  }
  if (kind === 'rename' && row) {
    await update($, view, desk => ({
      ...desk,
      cards: desk.cards.map(c => (c.id === row.id ? { ...c, status: name } : c)),
      done: desk.done.map(d => (d.id === row.id ? { ...d, title: name } : d)),
    }))
    void runThenGather($, argv.slice(1).map((arg, i) => (i === 0 ? arg.split('/').pop() ?? arg : arg)), 'Not saved')
    return
  }
  const ran = await runProc($, argv, { timeoutMs: 30_000 })
  $.ui.toast(ran.exitCode === 0 ? ran.stdout.trim().slice(0, 120) : `Not saved: ${ran.stderr || ran.stdout}`.slice(0, 160))
  await gather($)
}

// Enter on a row shows its session in its own Superset workspace, so every session keeps running.
// A row without a session gets a new, empty one: named in Maxim's form and linked to the item.
// A random version 4 UUID, the form `claude --session-id` takes.
function newSessionId(): string {
  const hex = [...Array(32)].map(() => Math.floor(Math.random() * 16).toString(16))
  hex[12] = '4'
  hex[16] = ((parseInt(hex[16] ?? '0', 16) & 0x3) | 0x8).toString(16)
  const h = hex.join('')

  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

// Items whose session the pane opens now. A second open of the same item waits for the first:
// two opens of an item without a session would start two sessions.
const opening = new Set<string>()

async function openSession($: EngineInterface, row: DeskCard | DeskDone, message = '') {
  if (opening.has(row.id)) {
    return
  }
  opening.add(row.id)
  try {
    await openSessionNow($, row, message)
  } finally {
    opening.delete(row.id)
  }
}

async function openSessionNow($: EngineInterface, row: DeskCard | DeskDone, message = '') {
  logEvent($, { type: 'open', id: row.id, session: row.sessions[0]?.id ?? '' })
  // A done item keeps its sessions in done.json; its newest one opens like an open item's.
  let id = row.session || row.sessions[0]?.id || ''
  let name = 'turn' in row ? row.status : (row.sessions[0]?.title ?? row.title)
  if ('turn' in row) {
    id = row.session || row.sessions[0]?.id || ''
    name = row.session ? row.status : (row.sessions[0]?.title ?? sessionName(row))
  }
  const here = await $.session.id().catch(() => '')
  if (id !== '' && id === here) {
    $.ui.toast('This is the session you are in.')
    return
  }
  if (id === '' && !('turn' in row)) {
    $.ui.toast('This item has no session.')
    return
  }
  const home = (await $.env.get('HOME')) ?? ''
  const argv = ['python3', `${home}/desk/scripts/open-session.py`]
  if (id !== '') {
    argv.push('resume', id, name.slice(0, 60), row.project)
  } else if ('turn' in row) {
    argv.push('new', row.id, sessionName(row), row.project, ...(message !== '' ? [message] : []))
  }
  // A new session shows on its item at once: the pane picks its id, and the script starts Claude with it.
  // Creating the workspace takes a few seconds; the item does not wait for it.
  const created = id === '' && 'turn' in row ? newSessionId() : ''
  if (created !== '' && 'turn' in row) {
    const linked = { id: created, title: sessionName(row), state: 'live', mtime: Date.now() / 1000, last: '' }
    await update($, started, all => ({ ...all, [row.id]: { session: linked, at: Date.now() / 1000 } }))
    await update($, liveIds, ids => (ids.includes(created) ? ids : [...ids, created]))
    await update($, view, desk => ({
      ...desk,
      cards: desk.cards.map(c => (c.id === row.id ? { ...c, sessions: [linked, ...c.sessions], sessionState: 'live', turn: 'running' } : c)),
    }))
  }
  // w on an item without a session: the session starts with the message in the background, and Maxim stays here.
  const isBackground = created !== '' && message !== ''
  $.ui.toast(isBackground ? 'Starting the session in the background…' : 'Opening the session in its own workspace…')
  const env: Record<string, string> = created !== '' ? { DESK_SESSION_ID: created } : {}
  if (isBackground) {
    env.DESK_BACKGROUND = '1'
  }
  const ran = await runProc($, argv, { timeoutMs: 90_000, env })
  if (ran.exitCode !== 0) {
    $.ui.toast(`Could not open it: ${ran.stderr || ran.stdout}`.slice(0, 160))
    if (created !== '') {
      await update($, started, all => Object.fromEntries(Object.entries(all).filter(([key]) => key !== row.id)))
      await update($, view, desk => ({
        ...desk,
        cards: desk.cards.map(c => (c.id === row.id ? { ...c, sessions: c.sessions.filter(x => x.id !== created) } : c)),
      }))
    }
    return
  }
  // Claude writes the session file a few seconds after the workspace opens, so a gather runs later and reads it.
  if (created !== '') {
    void runProc($, ['sleep', '20'], { timeoutMs: 30_000 }).then(() => gather($))
  }
  await gather($)
}

// Maxim's session names: "[TAG] topic". A title that already starts with a tag stays as it is.
function sessionName(row: DeskCard): string {
  if (/^\s*\[[^\]]+\]/.test(row.status)) {
    return row.status.trim().slice(0, 60)
  }
  const words = row.status.replace(/https?:\/\/\S+/g, '').split(/\s+/).filter(Boolean).slice(0, 6).join(' ')

  return `[${row.project}] ${words}`.slice(0, 60)
}

// x on a done item: it goes back to the open list at once; the script and a new gather run behind it.
async function reopen($: EngineInterface, id: string) {
  logEvent($, { type: 'reopen', id })
  await update($, view, desk => {
    const row = desk.done.find(d => d.id === id)
    if (!row) {
      return desk
    }
    const back: DeskCard = {
      id, kind: row.kind, project: row.project, status: row.title, note: row.note, act: '', url: row.url, links: row.links,
      mins: 15, impact: 'P2', age: 0, session: '', sessions: row.sessions, sessionState: '', turn: 'todo', prs: [], notes: row.notes, meetings: [], parent: '', children: [], contact: '',
    }
    const cards = [back, ...desk.cards]

    return { ...desk, cards, done: desk.done.filter(d => d.id !== id) }
  })
  await select($, id)
  void runThenGather($, ['desk-close.py', id, 'reopen'], 'Not reopened')
}

// x on an open item: it moves to the done rows at once, and the next row takes the selection.
async function markDone($: EngineInterface, id: string) {
  logEvent($, { type: 'done', id })
  const before = (await listRows($)).rows
  const at = before.findIndex(row => row.id === id)
  await update($, view, desk => {
    const card = desk.cards.find(c => c.id === id)
    if (!card) {
      return desk
    }
    const row: DeskDone = {
      id, title: card.status, project: card.project, verb: 'do', date: new Date().toISOString().slice(0, 10),
      note: 'done from the pane', session: '', url: card.url, links: card.links, kind: card.kind, notes: card.notes, sessions: card.sessions,
    }

    return { ...desk, cards: desk.cards.filter(c => c.id !== id), done: [row, ...desk.done] }
  })
  const after = (await listRows($)).rows
  const next = after[Math.min(Math.max(at, 0), after.length - 1)]
  if (next) {
    await select($, next.id)
  }
  await update($, lastUndo, () => `done:${id}`)
  void runThenGather($, ['desk-close.py', id, 'do', 'done from the pane'], 'Not done')
}

// e: the item leaves the list at once; desk.py removes it and keeps a copy in data/deleted.json.
async function deleteItem($: EngineInterface, id: string) {
  logEvent($, { type: 'erase', id })
  const before = (await listRows($)).rows
  const at = before.findIndex(row => row.id === id)
  await update($, view, desk => ({ ...desk, cards: desk.cards.filter(c => c.id !== id), done: desk.done.filter(d => d.id !== id) }))
  const after = (await listRows($)).rows
  const next = after[Math.min(Math.max(at, 0), after.length - 1)]
  if (next) {
    await select($, next.id)
  }
  $.ui.toast('Deleted. u: Undo')
  await update($, lastUndo, () => `erase:${id}`)
  void runThenGather($, ['desk.py', 'delete', id], 'Not deleted')
}

// u: takes back the last x, e or y. A done item opens again; an erased item comes back from data/deleted.json;
// an item y pushed out goes back into the queue, and one y put in leaves it.
async function undoLast($: EngineInterface) {
  const last = await read($, lastUndo)
  if (last === '') {
    return
  }
  await update($, lastUndo, () => '')
  logEvent($, { type: 'undo', what: last })
  const [what, ...rest] = last.split(':')
  if (what === 'unmark') {
    const [queuedAt, seenBefore, ...idParts] = rest
    const id = idParts.join(':')
    await update($, queued, all => ({ ...all, [id]: Number(queuedAt) }))
    await update($, seenAt, seen => ({ ...seen, [id]: Number(seenBefore) }))
    await saveMarks($)
    await select($, id)
    $.ui.toast('Back in the queue.')
    return
  }
  const id = rest.join(':')
  if (what === 'mark') {
    await update($, queued, all => Object.fromEntries(Object.entries(all).filter(([key]) => key !== id)))
    await saveMarks($)
    $.ui.toast('Out of the queue again.')
    return
  }
  if (what === 'done') {
    await reopen($, id)
  } else {
    await runThenGather($, ['desk.py', 'restore', id], 'Not restored')
    await select($, id)
  }
  $.ui.toast('Undone.')
}

// l: on a child, the link to its parent goes at once. On any other item, the list waits for the parent pick;
// l again cancels it.
async function startLink($: EngineInterface, card: DeskCard) {
  if ((await read($, linking)) !== '') {
    await update($, linking, () => '')
    return
  }
  if (card.parent) {
    await update($, view, desk => ({
      ...desk,
      cards: desk.cards.map(c => {
        if (c.id === card.id) {
          return { ...c, parent: '' }
        }
        if (c.id === card.parent) {
          return { ...c, children: c.children.filter(k => k.id !== card.id) }
        }

        return c
      }),
    }))
    void runThenGather($, ['desk.py', 'parent', card.id, 'none'], 'Not unlinked')
    return
  }
  await update($, linking, () => card.id)
}

// The list as desk.py will write it: the child sits right after its parent's last child.
function nestCards(cards: readonly DeskCard[], child: string, parent: string): DeskCard[] {
  const moved = cards.find(c => c.id === child)
  if (!moved) {
    return [...cards]
  }
  const rest = cards
    .filter(c => c.id !== child)
    .map(c => (c.id === parent ? { ...c, children: [...c.children, { id: child, title: moved.status, isDone: false }] } : c))
  let at = rest.findIndex(c => c.id === parent)
  while (at + 1 < rest.length && rest[at + 1]?.parent === parent) {
    at += 1
  }
  rest.splice(at + 1, 0, { ...moved, parent })

  return rest
}

async function pickParent($: EngineInterface, child: string, parent: string) {
  await update($, linking, () => '')
  if (child === parent) {
    return
  }
  const target = (await read($, view)).cards.find(c => c.id === parent)
  if (!target || target.parent) {
    $.ui.toast('Pick an open top-level item as the parent.')
    return
  }
  await update($, view, desk => ({ ...desk, cards: nestCards(desk.cards, child, parent) }))
  await select($, child)
  $.ui.toast(`Linked under "${target.status.slice(0, 50)}".`)
  void runThenGather($, ['desk.py', 'parent', child, parent], 'Not linked')
}

// Runs one desk script, shows a toast only when it fails, then gathers in the background.
async function runThenGather($: EngineInterface, args: string[], failure: string) {
  const home = (await $.env.get('HOME')) ?? ''
  const [script, ...rest] = args
  const ran = await runProc($, ['python3', `${home}/desk/scripts/${script}`, ...rest], { timeoutMs: 30_000 })
  if (ran.exitCode !== 0) {
    $.ui.toast(`${failure}: ${ran.stderr || ran.stdout}`.slice(0, 160))
  }
  await gather($)
}

async function refresh($: EngineInterface) {
  const home = (await $.env.get('HOME')) ?? ''
  const raw = await $.fs.read(`${(await deskProfile($)).data}/cards.json`).catch(() => '')
  if (raw === '') {
    return
  }
  const file = JSON.parse(raw) as Raw
  const cards = (Array.isArray(file.cards) ? file.cards : []) as Raw[]
  const doneRaw = await $.fs.read(`${(await deskProfile($)).data}/done.json`).catch(() => '{}')
  const doneList = ((JSON.parse(doneRaw) as Raw).done ?? []) as Raw[]
  const contactsRaw = await $.fs.read(`${(await deskProfile($)).data}/contacts.json`).catch(() => '{}')
  await update($, contactList, () => contactsOf(contactsRaw))
  const next: DeskView = {
    at: text(file.at),
    cards: cards.map(c => ({
      id: text(c.id),
      project: text(c.project) || 'MISC',
      status: text(c.status),
      note: text(c.note),
      act: text(c.act),
      url: text(c.url),
      links: linksOf(c.links, c.url),
      mins: Number(c.mins) || 0,
      impact: text(c.impact),
      age: Number(c.age) || 0,
      session: text(c.session),
      sessions: ((Array.isArray(c.sessions) ? c.sessions : []) as Raw[]).map(one => ({
        id: text(one.id),
        title: text(one.title) || '(no name)',
        state: text(one.state),
        mtime: Number(one.mtime) || 0,
        last: text(one.last),
      })),
      notes: notesOf(c.notes),
      meetings: (Array.isArray(c.meetings) ? c.meetings : []).map(text),
      parent: text(c.parent),
      children: ((Array.isArray(c.children) ? c.children : []) as Raw[]).map(one => ({
        id: text(one.id),
        title: text(one.title),
        isDone: one.isDone === true,
      })),
      sessionState: text(c.sessionState),
      turn: text(c.turn) || 'todo',
      kind: text(c.kind) || 'Todo',
      contact: text(c.contact),
      parked: text(c.parked),
      prs: ((Array.isArray(c.prs) ? c.prs : []) as Raw[]).map(one => ({
        num: text(one.num),
        title: text(one.title),
        url: text(one.url),
        state: text(one.state),
      })),
    })),
    done: doneList.map(d => ({
      id: text(d.id),
      title: text(d.title),
      project: text(d.project) || 'MISC',
      verb: text(d.verb),
      date: text(d.date),
      note: text(d.note),
      session: text(d.session),
      url: text(d.url),
      links: linksOf(d.links, d.url),
      kind: text(d.kind) || 'Todo',
      notes: notesOf(d.notes),
      sessions: ((Array.isArray(d.sessions) ? d.sessions : []) as Raw[]).map(one => ({
        id: text(one.id),
        title: text(one.title) || '(no name)',
        state: text(one.state),
        mtime: Number(one.mtime) || 0,
        last: text(one.last),
      })),
    })),
    parked: Array.isArray(file.parked) ? file.parked.length : 0,
    closedToday: Number(file.closedToday) || 0,
  }
  const isFirstLoad = (await read($, view)).at === ''
  // A started session leaves the list once cards.json links it, or once its window is over.
  const pending = pendingStarted(await read($, started))
  const linkedNow = Object.keys(pending).filter(id => next.cards.find(c => c.id === id)?.sessions.some(x => x.id === pending[id]?.session.id))
  if (linkedNow.length > 0 || Object.keys(pending).length !== Object.keys(await read($, started)).length) {
    await update($, started, () => Object.fromEntries(Object.entries(pending).filter(([id]) => !linkedNow.includes(id))))
  }
  await update($, view, () => withStarted(next, pending))
  if (isFirstLoad) {
    await restoreSelection($, home)
  }
  // The picked item always exists: an unknown id (first load, an item that left) becomes the first row shown,
  // so a search keeps the item in the body until an arrow or Enter picks a result.
  const pickedNow = await read($, picked)
  if (![...next.cards, ...next.done].some(row => row.id === pickedNow) && (await read($, query)) === '') {
    const first = (await listRows($)).rows[0]
    await update($, picked, () => first?.id ?? '')
  }
  // After /resume the pane survives but its last drawing came from the old session's state; draw it again.
  $.ui.invalidate('ui.render')
  const hidden = await read($, isHidden)
  $.ui.status(next.cards.length > 0 ? `${next.cards.length} waiting${hidden ? ' · /desk-pane' : ''}` : undefined)
  const isUp = (await $.ui.panes()).some(pane => pane.id === PANE)
  if (!isUp && !hidden) {
    await $.ui.open(PANE_SIZE).catch(() => undefined)
  }
}

async function restoreSelection($: EngineInterface, home: string) {
  const raw = await $.fs.read(`${(await deskProfile($)).data}/pane.json`).catch(() => '')
  if (raw === '') {
    return
  }
  const kept = JSON.parse(raw) as Raw
  await update($, picked, () => text(kept.picked))
  await update($, filter, () => text(kept.filter))
  await update($, offset, () => Number(kept.offset) || 0)
  await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
}

// A gather asked for while one runs starts once more after it, so the last change is never lost.
let gatherAgain = false

// The tasks.json write time the last gather saw.
let tasksSeen = 0

async function gather($: EngineInterface) {
  if (await read($, gathering)) {
    gatherAgain = true
    return
  }
  await update($, gathering, () => true)
  const home = (await $.env.get('HOME')) ?? ''
  tasksSeen = (await $.fs.stat(`${(await deskProfile($)).data}/tasks.json`).catch(() => undefined))?.mtimeMs ?? Date.now()
  await runProc($, ['python3', `${home}/desk/scripts/desk.py`], { timeoutMs: 120_000 })
    .catch(() => undefined)
  // desk.py writes tasks.json itself; that write must not start the next gather.
  tasksSeen = (await $.fs.stat(`${(await deskProfile($)).data}/tasks.json`).catch(() => undefined))?.mtimeMs ?? Date.now()
  await update($, gathering, () => false)
  await refresh($)
  if (gatherAgain) {
    gatherAgain = false
    await gather($)
  }
}

// g: the transcripts open in VS Code; the default app for .md files shows nothing.
async function openMeetings($: EngineInterface, paths: string[]) {
  const home = (await $.env.get('HOME')) ?? ''
  await runProc($, ['python3', `${home}/desk/scripts/mac.py`, 'code', ...paths], { timeoutMs: 10_000 }).catch(() => undefined)
}

// "Meeting 2026-10-16 weekly-sync" from ".../call-transcript-2026-10-16-weekly-sync.md".
function meetingLabel(path: string): string {
  const name = path.split('/').pop() ?? path
  const m = /(\d{4}-\d{2}-\d{2})-(.+)\.md$/.exec(name)

  return m ? `${m[1]} ${m[2]}` : name
}

async function openUrls($: EngineInterface, urls: string[]) {
  const home = (await $.env.get('HOME')) ?? ''
  await runProc($, ['python3', `${home}/desk/scripts/mac.py`, 'open', ...urls], { timeoutMs: 10_000 }).catch(() => undefined)
}

// The pane shows at most this many links; the rest stay in tasks.json.
const MAX_LINKS = 4

// A short name for a link: Teams by team and channel, GitHub and Linear by number, others by host and path.
function linkLabel(url: string): string {
  let u: URL
  try {
    u = new URL(url)
  } catch {
    return url
  }
  const q = u.searchParams
  if (u.hostname.endsWith('teams.microsoft.com')) {
    const where = [q.get('teamName'), q.get('channelName')].filter(Boolean).join(' › ')
    return `Teams${where ? ` · ${where}` : ''}`
  }
  const pr = /^\/([^/]+\/[^/]+)\/(pull|issues)\/(\d+)/.exec(u.pathname)
  if (u.hostname === 'github.com' && pr) {
    return `GitHub ${pr[1]} #${pr[3]}`
  }
  const issue = /\/issue\/([A-Z]+-\d+)/.exec(u.pathname)
  if (u.hostname === 'linear.app' && issue) {
    return `Linear ${issue[1]}`
  }
  const session = /^\/sessions\/([^/]+)/.exec(u.pathname)
  if (u.hostname.endsWith('app.example.com') && session) {
    return `Session ${session[1]}`
  }

  return `${u.hostname.replace(/^www\./, '')}${u.pathname === '/' ? '' : u.pathname}`
}

function linksOf(value: unknown, url: unknown): string[] {
  const list = (Array.isArray(value) ? value : []).map(text).filter(Boolean)
  if (list.length > 0) {
    return list
  }

  return text(url) ? [text(url)] : []
}

async function showCard($: EngineInterface, input: Raw): Promise<string> {
  const id = text(input.item_id).trim()
  const desk = await read($, view)
  if (!desk.cards.some(card => card.id === id)) {
    return `Not shown: no card ${id} is on the desk. Run desk.py first.`
  }
  const options: DeskOption[] = ((Array.isArray(input.options) ? input.options : []) as Raw[])
    .map(option => ({
      label: text(option.label).trim().slice(0, 60),
      verb: VERBS.includes(text(option.verb)) ? text(option.verb) : 'do',
    }))
    .filter(option => option.label !== '')
    .slice(0, OPTION_KEYS.length)
  if (options.length === 0) {
    return 'Not shown: give at least one option with a label and a verb.'
  }
  await update($, brief, () => ({ cardId: id, fact: text(input.fact), context: text(input.context), options }))
  await update($, picked, () => id)
  await update($, isHidden, () => false)
  await update($, previewOn, () => false)
  await $.ui.open({ ...PANE_SIZE, focus: true }).catch(() => undefined)
  // New choices are drawn above the list; the ring is put back on the selected row at once.
  await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
  const keys = options.map((option, i) => `${OPTION_KEYS[i]}: ${option.label}`).join(', ')

  return `The choices for item ${id} are in the pane (${keys}). Stop here. Maxim's pick or his own words arrive as "Re desk item ${id}: ...".`
}

async function choose($: EngineInterface, current: DeskBrief, option: DeskOption) {
  logEvent($, { type: 'choice', id: current.cardId, label: option.label, verb: option.verb })
  await update($, brief, b => (b?.cardId === current.cardId ? null : b))
  await $.prompt.submit({
    text: `Re desk item ${current.cardId}: ${option.label} (${option.verb}). Execute it per the recipe, then record it with desk_close.`,
    asUser: true,
  })
}

async function closeCard($: EngineInterface, input: Raw): Promise<string> {
  const id = text(input.item_id).trim()
  const verb = text(input.verb).trim()
  if (!VERBS.includes(verb)) {
    return `Not closed: the verb must be one of ${VERBS.join(', ')}.`
  }
  const home = (await $.env.get('HOME')) ?? ''
  const argv = ['python3', `${home}/desk/scripts/desk-close.py`, id, verb]
  if (verb === 'defer') {
    const trigger = text(input.trigger).trim()
    if (trigger === '') {
      return 'Not closed: a defer needs a trigger (date:YYYY-MM-DD, tomorrow, a weekday, pr-merged:<n>).'
    }
    argv.push(trigger, text(input.act))
  } else {
    argv.push(text(input.note))
  }
  const ran = await runProc($, argv, { timeoutMs: 30_000 })
  if (ran.exitCode !== 0) {
    return `Not closed: ${ran.stderr || ran.stdout}`.slice(0, 400)
  }
  await update($, brief, b => (b?.cardId === id ? null : b))
  await update($, picked, () => '')
  await gather($)
  const top = (await read($, view)).cards[0]

  return `${ran.stdout.trim()}${top ? `\nNow on top: ${top.id} (${top.project}) ${top.status}` : ''}`
}

// The preview as terminal rows: each message wrapped to the width, its first row starting with who spoke.
type Word = { text: string; style: string; href?: string }
type PreviewRow = { prefix: string; words: Word[]; indent?: number }

// The preview as terminal rows, newest last: each message keeps its line breaks and bullets, and its
// **bold**, `code` and [links] keep their style word by word, so a wrap never breaks the format.
function previewLines(messages: readonly { role: string; text: string }[], width: number): PreviewRow[] {
  const rows: PreviewRow[] = []
  for (const message of messages) {
    // A title line: who spoke, then a divider in the same color to the right edge. The message follows.
    const who = message.role === 'you' ? 'You' : 'Claude'
    rows.push({ prefix: who, words: [{ text: ` ${'─'.repeat(Math.max(1, width - who.length - 1))}`, style: who === 'You' ? 'you' : 'claude' }] })
    const prefix = ''
    // Code fence lines (```) carry no text; the lines between them stay.
    const lines = message.text.split('\n').filter(line => !/^\s*```/.test(line)).filter((line, n, all) => line.trim() !== '' || (all[n - 1] ?? '').trim() !== '')
    for (const raw of lines) {
      const heading = /^#{1,6}\s+/.test(raw)
      const line = raw.replace(/^#{1,6}\s+/, '').replace(/^(\s*)[-*]\s+/, '$1• ').trimEnd()
      const words: Word[] = []
      for (const part of line.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g)) {
        let style = heading ? 'bold' : ''
        let body = part
        let href: string | undefined
        const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part)
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
          style = 'bold'
          body = part.slice(2, -2)
        } else if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
          style = 'code'
          body = part.slice(1, -1)
        } else if (link) {
          style = 'link'
          body = link[1] ?? part
          href = link[2]
        }
        for (const word of body.split(' ')) {
          if (word === '') {
            continue
          }
          // A bare URL is a link too; its trailing punctuation stays outside the link.
          const bare = !href && style !== 'code' ? /^(https?:\/\/\S+?)[.,;:!?)]*$/.exec(word)?.[1] : undefined
          words.push(bare ? { text: word, style: 'link', href: bare } : { text: word, style, href })
        }
      }
      // The line keeps its indent; the wrapped rows of a bullet hang under its text.
      const lead = /^\s*/.exec(line)?.[0].length ?? 0
      const hang = lead + (line.slice(lead).startsWith('• ') ? 2 : 0)
      let indent = lead
      let row: Word[] = []
      let used = 0
      for (const word of words) {
        if (row.length > 0 && indent + used + 1 + word.text.length > width) {
          rows.push({ prefix, words: row, indent })
          row = []
          used = 0
          indent = hang
        }
        used += (row.length > 0 ? 1 : 0) + word.text.length
        row.push(word)
      }
      rows.push({ prefix, words: row, indent })
    }
    rows.push({ prefix: '', words: [] })
  }

  return rows.slice(0, -1).slice(-120)
}

// Tree marks in the title column: ▾ on a parent with open children, ├─ and └─ on its children.
// Inside a status group: yellow items first, then todos Maxim added today without a session, then the rest.
// Inside each of those: the newest touch first (a k/j move sets its own sort time). Children stay under their parent.
function yellowFirst(cards: DeskCard[], isYellow: (c: DeskCard) => boolean, sortTime: (c: DeskCard) => number, isWaking: (c: DeskCard) => boolean): DeskCard[] {
  const blocks: DeskCard[][] = []
  for (const c of cards) {
    const head = blocks.at(-1)?.[0]
    if (c.parent !== '' && head && c.parent === head.id) {
      blocks.at(-1)?.push(c)
      continue
    }
    blocks.push([c])
  }
  // The status of a block is its most urgent row, as desk.py sorts it.
  const rank = (block: DeskCard[]) => Math.min(...block.map(c => TURN_ORDER[c.turn] ?? 9))
  const out: DeskCard[] = []
  let i = 0
  while (i < blocks.length) {
    let end = i
    while (end < blocks.length && rank(blocks[end] ?? []) === rank(blocks[i] ?? [])) {
      end++
    }
    const run = blocks.slice(i, end)
    const isNew = (c: DeskCard) => c.kind === 'Todo' && c.age < 1 && c.sessions.length === 0
    const place = (b: DeskCard[]) => {
      if (b.some(isYellow)) {
        return 0
      }

      // An item with a wake timer waits until then: it goes to the end of its group.
      if (b[0] && isWaking(b[0])) {
        return 3
      }

      return b[0] && isNew(b[0]) ? 1 : 2
    }
    const time = (b: DeskCard[]) => (b[0] ? sortTime(b[0]) : 0)
    out.push(...[0, 1, 2, 3].flatMap(p => run.filter(b => place(b) === p).sort((a, b) => time(b) - time(a)).flat()))
    i = end
  }

  return out
}

// True for an item with a wake timer (z) that is not due yet.
async function wakeTest($: EngineInterface): Promise<(c: DeskCard) => boolean> {
  const reminds = await read($, reminders)
  const now = Date.now() / 1000

  // A deferred item (desk-close.py defer) also shows ⏰ until its trigger fires.
  return c => (reminds[c.id]?.at ?? 0) > now || (c.parked ?? '') !== ''
}

// The sort time of a top-level item: its k/j time while it has no newer activity, else its last touch.
async function sortTimeTest($: EngineInterface): Promise<(c: DeskCard) => number> {
  const times = await read($, fileTimes)
  const moved = await read($, bumps)
  // Items with the same time keep desk.py's order: a tiny step by position makes every time distinct,
  // so k and j always find room between two neighbours.
  const position = new Map((await read($, view)).cards.map((c, i) => [c.id, i]))

  return c => {
    const touch = touchedAt(c, times)
    const bump = moved[c.id]
    return (bump && touch <= bump.touch + 1 ? bump.at : touch) - (position.get(c.id) ?? 0) * 0.001
  }
}

// Yellow means in the queue.
async function yellowTest($: EngineInterface): Promise<(c: DeskCard) => boolean> {
  const inQueue = await read($, queued)

  return c => inQueue[c.id] !== undefined
}

// Puts items into the queue: a session that just idled, a due reminder. Runs after each live check and refresh.

async function syncQueue($: EngineInterface) {
  if (await read($, isHidden)) {
    return
  }
  const desk = await read($, view)
  const live = await read($, liveIds)
  const times = await read($, fileTimes)
  const outAt = await read($, seenAt)
  const reminds = await read($, reminders)
  const before = await read($, queued)
  const isFirstCheck = !(await read($, queueChecked))
  await update($, queueChecked, () => true)
  const now = Date.now() / 1000
  const add: Record<string, number> = {}
  const dueIds: string[] = []
  for (const c of desk.cards) {
    if (before[c.id] !== undefined) {
      continue
    }
    const isDue = (reminds[c.id]?.at ?? Infinity) <= now
    if (isDue) {
      dueIds.push(c.id)
    }
    if (isDue || isIdleFresh(c, live, times, outAt)) {
      add[c.id] = now
      logEvent($, { type: 'queue_in', id: c.id, reason: isDue ? 'reminder' : 'idle', session: c.sessions[0]?.id ?? '' })
    }
  }
  // Items that left the list (done, erased) leave the queue too.
  const openIds = new Set(desk.cards.map(c => c.id))
  const kept = Object.fromEntries(Object.entries(before).filter(([id]) => openIds.has(id)))
  const isChanged = Object.keys(add).length > 0 || Object.keys(kept).length !== Object.keys(before).length
  if (!isChanged || desk.cards.length === 0) {
    return
  }
  await update($, queued, () => ({ ...kept, ...add }))
  // A short sound for each arrival. The first check after a reload only fills the queue, so it stays quiet.
  if (Object.keys(add).length > 0 && !isFirstCheck) {
    void $.env.get('HOME').then(home => runProc($, ['python3', `${home ?? ''}/desk/scripts/mac.py`, 'sound'], { timeoutMs: 5_000 })).catch(() => undefined)
  }
  if (dueIds.length > 0) {
    await update($, reminders, all => Object.fromEntries(Object.entries(all).filter(([id]) => !dueIds.includes(id))))
  }
  await saveMarks($)
}

function treeMark(row: DeskCard, next: DeskCard | DeskDone | undefined): string {
  if (row.parent) {
    const isLast = !(next && 'turn' in next && next.parent === row.parent)

    return isLast ? '  └─ ' : '  ├─ '
  }

  return row.children.some(k => !k.isDone) ? '▾ ' : ''
}

function notesOf(value: unknown): { date: string; text: string }[] {
  return ((Array.isArray(value) ? value : []) as Raw[]).map(one => ({ date: text(one.date), text: text(one.text) }))
}

// Reads the last messages of one session for the preview. A gather empties it, so the preview stays fresh.
// One read at a time per session and file time: the pane draws again many times while a read runs.
let previewLoading = ''

async function loadPreview($: EngineInterface, sessionId: string, mtime: number) {
  const key = `${sessionId}:${mtime}`
  if (previewLoading === key) {
    return
  }
  previewLoading = key
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await runProc($, ['python3', `${home}/desk/scripts/session-preview.py`, sessionId, '30'], { timeoutMs: 15_000 })
    .finally(() => {
      previewLoading = ''
    })
  let messages: { role: string; text: string }[] = []
  if (ran.exitCode === 0) {
    messages = ((JSON.parse(ran.stdout || '[]') as Raw[]) ?? []).map(one => ({ role: text(one.role), text: text(one.text) }))
  }
  await update($, preview, () => ({ id: sessionId, mtime, v: PREVIEW_V, messages }))
}

// t: the new todo shows and takes the selection at once; task.py saves it and a gather runs behind it.
// The pane picks the id, so the row it shows is the row the script writes.
async function addTodo($: EngineInterface, title: string) {
  const only = await read($, filter)
  const desk = await read($, view)
  const taken = new Set([...desk.cards, ...desk.done].map(row => row.id))
  const base = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '') || 'task'
  let slug = base
  for (let n = 2; taken.has(`task:${slug}`); n++) {
    slug = `${base}-${n}`
  }
  const id = `task:${slug}`
  const tag = /^\s*\[([^\]]+)\]/.exec(title)?.[1]?.toUpperCase()
  const card: DeskCard = {
    id, kind: 'Todo', project: (only === QUEUE ? '' : only) || tag || 'MISC', status: title, note: '', act: '', url: '', links: [],
    mins: 15, impact: 'P2', age: 0, session: '', sessions: [], sessionState: '', turn: 'todo', prs: [], notes: [], meetings: [], parent: '', children: [], contact: '',
  }
  await update($, view, current => {
    const firstTodo = current.cards.findIndex(c => c.turn === 'todo')
    const cards = [...current.cards]
    cards.splice(firstTodo < 0 ? cards.length : firstTodo, 0, card)

    return { ...current, cards }
  })
  await update($, query, () => '')
  await select($, id)
  void runThenGather($, ['task.py', 'add', title, '--project', only === '' || only === QUEUE ? 'auto' : only, '--id', slug], 'Todo not saved')
}

// t in the Chat tab: one step for a new chat. The item is saved first, so the session's briefing finds it;
// then its session starts in the background with the message. The session names the item itself.
async function startChat($: EngineInterface, message: string) {
  const desk = await read($, view)
  const taken = new Set([...desk.cards, ...desk.done].map(row => row.id))
  const title = message.replace(/https?:\/\/\S+/g, '').split(/\s+/).filter(Boolean).slice(0, 8).join(' ') || 'chat'
  const base = `chat-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 34).replace(/-+$/, '')}`
  let slug = base
  for (let n = 2; taken.has(`task:${slug}`); n++) {
    slug = `${base}-${n}`
  }
  const card: DeskCard = {
    id: `task:${slug}`, kind: 'Todo', project: CHAT, status: title, note: '', act: '', url: '', links: [],
    mins: 15, impact: 'P2', age: 0, session: '', sessions: [], sessionState: '', turn: 'todo', prs: [], notes: [], meetings: [], parent: '', children: [], contact: '',
  }
  await update($, view, current => ({ ...current, cards: [card, ...current.cards] }))
  await update($, query, () => '')
  await select($, card.id)
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await runProc($, ['python3', `${home}/desk/scripts/task.py`, 'add', title, '--project', CHAT, '--id', slug, '--body', message], { timeoutMs: 30_000 })
  if (ran.exitCode !== 0) {
    $.ui.toast(`Chat not saved: ${ran.stderr || ran.stdout}`.slice(0, 160))
    await gather($)
    return
  }
  await openSession($, card, message)
}

// n: a dated note on the selected item, shown at once and saved to tasks.json behind it. Links in it go to the Links row.
async function addNote($: EngineInterface, row: DeskCard | DeskDone, note: string) {
  const urls = note.match(/https?:\/\/\S+/g) ?? []
  const rest = note.replace(/https?:\/\/\S+/g, ' ').split(/\s+/).filter(Boolean).join(' ')
  const entries = rest === '' ? [] : [{ date: new Date().toISOString().slice(0, 10), text: rest }]
  const add = <T extends { notes: DeskNote[]; links: string[] }>(r: T): T => ({
    ...r,
    notes: [...r.notes, ...entries],
    links: [...new Set([...r.links, ...urls])],
  })
  await update($, view, desk => ({
    ...desk,
    cards: desk.cards.map(c => (c.id === row.id ? add(c) : c)),
    done: desk.done.map(d => (d.id === row.id ? add(d) : d)),
  }))
  void runThenGather($, ['task.py', 'note', '--mine', row.id, note], 'Note not saved')
}

// The last touch of an item: the newest session write, else the newest note, else its creation.
function touchedAt(card: DeskCard, times: Record<string, number>): number {
  const sessions = card.sessions.map(x => times[x.id] ?? x.mtime)
  const notes = card.notes.map(note => Date.parse(note.date) / 1000).filter(t => !Number.isNaN(t))
  // age is whole days, so the creation time is the start of that day: a value that does not move with the clock.
  const midnight = new Date()
  midnight.setHours(0, 0, 0, 0)
  const created = midnight.getTime() / 1000 - card.age * 86_400

  return Math.max(created, ...sessions, ...notes)
}

// The list column: "now" under a minute, then minutes, hours, days.
function shortAgo(at: number): string {
  if (Date.now() / 1000 - at < 60) {
    return 'now'
  }

  return ago(at)
}

function ago(mtime: number): string {
  const mins = (Date.now() / 1000 - mtime) / 60
  if (mins < 60) {
    return `${Math.round(mins)}m`
  }
  if (mins < 1440) {
    return `${Math.round(mins / 60)}h`
  }

  return `${Math.round(mins / 1440)}d`
}

// One row layout for open and done items: number, icon, project, age, title. Narrow: number and title.
// y: pushes a queued item out of the queue; puts any other item into it.
async function toggleYellow($: EngineInterface, id: string, isYellow: boolean) {
  if (isYellow) {
    // u puts it back with its old queue time, so it keeps its place in the queue.
    const queuedAt = (await read($, queued))[id] ?? Date.now() / 1000
    const seenBefore = (await read($, seenAt))[id] ?? 0
    await update($, lastUndo, () => `unmark:${queuedAt}:${seenBefore}:${id}`)
    // In the Queue tab the row goes away, so the next row takes the selection, as after x.
    const before = (await listRows($)).rows
    const at = before.findIndex(row => row.id === id)
    await clearYellow($, id)
    if ((await read($, filter)) === QUEUE) {
      const after = (await listRows($)).rows
      const next = after[Math.min(Math.max(at, 0), after.length - 1)]
      if (next) {
        await select($, next.id)
      } else {
        await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
      }
    }
    return
  }
  await update($, queued, all => ({ ...all, [id]: Date.now() / 1000 }))
  await update($, lastUndo, () => `mark:${id}`)
  logEvent($, { type: 'queue_in', id, reason: 'manual' })
  await saveMarks($)
}

// y on a queued item: it leaves the queue. Only a new event (idle, reminder) brings it back.
async function clearYellow($: EngineInterface, id: string) {
  logEvent($, { type: 'push_out', id })
  await update($, seenAt, seen => ({ ...seen, [id]: Date.now() / 1000 }))
  let isChanged = true
  await update($, queued, all => Object.fromEntries(Object.entries(all).filter(([key]) => key !== id)))
  // A reminder that is not due yet stays.
  const remind = (await read($, reminders))[id]
  if (remind && remind.at <= Date.now() / 1000) {
    await update($, reminders, all => Object.fromEntries(Object.entries(all).filter(([key]) => key !== id)))
    isChanged = true
  }
  if (isChanged) {
    await saveMarks($)
  }
}

// w: the text goes into the newest session of the item, as if Maxim typed it there and pressed Enter.
// A screenshot from the clipboard goes as a file path; the session opens it with its Read tool.
async function withShot($: EngineInterface, value: string, shot: number): Promise<string> {
  if (shot === 0) {
    return value
  }
  const home = (await $.env.get('HOME')) ?? ''
  const file = `${(await deskProfile($)).root}/inbox/shots/${new Date().toISOString().replace(/[:.]/g, '-')}.png`
  await runProc($, ['mkdir', '-p', `${(await deskProfile($)).root}/inbox/shots`], { timeoutMs: 5_000 }).catch(() => undefined)
  const saved = await runProc($, ['python3', `${home}/desk/scripts/clip-image.py`, 'save', file], { timeoutMs: 15_000 }).catch(() => undefined)
  if (saved?.exitCode !== 0) {
    return value
  }
  await update($, sentShot, () => shot)
  await saveMarks($)

  return `${value} [Screenshot: ${file}]`.trim()
}

async function sendToSession($: EngineInterface, sessionId: string, value: string) {
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await runProc($, ['python3', `${home}/desk/scripts/send-message.py`, sessionId, value], { timeoutMs: 60_000 })
    .catch(() => undefined)
  $.ui.toast(`${ran?.stdout || ran?.stderr || 'Message not sent.'}`.trim().slice(0, 160))
}

// The screenshot on the clipboard stays out of this message only. The next w offers it again until it is sent.
async function dropShot($: EngineInterface) {
  await update($, clipShot, () => 0)
  const rest = (await read($, writeTyped)).replace(/^\[screenshot\]\s?/, '')
  await update($, writeDraft, () => rest)
  await update($, writeTyped, () => rest)
  await $.ui.focus({ requestId: PANE, key: 'edit' }).catch(() => undefined)
}

// w opens: a screenshot on the clipboard that was not sent yet goes with the message, and the label says so.
async function checkClipboardShot($: EngineInterface) {
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await runProc($, ['python3', `${home}/desk/scripts/clip-image.py`, 'check'], { timeoutMs: 10_000 }).catch(() => undefined)
  if (!ran || ran.exitCode !== 0) {
    return
  }
  const clip = JSON.parse(ran.stdout || '{}') as Raw
  const count = Number(clip.count) || 0
  if (clip.isImage === true && count !== (await read($, sentShot)) && (await read($, editing)) === 'write') {
    await update($, clipShot, () => count)
    if ((await read($, writeTyped)) === '') {
      await update($, writeDraft, () => SHOT_MARK)
      await update($, writeTyped, () => SHOT_MARK)
    }
  }
}

// z: "<when> <why>". An unknown time saves nothing and leaves the text in the field's label.
async function setReminder($: EngineInterface, id: string, value: string) {
  // Haiku reads any wording; the fixed rules are the fallback when the call fails.
  $.ui.toast("⏰ Reading the time…")
  const parsed = (await askWhen($, value)) ?? parseWhen(value, new Date())
  if (!parsed) {
    $.ui.toast('Time not understood. Examples: 15m, 2 hours, tomorrow 9, fr 14:30')
    await update($, editing, () => 'remind')
    await $.ui.focus({ requestId: PANE, key: 'edit' }).catch(() => undefined)
    return
  }
  const reason = parsed.rest || 'Reminder'
  logEvent($, { type: 'remind_set', id, at: parsed.at, text: reason })
  await update($, reminders, all => ({ ...all, [id]: { at: parsed.at, text: reason } }))
  await saveMarks($)
  // The pane says what it understood, so a wrong read shows at once; z again sets it new.
  $.ui.toast(`⏰ ${whenLabel(parsed.at)}: ${reason}`)
}

// Haiku turns the reminder text into a local time and the reason. Null when the call fails or it finds no time.
async function askWhen($: EngineInterface, value: string): Promise<{ at: number; rest: string } | null> {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`
  const weekday = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][now.getDay()]
  const result = await $.model.complete({
    model: 'haiku',
    maxTokens: 200,
    system: 'You turn a reminder note into a time and a reason. Answer with one JSON object only, no other text.',
    prompt: [
      `Now: ${weekday} ${today} (local time).`,
      'The note is in English or German. It names when, and usually why.',
      'Rules: a day without a time means 09:00. A time without a day means today, or tomorrow when that time has passed.',
      'Vague times: morning 09:00, after lunch 13:30, afternoon 14:00, end of day 17:00, evening 18:00.',
      'Answer {"at": "YYYY-MM-DD HH:MM", "reason": "<the why, in the words of the note, without the time words>"}.',
      'When the note names no time at all, answer {"at": null, "reason": ""}.',
      `Note: ${JSON.stringify(value)}`,
    ].join('\n'),
  }).catch(() => undefined)
  if (!result?.isAnswered) {
    return null
  }
  const json = /\{[\s\S]*\}/.exec(result.text)?.[0] ?? ''
  let answer: Raw = {}
  try {
    answer = JSON.parse(json) as Raw
  } catch {
    return null
  }
  const when = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/.exec(text(answer.at))
  if (!when) {
    return null
  }
  const at = new Date(Number(when[1]), Number(when[2]) - 1, Number(when[3]), Number(when[4]), Number(when[5])).getTime() / 1000
  // A time in the past is a misread; the rules get their turn.
  if (!(at > now.getTime() / 1000)) {
    return null
  }

  return { at, rest: text(answer.reason) }
}

// When words first, then the reason: "tomorrow 9", "fr 14:30", "2026-10-20", "16.10. 9", "9:30", "2h", "30m", "3d".
// A day without a time means 9:00. A time without a day means today, or tomorrow when it has passed.
function parseWhen(value: string, now: Date): { at: number; rest: string } | null {
  const words = value.trim().split(/\s+/)
  let i = words[0]?.toLowerCase() === 'in' ? 1 : 0
  const first = (words[i] ?? '').toLowerCase()
  // A span: "15m", "15 mins", "2 hours", "1 day", also German ("20 Minuten", "2 Std", "3 Tage").
  const units: [RegExp, number][] = [
    [/^(m|min|mins|minute|minutes|minuten)$/, 60],
    [/^(h|hr|hrs|hour|hours|std|stunde|stunden)$/, 3600],
    [/^(d|day|days|tag|tage|tagen)$/, 86_400],
  ]
  const glued = first.match(/^(\d+)([a-z]+)$/)
  const amount = glued?.[1] ?? (/^\d+$/.test(first) ? first : undefined)
  const unit = glued ? glued[2] : (words[i + 1] ?? '').toLowerCase()
  const factor = units.find(([pattern]) => pattern.test(unit ?? ''))?.[1]
  if (amount !== undefined && factor !== undefined) {
    return { at: now.getTime() / 1000 + Number(amount) * factor, rest: words.slice(i + (glued ? 1 : 2)).join(' ') }
  }
  let day: Date | undefined
  const weekdays = ['su', 'mo', 'tu', 'we', 'th', 'fr', 'sa']
  const iso = first.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  const dotted = first.match(/^(\d{1,2})\.(\d{1,2})\.?$/)
  if (first === 'today' || first === 'heute') {
    day = new Date(now)
  } else if (first === 'tomorrow' || first === 'morgen') {
    day = new Date(now)
    day.setDate(day.getDate() + 1)
  } else if (iso) {
    day = new Date(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]))
  } else if (dotted) {
    day = new Date(now.getFullYear(), Number(dotted[2]) - 1, Number(dotted[1]))
  } else if (first.length >= 2 && weekdays.includes(first.slice(0, 2)) && /^[a-z]+$/.test(first)) {
    day = new Date(now)
    const ahead = (weekdays.indexOf(first.slice(0, 2)) - now.getDay() + 7) % 7 || 7
    day.setDate(day.getDate() + ahead)
  }
  if (day) {
    i++
  }
  // Filler before the time: "tomorrow at 14", "morgen um 9", "fr @ 16:30".
  if (['at', 'um', '@'].includes((words[i] ?? '').toLowerCase())) {
    i++
  }
  const time = (words[i] ?? '').match(/^(\d{1,2})(?::(\d{2}))?(?:h|uhr)?$/i)
  let hour = 9
  let minute = 0
  if (time && Number(time[1]) < 24) {
    hour = Number(time[1])
    minute = Number(time[2] ?? 0)
    i++
  } else if (!day) {
    return null
  }
  const at = new Date(day ?? now)
  at.setHours(hour, minute, 0, 0)
  if (!day && at.getTime() <= now.getTime()) {
    at.setDate(at.getDate() + 1)
  }

  return { at: at.getTime() / 1000, rest: words.slice(i).join(' ') }
}

// A pending reminder in the Next row: "today 14:30", "tomorrow 9:00", or "Fr 16.10. 9:00".
function whenLabel(at: number): string {
  const d = new Date(at * 1000)
  const time = `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
  const today = new Date()
  const tomorrow = new Date()
  tomorrow.setDate(today.getDate() + 1)
  if (d.toDateString() === today.toDateString()) {
    return `today ${time}`
  }
  if (d.toDateString() === tomorrow.toDateString()) {
    return `tomorrow ${time}`
  }

  return `${['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'][d.getDay()]} ${d.getDate()}.${d.getMonth() + 1}. ${time}`
}

// The profile on screen: the active one in ~/desk/profiles.json, read once and again after v.
async function deskProfile($: EngineInterface): Promise<DeskProfile> {
  const known = await read($, profile)
  if (known.data !== '' && Array.isArray(known.tabs) && Array.isArray(known.order) && typeof known.contacts === 'string' && typeof known.labels === 'object') {
    return known
  }
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await $.process.run(['python3', `${home}/desk/scripts/desk_profile.py`, 'active'], { timeoutMs: 10_000 }).catch(() => undefined)
  let found: DeskProfile = { name: 'work', label: 'Work', root: `${home}/desk`, data: `${home}/desk/data`, all: ['work'], labels: { work: 'Work' }, tabs: [], order: [], contacts: '' }
  try {
    const raw = JSON.parse(ran?.stdout ?? '') as Raw
    found = { name: text(raw.name), label: text(raw.label), root: text(raw.root), data: text(raw.data), all: (Array.isArray(raw.all) ? raw.all : []).map(text), tabs: (Array.isArray(raw.tabs) ? raw.tabs : []).map(text), order: (Array.isArray(raw.order) ? raw.order : []).map(text), contacts: text(raw.contacts),
      labels: Object.fromEntries(Object.entries((raw.labels ?? {}) as Raw).map(([k, v]) => [k, text(v)])) }
  } catch {
    // desk_profile.py failed: the default desk, as before profiles.
  }
  await update($, profile, () => found)
  return found
}

// Every process the pane starts gets the profile, so the scripts read and write the desk on screen.
async function runProc($: EngineInterface, argv: string[], options: { timeoutMs: number; env?: Record<string, string> }) {
  const { name } = await deskProfile($)
  return $.process.run(argv, { ...options, env: { ...options.env, DESK_PROFILE: name } })
}

// v: the next profile. Its desk replaces the list, the queue and the marks; the keys stay the same.
async function switchProfile($: EngineInterface) {
  const home = (await $.env.get('HOME')) ?? ''
  const ran = await runProc($, ['python3', `${home}/desk/scripts/desk_profile.py`, 'next'], { timeoutMs: 10_000 })
  if (ran.exitCode !== 0) {
    $.ui.toast(`Profile not switched: ${ran.stderr || ran.stdout}`.slice(0, 160))
    return
  }
  await update($, profile, () => NO_PROFILE)
  await update($, view, () => EMPTY)
  await update($, picked, () => '')
  await update($, filter, () => '')
  await update($, offset, () => 0)
  await update($, query, () => '')
  await update($, linking, () => '')
  await update($, lastUndo, () => '')
  await update($, brief, () => null)
  await update($, queued, () => ({}))
  await update($, queueChecked, () => false)
  await update($, seenAt, () => ({}))
  await update($, reminders, () => ({}))
  await update($, bumps, () => ({}))
  await update($, started, () => ({}))
  await update($, liveIds, () => [])
  await update($, contactsOn, () => false)
  await update($, contactList, () => [])
  await update($, contactPicked, () => '')
  await update($, contactKind, () => '')
  const now = await deskProfile($)
  await refresh($)
  await loadMarks($)
  $.ui.toast(`Desk: ${now.label}`)
  void gather($)
}

const PROFILE_COLORS = ['cyan', 'green', 'magenta', 'yellow']

// The contact controls before the list in the tree (drawn below it), for the arrow direction.
const CONTACT_BELOW_LIST = ['contact-open']

// The contacts of one kind tab: active first (contacted, talking), then lead, won, lost; by name inside a stage.
function shownContacts(all: readonly DeskContact[], kind: string): DeskContact[] {
  const rank = (c: DeskContact) => ({ talking: 0, contacted: 1, lead: 2, won: 3, lost: 4 })[c.stage] ?? 5
  return all.filter(c => kind === '' || c.kind === kind).sort((a, b) => rank(a) - rank(b) || (a.name || a.slug).localeCompare(b.name || b.slug))
}

function contactsOf(raw: string): DeskContact[] {
  let file: Raw = {}
  try {
    file = JSON.parse(raw) as Raw
  } catch {
    return []
  }
  const steps = (value: unknown) => ((Array.isArray(value) ? value : []) as Raw[]).map(one => ({ id: text(one.id), title: text(one.title) }))
  return ((Array.isArray(file.contacts) ? file.contacts : []) as Raw[]).map(c => ({
    id: text(c.id), slug: text(c.slug), name: text(c.name), kind: text(c.kind) || 'customer', company: text(c.company), role: text(c.role),
    email: text(c.email), phone: text(c.phone), stage: text(c.stage) || 'lead', source: text(c.source), since: text(c.since),
    path: text(c.path), notes: text(c.notes), mtime: Number(c.mtime) || 0, open: steps(c.open), done: steps(c.done),
  }))
}

// i: the contacts view and back. A profile without contacts has no view.
async function toggleContacts($: EngineInterface) {
  if ((await deskProfile($)).contacts === '') {
    return
  }
  await update($, editing, () => '')
  await update($, contactsOn, on => !on)
  await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
}

async function pickContactKind($: EngineInterface, kind: string) {
  await update($, contactKind, () => kind)
  const first = shownContacts(await read($, contactList), kind)[0]
  await update($, contactPicked, () => first?.id ?? '')
  await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
}

// a, t and g in the contacts view: contact.py writes the file, a gather adds the next steps and contacts.json.
async function saveContactEdit($: EngineInterface, kind: string, value: string) {
  const home = (await $.env.get('HOME')) ?? ''
  const script = `${home}/desk/scripts/contact.py`
  const people = shownContacts(await read($, contactList), await read($, contactKind))
  const pickedNow = await read($, contactPicked)
  const person = people.find(c => c.id === pickedNow) ?? people[0]
  let argv: string[] = []
  if (kind === 'contact-new') {
    // "Name, Company, kind": the kind is optional and defaults to the kind tab, else customer.
    const [name = '', company = '', given = ''] = value.split(',').map(part => part.trim())
    const tab = await read($, contactKind)
    const kindWord = CONTACT_KINDS.find(k => given !== '' && k.startsWith(given.toLowerCase())) ?? (tab || 'customer')
    argv = ['python3', script, 'add', name, '--kind', kindWord, ...(company !== '' ? ['--company', company] : [])]
  } else if (kind === 'contact-step' && person) {
    argv = ['python3', script, 'next', person.slug, value]
  } else if (kind === 'contact-stage' && person) {
    const word = value.toLowerCase()
    const stage = CONTACT_STAGES.find(st => st.startsWith(word))
    const relation = CONTACT_KINDS.find(k => k.startsWith(word))
    if (!stage && !relation) {
      $.ui.toast(`Unknown stage or kind: ${value}`)
      return
    }
    argv = stage ? ['python3', script, 'stage', person.slug, stage] : ['python3', script, 'set', person.slug, 'kind', relation ?? '']
  }
  if (argv.length === 0) {
    return
  }
  const ran = await runProc($, argv, { timeoutMs: 30_000 })
  if (ran.exitCode !== 0) {
    $.ui.toast(`Not saved: ${ran.stderr || ran.stdout}`.slice(0, 160))
    return
  }
  const added = /contact:(\S+) added/.exec(ran.stdout)?.[1]
  await gather($)
  if (added) {
    await update($, contactPicked, () => `contact:${added}`)
  }
  await $.ui.focus({ requestId: PANE, key: 'list' }).catch(() => undefined)
}

async function loadMarks($: EngineInterface) {
  const home = (await $.env.get('HOME')) ?? ''
  const raw = await $.fs.read(`${(await deskProfile($)).data}/marks.json`).catch(() => '')
  if (raw === '') {
    return
  }
  const file = JSON.parse(raw) as Raw
  const ids = file.marked
  await update($, marked, () => (Array.isArray(ids) ? ids.map(text) : []))
  const saved: Record<string, DeskReminder> = {}
  for (const [id, one] of Object.entries((file.reminders ?? {}) as Record<string, Raw>)) {
    saved[id] = { at: Number(one.at) || 0, text: text(one.text) }
  }
  await update($, reminders, () => saved)
  const moved: Record<string, { at: number; touch: number }> = {}
  for (const [id, one] of Object.entries((file.bumps ?? {}) as Record<string, Raw>)) {
    moved[id] = { at: Number(one.at) || 0, touch: Number(one.touch) || 0 }
  }
  await update($, bumps, () => moved)
  await update($, sentShot, () => Number(file.sentShot) || 0)
  const inQueue: Record<string, number> = {}
  for (const [id, at] of Object.entries((file.queued ?? {}) as Raw)) {
    inQueue[id] = Number(at) || 0
  }
  // Marks from before the queue go into it.
  for (const id of Array.isArray(ids) ? ids.map(text) : []) {
    inQueue[id] ??= Date.now() / 1000
  }
  await update($, queued, () => inQueue)
  const out: Record<string, number> = {}
  for (const [id, at] of Object.entries((file.outAt ?? {}) as Raw)) {
    out[id] = Number(at) || 0
  }
  await update($, seenAt, () => out)
  await update($, marked, () => [])
}

async function saveMarks($: EngineInterface) {
  const home = (await $.env.get('HOME')) ?? ''
  await $.fs.write(`${(await deskProfile($)).data}/marks.json`, `${JSON.stringify({ queued: await read($, queued), outAt: await read($, seenAt), reminders: await read($, reminders), bumps: await read($, bumps), sentShot: await read($, sentShot) })}\n`)
}

// An item is fresh when none of its sessions works and one of them changed in the last 10 minutes: Claude just idled.
function isIdleFresh(card: DeskCard, live: string[], times: Record<string, number>, seen: Record<string, number>): boolean {
  const now = Date.now() / 1000
  const opened = seen[card.id] ?? 0
  return !card.sessions.some(x => live.includes(x.id))
    && card.sessions.some(x => {
      const written = times[x.id] ?? x.mtime
      return now - written < IDLE_FRESH_S && written > opened
    })
}

// One color per project tab (set your own; a project without one stays plain).
const PROJECT_COLORS: Record<string, string> = {
  ACME: '#e5484d',
  GLOBEX: '#5b6cff',
  INITECH: '#ed6d00',
}

function listRow(args: { n: number; icon: string; project: string; age: string; touched: string; title: string; width: number; isNarrow: boolean }): string {
  const { n, icon, project, age, title, width, isNarrow } = args
  const num = String(n).padStart(3)
  // ⏰ is one character but two cells wide, so it gets two spaces, not three.
  const time = args.touched === '⏰' ? '  ⏰' : args.touched.padStart(4)
  // Each icon is two cells wide; the label pads on its own so the columns stay aligned.
  // The last touch sits at the right edge, so the eye finds it in the same column on every row.
  if (isNarrow) {
    // Cells: number 3, space, icon 2, space; then the title; then space and the time 4.
    const room = Math.max(10, width - 12)
    return `${num} ${icon} ${clip(title, room).padEnd(room)} ${time}`
  }
  // Cells: number 3, space, icon 2, space, project 8, space, age 4, two spaces; then the title; then space and the time 4.
  const room = Math.max(10, width - 27)

  return `${num} ${icon} ${project.padEnd(8)} ${age.padStart(4)}  ${clip(title, room).padEnd(room)} ${time}`
}

function daysSince(iso: string): string {
  const time = Date.parse(iso)
  if (Number.isNaN(time)) {
    return ''
  }

  return days((Date.now() - time) / 86_400_000)
}

function days(age: number): string {
  return age < 1 ? 'new' : `${Math.round(age)}d`
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

function oneLine(value: string): string {
  return value.replace(/\s*[\r\n]+\s*/g, ' ').trim()
}

function clip(value: string, n: number): string {
  return value.length <= n ? value : `${value.slice(0, n - 1)}…`
}
