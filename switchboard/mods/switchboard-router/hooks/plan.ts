// Where Switchboard's router files are, and reading the plan. The plan is the one file this plugin reads.

import type { EngineInterface } from 'claude-code'

export const MAX_PLAN_BYTES = 16384
// A plan read is kept this long before it is read again.
export const REREAD_MS = 15000

type Obj = Record<string, unknown>

export function isObj(v: unknown): v is Obj {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

function folder(path: string): string {
  return path.replace(/\/+$/, '')
}

// `<data folder>/router`: the data folder is SWITCHBOARD_HOME when set, else ~/.switchboard. Undefined when neither
// gives an absolute path, and then the plugin does nothing.
export async function routerDir($: EngineInterface): Promise<string | undefined> {
  const home = await $.env.get('SWITCHBOARD_HOME')
  if (home !== undefined && home !== '') return home.startsWith('/') ? folder(home) + '/router' : undefined
  const user = await $.env.get('HOME')
  if (user === undefined || !user.startsWith('/')) return undefined
  return folder(user) + '/.switchboard/router'
}

// The plan as JSON data, or null when it is missing, too big, unreadable or not an object.
export async function readPlanFile($: EngineInterface, dir: string): Promise<Obj | null> {
  const path = dir + '/plan.json'
  if (!(await $.fs.exists(path))) return null
  const text = await $.fs.read(path, { length: MAX_PLAN_BYTES + 1 })
  if (typeof text !== 'string') return null
  if (new TextEncoder().encode(text).length > MAX_PLAN_BYTES) return null
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return null
  }
  return isObj(data) ? data : null
}

// The last read, kept for REREAD_MS by the engine's clock so a busy turn does not read the file at every step.
export type PlanCache = { plan: Obj | null; dir: string; readAt: number }

export function newPlanCache(): PlanCache {
  return { plan: null, dir: '', readAt: -Infinity }
}

export async function readPlan($: EngineInterface, cache: PlanCache, dir: string, nowMs: number): Promise<Obj | null> {
  const age = nowMs - cache.readAt
  if (cache.dir === dir && age >= 0 && age <= REREAD_MS) return cache.plan
  let plan: Obj | null
  try {
    plan = await readPlanFile($, dir)
  } catch {
    plan = null
  }
  cache.plan = plan
  cache.dir = dir
  cache.readAt = nowMs
  return plan
}
