// The kind of a piece of work, read from its words by the word table the plan carries: the same rule as
// Switchboard's router/resolve.py, which reads with providers/kinds.py. The words are read here, in memory, and
// nothing of them is kept or written.
//
// The rule: the first sentence of the text (a leading list mark dropped), at most 300 characters of it, in lower
// case, and at most the plan's `cut` of those. Each word or phrase of the table is looked for as whole words. The
// `light` kind is the answer only when no word of another kind is there; otherwise the earliest match wins, then
// the phrase with more words, then the longer phrase, then the kind's place in `order`. Nothing matched: "other".
// A kinds block that cannot be read gives no kind at all (null), and then nothing is done.

// providers/kinds.py's own names, in its order, and its light kind and cut. The plan's kinds are read in these
// names: its heavier kinds take the other names in the plan's order, its light kind "find", "other" itself.
const KINDS = ['build', 'fix', 'review', 'plan', 'find', 'write', 'other']
const SLOTS = ['build', 'fix', 'review', 'plan', 'write']
const LIGHT = 'find'
const OTHER = 'other'
const CUT = 300

export type Kinds = {
  // Each kind of the plan: its place in KINDS (the tie order), whether it is the light kind, and its phrases.
  kinds: { name: string; place: number; isLight: boolean; phrases: string[] }[]
  cut: number
}

// Python's whitespace (str.isspace) and word characters (str.isalnum or "_"), spelled out so JavaScript's own
// \s and \w, which differ from Python's, are not used.
const WS = '[\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]'
const WORD = '[\\p{L}\\p{N}_]'
const LEAD_WS = new RegExp('^' + WS + '+', 'u')
const MARK = new RegExp('^' + WS + '*(?:\\p{Nd}{1,3}[.)]|[-*\\u2022])' + WS + '+', 'u')
const END = new RegExp('[.!?](?=' + WS + '|$)|\\n', 'u')
const SPLIT = new RegExp(WS + '+', 'u')
const BLANK = new RegExp('^' + WS + '*$', 'u')

function isObj(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

// The plan's kinds block, or null when it cannot be read: an order of distinct names ending with "other", a light
// kind in it (not "other"), a whole cut from 1 to 300, and a table of the order's kinds, each a list of phrases
// that are not blank.
export function kindsOf(raw: unknown): Kinds | null {
  if (!isObj(raw)) return null
  const { order, light, cut, table } = raw
  if (!Array.isArray(order) || order.length === 0 || !order.every(k => typeof k === 'string')) return null
  if (new Set(order).size !== order.length || order[order.length - 1] !== OTHER) return null
  if (typeof light !== 'string' || !order.includes(light) || light === OTHER) return null
  if (typeof cut !== 'number' || !Number.isInteger(cut) || cut < 1 || cut > CUT || !isObj(table)) return null
  const heavy = (order as string[]).slice(0, -1).filter(k => k !== light)
  if (heavy.length > SLOTS.length) return null
  const slotOf = new Map<string, string>(heavy.map((k, i) => [k, SLOTS[i]!]))
  slotOf.set(light, LIGHT)
  slotOf.set(OTHER, OTHER)
  const kinds: Kinds['kinds'] = []
  for (const [name, phrases] of Object.entries(table)) {
    const slot = slotOf.get(name)
    if (slot === undefined || !Array.isArray(phrases)) return null
    if (!phrases.every(p => typeof p === 'string' && !BLANK.test(p))) return null
    kinds.push({ name, place: KINDS.indexOf(slot), isLight: slot === LIGHT, phrases: phrases as string[] })
  }
  kinds.sort((a, b) => a.place - b.place)
  return { kinds, cut }
}

// The text's first sentence in lower case, at most 300 characters ("" for no text).
export function firstSentence(text: unknown): string {
  let s = typeof text === 'string' ? text : text === null || text === undefined ? '' : String(text)
  s = s.replace(LEAD_WS, '').replace(MARK, '')
  // Only the start can count: CUT code points are at most 2 * CUT code units, and the end of a sentence found later
  // than that is past the cut. So a long paste is never copied whole.
  s = s.slice(0, 4 * CUT)
  const m = END.exec(s)
  const sentence = m ? s.slice(0, m.index) : s
  return Array.from(sentence).slice(0, CUT).join('').toLowerCase()
}

function escape(word: string): string {
  return word.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')
}

function wordsOf(phrase: string): string[] {
  return phrase.split(SPLIT).filter(w => w !== '')
}

const compiled = new Map<string, RegExp>()

function patternOf(phrase: string): RegExp {
  let rx = compiled.get(phrase)
  if (rx === undefined) {
    rx = new RegExp('(?<!' + WORD + ')' + wordsOf(phrase).map(escape).join(WS + '+') + '(?!' + WORD + ')', 'u')
    if (compiled.size < 2000) compiled.set(phrase, rx)
  }
  return rx
}

// [is light, place in the sentence, -words, -characters, place in KINDS]: the smaller wins, as in kinds.py.
type Rank = [number, number, number, number, number]

function less(a: Rank, b: Rank): boolean {
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return (a[i] as number) < (b[i] as number)
  }
  return false
}

// kinds.py's kind_of over a text.
function kindOfSentence(kinds: Kinds, text: string): string {
  const sentence = firstSentence(text)
  if (BLANK.test(sentence)) return OTHER
  let best: { rank: Rank; name: string } | undefined
  for (const kind of kinds.kinds) {
    for (const phrase of kind.phrases) {
      const m = patternOf(phrase).exec(sentence)
      if (m === null) continue
      const length = -Array.from(phrase).length
      const rank: Rank = [kind.isLight ? 1 : 0, m.index, -wordsOf(phrase).length, length, kind.place]
      if (best === undefined || less(rank, best.rank)) best = { rank, name: kind.name }
    }
  }
  return best === undefined ? OTHER : best.name
}

// The kind of one text by the plan's word table.
export function kindOfText(kinds: Kinds, text: unknown): string {
  let s = typeof text === 'string' ? text : ''
  if (kinds.cut < CUT) s = Array.from(firstSentence(s)).slice(0, kinds.cut).join('')
  return kindOfSentence(kinds, s)
}

// The kind of a helper's work: its type when the plan names it with a kind of its order, else its short
// description, and when that reads as "other", the first sentence of its task. Null when the kinds block cannot be
// read.
export function kindOfWork(
  rawKinds: unknown,
  types: unknown,
  helperType: unknown,
  description: unknown,
  task: unknown,
): string | null {
  const kinds = kindsOf(rawKinds)
  if (kinds === null) return null
  if (isObj(types) && typeof helperType === 'string' && Object.prototype.hasOwnProperty.call(types, helperType)) {
    const named = types[helperType]
    if (typeof named === 'string' && ((rawKinds as Record<string, unknown>).order as string[]).includes(named)) {
      return named
    }
  }
  const fromDescription = kindOfText(kinds, description)
  return fromDescription !== OTHER ? fromDescription : kindOfText(kinds, task)
}
