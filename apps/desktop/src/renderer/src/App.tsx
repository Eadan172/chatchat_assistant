import { useEffect, useState } from 'react'
import { features, flagEnabled } from '../../shared/features'
import { ChatPanel } from './components/chat/ChatPanel'
import { ClarifyPanel } from './components/clarify/ClarifyPanel'
import { DecisionSettings } from './components/decision/DecisionSettings'
import { LibraryPanel } from './components/library/LibraryPanel'
import { VerifyPlaceholder } from './components/verify/VerifyPlaceholder'

interface HealthState {
  ok?: boolean
  version?: string
  error?: string
}

const clarifyOn = flagEnabled('clarify', import.meta.env.VITE_FEATURE_CLARIFY)
const jevOn = flagEnabled('jev', import.meta.env.VITE_FEATURE_JEV)

export function App() {
  const [health, setHealth] = useState<HealthState | null>(null)

  useEffect(() => {
    if (!window.desktop) return
    void window.desktop.getHealth().then((payload) => setHealth(payload))
  }, [])

  return (
    <main
      className="min-h-screen bg-mist text-ink"
      data-clarify={String(clarifyOn)}
      data-jev={String(jevOn)}
      data-verify={String(features.verify)}
    >
      <div className="mx-auto max-w-6xl px-6 py-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm text-sage">本地知识库</p>
            <h1 className="mt-1 text-3xl font-semibold">知识引擎</h1>
          </div>
          <p className="text-sm text-slate-600">
            {health == null && '等待 sidecar'}
            {health?.ok && `sidecar 已连接 · ${health.version ?? ''}`}
            {health && !health.ok && (health.error ?? 'sidecar 未连接')}
          </p>
        </header>
        <div className="mt-6 grid items-start gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <LibraryPanel />
          <ChatPanel />
        </div>
        {clarifyOn ? <ClarifyPanel /> : null}
        {jevOn ? <DecisionSettings /> : null}
        {features.verify ? <VerifyPlaceholder /> : null}
      </div>
    </main>
  )
}
