// The decisions the hooks act on, held without the engine: the cases a test cannot raise through the engine's own
// calls (a fork, a resume, a teammate, a workflow agent) and the edges of each answer.

import { describe, expect, test } from 'claude-code/testing'

import { atLines, decideChat, decideSpawn, effortOf, effortRecord, modelOf, spawnFacts, typeOf } from '../hooks/decide'
import { MADE_AT, plan } from './world'

const NOW = MADE_AT + 100
const TYPES = { Explore: 'find', Plan: 'plan' }

describe('which helpers are looked at', () => {
  test('a fork is never touched', () => {
    expect(spawnFacts({ subagentType: 'fork', fork: true }, TYPES)).toBe(undefined)
  })
  test('a resume is never touched', () => {
    expect(spawnFacts({ subagentType: 'Explore', fork: false, resume: { agentId: 'a1' } }, TYPES)).toBe(undefined)
  })
  test('a subagent is a helper of its type', () => {
    expect(spawnFacts({ subagentType: 'Explore', fork: false, model: 'opus', parentModel: 'p' }, TYPES)).toEqual({
      where: 'helper', type: 'Explore', callerModel: 'opus', parentModel: 'p' })
  })
  test('a teammate is a helper, recorded as a teammate', () => {
    expect(spawnFacts({ subagentType: 'any role', fork: false, isTeammate: true }, TYPES)).toMatchObject({
      where: 'helper', type: 'teammate' })
  })
  test('a workflow agent is recorded as one', () => {
    expect(spawnFacts({ subagentType: 'general-purpose', fork: false, workflow: { runId: 'wf_1', agentIndex: 1 } },
      TYPES)).toMatchObject({ where: 'workflow', type: 'workflow' })
  })
  test('a type of a person\'s or a plugin\'s own is recorded as other', () => {
    expect(typeOf('my-team-reviewer', TYPES)).toBe('other')
    expect(typeOf('general-purpose', TYPES)).toBe('general-purpose')
    expect(typeOf('Plan', TYPES)).toBe('Plan')
    expect(typeOf(42, TYPES)).toBe('other')
  })
})

describe('a helper starts', () => {
  const facts = { where: 'workflow' as const, type: 'workflow', callerModel: undefined, parentModel: 'opus-x' }

  test('a workflow agent on a route starts on the route model', () => {
    const change = decideSpawn(plan(), 'find', facts, NOW)
    expect(change).toMatchObject({ model: 'haiku', effort: 'medium' })
    expect(change!.rec).toMatchObject({ where: 'workflow', from_model: 'opus-x', did: 'model' })
  })
  test('no route: nothing, and nothing to record', () => {
    expect(decideSpawn(plan(), 'build', facts, NOW)).toBe(undefined)
  })
  test('a plan past its time: nothing, and nothing to record', () => {
    expect(decideSpawn(plan(), 'find', facts, MADE_AT + 181)).toBe(undefined)
  })
  test('every choice passed: nothing changes and the record says so', () => {
    const agents = { claude: { can: true, room: 'out', back_at: 5000 }, codex: { can: true, room: 'room' } }
    const change = decideSpawn(plan({ agents }), 'find', facts, NOW)
    expect(change).toEqual({ rec: expect.objectContaining({ did: 'none', reason: 'all_passed',
      passed: [['claude', 'out'], ['codex', 'hand_off']] }) })
  })
  test('a model word the engine would not take is not handed on, and nothing is recorded', () => {
    const routes = { find: [{ agent: 'claude', model: 'two words', effort: 'loud' }] }
    expect(decideSpawn(plan({ routes }), 'find', facts, NOW)).toBe(undefined)
  })
  test('a route choice that names neither a model nor an effort: nothing, and nothing to record', () => {
    const routes = { find: [{ agent: 'claude', model: null, effort: null }] }
    expect(decideSpawn(plan({ routes }), 'find', facts, NOW)).toBe(undefined)
  })
  test('a route to the other agent with the hand switch on: nothing, and nothing to record', () => {
    const p = plan({ hand: { on: true, write: false, max_s: 900 } })
    expect(decideSpawn(p, 'review', facts, NOW)).toBe(undefined)
    expect(decideSpawn({ ...p, dry: true }, 'review', facts, NOW)).toBe(undefined)
  })
  test('a caller model or a parent model that is not a model word is never written', () => {
    const named = decideSpawn(plan(), 'find', { ...facts, callerModel: 'my notes/plan' }, NOW)
    expect(named!.rec).toMatchObject({ did: 'kept', model: null, from_model: null, reason: 'caller_model' })
    const parent = decideSpawn(plan(), 'find', { ...facts, parentModel: 'two words' }, NOW)
    expect(parent!.rec).toMatchObject({ did: 'model', model: 'haiku', from_model: null })
  })
  test('an effort alone is kept for the helper even when the caller named a model', () => {
    const routes = { find: [{ agent: 'claude', model: null, effort: 'low' }] }
    const change = decideSpawn(plan({ routes }), 'find', { ...facts, callerModel: 'opus' }, NOW)
    expect(change).toMatchObject({ effort: 'low', rec: { did: 'effort', model: null } })
    expect(change!.model).toBe(undefined)
  })
})

describe('a turn in the chat', () => {
  const on = (effort: boolean, model: boolean) => plan({ chat: { effort, model_on_first_turn: model } })

  test('both chat switches off: nothing is looked at', () => {
    expect(decideChat(on(false, false), 'fix', { fromModel: 'opus-x', isFirstTurn: true }, NOW)).toBe(undefined)
  })
  test('the effort only, when only chat.effort is on', () => {
    expect(decideChat(on(true, false), 'fix', { fromModel: 'opus-x', isFirstTurn: true, effort: 'high' }, NOW))
      .toMatchObject({ effort: 'max', rec: { did: 'effort', model: null, effort: 'max' } })
  })
  test('the model only on the first turn', () => {
    expect(decideChat(on(false, true), 'fix', { fromModel: 'opus-x', isFirstTurn: true }, NOW)).toMatchObject({
      model: 'sonnet', rec: { did: 'model' } })
    // A later turn has nothing to change, so nothing is recorded ("none" means every choice was passed).
    expect(decideChat(on(false, true), 'fix', { fromModel: 'opus-x', isFirstTurn: false }, NOW)).toBe(undefined)
    expect(decideChat({ ...on(false, true), dry: true }, 'fix', { fromModel: 'opus-x', isFirstTurn: false }, NOW))
      .toBe(undefined)
  })
  test('a route effort on a step with no effort level: the effort is kept for later steps, nothing recorded', () => {
    const change = decideChat(on(true, false), 'fix', { fromModel: 'opus-x', isFirstTurn: true }, NOW)
    expect(change).toEqual({ effort: 'max', reason: 'route' })
  })
  test('every choice passed in the chat: nothing changes and the record says so', () => {
    const agents = { claude: { can: true, room: 'out', back_at: 5000 }, codex: { can: true, room: 'room' } }
    const change = decideChat(plan({ agents, chat: { effort: true, model_on_first_turn: false } }), 'find',
      { fromModel: 'opus-x', isFirstTurn: true, effort: 'high' }, NOW)
    expect(change).toEqual({ rec: expect.objectContaining({ where: 'chat', did: 'none', reason: 'all_passed' }) })
  })
  test('a dry plan changes nothing and records would', () => {
    const change = decideChat(plan({ dry: true, chat: { effort: true, model_on_first_turn: true } }), 'fix',
      { fromModel: 'opus-x', isFirstTurn: true }, NOW)
    expect(change).toEqual({ rec: expect.objectContaining({ did: 'would', model: 'sonnet', effort: 'max' }) })
  })
})

describe('a default, for work whose kind has no route', () => {
  const defaults = { workflow: { model: null, effort: 'xhigh' }, helper: { model: { claude: 'sonnet' }, effort: null },
    chat: { model: { claude: 'opus' }, effort: 'high' } }
  const workflow = { where: 'workflow' as const, type: 'workflow', callerModel: undefined, parentModel: 'opus-x' }
  const helper = { where: 'helper' as const, type: 'general-purpose', callerModel: undefined, parentModel: 'opus-x' }

  test('a workflow agent gets the effort, and its record waits for its first step', () => {
    const change = decideSpawn(plan({ defaults }), 'build', workflow, NOW)
    expect(change).toMatchObject({ effort: 'xhigh', reason: 'default' })
    expect([change!.model, change!.rec]).toEqual([undefined, undefined])
    expect(change!.later).toMatchObject({ where: 'workflow', kind: 'build', reason: 'default', why: null })
  })
  test('a helper gets the model, recorded at once', () => {
    const change = decideSpawn(plan({ defaults }), 'build', helper, NOW)
    expect(change).toMatchObject({ model: 'sonnet', reason: 'default',
      rec: { did: 'model', model: 'sonnet', effort: null, reason: 'default', why: null } })
    expect(change!.effort).toBe(undefined)
  })
  test('a default model set for the other agent is never applied here', () => {
    const other = { ...defaults, helper: { model: { codex: 'gpt-6-luna' }, effort: null } }
    expect(decideSpawn(plan({ defaults: other }), 'build', helper, NOW)).toBe(undefined)
    const bare = { ...defaults, helper: { model: 'sonnet', effort: null } }
    expect(decideSpawn(plan({ defaults: bare }), 'build', helper, NOW)).toBe(undefined)
  })
  test('a kind with a route takes the route, never the default', () => {
    expect(decideSpawn(plan({ defaults }), 'find', workflow, NOW)).toMatchObject({ model: 'haiku', effort: 'medium',
      reason: 'route', rec: { did: 'model', reason: 'route', why: 'your route for lookups' } })
  })
  test('a kind that could not be read takes the default', () => {
    expect(decideSpawn(plan({ defaults }), null, workflow, NOW)).toMatchObject({ effort: 'xhigh', reason: 'default' })
  })
  test('no default for where the work started: nothing, and nothing to record', () => {
    expect(decideSpawn(plan({ defaults: { chat: defaults.chat } }), 'build', workflow, NOW)).toBe(undefined)
    expect(decideSpawn(plan(), 'build', workflow, NOW)).toBe(undefined)
  })
  test('a model the caller named is kept from a default as from a route', () => {
    const change = decideSpawn(plan({ defaults }), 'build', { ...helper, callerModel: 'opus' }, NOW)
    expect(change).toEqual({ rec: expect.objectContaining({ did: 'kept', model: 'opus', reason: 'caller_model' }) })
  })
  test('a dry plan records what the default would do', () => {
    expect(decideSpawn(plan({ defaults, dry: true }), 'build', workflow, NOW)).toEqual({
      rec: expect.objectContaining({ did: 'would', effort: 'xhigh', reason: 'default' }) })
  })
  test('the effort record: lowered, or kept when the work was already at or under it', () => {
    const later = decideSpawn(plan({ defaults }), 'build', workflow, NOW)!.later!
    expect(effortRecord(later, 'max', 'xhigh', NOW + 5)).toMatchObject({ t: NOW + 5, did: 'effort', effort: 'xhigh',
      reason: 'default' })
    expect(effortRecord(later, 'xhigh', 'xhigh', NOW)).toMatchObject({ did: 'kept', effort: 'xhigh',
      reason: 'effort_lower' })
    expect(effortRecord(later, 'medium', 'xhigh', NOW)).toMatchObject({ did: 'kept', reason: 'effort_lower' })
    expect(effortRecord(later, undefined, 'xhigh', NOW)).toBe(undefined)
    expect(effortRecord(later, 3, 'xhigh', NOW)).toBe(undefined)
  })
  test('the chat: a default effort lowers the turn, or is recorded as kept', () => {
    const p = plan({ defaults, chat: { effort: true, model_on_first_turn: false } })
    expect(decideChat(p, 'build', { fromModel: 'opus-x', isFirstTurn: false, effort: 'max' }, NOW)).toMatchObject({
      effort: 'high', reason: 'default', rec: { where: 'chat', did: 'effort', effort: 'high', reason: 'default' } })
    expect(decideChat(p, 'build', { fromModel: 'opus-x', isFirstTurn: false, effort: 'medium' }, NOW)!.rec)
      .toMatchObject({ did: 'kept', effort: 'high', reason: 'effort_lower' })
  })
  test('the chat: the two chat switches decide for a default as for a route', () => {
    const off = plan({ defaults })
    expect(decideChat(off, 'build', { fromModel: 'opus-x', isFirstTurn: true, effort: 'max' }, NOW)).toBe(undefined)
    const modelOnly = plan({ defaults, chat: { effort: false, model_on_first_turn: true } })
    const first = decideChat(modelOnly, 'build', { fromModel: 'opus-x', isFirstTurn: true, effort: 'max' }, NOW)
    expect(first).toMatchObject({ model: 'opus', rec: { did: 'model', model: 'opus', effort: null } })
    expect(first!.effort).toBe(undefined)
    // A later turn sets no model, so there is nothing to change and nothing to record.
    expect(decideChat(modelOnly, 'build', { fromModel: 'opus-x', isFirstTurn: false, effort: 'max' }, NOW))
      .toBe(undefined)
  })
  test('the chat: a route raises the effort as well as lowering it', () => {
    const p = plan({ chat: { effort: true, model_on_first_turn: false } })
    expect(decideChat(p, 'fix', { fromModel: 'opus-x', isFirstTurn: false, effort: 'low' }, NOW)).toMatchObject({
      effort: 'max', rec: { did: 'effort', effort: 'max', reason: 'route' } })
  })
})

describe('the words the engine takes', () => {
  test('efforts', () => {
    expect(['low', 'medium', 'high', 'xhigh', 'max'].map(effortOf)).toEqual(['low', 'medium', 'high', 'xhigh', 'max'])
    expect([3, 'MAX', '', null].map(effortOf)).toEqual([undefined, undefined, undefined, undefined])
  })
  test('models', () => {
    expect(modelOf('claude-sonnet-4-5[1m]')).toBe('claude-sonnet-4-5[1m]')
    expect(modelOf('haiku')).toBe('haiku')
    expect([' haiku', 'a b', '', 'x'.repeat(101), null].map(modelOf)).toEqual([undefined, undefined, undefined,
      undefined, undefined])
  })
})

describe("the person's lines", () => {
  test('each window at or past its line, by the plan\'s names', () => {
    expect(atLines(plan(), [{ kind: 'five_hour', percentUsed: 90 }, { kind: 'seven_day', percentUsed: 96.5 },
      { kind: 'spend_limit', percentUsed: 120 }])).toEqual([['five_hour', 90], ['week', 96.5]])
  })
  test('no lines in the plan: none', () => {
    const agents = { claude: { can: true, room: 'room' } }
    expect(atLines(plan({ agents }), [{ kind: 'five_hour', percentUsed: 100 }])).toEqual([])
  })
})
