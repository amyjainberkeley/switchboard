// The plugin's hooks against the engine's own host, with a made-up disk beneath: what a helper starts on, what a
// step asks for, what is recorded, what is signalled, and that every hook does nothing without a usable plan.

import type { AgentSpawnInput, EngineInterface, TurnStepInput } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import {
  DECIDED,
  DECISIONS,
  DIR,
  MADE_AT,
  PLAN,
  decisions,
  engineSpawn,
  engineStep,
  plan,
  world,
  written,
} from './world'

async function step($: Engine, input: TurnStepInput) {
  const stream = $.turn.step(input)
  const chunks: unknown[] = []
  for (;;) {
    const got = await stream.next()
    if (got.done) return { chunks, result: got.value }
    chunks.push(got.value)
  }
}

// A helper start as the engine raises it: the whole event, with made-up values where the test says nothing.
function spawn($: Engine, over: Partial<AgentSpawnInput>) {
  return $.agent.spawn({
    tool_use_id: 'toolu_made_up',
    prompt: '',
    description: '',
    subagentType: 'general-purpose',
    provider: { plugin: 'engine', tier: 'core' },
    parentModel: 'parent-model',
    background: false,
    fork: false,
    ...over,
  })
}

const TASK = {
  prompt: 'Find where the settings are read.',
  description: 'Look around',
  subagentType: 'general-purpose',
}

describe('no usable plan: every hook passes the event on and nothing is written', () => {
  const plans: [string, Record<string, unknown> | string | null, number?][] = [
    ['no plan file', null],
    ['a plan past its time', plan(), MADE_AT + 181],
    ['a plan that is not JSON', '{"v": 1, "made_at": '],
    ['a plan of another v', plan({ v: 2 })],
    ['a plan the person turned off', plan({ on: false })],
    ['a plan over 16 KB', JSON.stringify({ ...plan(), padding: 'x'.repeat(17000) })],
    // Under 16,384 characters, over 16,384 bytes: the limit is in bytes, as the engine's read is.
    ['a plan over 16 KB in bytes but not in characters', JSON.stringify({ ...plan(), padding: '\u00e9'.repeat(9000) })],
    ['a plan that is a list', '[1, 2]'],
  ]
  for (const [name, p, nowS] of plans) {
    test(name, async ($, on) => {
      const { disk } = world(on, { plan: p, nowS })
      const spawned = engineSpawn(on)
      const stepped = engineStep(on)
      await spawn($, { ...TASK, subagentType: 'Explore' })
      await $.turn.start({ turnId: 't1', text: 'fix the crash' })
      const { chunks } = await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
      const rateLimits = [{ kind: 'five_hour', percentUsed: 99 }]
      await $.session.measure({ context: { window: 200000 } as never, rateLimits,
        changed: ['rateLimits'] })
      expect(spawned).toEqual([{ model: undefined, subagentType: 'Explore' }])
      expect(stepped).toEqual([{ model: 'opus-x', effort: 'high', agentId: undefined }])
      expect(chunks.length).toBe(2)
      expect(disk.writes).toEqual([])
    })
  }

  test('a plan of exactly 16,384 bytes, many of them in two-byte characters, is used', async ($, on) => {
    const base = new TextEncoder().encode(JSON.stringify({ ...plan(), padding: '' })).length
    const room = 16384 - base
    const text = JSON.stringify({ ...plan(), padding: 'x'.repeat(room % 2) + '\u00e9'.repeat(Math.floor(room / 2)) })
    expect(new TextEncoder().encode(text).length).toBe(16384)
    expect(text.length).toBeLessThan(16384)
    world(on, { plan: text })
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    expect(spawned[0]!.model).toBe('haiku')
  })

  test('no data folder that is a full path: nothing is read', async ($, on) => {
    const { disk } = world(on, { env: { SWITCHBOARD_HOME: 'relative/folder' } })
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    expect(spawned[0]!.model).toBe(undefined)
    expect(disk.reads).toEqual([])
  })

  test('a plan that cannot be read is passed over', async ($, on) => {
    // Registered first, so it answers above the made-up disk.
    on('fs.read', { path: PLAN }, () => ({ deny: 'made-up refusal' }))
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    expect(spawned[0]!.model).toBe(undefined)
    expect(disk.writes).toEqual([])
  })
})

describe('a helper starts', () => {
  test('its kind from its type: it starts on the route model and the decision is recorded', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    const answer = await spawn($, { ...TASK, subagentType: 'Explore' })
    expect(spawned[0]!.model).toBe('haiku')
    expect(answer.model).toBe('haiku')
    const recs = decisions(disk)
    expect(recs.length).toBe(1)
    expect(recs[0]).toEqual({
      t: MADE_AT + 100,
      agent: 'claude',
      where: 'helper',
      type: 'Explore',
      kind: 'find',
      did: 'model',
      from_model: 'parent-model',
      model: 'haiku',
      effort: 'medium',
      to: null,
      reason: 'route',
      why: 'your route for lookups',
      passed: [],
      plan: MADE_AT,
    })
  })

  test('its kind from its task when the description reads as other', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { prompt: 'Fix the crash on start. Then rest.', description: 'Quick job',
      subagentType: 'general-purpose' })
    expect(spawned[0]!.model).toBe('sonnet')
    expect(decisions(disk)[0]!.kind).toBe('fix')
  })

  test('a model the caller named is kept, and recorded as kept', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore', model: 'opus' })
    expect(spawned[0]!.model).toBe('opus')
    expect(decisions(disk)[0]).toMatchObject({ did: 'kept', model: 'opus', reason: 'caller_model' })
  })

  test('with helpers.force, the route model wins over the caller\'s', async ($, on) => {
    world(on, { plan: plan({ helpers: { force: true } }) })
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore', model: 'opus' })
    expect(spawned[0]!.model).toBe('haiku')
  })

  test('a route to the other agent: the helper starts as asked and nothing is recorded', async ($, on) => {
    // The plugin does not hand work over yet. The other agent could take it, so no record says nothing could.
    const { disk } = world(on, { plan: plan({ hand: { on: true, write: false, max_s: 900 } }) })
    const spawned = engineSpawn(on)
    await spawn($, { prompt: 'Review the patch.', description: 'Review', subagentType: 'general-purpose' })
    expect(spawned[0]!.model).toBe(undefined)
    expect(decisions(disk)).toEqual([])
  })

  test('with the hand switch off, a route that starts with the other agent goes on to this one', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { prompt: 'Review the patch.', description: 'Review', subagentType: 'general-purpose' })
    expect(spawned[0]!.model).toBe('sonnet')
    expect(decisions(disk)[0]).toMatchObject({ did: 'model', passed: [['codex', 'hand_off']] })
  })

  test('a kind with no route: nothing is changed and nothing is recorded', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { prompt: 'Add a flag.', description: 'Add a flag', subagentType: 'general-purpose' })
    expect(spawned[0]!.model).toBe(undefined)
    expect(disk.writes).toEqual([])
  })

  test('a dry plan changes nothing and records what it would have done', async ($, on) => {
    const { disk } = world(on, { plan: plan({ dry: true }) })
    const spawned = engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, { ...TASK, subagentType: 'Explore' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 3, agentId: answer.agentId })
    expect(spawned[0]!.model).toBe(undefined)
    expect(stepped[0]!.effort).toBe('high')
    expect(decisions(disk)[0]).toMatchObject({ did: 'would', model: 'haiku', effort: 'medium', reason: 'route' })
  })

  test('a spawn the engine refuses records nothing', async ($, on) => {
    const { disk } = world(on)
    on('agent.spawn', () => ({ deny: 'made-up refusal' }))
    const answer = await spawn($, { ...TASK, subagentType: 'Explore' })
    expect(answer.deny).toBe('made-up refusal')
    expect(disk.writes).toEqual([])
  })

  test('each decision is its own whole file, and decisions.jsonl is neither read nor written', async ($, on) => {
    const { disk, clock } = world(on, { files: { [DECISIONS]: '{"t": 1}' } })
    engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    await spawn($, { ...TASK, subagentType: 'Explore' })
    await clock.advance(1)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    const names = Object.keys(disk.files).filter(p => p.startsWith(DECIDED)).sort()
    expect(names.length).toBe(3)
    for (const name of names) {
      expect(name.slice(DECIDED.length)).toMatch(/^[0-9]{13}-[0-9a-f]{8}\.json$/)
      expect(JSON.parse(disk.files[name]!)).toMatchObject({ did: 'model', model: 'haiku' })
    }
    expect(names[0]!.slice(DECIDED.length, DECIDED.length + 13)).toBe('0000001100000')
    expect(names[2]!.slice(DECIDED.length, DECIDED.length + 13)).toBe('0000001100001')
    expect(new Set(names).size).toBe(3)
    expect(disk.files[DECISIONS]).toBe('{"t": 1}')
    expect(disk.reads.filter(p => p === DECISIONS)).toEqual([])
    expect(disk.writes.filter(p => p === DECISIONS)).toEqual([])
  })

  test('a kinds block that cannot be read gives no kind: nothing is changed or recorded', async ($, on) => {
    const kinds = { order: ['find', 'build'], light: 'find', cut: 300, table: {} }
    const { disk } = world(on, { plan: plan({ kinds, chat: { effort: true, model_on_first_turn: true } }) })
    const spawned = engineSpawn(on)
    const stepped = engineStep(on)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    await $.turn.start({ turnId: 't1', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    expect(spawned[0]!.model).toBe(undefined)
    expect(stepped[0]).toEqual({ model: 'opus-x', effort: 'high', agentId: undefined })
    expect(disk.writes).toEqual([])
  })

  test('a fork is never touched and nothing is recorded', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'fork', fork: true, tool_use_id: '' })
    expect(spawned[0]!.model).toBe(undefined)
    expect(disk.writes).toEqual([])
    expect(disk.reads).toEqual([])
  })

  test('a resume is never touched and nothing is recorded', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore', resume: { agentId: 'helper-9' } })
    expect(spawned[0]!.model).toBe(undefined)
    expect(disk.writes).toEqual([])
  })

  test('a teammate starts on the route model and is recorded as a teammate', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'a role of the team', isTeammate: true, background: true })
    expect(spawned[0]!.model).toBe('haiku')
    expect(decisions(disk)[0]).toMatchObject({ where: 'helper', type: 'teammate', kind: 'find' })
  })

  test('a workflow agent starts on the route model and is recorded as a workflow agent', async ($, on) => {
    const { disk } = world(on)
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, workflow: { runId: 'wf_made_up', agentIndex: 1 } })
    expect(spawned[0]!.model).toBe('haiku')
    expect(decisions(disk)[0]).toMatchObject({ where: 'workflow', type: 'workflow', kind: 'find' })
  })
})

describe('a step of a turn', () => {
  test('in a helper the plugin started on a route: that effort on each step, every chunk untouched', async ($, on) => {
    world(on)
    engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, { ...TASK, subagentType: 'Explore' })
    const agentId = answer.agentId!
    const first = await step($, { turnId: 'h1', index: 0, model: 'haiku', effort: 'high', messageCount: 1, agentId })
    await step($, { turnId: 'h1', index: 1, model: 'haiku', effort: 'high', messageCount: 3, agentId })
    expect(stepped).toEqual([
      { model: 'haiku', effort: 'medium', agentId },
      { model: 'haiku', effort: 'medium', agentId },
    ])
    expect(first.chunks).toEqual([
      { kind: 'text', index: 0, text: 'made-up answer' },
      { kind: 'stop', stopReason: 'end_turn', usage: null },
    ])
    expect(first.result.answer).toBe('made-up answer')
  })

  test('a model that takes no effort is given none', async ($, on) => {
    world(on)
    engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, { ...TASK, subagentType: 'Explore' })
    await step($, { turnId: 'h1', index: 0, model: 'haiku', messageCount: 1, agentId: answer.agentId })
    expect(stepped[0]!.effort).toBe(undefined)
  })

  test('another helper is left as it is', async ($, on) => {
    world(on)
    const stepped = engineStep(on)
    await step($, { turnId: 'h1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1, agentId: 'someone-else' })
    expect(stepped[0]!.effort).toBe('high')
  })

  test('in the chat with chat.effort off: nothing changes and nothing is recorded', async ($, on) => {
    const { disk } = world(on)
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    expect(stepped[0]).toEqual({ model: 'opus-x', effort: 'high', agentId: undefined })
    expect(disk.writes).toEqual([])
  })

  test('in the chat with chat.effort on: the route effort for the typed text, on each step', async ($, on) => {
    const { disk } = world(on, { plan: plan({ chat: { effort: true, model_on_first_turn: false } }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    await step($, { turnId: 't1', index: 1, model: 'opus-x', effort: 'high', messageCount: 3 })
    expect(stepped).toEqual([
      { model: 'opus-x', effort: 'max', agentId: undefined },
      { model: 'opus-x', effort: 'max', agentId: undefined },
    ])
    const recs = decisions(disk)
    expect(recs.length).toBe(1)
    expect(recs[0]).toMatchObject({ where: 'chat', type: null, kind: 'fix', did: 'effort', from_model: 'opus-x',
      model: null, effort: 'max', reason: 'route' })
  })

  test('in the chat, the model on the first turn only when model_on_first_turn is on', async ($, on) => {
    const { disk, clock } = world(on, { plan: plan({ chat: { effort: false, model_on_first_turn: true } }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    await step($, { turnId: 't1', index: 1, model: 'opus-x', effort: 'high', messageCount: 3 })
    // Decision files are in time order by name; within one millisecond their order is not kept.
    await clock.advance(1)
    // A later turn of another kind keeps the chat's model: it is not changed after the first turn.
    await $.turn.start({ turnId: 't2', text: 'Find where the settings are read.' })
    await step($, { turnId: 't2', index: 0, model: 'opus-x', effort: 'high', messageCount: 5 })
    expect(stepped.map(s => [s.model, s.effort])).toEqual([
      ['sonnet', 'high'],
      ['sonnet', 'high'],
      ['sonnet', 'high'],
    ])
    // The later turn changed nothing, so it has no record: "none" means every choice was passed.
    expect(decisions(disk).map(r => [r.did, r.model])).toEqual([['model', 'sonnet']])
  })

  test('a new chat keeps nothing of the model set for the chat before it', async ($, on) => {
    world(on, { plan: plan({ chat: { effort: false, model_on_first_turn: true } }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    // After /clear: the first turn of a new chat, of a kind with no route.
    await $.turn.start({ turnId: 't2', text: 'Add a flag.' })
    await step($, { turnId: 't2', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    expect(stepped.map(s => s.model)).toEqual(['sonnet', 'opus-x'])
  })

  test('in the chat, a turn after the first does not set the model', async ($, on) => {
    world(on, { plan: plan({ chat: { effort: false, model_on_first_turn: true } }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't3', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't3', index: 0, model: 'opus-x', effort: 'high', messageCount: 7 })
    expect(stepped[0]!.model).toBe('opus-x')
  })

  test('in the chat, a model the person switched to is left alone', async ($, on) => {
    world(on, { plan: plan({ chat: { effort: false, model_on_first_turn: true } }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    await $.turn.start({ turnId: 't2', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't2', index: 0, model: 'other-model', effort: 'high', messageCount: 5 })
    expect(stepped.map(s => s.model)).toEqual(['sonnet', 'other-model'])
  })

  test('in the chat, a person who switches back to the first model keeps it', async ($, on) => {
    world(on, { plan: plan({ chat: { effort: false, model_on_first_turn: true } }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    await $.turn.start({ turnId: 't2', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't2', index: 0, model: 'other-model', effort: 'high', messageCount: 5 })
    // The person picks the chat's first model again (/model): it is theirs now, never changed back.
    await $.turn.start({ turnId: 't3', text: 'Fix the crash in the parser.' })
    await step($, { turnId: 't3', index: 0, model: 'opus-x', effort: 'high', messageCount: 7 })
    await step($, { turnId: 't3', index: 1, model: 'opus-x', effort: 'high', messageCount: 9 })
    expect(stepped.map(s => s.model)).toEqual(['sonnet', 'other-model', 'opus-x', 'opus-x'])
  })

  test('the plan is read again after 15 seconds, not at every step', async ($, on) => {
    const { disk, clock } = world(on, { plan: null })
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    disk.files[PLAN] = JSON.stringify(plan({ made_at: MADE_AT + 20 }))
    await clock.advance(10000)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    await clock.advance(6000)
    await spawn($, { ...TASK, subagentType: 'Explore' })
    expect(spawned.map(s => s.model)).toEqual([undefined, undefined, 'haiku'])
    expect(disk.reads.filter(p => p === PLAN).length).toBe(1)
  })
})

describe('a default, for work whose kind has no route', () => {
  const defaults = { workflow: { model: null, effort: 'xhigh' }, helper: { model: null, effort: 'medium' },
    chat: { model: null, effort: 'high' } }
  const BUILD = { prompt: 'Add a flag.', description: 'Add a flag', subagentType: 'general-purpose' }
  const WORKFLOW = { ...BUILD, workflow: { runId: 'wf_made_up', agentIndex: 1 } }

  test('a workflow agent at max runs at the default effort on every step, recorded once', async ($, on) => {
    const { disk } = world(on, { plan: plan({ defaults }) })
    const spawned = engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, WORKFLOW)
    const agentId = answer.agentId!
    expect(spawned[0]!.model).toBe(undefined)
    expect(decisions(disk)).toEqual([])
    await step($, { turnId: 'w1', index: 0, model: 'opus-x', effort: 'max', messageCount: 1, agentId })
    await step($, { turnId: 'w1', index: 1, model: 'opus-x', effort: 'max', messageCount: 3, agentId })
    expect(stepped.map(s => s.effort)).toEqual(['xhigh', 'xhigh'])
    const recs = decisions(disk)
    expect(recs.length).toBe(1)
    expect(recs[0]).toMatchObject({ where: 'workflow', type: 'workflow', kind: 'build', did: 'effort', model: null,
      effort: 'xhigh', reason: 'default', why: null, passed: [] })
  })

  test('a workflow agent at or under the default is left as it is, and recorded as kept once', async ($, on) => {
    const { disk } = world(on, { plan: plan({ defaults }) })
    engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, WORKFLOW)
    const agentId = answer.agentId!
    await step($, { turnId: 'w1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1, agentId })
    await step($, { turnId: 'w1', index: 1, model: 'opus-x', effort: 'high', messageCount: 3, agentId })
    expect(stepped.map(s => s.effort)).toEqual(['high', 'high'])
    expect(decisions(disk).map(r => [r.did, r.reason, r.effort])).toEqual([['kept', 'effort_lower', 'xhigh']])
  })

  test('a step with no effort level sets nothing and records nothing yet', async ($, on) => {
    const { disk } = world(on, { plan: plan({ defaults }) })
    engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, WORKFLOW)
    await step($, { turnId: 'w1', index: 0, model: 'opus-x', messageCount: 1, agentId: answer.agentId })
    await step($, { turnId: 'w1', index: 1, model: 'opus-x', effort: 7, messageCount: 3, agentId: answer.agentId })
    expect(stepped.map(s => s.effort)).toEqual([undefined, 7])
    expect(decisions(disk)).toEqual([])
  })

  test('the default is by where the work started: a helper gets the helpers\' one', async ($, on) => {
    const { disk } = world(on, { plan: plan({ defaults }) })
    engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, BUILD)
    await step($, { turnId: 'h1', index: 0, model: 'opus-x', effort: 'max', messageCount: 1, agentId: answer.agentId })
    expect(stepped[0]!.effort).toBe('medium')
    expect(decisions(disk)[0]).toMatchObject({ where: 'helper', did: 'effort', effort: 'medium', reason: 'default' })
  })

  test('a default with a model and an effort records the model now, the effort at the first step', async ($, on) => {
    const both = { ...defaults, helper: { model: { claude: 'sonnet' }, effort: 'medium' } }
    const { disk, clock } = world(on, { plan: plan({ defaults: both }) })
    const spawned = engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, BUILD)
    expect(spawned[0]!.model).toBe('sonnet')
    // Decision files are in time order by name; within one millisecond their order is not kept.
    await clock.advance(1)
    await step($, { turnId: 'h1', index: 0, model: 'sonnet', effort: 'max', messageCount: 1, agentId: answer.agentId })
    await step($, { turnId: 'h1', index: 1, model: 'sonnet', effort: 'max', messageCount: 3, agentId: answer.agentId })
    expect(stepped.map(s => s.effort)).toEqual(['medium', 'medium'])
    expect(decisions(disk).map(r => [r.did, r.model, r.effort, r.reason])).toEqual([
      ['model', 'sonnet', null, 'default'],
      ['effort', 'sonnet', 'medium', 'default'],
    ])
  })

  test('a kind with a route takes the route, not the default', async ($, on) => {
    const { disk } = world(on, { plan: plan({ defaults }) })
    const spawned = engineSpawn(on)
    await spawn($, { ...TASK, workflow: { runId: 'wf_made_up', agentIndex: 1 } })
    expect(spawned[0]!.model).toBe('haiku')
    expect(decisions(disk)[0]).toMatchObject({ reason: 'route', why: 'your route for lookups' })
  })

  test('a kinds block that cannot be read still gets the default for where', async ($, on) => {
    const kinds = { order: ['find', 'build'], light: 'find', cut: 300, table: {} }
    const { disk } = world(on, { plan: plan({ defaults, kinds }) })
    engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, { ...TASK, workflow: { runId: 'wf_made_up', agentIndex: 1 } })
    await step($, { turnId: 'w1', index: 0, model: 'opus-x', effort: 'max', messageCount: 1, agentId: answer.agentId })
    expect(stepped[0]!.effort).toBe('xhigh')
    expect(decisions(disk)[0]).toMatchObject({ kind: null, did: 'effort', reason: 'default' })
  })

  test('the chat, with chat.effort on: a default lowers each step of the turn', async ($, on) => {
    const { disk } = world(on, { plan: plan({ defaults, chat: { effort: true, model_on_first_turn: false } }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Add a flag.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'max', messageCount: 1 })
    await step($, { turnId: 't1', index: 1, model: 'opus-x', effort: 'max', messageCount: 3 })
    expect(stepped.map(s => s.effort)).toEqual(['high', 'high'])
    expect(decisions(disk).map(r => [r.where, r.did, r.effort, r.reason])).toEqual([['chat', 'effort', 'high',
      'default']])
  })

  test('the chat, with both chat switches off: the default is not looked at', async ($, on) => {
    const { disk } = world(on, { plan: plan({ defaults }) })
    const stepped = engineStep(on)
    await $.turn.start({ turnId: 't1', text: 'Add a flag.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'max', messageCount: 1 })
    expect(stepped[0]!.effort).toBe('max')
    expect(disk.writes).toEqual([])
  })
})

describe('a route\'s effort is set up as well as down', () => {
  test('a helper on a route whose step runs lower is raised to the route\'s effort', async ($, on) => {
    world(on)
    engineSpawn(on)
    const stepped = engineStep(on)
    const answer = await spawn($, { ...TASK, subagentType: 'Explore' })
    await step($, { turnId: 'h1', index: 0, model: 'haiku', effort: 'low', messageCount: 1, agentId: answer.agentId })
    expect(stepped[0]!.effort).toBe('medium')
  })
})

describe('the words of a task are never kept or written', () => {
  test('a made-up marker word in the task, the description and the typed text is in nothing written', async ($, on) => {
    const { disk } = world(on, { plan: plan({ chat: { effort: true, model_on_first_turn: true } }) })
    engineSpawn(on)
    engineStep(on)
    await spawn($, { prompt: 'Find the zebraquill. Then more zebraquill.', description: 'zebraquill look',
      subagentType: 'general-purpose' })
    await spawn($, { prompt: 'Fix zebraquill.', description: 'zebraquill', subagentType: 'zebraquill' })
    await $.turn.start({ turnId: 't1', text: 'Fix the zebraquill crash.' })
    await step($, { turnId: 't1', index: 0, model: 'opus-x', effort: 'high', messageCount: 1 })
    expect(decisions(disk).length).toBe(3)
    expect(written(disk)).not.toContain('zebraquill')
  })
})

describe("Claude Code's own numbers", () => {
  const measure = ($: Engine, rateLimits: { kind: string; percentUsed: number }[]) =>
    $.session.measure({ context: { window: 200000 } as never, rateLimits, changed: ['rateLimits'] })

  const signals = (files: Record<string, string>) =>
    Object.keys(files).filter(p => p.startsWith(DIR + '/signals/'))

  test('a window at the person\'s line sends one signal, and no second one within 10 minutes', async ($, on) => {
    const { disk, clock } = world(on)
    await measure($, [{ kind: 'five_hour', percentUsed: 91 }, { kind: 'seven_day', percentUsed: 40 }])
    await clock.advance(30000)
    await measure($, [{ kind: 'five_hour', percentUsed: 92 }])
    const sent = signals(disk.files)
    expect(sent.length).toBe(1)
    expect(sent[0]!).toMatch(/\/signals\/1100000-[0-9a-f]{8}\.json$/)
    expect(JSON.parse(disk.files[sent[0]!]!)).toEqual({ t: MADE_AT + 100, agent: 'claude', what: 'at_line',
      window: 'five_hour', pct: 91 })
  })

  test('after 10 minutes the same window may send again', async ($, on) => {
    const { disk, clock } = world(on)
    await measure($, [{ kind: 'five_hour', percentUsed: 91 }])
    await clock.advance(600000)
    disk.files[PLAN] = JSON.stringify(plan({ made_at: MADE_AT + 600 }))
    await measure($, [{ kind: 'five_hour', percentUsed: 93 }])
    expect(signals(disk.files).length).toBe(2)
  })

  test('the seven-day window is the plan\'s week', async ($, on) => {
    const { disk } = world(on)
    await measure($, [{ kind: 'seven_day', percentUsed: 95 }])
    const sent = signals(disk.files)
    expect(sent.length).toBe(1)
    expect(JSON.parse(disk.files[sent[0]!]!).window).toBe('week')
  })

  test('below the line, or with a dry plan, nothing is sent', async ($, on) => {
    const { disk } = world(on, { plan: plan({ dry: true }) })
    await measure($, [{ kind: 'five_hour', percentUsed: 99 }])
    expect(disk.writes).toEqual([])
  })

  test('below the line nothing is sent', async ($, on) => {
    const { disk } = world(on)
    await measure($, [{ kind: 'five_hour', percentUsed: 89.9 }, { kind: 'seven_day', percentUsed: 94 }])
    expect(disk.writes).toEqual([])
  })
})

test('the data folder is ~/.switchboard when SWITCHBOARD_HOME is not set', async ($, on) => {
  const { disk } = world(on, { plan: null, env: { HOME: '/made-up/home' } })
  disk.files['/made-up/home/.switchboard/router/plan.json'] = JSON.stringify(plan())
  const spawned = engineSpawn(on)
  await spawn($, { ...TASK, subagentType: 'Explore' })
  expect(spawned[0]!.model).toBe('haiku')
  expect(Object.keys(disk.files).some(p => p.startsWith('/made-up/home/.switchboard/router/decisions/'))).toBe(true)
})

export type { EngineInterface }
