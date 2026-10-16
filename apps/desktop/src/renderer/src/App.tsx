import { useEffect, useState } from 'react'
import { flagEnabled } from '../../shared/features'
import { AskSettings } from './components/ask/AskSettings'
import { ChatPanel } from './components/chat/ChatPanel'
import { ClarifyPanel } from './components/clarify/ClarifyPanel'
import { DecisionSettings } from './components/decision/DecisionSettings'
import { ImaPanel } from './components/connectors/ImaPanel'
import { LibraryPanel } from './components/library/LibraryPanel'

interface HealthState {
  ok?: boolean
  version?: string
  error?: string
}

const clarifyOn = flagEnabled('clarify', import.meta.env.VITE_FEATURE_CLARIFY)
const jevOn = flagEnabled('jev', import.meta.env.VITE_FEATURE_JEV)
const verifyOn = flagEnabled('verify', import.meta.env.VITE_FEATURE_VERIFY)
const imaOn = flagEnabled('ima', import.meta.env.VITE_FEATURE_IMA)
const askOn = flagEnabled('ask', import.meta.env.VITE_FEATURE_ASK)

export function App() {
  const [health, setHealth] = useState<HealthState | null>(null)

  useEffect(() => {
    if (window.desktop) {
      void window.desktop.getHealth().then((payload) => setHealth(payload))
      return
    }
    const base = import.meta.env.VITE_SIDECAR_URL
    const token = import.meta.env.VITE_SIDECAR_TOKEN
    if (!base) return
    void fetch(`${base}/health`, { headers: token ? { 'x-sidecar-token': token } : {} })
      .then((response) => response.json())
      .then((payload) => setHealth(payload))
      .catch((error: unknown) => {
        setHealth({ ok: false, error: error instanceof Error ? error.message : 'sidecar 未连接' })
      })
  }, [])

  return (
    <main
      className="min-h-screen bg-mist text-ink"
      data-clarify={String(clarifyOn)}
      data-jev={String(jevOn)}
      data-verify={String(verifyOn)}
      data-ima={String(imaOn)}
      data-ask={String(askOn)}
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
        {imaOn ? <ImaPanel /> : null}
        <div className="mt-6 grid items-start gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <LibraryPanel verifyOn={verifyOn} />
          <ChatPanel verifyOn={verifyOn} imaOn={imaOn} askOn={askOn} />
        </div>
        {askOn ? <AskSettings /> : null}
        {clarifyOn ? <ClarifyPanel /> : null}
        {jevOn ? <DecisionSettings /> : null}
      </div>
    </main>
  )
}
