import { describe, expect, it } from 'vitest'
import { features, flagEnabled } from './features'

describe('feature flags', () => {
  it('keeps clarify, jev, verify, and ima off by default', () => {
    expect(features.clarify).toBe(false)
    expect(features.jev).toBe(false)
    expect(features.verify).toBe(false)
    expect(features.ima).toBe(false)
    expect(flagEnabled('clarify')).toBe(false)
    expect(flagEnabled('ima')).toBe(false)
  })

  it('lets an explicit override turn clarify on', () => {
    expect(flagEnabled('clarify', 'true')).toBe(true)
    expect(flagEnabled('clarify', '1')).toBe(true)
    expect(flagEnabled('jev', 'true')).toBe(true)
    expect(flagEnabled('verify', '0')).toBe(false)
  })
})
