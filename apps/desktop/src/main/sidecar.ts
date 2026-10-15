import { spawn, type ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { createServer, type AddressInfo } from 'node:net'
import path from 'node:path'
import { app } from 'electron'
import log from 'electron-log/main'
import { allowedSidecarPath } from '../shared/sidecarPaths'

let child: ChildProcess | null = null
export let sidecarBaseUrl = ''
export const sidecarToken = randomBytes(24).toString('hex')

function findRepoRoot(start: string): string {
  let dir = start
  for (let i = 0; i < 8; i += 1) {
    if (existsSync(path.join(dir, 'services', 'sidecar', 'app', 'main.py'))) {
      return dir
    }
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return path.resolve(process.cwd(), '../..')
}

function pythonBin(repoRoot: string): string {
  if (process.env.SIDECAR_PYTHON) return process.env.SIDECAR_PYTHON
  const candidates =
    process.platform === 'win32'
      ? [
          path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
          path.join(repoRoot, 'services', 'sidecar', '.venv', 'Scripts', 'python.exe'),
        ]
      : [
          path.join(repoRoot, '.venv', 'bin', 'python'),
          path.join(repoRoot, 'services', 'sidecar', '.venv', 'bin', 'python'),
        ]
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate
  }
  return process.platform === 'win32' ? 'python' : 'python3'
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address() as AddressInfo | null
      if (!address || typeof address === 'string') {
        server.close()
        reject(new Error('failed to allocate a sidecar port'))
        return
      }
      server.close((error) => (error ? reject(error) : resolve(address.port)))
    })
  })
}

export async function startSidecar(): Promise<string> {
  const port = await freePort()
  const isDev = !app.isPackaged
  const repoRoot = findRepoRoot(__dirname)
  const sidecarRoot = path.join(repoRoot, 'services', 'sidecar')
  const cmd = isDev
    ? pythonBin(repoRoot)
    : path.join(process.resourcesPath, 'sidecar', process.platform === 'win32' ? 'sidecar.exe' : 'sidecar')
  const args = isDev
    ? ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)]
    : ['--port', String(port)]

  child = spawn(cmd, args, {
    cwd: isDev ? sidecarRoot : undefined,
    env: {
      ...process.env,
      SIDECAR_TOKEN: sidecarToken,
      DATA_DIR: app.getPath('userData'),
      PYTHONUNBUFFERED: '1',
    },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.stdout?.on('data', (chunk: Buffer) => log.info('[sidecar]', chunk.toString().trim()))
  child.stderr?.on('data', (chunk: Buffer) => log.info('[sidecar]', chunk.toString().trim()))
  child.on('error', (error) => log.error('[sidecar] failed to spawn', error))
  child.on('exit', (code) => {
    log.error('[sidecar] exited', code)
    child = null
  })

  sidecarBaseUrl = `http://127.0.0.1:${port}`
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`${sidecarBaseUrl}/health`, {
        headers: { 'x-sidecar-token': sidecarToken },
      })
      if (response.ok) return sidecarBaseUrl
    } catch {
      // Process is still booting.
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error('sidecar 启动超时')
}

export function stopSidecar(): void {
  if (child && !child.killed) child.kill()
  child = null
}

export interface SidecarResult {
  ok: boolean
  status: number
  data?: unknown
  events?: { event: string; data: unknown }[]
  error?: string
}

function parseSse(text: string): { event: string; data: unknown }[] {
  const events: { event: string; data: unknown }[] = []
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

export async function streamSidecar(
  requestPath: string,
  body: unknown,
  onEvent: (event: { event: string; data: unknown }) => void,
): Promise<SidecarResult> {
  if (!sidecarBaseUrl) return { ok: false, status: 0, error: 'sidecar not started' }
  const pathOnly = requestPath.startsWith('/') ? requestPath : `/${requestPath}`
  if (!allowedSidecarPath('POST', pathOnly)) return { ok: false, status: 0, error: 'path not allowed' }
  try {
    const response = await fetch(`${sidecarBaseUrl}${pathOnly}`, {
      method: 'POST',
      headers: {
        'x-sidecar-token': sidecarToken,
        'content-type': 'application/json',
      },
      body: JSON.stringify(body ?? {}),
    })
    if (!response.body) {
      const text = await response.text()
      for (const event of parseSse(text)) onEvent(event)
      return { ok: response.ok, status: response.status, events: parseSse(text) }
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    const events: { event: string; data: unknown }[] = []
    while (true) {
      const step = await reader.read()
      if (step.done) break
      buffer += decoder.decode(step.value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const parsed = parseSse(`${part}\n\n`)
        for (const event of parsed) {
          events.push(event)
          onEvent(event)
        }
      }
    }
    if (buffer.trim()) {
      for (const event of parseSse(buffer)) {
        events.push(event)
        onEvent(event)
      }
    }
    return { ok: response.ok, status: response.status, events }
  } catch (error) {
    return { ok: false, status: 0, error: error instanceof Error ? error.message : 'request failed' }
  }
}

export async function sidecarFetch(method: string, requestPath: string, body?: unknown): Promise<SidecarResult> {
  if (!sidecarBaseUrl) return { ok: false, status: 0, error: 'sidecar not started' }
  const pathOnly = requestPath.startsWith('/') ? requestPath : `/${requestPath}`
  if (!allowedSidecarPath(method.toUpperCase(), pathOnly)) {
    return { ok: false, status: 0, error: 'path not allowed' }
  }
  try {
    const response = await fetch(`${sidecarBaseUrl}${pathOnly}`, {
      method: method.toUpperCase(),
      headers: {
        'x-sidecar-token': sidecarToken,
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
