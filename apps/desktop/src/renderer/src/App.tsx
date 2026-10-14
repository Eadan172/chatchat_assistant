import { useEffect, useState } from 'react'
import { features, flagEnabled } from '../../shared/features'
import { ClarifyPanel } from './components/clarify/ClarifyPanel'
import { VerifyPlaceholder } from './components/verify/VerifyPlaceholder'

interface HealthState {
  ok?: boolean
  version?: string
  error?: string
}

const clarifyOn = flagEnabled('clarify', import.meta.env.VITE_FEATURE_CLARIFY)

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
      data-jev={String(features.jev)}
      data-verify={String(features.verify)}
    >
      <div className="mx-auto max-w-3xl px-8 py-12">
        <p className="text-sm text-sage">本地知识库</p>
        <h1 className="mt-2 text-3xl font-semibold">知识引擎</h1>
        <p className="mt-3 max-w-xl text-slate-600">
          桌面壳已接通。问答、采集和索引仍是占位，后续里程碑再接上。
        </p>
        <section className="mt-8 rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
          <h2 className="text-sm font-medium text-sage">Sidecar</h2>
          <p className="mt-2 text-sm">
            {health == null && '等待主进程回报健康状态'}
            {health?.ok && `已连接 · ${health.version ?? ''}`}
            {health && !health.ok && (health.error ?? '未连接')}
          </p>
        </section>
        {clarifyOn ? <ClarifyPanel /> : null}
        {features.verify ? <VerifyPlaceholder /> : null}
      </div>
    </main>
  )
}
