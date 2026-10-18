import { useEffect, useMemo, useState } from 'react'
import { useI18n, type Locale } from '../../i18n'
import { errorMessage, sidecarRequest } from '../clarify/client'
import { ClearAllButton } from '../conversations/ClearAllButton'

interface StyleRow {
  id: string
  domain: string
  locale: string
  label: string
  summary: string
}

interface AppInfo {
  version: string
  data_dir: string
  db_path: string
  db_bytes: number
  features: Record<string, boolean>
  limits: { fork_tokens: number; import_max_bytes: number }
}

const KEY_FIELDS: { field: string; env: string; label: string }[] = [
  { field: 'deepseek_api_key', env: 'DEEPSEEK_API_KEY', label: 'DeepSeek API Key' },
  { field: 'serper_api_key', env: 'SERPER_API_KEY', label: 'Serper API Key' },
  { field: 'serpapi_api_key', env: 'SERPAPI_API_KEY', label: 'SerpApi API Key' },
  { field: 'tavily_api_key', env: 'TAVILY_API_KEY', label: 'Tavily API Key' },
]

function formatBytes(value: number): string {
  if (value > 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value > 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

export function SettingsView() {
  const { t, tf, locale, setLocale } = useI18n()
  const [styles, setStyles] = useState<StyleRow[]>([])
  const [defaultStyle, setDefaultStyle] = useState('auto')
  const [configured, setConfigured] = useState<Record<string, boolean>>({})
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [info, setInfo] = useState<AppInfo | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      const [styleRows, ask, ui, app] = await Promise.all([
        sidecarRequest('GET', `/styles?locale=${locale}`),
        sidecarRequest('GET', '/settings/ask'),
        sidecarRequest('GET', '/settings/ui'),
        sidecarRequest('GET', '/settings/app'),
      ])
      if (styleRows.ok && styleRows.data) {
        setStyles((styleRows.data as { styles?: StyleRow[] }).styles || [])
      }
      if (ask.ok && ask.data) {
        const status = ask.data as { configured?: Record<string, boolean> }
        setConfigured(status.configured || {})
      }
      if (ui.ok && ui.data) {
        const prefs = ui.data as { default_style?: string }
        setDefaultStyle(prefs.default_style || 'auto')
      }
      if (app.ok && app.data) setInfo(app.data as AppInfo)
    })()
  }, [locale])

  const grouped = useMemo(() => {
    const groups: Record<string, StyleRow[]> = {}
    for (const style of styles) {
      groups[style.domain] = [...(groups[style.domain] || []), style]
    }
    return groups
  }, [styles])

  async function saveKeys() {
    const payload: Record<string, string> = {}
    for (const row of KEY_FIELDS) {
      const value = (draft[row.field] || '').trim()
      if (value) payload[row.field] = value
    }
    if (!Object.keys(payload).length) {
      setMessage(null)
      setError(t('请先填写要保存的密钥'))
      return
    }
    const result = await sidecarRequest('POST', '/settings/ask', payload)
    if (result.ok && result.data) {
      const status = result.data as { configured?: Record<string, boolean> }
      setConfigured(status.configured || {})
      setDraft({})
      setError(null)
      setMessage(t('密钥已保存'))
    } else {
      setMessage(null)
      setError(errorMessage(result.data, t('密钥保存失败')))
    }
  }

  async function saveStyle(value: string) {
    setDefaultStyle(value)
    const result = await sidecarRequest('POST', '/settings/ui', { default_style: value })
    if (result.ok) setMessage(t('保存成功'))
  }

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2" data-testid="settings-view">
      <section className="rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
        <h2 className="text-sm font-medium text-sage">{t('设置')}</h2>
        <div className="mt-4 space-y-4 text-sm">
          <div>
            <p className="mb-1 text-xs uppercase tracking-wide text-slate-500">{t('界面语言')}</p>
            <div className="flex gap-2">
              {(['zh', 'en'] as Locale[]).map((code) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => setLocale(code)}
                  className={`rounded-full px-4 py-1.5 text-sm ring-1 ${locale === code ? 'bg-sage text-white ring-sage' : 'text-sage ring-pool'}`}
                >
                  {code === 'zh' ? '中文' : 'English'}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="mb-1 text-xs uppercase tracking-wide text-slate-500">{t('默认文体')}</p>
            <select
              value={defaultStyle}
              onChange={(event) => void saveStyle(event.target.value)}
              className="w-full rounded-xl border border-pool px-3 py-2"
            >
              <option value="auto">{t('自动（按内容判断）')}</option>
              {styles.map((style) => (
                <option key={style.id} value={style.id}>{style.label}</option>
              ))}
            </select>
          </div>

          <div>
            <p className="mb-1 text-xs uppercase tracking-wide text-slate-500">{t('密钥')}</p>
            <p className="mb-2 text-xs text-slate-500">{t('敏感配置只保存在本机，不会进入知识库数据库。')}</p>
            <div className="space-y-2">
              {KEY_FIELDS.map((row) => {
                const name = row.field.replace('_api_key', '')
                const ready = configured[name]
                return (
                  <label key={row.field} className="block">
                    <span className="flex items-center justify-between text-xs text-slate-600">
                      <span>{row.label}</span>
                      <span className={ready ? 'text-sage' : 'text-slate-400'}>
                        {ready ? t('已配置') : t('未配置')}
                      </span>
                    </span>
                    <input
                      type="password"
                      value={draft[row.field] || ''}
                      onChange={(event) => setDraft({ ...draft, [row.field]: event.target.value })}
                      placeholder={row.env}
                      className="mt-1 w-full rounded-xl border border-pool px-3 py-2 text-sm"
                    />
                  </label>
                )
              })}
            </div>
            <button type="button" onClick={() => void saveKeys()} className="mt-3 rounded-full bg-sage px-4 py-1.5 text-sm text-white">
              {t('保存密钥')}
            </button>
          </div>
        </div>
      </section>

      <section className="space-y-4">
        <div className="rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
          <h2 className="text-sm font-medium text-sage">{t('数据与存储')}</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">{t('数据目录')}</dt>
              <dd className="max-w-[60%] truncate text-right font-mono text-xs" title={info?.data_dir}>{info?.data_dir || '—'}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">{t('数据库大小')}</dt>
              <dd>{info ? formatBytes(info.db_bytes) : '—'}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">{t('版本')}</dt>
              <dd>{info?.version || '—'}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">{t('功能开关')}</dt>
              <dd className="text-right">
                {info
                  ? Object.entries(info.features).map(([key, on]) => `${key}:${on ? t('已启用') : t('未启用')}`).join(' · ')
                  : '—'}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">{t('上下文')}</dt>
              <dd>{info ? tf('分叉阈值：{n} tokens', { n: info.limits.fork_tokens }) : '—'}</dd>
            </div>
          </dl>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <button
              type="button"
              onClick={() => {
                void (async () => {
                  const result = await sidecarRequest('GET', '/conversations/export?format=json')
                  if (!result.ok || !result.data) return
                  const payload = result.data as { payload?: unknown; filename?: string }
                  const blob = new Blob([JSON.stringify(payload.payload ?? payload, null, 2)], { type: 'application/json' })
                  const anchor = document.createElement('a')
                  anchor.href = URL.createObjectURL(blob)
                  anchor.download = payload.filename || 'conversations.json'
                  anchor.click()
                })()
              }}
              className="rounded-full px-3 py-1 text-sage ring-1 ring-pool"
            >
              {t('导出全部对话')}
            </button>
            <label className="cursor-pointer rounded-full px-3 py-1 text-sage ring-1 ring-pool">
              {t('导入对话记录')}
              <input
                type="file"
                accept=".json,.jsonl,.md,.txt"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (!file) return
                  void file.text().then(async (text) => {
                    const result = await sidecarRequest('POST', '/conversations/import', {
                      filename: file.name,
                      content: text,
                    })
                    setMessage(result.ok ? t('导入成功') : null)
                    setError(result.ok ? null : errorMessage(result.data, t('导入失败')))
                  })
                }}
              />
            </label>
          </div>
          <ClearAllButton />
        </div>

        <div className="rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
          <h2 className="text-sm font-medium text-sage">{t('样式目录')}</h2>
          <div className="mt-3 space-y-2 text-xs">
            {Object.entries(grouped).map(([domain, rows]) => (
              <div key={domain}>
                <p className="font-medium text-slate-600">{domain}</p>
                <ul className="ml-3 list-disc text-slate-500">
                  {rows.map((style) => (
                    <li key={style.id}>{style.label} — {style.summary}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        {message ? <p className="text-xs text-sage">{message}</p> : null}
        {error ? <p className="text-xs text-rose-600">{error}</p> : null}
      </section>
    </div>
  )
}
