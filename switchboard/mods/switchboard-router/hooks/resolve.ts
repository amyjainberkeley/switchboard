// The rule every adapter applies to the plan (the same rule as Switchboard's router/resolve.py): where a piece of
// work of one kind runs, given the agent it was started in, how it was started (`where`) and the time. Pure: no
// file, no clock, no network. Anything it cannot read in the plan answers {"do": "nothing", "reason": "no_plan"},
// and the work goes on as if there were no plan.
//
// A kind with no route (or no kind at all) takes the plan's default for `where` when it names a model or an effort:
// {"do": "model", "reason": "default"}. A default's model is given per agent; only the entry for `here` is used. A
// default never hands work over. A route's answer says "reason": "route".

export type Passed = [string, string]

// How a piece of work was started: a workflow script's agent, any other helper or teammate, a turn of the chat.
export type Where = 'workflow' | 'helper' | 'chat'

export type Answer =
  | {
      do: 'nothing'
      reason: string
      kind: string | null
      passed: Passed[]
      why: string | null
      back_at?: number | null
    }
  | {
      do: 'model'
      model: string | null
      effort: string | null
      reason: 'route' | 'default'
      kind: string | null
      passed: Passed[]
      why: string | null
    }
  | {
      do: 'hand'
      to: string
      model: string | null
      effort: string | null
      reason: 'route'
      kind: string | null
      passed: Passed[]
      why: string | null
    }

type Obj = Record<string, unknown>

const WHY_NOT: readonly string[] = ['not_added', 'not_installed', 'signed_out', 'too_old', 'never']
const ROOMS: readonly string[] = ['room', 'out', 'unknown']
const WHERE: readonly string[] = ['workflow', 'helper', 'chat']
// A plan that says it is good for longer cannot be used; one made more than AHEAD_S after now is not current.
const MAX_GOOD_FOR_S = 3600
const AHEAD_S = 60
// Lowest first.
export const EFFORTS: readonly string[] = ['low', 'medium', 'high', 'xhigh', 'max']

function isObj(v: unknown): v is Obj {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function own(o: Obj, key: string): unknown {
  return Object.prototype.hasOwnProperty.call(o, key) ? o[key] : undefined
}

// Why a plan cannot be used now (`no_plan`, `old_plan`, `off`), or null when it can.
export function unusable(plan: unknown, now: number): string | null {
  if (!isObj(plan) || !isNum(plan.v) || plan.v !== 1) return 'no_plan'
  const hand = plan.hand
  if (!isNum(plan.made_at) || !isNum(plan.good_for_s) || typeof plan.on !== 'boolean' || !isObj(plan.agents) ||
      !isObj(plan.routes) || !isObj(hand) || typeof hand.on !== 'boolean') {
    return 'no_plan'
  }
  if (plan.good_for_s > MAX_GOOD_FOR_S) return 'no_plan'
  // A plan from the future (a clock set back, a hand-made file) is not current, however long it says it is good.
  if (!isNum(now) || now > plan.made_at + plan.good_for_s || plan.made_at > now + AHEAD_S) return 'old_plan'
  if (!plan.on) return 'off'
  return null
}

export function usable(plan: unknown, now: number): boolean {
  return unusable(plan, now) === null
}

type Choice = { agent: string; model: string | null; effort: string | null }

// This kind's choices; [] for no route; null for a route the rule cannot read.
function routeOf(routes: Obj, kind: unknown): Choice[] | null {
  if (typeof kind !== 'string') return []
  const got = own(routes, kind)
  if (got === undefined || got === null) return []
  if (!Array.isArray(got)) return null
  const out: Choice[] = []
  for (const c of got) {
    if (!isObj(c) || typeof c.agent !== 'string') return null
    const model = c.model ?? null
    const effort = c.effort ?? null
    if ((model !== null && typeof model !== 'string') || (effort !== null && typeof effort !== 'string')) return null
    out.push({ agent: c.agent, model, effort })
  }
  return out
}

// The plan's default for `where` as [model, effort], or null when there is none it can read or it names neither.
// A default's model names its agent ({"claude": "haiku"}): only the entry for `here` is read, so a model set for one
// agent is never given to another. A model that is not such an object (a bare word) cannot be read.
function defaultOf(plan: Obj, where: unknown, here: string): [string | null, string | null] | null {
  if (typeof where !== 'string' || !WHERE.includes(where)) return null
  const all = plan.defaults
  const got = isObj(all) ? own(all, where) : undefined
  if (!isObj(got)) return null
  let model = got.model ?? null
  if (isObj(model)) model = (typeof here === 'string' ? own(model, here) : undefined) ?? null
  else if (model !== null) return null
  const effort = got.effort ?? null
  if ((model !== null && typeof model !== 'string') || (effort !== null && typeof effort !== 'string')) return null
  if (model === null && effort === null) return null
  return [model, effort]
}

type Look = { code: string | null; room: string | null; back: number | null }

function look(agents: Obj, agent: string, here: string, handOn: boolean): Look {
  const a = own(agents, agent)
  if (!isObj(a)) return { code: 'not_added', room: null, back: null }
  if (a.can !== true) {
    const why = a.why_not
    return { code: typeof why === 'string' && WHY_NOT.includes(why) ? why : 'not_added', room: null, back: null }
  }
  const room = typeof a.room === 'string' && ROOMS.includes(a.room) ? a.room : 'unknown'
  if (room === 'out') return { code: 'out', room: null, back: isNum(a.back_at) ? a.back_at : null }
  if (agent !== here && !handOn) return { code: 'hand_off', room: null, back: null }
  return { code: null, room, back: null }
}

function answer(plan: unknown, kind: string | null, here: string, now: number, where: unknown): Answer {
  const notUsable = unusable(plan, now)
  if (notUsable !== null) return { do: 'nothing', reason: notUsable, kind, passed: [], why: null }
  const p = plan as Obj & { agents: Obj; routes: Obj; hand: Obj }
  const words = p.why
  const said = isObj(words) && typeof kind === 'string' ? own(words, kind) : undefined
  const why = typeof said === 'string' ? said : null
  const route = routeOf(p.routes, kind)
  if (route === null) return { do: 'nothing', reason: 'no_plan', kind, passed: [], why: null }
  if (route.length === 0) {
    const got = defaultOf(p, where, here)
    if (got !== null) {
      return { do: 'model', model: got[0], effort: got[1], reason: 'default', kind, passed: [], why: null }
    }
    return { do: 'nothing', reason: 'no_route', kind, passed: [], why }
  }
  const looked = route.map(c => look(p.agents, c.agent, here, p.hand.on === true))
  const passed: Passed[] = []
  const backs: number[] = []
  for (let i = 0; i < route.length; i++) {
    const c = route[i]!
    let { code } = looked[i]!
    const { room, back } = looked[i]!
    const roomLater = looked.slice(i + 1).some(l => l.code === null && l.room === 'room')
    if (code === null && room === 'unknown' && roomLater) code = 'unknown'
    if (code !== null) {
      passed.push([c.agent, code])
      if (back !== null) backs.push(back)
      continue
    }
    if (c.agent !== here) {
      return { do: 'hand', to: c.agent, model: c.model, effort: c.effort, reason: 'route', kind, passed, why }
    }
    if (c.model === null && c.effort === null) return { do: 'nothing', reason: 'stays', kind, passed, why }
    return { do: 'model', model: c.model, effort: c.effort, reason: 'route', kind, passed, why }
  }
  const back_at = backs.length > 0 ? Math.min(...backs) : null
  return { do: 'nothing', reason: 'all_passed', kind, passed, why, back_at }
}

export function resolve(
  plan: unknown,
  kind: string | null,
  here: string,
  now: number,
  where: Where | null = null,
): Answer {
  try {
    return answer(plan, kind, here, now, where)
  } catch {
    return { do: 'nothing', reason: 'no_plan', kind, passed: [], why: null }
  }
}

// The effort to set on work now running at `current`, or null to leave it (the same rule as router/resolve.py's
// apply_effort). A route's effort is set, up or down: the person chose it for that kind of work. A default's effort
// is a ceiling: work above it is lowered to it, work at or under it is left as someone chose it. Nothing is set on
// work with no effort, or when either word is not one of the five.
export function applyEffort(current: unknown, effort: unknown, reason: unknown): string | null {
  if (typeof current !== 'string' || typeof effort !== 'string') return null
  const now = EFFORTS.indexOf(current)
  const to = EFFORTS.indexOf(effort)
  if (now < 0 || to < 0) return null
  if (reason === 'route') return effort
  if (reason === 'default' && now > to) return effort
  return null
}
