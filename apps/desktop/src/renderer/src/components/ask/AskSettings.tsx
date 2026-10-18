import { useEffect, useState } from 'react'
import { useI18n } from '../../i18n'
import { errorMessage, sidecarRequest } from '../clarify/client'

const FIELDS = [
  { name: 'deepseek_api_key', label: 'DeepSeek', hint: 'DEEPSEEK_API_KEY' },
  { name: 'serper_api_key', label: 'Serper（Google）', hint: 'SERPER_API_KEY' },
  { name: 'serpapi_api_key', label: 'SerpApi（Bing）', hint: 'SERPAPI_API_KEY' },
  { name: 'tavily_api_key', label: 'Tavily', hint: 'TAVILY_API_KEY' },
] as const

type FieldName = (typeof FIELDS)[number]['name']

export function AskSettings() {
  const { t } = useI18n()
  const [configured, setConfigured] = useState<Record<string, boolean>>({})
  const [source, setSource] = useState<Record<string, string | null>>({})
  const [drafts, setDrafts] = useState<Record<FieldName, string>>({
    deepseek_api_key: '',
    serper_api_key: '',
    serpapi_api_key: '',
    tavily_api_key: '',
  })
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  useEffect(() => {
    void sidecarRequest('GET', '/settings/ask').then((result) => {
      if (!result.ok || !result.data || typeof result.data !== 'object') return
      const body = result.data as { configured?: Record<string, boolean>; credential_source?: Record<string, string | null> }
      setConfigured(body.configured || {})
      setSource(body.credential_source || {})
    })
  }, [])

  async function save() {
    const payload: Record<string, string> = {}
    for (const field of FIELDS) {
      const value = drafts[field.name].trim()
      if (value) payload[field.name] = value
    }
    if (!Object.keys(payload).length) return
    setPending(true)
    setError(null)
    setNotice(null)
    try {
      const result = await sidecarRequest('POST', '/settings/ask', payload)
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('密钥保存失败')))
      const body = result.data as { configured?: Record<string, boolean>; credential_source?: Record<string, string | null> }
      setConfigured(body.configured || {})
      setSource(body.credential_source || {})
      setDrafts({ deepseek_api_key: '', serper_api_key: '', serpapi_api_key: '', tavily_api_key: '' })
      setNotice(t('密钥已保存在本机，不会写入知识库数据库，也不会出现在日志里。'))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('密钥保存失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <section data-feature="ask" className="mt-6 rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
      <h2 className="text-sm font-medium text-sage">{t('问答密钥')}</h2>
      <p className="mt-2 text-sm text-slate-600">
        {t('DeepSeek 用来选择来源和写回答。联网搜索按 Serper、SerpApi、Tavily 的顺序兜底。留空表示不修改已保存的值。')}
      </p>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {FIELDS.map((field) => (
          <label key={field.name} className="block text-sm">
            <span className="text-slate-600">
              {field.label}
              <span className="ml-2 text-xs text-slate-400">{configured[field.name] ? t('已配置') : t('未配置')}</span>
              {source[field.name] ? <span className="ml-2 text-xs text-slate-400">{source[field.name]}</span> : null}
            </span>
            <input
              data-testid={`ask-key-${field.name}`}
              type="password"
              autoComplete="off"
              className="mt-1 w-full rounded-xl bg-mist px-3 py-2 text-sm outline-none ring-1 ring-pool"
              placeholder={field.hint}
              value={drafts[field.name]}
              onChange={(event) => setDrafts((current) => ({ ...current, [field.name]: event.target.value }))}
            />
          </label>
        ))}
      </div>
      {notice ? <p className="mt-3 text-sm text-sage">{notice}</p> : null}
      {error ? <p className="mt-3 text-sm">{error}</p> : null}
      <button
        type="button"
        data-testid="ask-key-save"
        className="mt-4 rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-50"
        disabled={pending}
        onClick={() => void save()}
      >
        {t('保存密钥')}
      </button>
    </section>
  )
}
