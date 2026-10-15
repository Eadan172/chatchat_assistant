/// <reference types="vite/client" />

interface DesktopHealth {
  ok: boolean
  version?: string
  error?: string
  models?: { embedder: string; reranker: string }
}

interface DesktopBridge {
  features: { clarify: boolean; jev: boolean; verify: boolean }
  getHealth: () => Promise<DesktopHealth>
  sidecarRequest: (payload: { method: string; path: string; body?: unknown }) => Promise<{
    ok: boolean
    status: number
    data?: unknown
    events?: { event: string; data: unknown }[]
    error?: string
  }>
  streamChat?: (
    body: unknown,
    onEvent: (event: { event: string; data: unknown }) => void,
  ) => Promise<{ ok: boolean; status: number; error?: string }>
  openPath?: (target: string) => Promise<{ ok: boolean; error?: string }>
}

declare global {
  interface ImportMetaEnv {
    readonly VITE_FEATURE_CLARIFY?: string
    readonly VITE_FEATURE_JEV?: string
    readonly VITE_SIDECAR_URL?: string
    readonly VITE_SIDECAR_TOKEN?: string
  }

  interface Window {
    desktop?: DesktopBridge
  }
}

export {}
