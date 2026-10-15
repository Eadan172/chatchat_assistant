import { describe, expect, it } from 'vitest'
import { parseDecisionEvents, parseDecisionLogs, parseDecisionSettings } from './view'

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

  it('reads decision events and log rows, including applied=false', () => {
    const events = parseDecisionEvents([
      { event: 'delta', data: { text: 'stub' } },
      {
        event: 'decision',
        data: {
          question_id: 'route_channels',
          qtype: 'choice',
          result: ['local'],
          confidence: 0.74,
          applied: false,
          fallback: null,
        },
      },
    ])
    expect(events).toEqual([
      {
        key: 'event-1',
        questionId: 'route_channels',
        qtype: 'choice',
        result: 'local',
        confidence: '0.74',
        applied: false,
        fallback: 'none',
      },
    ])

    const logs = parseDecisionLogs({
      items: [
        {
          id: 4,
          question_id: 'intent',
          qtype: 'choice',
          result: { value: 'lookup' },
          confidence: 0.6,
          applied: false,
          fallback: null,
        },
      ],
    })
    expect(logs[0]?.applied).toBe(false)
    expect(logs[0]?.result).toBe('lookup')
    expect(logs[0]?.key).toBe('log-4')
  })
})
