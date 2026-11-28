// What sits beneath the plugin in these tests: a made-up disk in memory, the environment, the clock, and the engine's
// answers to a helper starting and to a step of a turn. Nothing here touches a real file.

import type { On } from 'claude-code'
import { mock } from 'claude-code/testing'

export const HOME = '/made-up/data'
export const DIR = HOME + '/router'
export const PLAN = DIR + '/plan.json'
// The core's folded record, which the plugin never reads or writes, and the folder of one file per decision.
export const DECISIONS = DIR + '/decisions.jsonl'
export const DECIDED = DIR + '/decisions/'
// 1000 s after the epoch, as the plans below are made.
export const MADE_AT = 1000

export type Disk = { files: Record<string, string>; writes: string[]; reads: string[] }

const KINDS = {
  order: ['build', 'fix', 'review', 'plan', 'find', 'write', 'other'],
  light: 'find',
  cut: 300,
  table: {
    build: ['add', 'implement', 'write a', 'write the', 'change'],
    fix: ['fix', 'debug', 'bug', 'crash'],
    review: ['review'],
    plan: ['plan', 'design'],
    find: ['find', 'where', 'list', 'explain'],
    write: ['draft', 'summarize'],
  },
}

type Json = Record<string, unknown>

// A usable plan made at MADE_AT, with routes for lookups, fixes and reviews; `over` replaces top-level keys.
export function plan(over: Json = {}): Json {
  return {
    v: 1,
    made_at: MADE_AT,
    good_for_s: 180,
    on: true,
    dry: false,
    by: 'switchboard test',
    order: ['claude', 'codex'],
    agents: {
      claude: { can: true, why_not: null, room: 'room', back_at: null, at_lines: false,
        line: { five_hour: 90, week: 95 }, in_use: '1' },
      codex: { can: true, why_not: null, room: 'room', back_at: null, at_lines: false },
    },
    routes: {
      find: [
        { agent: 'claude', model: 'haiku', effort: 'medium' },
        { agent: 'codex', model: 'gpt-6-luna', effort: 'medium' },
      ],
      fix: [{ agent: 'claude', model: 'sonnet', effort: 'max' }],
      review: [
        { agent: 'codex', model: 'gpt-6-luna', effort: 'high' },
        { agent: 'claude', model: 'sonnet', effort: 'high' },
      ],
    },
    why: { find: 'your route for lookups', fix: 'your route for fixes', review: 'your route for reviews' },
    helpers: { force: false },
    chat: { effort: false, model_on_first_turn: false },
    hand: { on: false, write: false, max_s: 900 },
    carry_on: false,
    kinds: KINDS,
    types: { Explore: 'find', Plan: 'plan' },
    ...over,
  }
}

// The made-up disk, the environment and the clock (in milliseconds, MADE_AT + 100 s unless `nowS` says).
export function world(
  on: On,
  options: {
    plan?: Json | string | null
    env?: Record<string, string>
    nowS?: number
    files?: Record<string, string>
  } = {},
) {
  const disk: Disk = { files: { ...(options.files ?? {}) }, writes: [], reads: [] }
  const p = options.plan === undefined ? plan() : options.plan
  if (p !== null) disk.files[PLAN] = typeof p === 'string' ? p : JSON.stringify(p)
  mock.env(on, options.env ?? { SWITCHBOARD_HOME: HOME })
  const clock = mock.clock(on, { now: (options.nowS ?? MADE_AT + 100) * 1000 })
  engineQuiet(on)
  const has = (path: string) => Object.prototype.hasOwnProperty.call(disk.files, path)
  on('fs.exists', (_, e) => ({ value: has(e.path) }))
  on('fs.stat', (_, e) => ({
    value: { kind: 'file', size: new TextEncoder().encode(disk.files[e.path] ?? '').length, mtimeMs: 0, isLink: false },
  }))
  on('fs.read', (_, e) => {
    disk.reads.push(e.path)
    if (!has(e.path)) return { deny: 'no such file' }
    const text = disk.files[e.path] ?? ''
    if (e.length === undefined) return { value: text }
    // As the engine reads: `length` is in bytes, and a cut through a character gives U+FFFD at that edge.
    return { value: new TextDecoder().decode(new TextEncoder().encode(text).slice(0, e.length)) }
  })
  on('fs.write', (_, e) => {
    disk.writes.push(e.path)
    disk.files[e.path] = e.text
    return { value: undefined }
  })
  return { disk, clock }
}

// The engine's own start of a helper: it runs on the model it was handed, else the parent's.
export function engineSpawn(on: On) {
  const seen: { model?: string; subagentType: string }[] = []
  on('agent.spawn', (_, e) => {
    seen.push({ model: e.model, subagentType: e.subagentType })
    return { model: e.model ?? e.parentModel ?? 'parent-model', agentId: 'helper-' + seen.length }
  })
  return seen
}

// The engine's own step: it records what it was asked for and streams two made-up chunks.
export function engineStep(on: On) {
  const seen: { model: string; effort?: unknown; agentId?: string }[] = []
  on('turn.step', async function* (_, e) {
    seen.push({ model: e.model, effort: e.effort, agentId: e.agentId })
    yield { kind: 'text', index: 0, text: 'made-up answer' }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    const answer = 'made-up answer'
    return { turnId: e.turnId, index: e.index, answer, toolUses: [], stopReason: 'end_turn', usage: null }
  })
  return seen
}

// The decision files the plugin wrote, in name order (the order they were written in).
export function decisions(disk: Disk): Json[] {
  return Object.keys(disk.files)
    .filter(path => path.startsWith(DECIDED))
    .sort()
    .map(path => JSON.parse(disk.files[path]!) as Json)
}

export function written(disk: Disk): string {
  return Object.entries(disk.files)
    .filter(([path]) => path !== PLAN)
    .map(([path, text]) => path + '\n' + text)
    .join('\n')
}

// The engine's own answers to the events the plugin only looks at.
export function engineQuiet(on: On) {
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('session.measure', (_, e) => ({ changed: e.changed }))
}
