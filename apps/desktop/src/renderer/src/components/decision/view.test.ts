import { describe, expect, it } from 'vitest'
import { parseDecisionSettings } from './view'

describe('decision settings view', () => {
  it('defaults to shadow on and real routing off', () => {
    expect(parseDecisionSettings(null)).toEqual({
      shadow: true,
      realRouting: false,
      confidenceFloor: null,
    })
    expect(parseDecisionSettings({})).toEqual({
      shadow: true,
      realRouting: false,
      confidenceFloor: null,
    })
  })

  it('reads an explicit routing opt-in and floor', () => {
    expect(parseDecisionSettings({ shadow: false, real_routing: true, confidence_floor: 0.4 })).toEqual({
      shadow: false,
      realRouting: true,
      confidenceFloor: 0.4,
    })
  })
})
