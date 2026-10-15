import { useEffect, useRef, useState } from 'react'
import { errorMessage, sidecarRequest } from '../clarify/client'
import {
  parseDecisionEvents,
  parseDecisionLogs,
  parseDecisionSettings,
  type DecisionRowView,
  type DecisionSettingsView,
} from './view'

export function DecisionSettings() {
  const [settings, setSettings] = useState<DecisionSettingsView | null>(null)
  const [message, setMessage] = useState('去网上搜索最新的说法')
  const [events, setEvents] = useState<DecisionRowView[]>([])
  const [logs, setLogs] = useState<DecisionRowView[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const logsAnchor = useRef<HTMLDivElement>(null)

  async function loadLogs() {
    const result = await sidecarRequest('GET', '/decision/logs?limit=20&offset=0')
    if (!result.ok) {
      setError(result.error || errorMessage(result.data, '没能读到影子日志'))
      return
    }
    setLogs(parseDecisionLogs(result.data))
  }

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
      const listed = await sidecarRequest('GET', '/decision/logs?limit=20&offset=0')
      if (cancelled) return
      if (!listed.ok) {
        setError(listed.error || errorMessage(listed.data, '没能读到影子日志'))
        return
      }
      setLogs(parseDecisionLogs(listed.data))
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!events.length && !logs.length) return
    logsAnchor.current?.scrollIntoView({ block: 'nearest' })
  }, [events, logs])

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
              <span className="block text-xs text-slate-500">
                当前{settings.shadow ? '开启' : '关闭'}。开启时新日志的 applied 为 false。
              </span>
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
              <span className="block text-xs text-slate-500">
                当前{settings.realRouting ? '开启' : '关闭'}。本里程碑不切换检索或澄清。
              </span>
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
          <form
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault()
              const text = message.trim()
              if (!text) return
              void (async () => {
                setPending(true)
                setError(null)
                const result = await sidecarRequest('POST', '/chat', {
                  message: text,
                  mode: 'personal',
                  clarify: 'skip',
                  sources: ['local'],
                })
                setPending(false)
                if (!result.ok) {
                  setError(result.error || errorMessage(result.data, '问答请求失败'))
                  return
                }
                setEvents(parseDecisionEvents(result.events))
                await loadLogs()
              })()
            }}
          >
            <label className="block text-sm text-slate-600" htmlFor="jev-message">
              发一条问答，查看本次 decision 事件
            </label>
            <div className="flex gap-2">
              <input
                id="jev-message"
                data-jev-message
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                className="min-w-0 flex-1 rounded-xl border border-pool bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-sage/40"
              />
              <button
                type="submit"
                data-jev-send
                disabled={pending}
                className="rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-60"
              >
                发送
              </button>
            </div>
          </form>
          {events.length > 0 ? (
            <div data-jev-events>
              <h3 className="text-xs font-medium text-sage">本次 decision 事件</h3>
              <ul className="mt-2 space-y-1 text-xs text-slate-600">
                {events.map((row) => (
                  <li key={row.key}>
                    {row.questionId} · {row.result} · applied={row.applied ? 'true' : 'false'}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
      <div ref={logsAnchor} className="mt-5 border-t border-pool pt-4" data-jev-logs>
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium text-sage">影子日志</h3>
          <button
            type="button"
            data-jev-refresh-logs
            disabled={pending}
            className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool disabled:opacity-60"
            onClick={() => {
              setPending(true)
              void loadLogs().finally(() => setPending(false))
            }}
          >
            刷新记录
          </button>
        </div>
        {logs.length === 0 ? (
          <p className="mt-2 text-xs text-slate-500">还没有记录。发送一条问答后会出现。</p>
        ) : (
          <ul className="mt-2 max-h-52 space-y-1 overflow-auto text-xs text-slate-600">
            {logs.map((row) => (
              <li key={row.key} data-jev-log={row.questionId}>
                {row.questionId} · {row.qtype} · {row.result || '—'} · applied=
                {row.applied ? 'true' : 'false'}
                {row.fallback !== 'none' ? ` · fallback=${row.fallback}` : ''}
              </li>
            ))}
          </ul>
        )}
      </div>
      {settings != null && error ? <p className="mt-3 text-sm text-slate-600">{error}</p> : null}
    </section>
  )
}
