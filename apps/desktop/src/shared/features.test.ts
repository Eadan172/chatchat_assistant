import { describe, expect, it } from 'vitest'
import { features } from './features'

describe('feature flags', () => {
  it('keeps clarify, jev, and verify off by default', () => {
    expect(features.clarify).toBe(false)
    expect(features.jev).toBe(false)
    expect(features.verify).toBe(false)
  })
})
