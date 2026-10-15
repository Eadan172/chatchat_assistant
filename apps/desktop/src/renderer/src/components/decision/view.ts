/** Parse sidecar decision settings, SSE probes, and shadow log rows. */

export interface DecisionSettingsView {
  shadow: boolean
  realRouting: boolean
  confidenceFloor: number | null
}

export interface DecisionRowView {
  key: string
  questionId: string
  qtype: string
  result: string
  confidence: string
  applied: boolean
  fallback: string
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

function formatResult(value: unknown): string {
  if (value && typeof value === 'object' && 'value' in value) {
    return formatResult((value as { value: unknown }).value)
  }
  if (Array.isArray(value)) return value.map((item) => formatResult(item)).join(', ')
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value == null) return ''
  return String(value)
}

function formatConfidence(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : ''
}

function rowFrom(raw: Record<string, unknown>, key: string): DecisionRowView | null {
  if (typeof raw.question_id !== 'string' || !raw.question_id) return null
  return {
    key,
    questionId: raw.question_id,
    qtype: typeof raw.qtype === 'string' ? raw.qtype : '',
    result: formatResult(raw.result),
    confidence: formatConfidence(raw.confidence),
    applied: raw.applied === true,
    fallback: raw.fallback == null || raw.fallback === '' ? 'none' : String(raw.fallback),
  }
}

export function parseDecisionEvents(events: { event: string; data: unknown }[] | undefined): DecisionRowView[] {
  if (!events) return []
  const rows: DecisionRowView[] = []
  events.forEach((item, index) => {
    if (item.event !== 'decision' || !item.data || typeof item.data !== 'object') return
    const row = rowFrom(item.data as Record<string, unknown>, `event-${index}`)
    if (row) rows.push(row)
  })
  return rows
}

export function parseDecisionLogs(data: unknown): DecisionRowView[] {
  const items =
    data && typeof data === 'object' && Array.isArray((data as { items?: unknown }).items)
      ? (data as { items: unknown[] }).items
      : []
  const rows: DecisionRowView[] = []
  items.forEach((item, index) => {
    if (!item || typeof item !== 'object') return
    const raw = item as Record<string, unknown>
    const key = typeof raw.id === 'number' ? `log-${raw.id}` : `log-${index}`
    const row = rowFrom(raw, key)
    if (row) rows.push(row)
  })
  return rows
}
