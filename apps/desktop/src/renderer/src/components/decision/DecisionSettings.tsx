import { useEffect, useState } from 'react'
import { errorMessage, sidecarRequest } from '../clarify/client'
import { parseDecisionSettings, type DecisionSettingsView } from './view'

export function DecisionSettings() {
  const [settings, setSettings] = useState<DecisionSettingsView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const result = await sidecarRequest('GET', '/settings/decision')
      if (cancelled) return
      if (!result.ok) {
        setError(result.error || errorMessage(result.data, '没能读到决策层设置'))
        return
      }
      setSettings(parseDecisionSettings(result.data))
    })()
    return () => {
      cancelled = true
    }
  }, [])

  async function save(previous: DecisionSettingsView, next: DecisionSettingsView, shadowChanged: boolean) {
    setSettings(next)
    setPending(true)
    setError(null)
    const result = shadowChanged
      ? await sidecarRequest('POST', '/decision/shadow', { enabled: next.shadow })
      : await sidecarRequest('POST', '/settings/decision', {
          shadow: next.shadow,
          real_routing: next.realRouting,
        })
    setPending(false)
    if (!result.ok) {
      setSettings(previous)
      setError(result.error || errorMessage(result.data, '没能保存决策层设置'))
      return
    }
    setSettings(parseDecisionSettings(result.data))
  }

  return (
    <section data-feature="jev" className="mt-6 rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
      <h2 className="text-sm font-medium text-sage">决策层</h2>
      <p className="mt-2 text-sm text-slate-600">
        影子模式只记录意图、通道和是否查证。实路由默认关闭，问答正文保持当前路径。
      </p>
      {settings == null ? (
        <p className="mt-4 text-sm text-slate-500">{error ?? '正在读取决策层设置'}</p>
      ) : (
        <div className="mt-4 space-y-3">
          <label className="flex items-center justify-between gap-4 text-sm">
            <span>
              <span className="block text-ink">影子模式</span>
              <span className="block text-xs text-slate-500">默认开启。开启时日志里的 applied 为 false。</span>
            </span>
            <input
              type="checkbox"
              data-jev-shadow
              checked={settings.shadow}
              disabled={pending}
              onChange={(event) => {
                const next = { ...settings, shadow: event.target.checked }
                void save(settings, next, true)
              }}
            />
          </label>
          <label className="flex items-center justify-between gap-4 text-sm">
            <span>
              <span className="block text-ink">启用实路由</span>
              <span className="block text-xs text-slate-500">默认关闭。本里程碑不切换检索或澄清。</span>
            </span>
            <input
              type="checkbox"
              data-jev-routing
              checked={settings.realRouting}
              disabled={pending}
              onChange={(event) => {
                const next = { ...settings, realRouting: event.target.checked }
                void save(settings, next, false)
              }}
            />
          </label>
          <p className="text-xs text-slate-500">本地查看记录：GET /decision/logs</p>
        </div>
      )}
      {settings != null && error ? <p className="mt-3 text-sm text-slate-600">{error}</p> : null}
    </section>
  )
}
