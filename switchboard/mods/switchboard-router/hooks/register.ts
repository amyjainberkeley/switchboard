// Switchboard's router inside Claude Code. It reads one file, Switchboard's plan, and applies it with Claude Code's
// own levers: the model a helper starts on, and the effort (and on a chat's first turn, the model) of a step. It
// decides nothing the plan does not say. With no plan, an old plan, a plan it cannot read, or any error of its own,
// every hook passes the event on unchanged, as if the plugin were not there.
//
// It never refuses anything, never rewrites a prompt, draws nothing and registers no tool, command or agent. The
// words of a task are read in memory to find its kind and are never kept, written or sent.

import type { EngineInterface, Register } from 'claude-code'

import {
  atLines,
  decideChat,
  decideSpawn,
  effortOf,
  effortRecord,
  spawnFacts,
  type Change,
  type Effort,
  type Rec,
} from './decide'
import { kindOfText, kindOfWork, kindsOf } from './kinds'
import { isObj, newPlanCache, readPlan, routerDir, type PlanCache } from './plan'
import { addDecision, newWrites, sendSignal, type Writes } from './record'
import { applyEffort, usable } from './resolve'

// How many helpers' efforts are kept at once; the oldest goes first.
const MAX_HELPERS = 500

// The effort the plan gives a piece of work and why (a route's is set, a default's is a ceiling); `later` is the
// record still owed for a default's effort, written at the first step that shows the work's own effort.
type EffortRule = { effort: Effort; reason: 'route' | 'default'; later?: Rec }

type State = {
  cache: PlanCache
  writes: Writes
  // The effort rule for each helper this plugin started with one, by the helper's agent id. A rule lives as long as
  // its helper, a resumed one too (same agent id): it is applied only while a usable plan is there, but a route taken
  // out of the plan since the helper started does not take its rule away.
  helperEffort: Map<string, EffortRule>
  // The kind of the person's turn now running in the chat, read from its typed text at turn.start (null: the plan's
  // word table could not be read, so only a default can apply).
  chatTurn: { turnId: string; kind: string | null } | undefined
  // What the first step of that turn decided, for its later steps.
  chatStep: { turnId: string; rule?: EffortRule } | undefined
  // The model set on the chat's first turn. It is kept for the chat while every step shows the session's own model
  // as it was; the first step that shows another model drops it for the rest of the chat.
  chatModel: { from: string; to: string } | undefined
}

type Usable = { dir: string; plan: Record<string, unknown>; nowMs: number }

type StepFacts = {
  turnId: string
  index: number
  model: string
  effort?: unknown
  messageCount: number
  agentId?: string
}

type StepChange = { model?: string; effort?: Effort }

// The plan when it is usable now, with the folder and the time; undefined otherwise.
async function current($: EngineInterface, st: State): Promise<Usable | undefined> {
  const dir = await routerDir($)
  if (dir === undefined) return undefined
  const nowMs = await $.clock.now()
  const plan = await readPlan($, st.cache, dir, nowMs)
  if (plan === null || !usable(plan, nowMs / 1000)) return undefined
  return { dir, plan, nowMs }
}

function chatSwitches(plan: Record<string, unknown>): { effort: boolean; model: boolean } {
  const chat = isObj(plan.chat) ? plan.chat : {}
  return { effort: chat.effort === true, model: chat.model_on_first_turn === true }
}

function rememberEffort(st: State, agentId: string, rule: EffortRule): void {
  st.helperEffort.delete(agentId)
  st.helperEffort.set(agentId, rule)
  if (st.helperEffort.size > MAX_HELPERS) {
    const oldest = st.helperEffort.keys().next().value
    if (oldest !== undefined) st.helperEffort.delete(oldest)
  }
}

// Keeps the kind of the chat's typed text for the turn, only while the plan lets the chat's effort or model be set.
async function startTurn($: EngineInterface, st: State, turnId: string, text: string): Promise<void> {
  st.chatTurn = undefined
  const found = await current($, st)
  if (found === undefined) return
  const sw = chatSwitches(found.plan)
  const kinds = kindsOf(found.plan.kinds)
  // A kinds block that cannot be read gives no kind: only the plan's default for the chat can apply then.
  if (sw.effort || sw.model) st.chatTurn = { turnId, kind: kinds !== null ? kindOfText(kinds, text) : null }
}

// What one step of a turn should change, or undefined for nothing.
async function stepChange($: EngineInterface, st: State, e: StepFacts): Promise<StepChange | undefined> {
  const found = await current($, st)
  if (found === undefined || found.plan.dry === true) return undefined
  // A model with no effort levels carries no effort; none is added to it (applyEffort sets nothing then).
  if (e.agentId !== undefined) {
    const rule = st.helperEffort.get(e.agentId)
    if (rule === undefined) return undefined
    if (rule.later !== undefined) {
      const rec = effortRecord(rule.later, e.effort, rule.effort, found.nowMs / 1000)
      if (rec !== undefined) {
        rule.later = undefined
        await addDecision($, found.dir, found.nowMs, rec)
      }
    }
    const effort = effortOf(applyEffort(e.effort, rule.effort, rule.reason))
    return effort !== undefined ? { effort } : undefined
  }
  const sw = chatSwitches(found.plan)
  // A chat's first request carries one message: the person's first prompt. A new chat (after /clear, say) keeps
  // nothing of the model set for the one before.
  const isFirstTurn = e.index === 0 && e.messageCount === 1
  if (isFirstTurn) st.chatModel = undefined
  if (e.index === 0 && st.chatTurn !== undefined && st.chatTurn.turnId === e.turnId) {
    st.chatStep = { turnId: e.turnId }
    const facts = { fromModel: e.model, isFirstTurn, effort: e.effort }
    const change = decideChat(found.plan, st.chatTurn.kind, facts, found.nowMs / 1000)
    if (change !== undefined) {
      if (change.effort !== undefined && change.reason !== undefined) {
        st.chatStep.rule = { effort: change.effort, reason: change.reason }
      }
      if (change.model !== undefined) st.chatModel = { from: e.model, to: change.model }
      if (change.rec !== undefined) await addDecision($, found.dir, found.nowMs, change.rec)
    }
  }
  const out: StepChange = {}
  const step = st.chatStep
  if (sw.effort && step !== undefined && step.turnId === e.turnId && step.rule !== undefined) {
    const effort = effortOf(applyEffort(e.effort, step.rule.effort, step.rule.reason))
    if (effort !== undefined) out.effort = effort
  }
  // A hook's change lasts one step, and each step comes with the session's own model, so the first turn's model is
  // set again on every later step. Once a step shows any other model (the person used /model, even back to the
  // first one, or Claude Code fell back), the chat's model is theirs: it is never changed again in this chat.
  if (st.chatModel !== undefined && e.model !== st.chatModel.from) st.chatModel = undefined
  if (sw.model && st.chatModel !== undefined) out.model = st.chatModel.to
  return out.model === undefined && out.effort === undefined ? undefined : out
}

export const register: Register = on => {
  const st: State = {
    cache: newPlanCache(),
    writes: newWrites(),
    helperEffort: new Map(),
    chatTurn: undefined,
    chatStep: undefined,
    chatModel: undefined,
  }

  // A helper starts: a subagent, a teammate or a workflow agent. Never a fork, never a resume.
  on('agent.spawn', async ($, e, next) => {
    let found: Usable | undefined
    let change: Change | undefined
    try {
      // A fork or a resume is left before the plan is even read.
      if (spawnFacts(e, undefined) === undefined) return next(e)
      found = await current($, st)
      if (found === undefined) return next(e)
      const facts = spawnFacts(e, found.plan.types)
      if (facts === undefined) return next(e)
      // A kind that cannot be read is null: only the plan's default for where the helper started can apply.
      const kind = kindOfWork(found.plan.kinds, found.plan.types, e.subagentType, e.description, e.prompt)
      change = decideSpawn(found.plan, kind, facts, found.nowMs / 1000)
    } catch {
      return next(e)
    }
    if (change === undefined || found === undefined) return next(e)
    const result = await (change.model !== undefined ? next({ ...e, model: change.model }) : next(e))
    try {
      if (result.deny === undefined) {
        if (change.effort !== undefined && change.reason !== undefined && result.agentId !== undefined) {
          const rule: EffortRule = { effort: change.effort, reason: change.reason }
          if (change.later !== undefined) rule.later = change.later
          rememberEffort(st, result.agentId, rule)
        }
        if (change.rec !== undefined) await addDecision($, found.dir, found.nowMs, change.rec)
      }
    } catch {
      // The helper has started as decided; only the record is lost.
    }
    return result
  }).catch(($, e, next) => next(e))

  // A turn starts. Only the person's own chat is looked at here; the typed text itself is not kept.
  on('turn.start', async ($, e, next) => {
    try {
      if (e.agentId === undefined) await startTurn($, st, e.turnId, e.text)
    } catch {
      st.chatTurn = undefined
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  // A step of a turn: the request about to go to the model. Every chunk is passed on untouched.
  on('turn.step', async function* ($, e, next) {
    let change: StepChange | undefined
    try {
      change = await stepChange($, st, e)
    } catch {
      change = undefined
    }
    if (change === undefined) return yield* next(e)
    return yield* next({ ...e, ...change })
  })

  // Claude Code's own numbers: when a window's used percent reaches the person's line for it, one signal.
  on('session.measure', async ($, e, next) => {
    try {
      const found = await current($, st)
      if (found !== undefined && found.plan.dry !== true) {
        for (const [window, pct] of atLines(found.plan, e.rateLimits)) {
          await sendSignal($, st.writes, found.dir, found.nowMs, { what: 'at_line', window, pct })
        }
      }
    } catch {
      // Nothing is sent.
    }
    return next(e)
  }).catch(($, e, next) => next(e))
}
