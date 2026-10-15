/** Parse sidecar decision settings. Defaults match the shadow-on, routing-off policy. */

export interface DecisionSettingsView {
  shadow: boolean
  realRouting: boolean
  confidenceFloor: number | null
}

export function parseDecisionSettings(data: unknown): DecisionSettingsView {
  const row = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  const floor = row.confidence_floor
  return {
    shadow: row.shadow !== false,
    realRouting: row.real_routing === true,
    confidenceFloor: typeof floor === 'number' && Number.isFinite(floor) ? floor : null,
  }
}
