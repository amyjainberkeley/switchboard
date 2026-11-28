// The plugin's rule agrees with every case of fixtures.json, the file the core's rule is held to as well.

import { describe, expect, test } from 'claude-code/testing'

import fixtures from '../hooks/fixtures-data'
import { firstSentence, kindOfWork } from '../hooks/kinds'
import { applyEffort, resolve, type Where } from '../hooks/resolve'

type Case = Record<string, unknown> & { name: string }

const cases = fixtures as unknown as Case[]

describe('resolve', () => {
  for (const c of cases.filter(c => 'plan' in c)) {
    test(c.name, () => {
      const where = (c.where ?? null) as Where | null
      expect(resolve(c.plan, c.kind as string | null, c.here as string, c.now as number, where)).toEqual(c.want)
    })
  }
})

describe('a missing where is null', () => {
  for (const c of cases.filter(c => 'plan' in c)) {
    test(c.name, () => {
      const kind = c.kind as string | null
      expect(resolve(c.plan, kind, c.here as string, c.now as number)).toEqual(
        resolve(c.plan, kind, c.here as string, c.now as number, null))
    })
  }
})

describe('apply_effort', () => {
  for (const c of cases.filter(c => 'current' in c)) {
    test(c.name, () => {
      expect(applyEffort(c.current, c.answer, c.reason)).toBe(c.want as string | null)
    })
  }
})

describe('the kind of a piece of work', () => {
  for (const c of cases.filter(c => 'want_kind' in c)) {
    test(c.name, () => {
      expect(kindOfWork(c.kinds, c.types, c.helper_type, c.description, c.task)).toBe(c.want_kind as string)
    })
  }
})

describe('a long first sentence', () => {
  test('one long line with no full stop is cut to 300 code points, as short text is', () => {
    const long = 'Fix ' + 'a'.repeat(5_000_000)
    expect(firstSentence(long)).toBe('fix ' + 'a'.repeat(296))
    expect(firstSentence('\u{1F600}'.repeat(2_000_000))).toBe('\u{1F600}'.repeat(300))
    expect(firstSentence(' '.repeat(10_000) + '1. ' + 'B'.repeat(2_000) + '. rest')).toBe('b'.repeat(300))
  })
  test('an end of sentence just past the part that is read changes nothing', () => {
    for (const n of [1198, 1199, 1200, 1201]) {
      expect(firstSentence('a'.repeat(n) + '. Next')).toBe('a'.repeat(300))
    }
  })
})

test('the fixtures hold cases of every sort', () => {
  expect(cases.filter(c => 'plan' in c).length).toBeGreaterThan(20)
  expect(cases.filter(c => 'where' in c).length).toBeGreaterThanOrEqual(20)
  expect(cases.filter(c => 'current' in c).length).toBeGreaterThanOrEqual(15)
  expect(cases.filter(c => 'want_kind' in c).length).toBeGreaterThan(20)
})
