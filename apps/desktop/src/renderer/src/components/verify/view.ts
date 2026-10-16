/** Labels for local freshness. Web re-fetch is not represented here. */

export type FreshnessState = 'fresh' | 'stale' | 'unknown' | 'verifying' | string

export function freshnessLabel(state?: string | null): string {
  if (state === 'fresh') return '已核对'
  if (state === 'stale') return '可能过期'
  if (state === 'verifying') return '查证中'
  if (state === 'unknown') return '无法核实'
  return '未查证'
}

export function freshnessClass(state?: string | null): string {
  if (state === 'stale') return 'bg-amber-100 text-amber-950'
  if (state === 'fresh') return 'bg-mist text-sage'
  if (state === 'verifying') return 'bg-pool text-ink'
  return 'bg-slate-100 text-slate-600'
}

export interface VerifyItemResult {
  item_id?: number
  state?: string
  reason?: string
  action?: string
  prompt_update?: boolean
}

export interface VerifyJobView {
  job_id?: number
  state?: string
  items: VerifyItemResult[]
}

export function parseVerifyJob(data: unknown): VerifyJobView {
  const row = data && typeof data === 'object' ? (data as Record<string, unknown>) : {}
  const stats = row.stats && typeof row.stats === 'object' ? (row.stats as { items?: unknown }) : {}
  const rawItems = Array.isArray(stats.items) ? stats.items : []
  const items = rawItems.filter((item): item is VerifyItemResult => !!item && typeof item === 'object')
  return {
    job_id: typeof row.job_id === 'number' ? row.job_id : undefined,
    state: typeof row.state === 'string' ? row.state : undefined,
    items,
  }
}
