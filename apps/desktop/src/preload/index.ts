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
}

contextBridge.exposeInMainWorld('desktop', desktop)

export type DesktopApi = typeof desktop
