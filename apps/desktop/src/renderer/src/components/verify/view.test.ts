import { describe, expect, it } from 'vitest'
import { freshnessLabel, parseVerifyJob } from './view'

describe('freshness labels', () => {
  it('names each state and never calls unknown fresh', () => {
    expect(freshnessLabel('fresh')).toBe('已核对')
    expect(freshnessLabel('stale')).toBe('可能过期')
    expect(freshnessLabel('verifying')).toBe('查证中')
    expect(freshnessLabel('unknown')).toBe('无法核实')
    expect(freshnessLabel('unknown')).not.toBe('已核对')
  })

  it('reads item results from a verify job', () => {
    const job = parseVerifyJob({
      job_id: 3,
      state: 'done',
      stats: { items: [{ item_id: 8, state: 'stale', prompt_update: true }] },
    })
    expect(job.job_id).toBe(3)
    expect(job.items[0]?.state).toBe('stale')
    expect(job.items[0]?.prompt_update).toBe(true)
  })
})
