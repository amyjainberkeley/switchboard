// The two kinds of file this plugin writes, both under `<data folder>/router/`: one file per decision, in
// `decisions/`, for each piece of work it looked at, and a signal file for the core to act on. Nothing else is
// ever written, and Switchboard's own decisions.jsonl is never read or written here: the core folds the decision
// files into it.
//
// The engine's file calls write whole files: there is no append and no rename. A file of its own for each decision
// means two Claude Code windows writing at once cannot lose one; each is written whole in one call, and the core
// reads only a file that has not changed for 2 seconds.

import type { EngineInterface } from 'claude-code'

import type { Rec } from './decide'

// At most one signal per kind and window in this long.
export const SIGNAL_GAP_MS = 10 * 60 * 1000

export type Writes = { sent: Map<string, number> }

export function newWrites(): Writes {
  return { sent: new Map() }
}

// `<epoch ms, 13 digits>-<8 hex>.json`: names sort in the order they were written, and two never meet.
function fileName(nowMs: number): string {
  return `${String(Math.max(0, Math.floor(nowMs))).padStart(13, '0')}-${hex8()}.json`
}

// Writes one decision to its own file. Never throws: a record that cannot be written is left out.
export async function addDecision($: EngineInterface, dir: string, nowMs: number, rec: Rec): Promise<void> {
  try {
    await $.fs.write(`${dir}/decisions/${fileName(nowMs)}`, JSON.stringify(rec))
  } catch {
    // The work goes on as decided; only the record is lost.
  }
}

function hex8(): string {
  const bytes = new Uint8Array(4)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
}

// Writes one signal file unless one of the same kind and window went out less than SIGNAL_GAP_MS ago.
export async function sendSignal(
  $: EngineInterface,
  writes: Writes,
  dir: string,
  nowMs: number,
  fact: { what: 'at_line'; window: string; pct: number },
): Promise<boolean> {
  const key = fact.what + ':' + fact.window
  const last = writes.sent.get(key)
  if (last !== undefined && nowMs >= last && nowMs - last < SIGNAL_GAP_MS) return false
  writes.sent.set(key, nowMs)
  const body = { t: nowMs / 1000, agent: 'claude', what: fact.what, window: fact.window, pct: fact.pct }
  try {
    await $.fs.write(`${dir}/signals/${Math.floor(nowMs)}-${hex8()}.json`, JSON.stringify(body))
    return true
  } catch {
    return false
  }
}
