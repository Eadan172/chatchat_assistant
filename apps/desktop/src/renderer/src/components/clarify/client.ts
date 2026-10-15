/** Talk to the sidecar through Electron, or a dev URL when the shell is not there. */

export interface SidecarEvent {
  event: string
  data: unknown
}

export interface SidecarResult {
  ok: boolean
  status: number
  data?: unknown
  events?: SidecarEvent[]
  error?: string
}

export function parseSse(text: string): SidecarEvent[] {
  const events: SidecarEvent[] = []
  for (const block of text.split('\n\n')) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (!dataLines.length) continue
    const raw = dataLines.join('\n')
    let data: unknown = raw
    try {
      data = JSON.parse(raw)
    } catch {
      data = raw
    }
    events.push({ event, data })
  }
  return events
}

export function errorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string' && detail) return detail
  }
  return fallback
}

export async function sidecarRequest(method: string, path: string, body?: unknown): Promise<SidecarResult> {
  if (window.desktop?.sidecarRequest) {
    return window.desktop.sidecarRequest({ method, path, body })
  }
  const base = import.meta.env.VITE_SIDECAR_URL
  const token = import.meta.env.VITE_SIDECAR_TOKEN
  if (!base) return { ok: false, status: 0, error: 'sidecar bridge unavailable' }
  try {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { 'x-sidecar-token': token } : {}),
        ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    const text = await response.text()
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('text/event-stream')) {
      return { ok: response.ok, status: response.status, events: parseSse(text) }
    }
    let data: unknown = null
    if (text) {
      try {
        data = JSON.parse(text)
      } catch {
        data = { detail: text }
      }
    }
    return { ok: response.ok, status: response.status, data }
  } catch (error) {
    return { ok: false, status: 0, error: error instanceof Error ? error.message : 'request failed' }
  }
}
