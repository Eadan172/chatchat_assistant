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
}

declare global {
  interface Window {
    desktop?: DesktopBridge
  }
}

export {}
