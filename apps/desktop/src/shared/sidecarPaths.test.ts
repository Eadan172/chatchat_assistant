import { describe, expect, it } from 'vitest'
import { allowedSidecarPath } from './sidecarPaths'

describe('sidecar path allowlist', () => {
  it('allows decision settings, shadow, and paginated logs', () => {
    expect(allowedSidecarPath('GET', '/settings/decision')).toBe(true)
    expect(allowedSidecarPath('POST', '/settings/decision')).toBe(true)
    expect(allowedSidecarPath('POST', '/decision/shadow')).toBe(true)
    expect(allowedSidecarPath('GET', '/decision/logs?limit=20&offset=0')).toBe(true)
    expect(allowedSidecarPath('POST', '/decision/logs')).toBe(false)
  })

  it('still allows chat and clarify, and blocks other routes', () => {
    expect(allowedSidecarPath('POST', '/chat')).toBe(true)
    expect(allowedSidecarPath('GET', '/clarify/active')).toBe(true)
    expect(allowedSidecarPath('POST', '/clarify/12/answer')).toBe(true)
    expect(allowedSidecarPath('POST', '/verify')).toBe(false)
    expect(allowedSidecarPath('DELETE', '/decision/logs')).toBe(false)
  })
})
