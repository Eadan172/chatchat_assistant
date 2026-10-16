import { contextBridge, ipcRenderer } from 'electron'
import { features } from '../shared/features'

export interface HealthPayload {
  ok: boolean
  version?: string
  error?: string
  models?: { embedder: string; reranker: string }
}

const desktop = {
  features,
  getHealth: (): Promise<HealthPayload> => ipcRenderer.invoke('sidecar:health'),
  sidecarRequest: (payload: { method: string; path: string; body?: unknown }) =>
    ipcRenderer.invoke('sidecar:request', payload),
  streamChat: (body: unknown, onEvent: (event: { event: string; data: unknown }) => void) => {
    const channel = `chat-${Date.now()}-${Math.floor(Math.random() * 1000)}`
    const listener = (_event: unknown, payload: { event: string; data: unknown }) => onEvent(payload)
    ipcRenderer.on(channel, listener)
    return ipcRenderer.invoke('sidecar:stream', { path: '/chat', body, channel }).finally(() => {
      ipcRenderer.removeListener(channel, listener)
    })
  },
  streamAsk: (body: unknown, onEvent: (event: { event: string; data: unknown }) => void) => {
    const channel = `ask-${Date.now()}-${Math.floor(Math.random() * 1000)}`
    const listener = (_event: unknown, payload: { event: string; data: unknown }) => onEvent(payload)
    ipcRenderer.on(channel, listener)
    return ipcRenderer.invoke('sidecar:stream', { path: '/ask', body, channel }).finally(() => {
      ipcRenderer.removeListener(channel, listener)
    })
  },
  openPath: (target: string) => ipcRenderer.invoke('shell:openPath', target),
  openExternal: (target: string) => ipcRenderer.invoke('shell:openExternal', target),
}

contextBridge.exposeInMainWorld('desktop', desktop)

export type DesktopApi = typeof desktop
