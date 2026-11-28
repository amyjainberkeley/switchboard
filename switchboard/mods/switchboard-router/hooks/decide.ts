// What to do with one piece of work, from the plan and the rule: pure, so the tests can hold it to every case.
// Each answer says what to change (nothing, a model, an effort) and the line to record. A record never holds a word
// of a prompt or a description, a path, an email or an id.

import { isObj } from './plan'
import { applyEffort, resolve, type Passed, type Where } from './resolve'

export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

export type Rec = {
  t: number
  agent: 'claude'
  where: Where
  type: string | null
  kind: string | null
  did: 'model' | 'effort' | 'hand' | 'kept' | 'none' | 'would'
  from_model: string | null
  model: string | null
  effort: string | null
  to: string | null
  reason: string
  why: string | null
  passed: Passed[]
  plan: number
}

// What to do with a piece of work. `effort` is the effort the plan gives, applied at each step with applyEffort and
// `reason` (a route's is set, a default's is a ceiling). `rec` is recorded now; `later` is recorded at the first
// step that shows the work's own effort, for a default's effort that only that step can tell (lowered or kept).
export type Change = { model?: string; effort?: Effort; reason?: 'route' | 'default'; rec?: Rec; later?: Rec }

const EFFORTS: readonly string[] = ['low', 'medium', 'high', 'xhigh', 'max']
const MODEL = /^[A-Za-z0-9][A-Za-z0-9._:[\]-]{0,99}$/
const TYPE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/
// The one "nothing" answer that is recorded, as did "none": every choice on the route was passed. A route that names
// nothing to change ("stays"), a word the engine would not take, or a hand-over this plugin does not make yet
// changes nothing and is not recorded, since "none" would say no choice could take the work.
const RECORDED_NOTHING = 'all_passed'

export function effortOf(v: unknown): Effort | undefined {
  return typeof v === 'string' && EFFORTS.includes(v) ? (v as Effort) : undefined
}

export function modelOf(v: unknown): string | undefined {
  return typeof v === 'string' && MODEL.test(v) ? v : undefined
}

// A helper type as the record keeps it: Claude Code's general helper or a type the plan names, else "other". A type
// a person or a plugin defined may carry a project's or a person's name, so it is not written.
export function typeOf(v: unknown, types: unknown): string {
  if (typeof v !== 'string' || !TYPE.test(v)) return 'other'
  if (v === 'general-purpose') return v
  return isObj(types) && Object.prototype.hasOwnProperty.call(types, v) ? v : 'other'
}

function flag(plan: Record<string, unknown>, group: string, key: string): boolean {
  const g = plan[group]
  return isObj(g) && g[key] === true
}

function record(
  plan: Record<string, unknown>,
  nowS: number,
  where: Rec['where'],
  type: string | null,
  kind: string | null,
  fromModel: string | null,
  passed: Passed[],
  why: string | null,
): Rec {
  return {
    t: nowS,
    agent: 'claude',
    where,
    type,
    kind,
    did: 'none',
    from_model: fromModel,
    model: null,
    effort: null,
    to: null,
    reason: 'stays',
    why,
    passed,
    plan: plan.made_at as number,
  }
}

// A dry plan changes nothing and records what it would have done.
function dry(plan: Record<string, unknown>, change: Change): Change {
  if (plan.dry !== true || change.rec === undefined) return change
  return { rec: { ...change.rec, did: 'would' } }
}

// The record of a default's effort at the first step that shows the work's own effort: `did` effort when it was
// lowered, else `kept` with reason effort_lower (the work already ran at or under it).
export function effortRecord(later: Rec, current: unknown, effort: Effort, nowS: number): Rec | undefined {
  if (effortOf(current) === undefined) return undefined
  const to = applyEffort(current, effort, 'default')
  if (to !== null) return { ...later, t: nowS, did: 'effort', effort: to, reason: 'default' }
  return { ...later, t: nowS, did: 'kept', effort, reason: 'effort_lower' }
}

export type SpawnFacts = {
  where: 'helper' | 'workflow'
  type: string
  callerModel: string | undefined
  parentModel: string | undefined
}

// What agent.spawn carries that matters here (AgentSpawnInput's fields of the same names).
export type SpawnEvent = {
  subagentType: string
  model?: string
  parentModel?: string
  fork: boolean
  resume?: unknown
  isTeammate?: true
  workflow?: unknown
}

// The facts of a helper this plugin may act on, or undefined for a fork or a resume, which are never touched.
export function spawnFacts(e: SpawnEvent, types: unknown): SpawnFacts | undefined {
  if (e.fork === true || e.resume !== undefined) return undefined
  const isWorkflow = e.workflow !== undefined
  const type = isWorkflow ? 'workflow' : e.isTeammate === true ? 'teammate' : typeOf(e.subagentType, types)
  return { where: isWorkflow ? 'workflow' : 'helper', type, callerModel: e.model, parentModel: e.parentModel }
}

// A helper about to start (kind null: it could not be read). Undefined: leave it be and record nothing.
export function decideSpawn(plan: unknown, kind: string | null, facts: SpawnFacts, nowS: number): Change | undefined {
  const answer = resolve(plan, kind, 'claude', nowS, facts.where)
  if (answer.do === 'nothing' && answer.reason !== RECORDED_NOTHING) return undefined
  // Handing work to another agent is not done by this plugin yet: the helper starts as asked.
  if (answer.do === 'hand') return undefined
  const p = plan as Record<string, unknown>
  // A caller's model is its own text (a workflow script may pass anything): only a model word is written.
  const from = modelOf(facts.callerModel ?? facts.parentModel) ?? null
  const rec = record(p, nowS, facts.where, facts.type, kind, from, answer.passed, answer.why)
  if (answer.do === 'nothing') return dry(p, { rec: { ...rec, reason: answer.reason } })
  const model = modelOf(answer.model)
  const effort = effortOf(answer.effort)
  if (model === undefined && effort === undefined) return undefined
  if (model !== undefined && facts.callerModel !== undefined && !flag(p, 'helpers', 'force')) {
    return dry(p, { rec: { ...rec, did: 'kept', model: modelOf(facts.callerModel) ?? null, reason: 'caller_model' } })
  }
  const reason = answer.reason
  if (p.dry === true) {
    return { rec: { ...rec, did: 'would', model: model ?? null, effort: effort ?? null, reason } }
  }
  const change: Change = { reason }
  if (model !== undefined) change.model = model
  if (effort !== undefined) change.effort = effort
  if (reason === 'route') {
    change.rec = { ...rec, did: model !== undefined ? 'model' : 'effort', model: model ?? null,
      effort: effort ?? null, reason }
  } else if (model !== undefined) {
    // A default's model is recorded now. Its effort is a ceiling the helper's own steps may already be under, so it
    // has a record of its own at the first step that shows the helper's effort.
    change.rec = { ...rec, did: 'model', model, reason }
    if (effort !== undefined) change.later = { ...rec, model, reason }
  } else {
    change.later = { ...rec, reason }
  }
  return change
}

export type ChatFacts = { fromModel: string; isFirstTurn: boolean; effort?: unknown }

// The first step of a turn in the person's own chat (kind null: it could not be read). Undefined: leave it be and
// record nothing. At most one record per turn: the model when it is set, else the effort, else a default's effort
// kept; a turn that changes nothing has none, unless every choice on its route was passed.
export function decideChat(plan: unknown, kind: string | null, facts: ChatFacts, nowS: number): Change | undefined {
  if (!isObj(plan)) return undefined
  const effortOn = flag(plan, 'chat', 'effort')
  const modelOn = flag(plan, 'chat', 'model_on_first_turn')
  if (!effortOn && !modelOn) return undefined
  const answer = resolve(plan, kind, 'claude', nowS, 'chat')
  if (answer.do === 'nothing' && answer.reason !== RECORDED_NOTHING) return undefined
  if (answer.do === 'hand') return undefined
  const rec = record(plan, nowS, 'chat', null, kind, facts.fromModel, answer.passed, answer.why)
  if (answer.do === 'nothing') return dry(plan, { rec: { ...rec, reason: answer.reason } })
  const reason = answer.reason
  const effort = effortOn ? effortOf(answer.effort) : undefined
  const model = modelOn && facts.isFirstTurn ? modelOf(answer.model) : undefined
  // Nothing this turn can change (a later turn with only the model switch on, say): nothing to record.
  if (model === undefined && effort === undefined) return undefined
  if (plan.dry === true) {
    return { rec: { ...rec, did: 'would', model: model ?? null, effort: effort ?? null, reason } }
  }
  const change: Change = { reason }
  if (model !== undefined) change.model = model
  if (effort !== undefined) change.effort = effort
  const now = effort !== undefined ? applyEffort(facts.effort, effort, reason) : null
  if (model !== undefined) {
    change.rec = { ...rec, did: 'model', model, effort: now, reason }
  } else if (now !== null) {
    change.rec = { ...rec, did: 'effort', effort: now, reason }
  } else if (effort !== undefined && reason === 'default' && effortOf(facts.effort) !== undefined) {
    change.rec = { ...rec, did: 'kept', effort, reason: 'effort_lower' }
  }
  // Else the step shows no effort level to compare with: the effort is kept for the turn's later steps and nothing
  // is recorded.
  return change
}

// The window names of Claude Code's numbers, as the plan's `line` names them.
export const WINDOWS: Record<string, string> = { five_hour: 'five_hour', seven_day: 'week' }

export type Window = { kind: string; percentUsed: number }

// The windows whose used percent has reached the person's line for it: [window as the plan names it, percent].
export function atLines(plan: unknown, windows: readonly Window[]): [string, number][] {
  if (!isObj(plan) || !isObj(plan.agents)) return []
  const claude = plan.agents.claude
  if (!isObj(claude) || !isObj(claude.line)) return []
  const line = claude.line
  const out: [string, number][] = []
  for (const w of windows) {
    const name = Object.prototype.hasOwnProperty.call(WINDOWS, w.kind) ? WINDOWS[w.kind] : undefined
    if (name === undefined) continue
    const at = line[name]
    if (typeof at !== 'number' || !Number.isFinite(at) || typeof w.percentUsed !== 'number') continue
    if (w.percentUsed >= at) out.push([name, w.percentUsed])
  }
  return out
}
