import { useEffect, useState } from 'react'
import { useI18n } from '../../i18n'
import { errorMessage, sidecarRequest } from '../clarify/client'

interface KnowledgeBase {
  id: string
  name: string
  description?: string
}

interface Notebook {
  id: string
  name: string
  note_count?: number
}

interface ImaEntry {
  kind: 'note' | 'media' | 'folder'
  id: string
  title: string
  snippet?: string
  summary?: string
  knowledge_base_id?: string
  file_number?: number
  folder_number?: number
}

function asList<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

export function ImaPanel() {
  const { t, tf } = useI18n()
  const [clientId, setClientId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [configured, setConfigured] = useState(false)
  const [source, setSource] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [notebooks, setNotebooks] = useState<Notebook[]>([])
  const [activeKb, setActiveKb] = useState<string | null>(null)
  const [folders, setFolders] = useState<ImaEntry[]>([])
  const [entries, setEntries] = useState<ImaEntry[]>([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Record<string, ImaEntry>>({})
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    void sidecarRequest('GET', '/settings/ima').then((result) => {
      if (!result.ok || !result.data || typeof result.data !== 'object') return
      const body = result.data as { configured?: boolean; credential_source?: string | null }
      setConfigured(Boolean(body.configured))
      setSource(body.credential_source ?? null)
    })
  }, [])

  function toggle(entry: ImaEntry) {
    if (entry.kind === 'folder') return
    setSelected((current) => {
      const next = { ...current }
      if (next[entry.id]) delete next[entry.id]
      else next[entry.id] = entry
      return next
    })
  }

  async function saveKeys() {
    setPending(true)
    setError(null)
    setNotice(null)
    try {
      const result = await sidecarRequest('POST', '/settings/ima', { client_id: clientId, api_key: apiKey })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('ima 密钥保存失败')))
      setConfigured(true)
      setApiKey('')
      const body = result.data as { credential_source?: string }
      setSource(body.credential_source ?? 'settings')
      setNotice(t('密钥已保存在本机，不会写入知识库数据库。'))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('ima 密钥保存失败'))
    } finally {
      setPending(false)
    }
  }

  async function loadBases() {
    setPending(true)
    setError(null)
    setNotice(null)
    try {
      const result = await sidecarRequest('GET', '/connectors/ima/bases')
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('无法读取 ima 知识库')))
      const body = result.data as { knowledge_bases?: KnowledgeBase[]; notebooks?: Notebook[] }
      setBases(asList<KnowledgeBase>(body.knowledge_bases))
      setNotebooks(asList<Notebook>(body.notebooks))
      setFolders([])
      setEntries([])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('无法读取 ima 知识库'))
    } finally {
      setPending(false)
    }
  }

  async function openBase(id: string) {
    setActiveKb(id)
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('GET', `/connectors/ima/folders?knowledge_base_id=${encodeURIComponent(id)}`)
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('无法读取文件夹')))
      const body = result.data as { folders?: ImaEntry[]; items?: ImaEntry[] }
      setFolders(asList<ImaEntry>(body.folders))
      setEntries(asList<ImaEntry>(body.items))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('无法读取文件夹'))
    } finally {
      setPending(false)
    }
  }

  async function openFolder(folderId: string) {
    if (!activeKb) return
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest(
        'GET',
        `/connectors/ima/folders?knowledge_base_id=${encodeURIComponent(activeKb)}&folder_id=${encodeURIComponent(folderId)}`,
      )
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('无法读取文件夹')))
      const body = result.data as { folders?: ImaEntry[]; items?: ImaEntry[] }
      setFolders(asList<ImaEntry>(body.folders))
      setEntries(asList<ImaEntry>(body.items))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('无法读取文件夹'))
    } finally {
      setPending(false)
    }
  }

  async function openNotebook(id: string) {
    setActiveKb(null)
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('GET', `/connectors/ima/folders?notebook_id=${encodeURIComponent(id)}`)
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('无法读取笔记')))
      const body = result.data as { items?: ImaEntry[] }
      setFolders([])
      setEntries(asList<ImaEntry>(body.items))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('无法读取笔记'))
    } finally {
      setPending(false)
    }
  }

  async function runSearch() {
    const text = query.trim()
    if (!text) return
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('POST', '/connectors/ima/search', {
        query: text,
        knowledge_base_id: activeKb,
      })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('搜索失败')))
      const body = result.data as { hits?: ImaEntry[] }
      setFolders([])
      setEntries(asList<ImaEntry>(body.hits))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('搜索失败'))
    } finally {
      setPending(false)
    }
  }

  async function importSelected() {
    const items = Object.values(selected).filter((entry) => entry.kind === 'note' || entry.kind === 'media')
    if (!items.length) return
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('POST', '/connectors/ima/import', {
        items: items.map((entry) => ({
          kind: entry.kind,
          id: entry.id,
          title: entry.title,
          knowledge_base_id: entry.knowledge_base_id,
        })),
      })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('导入失败')))
      const body = result.data as { count?: number }
      setNotice(tf('已导入 {n} 条到本地知识库。', { n: body.count ?? items.length }))
      setSelected({})
      window.dispatchEvent(new Event('library-changed'))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('导入失败'))
    } finally {
      setPending(false)
    }
  }

  const selectedCount = Object.keys(selected).length

  return (
    <section className="mt-6 rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool" data-testid="ima-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-sage">{t('腾讯 ima')}</h2>
        <p className="text-xs text-slate-500" data-testid="ima-credential-state">
          {configured
            ? tf('密钥来源：{source}', { source: source === 'env' ? t('环境变量') : t('本机设置') })
            : t('尚未配置密钥')}
        </p>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        {t('密钥只保存在本机。从 https://ima.qq.com/agent-interface 复制 Client ID 和 API Key，不要使用登录 Cookie。')}
      </p>
      <form
        className="mt-3 flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          void saveKeys()
        }}
      >
        <input
          data-testid="ima-client-id"
          className="min-w-40 flex-1 rounded-full bg-mist px-3 py-1 text-xs outline-none ring-1 ring-pool"
          placeholder="IMA Client ID"
          value={clientId}
          autoComplete="off"
          onChange={(event) => setClientId(event.target.value)}
        />
        <input
          data-testid="ima-api-key"
          type="password"
          className="min-w-40 flex-1 rounded-full bg-mist px-3 py-1 text-xs outline-none ring-1 ring-pool"
          placeholder="IMA API Key"
          value={apiKey}
          autoComplete="off"
          onChange={(event) => setApiKey(event.target.value)}
        />
        <button
          type="submit"
          data-testid="ima-save"
          className="rounded-full bg-sage px-3 py-1 text-xs text-white disabled:opacity-50"
          disabled={pending || !clientId.trim() || !apiKey.trim()}
        >
          {t('保存密钥')}
        </button>
      </form>
      {error ? (
        <p role="alert" data-testid="ima-error" className="mt-3 rounded-xl bg-amber-100 px-3 py-2 text-sm text-amber-950">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p data-testid="ima-notice" className="mt-3 text-sm text-sage">
          {notice}
        </p>
      ) : null}
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="ima-list"
          className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool disabled:opacity-50"
          disabled={pending}
          onClick={() => void loadBases()}
        >
          {t('列出知识库')}
        </button>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div>
          <p className="text-xs text-slate-500">{t('知识库')}</p>
          <ul className="mt-1 space-y-1" data-testid="ima-bases">
            {bases.map((base) => (
              <li key={base.id}>
                <button
                  type="button"
                  data-testid="ima-base"
                  className="text-left text-sm text-ink"
                  onClick={() => void openBase(base.id)}
                >
                  {base.name}
                  {base.description ? <span className="ml-2 text-xs text-slate-500">{base.description}</span> : null}
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-slate-500">{t('笔记本')}</p>
          <ul className="mt-1 space-y-1" data-testid="ima-notebooks">
            {notebooks.map((folder) => (
              <li key={folder.id}>
                <button type="button" data-testid="ima-notebook" className="text-sm text-ink" onClick={() => void openNotebook(folder.id)}>
                  {folder.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-xs text-slate-500">{t('文件夹')}</p>
          <ul className="mt-1 space-y-1" data-testid="ima-folders">
            {folders.map((folder) => (
              <li key={folder.id}>
                <button type="button" data-testid="ima-folder" className="text-sm text-ink" onClick={() => void openFolder(folder.id)}>
                  {folder.title}
                  {typeof folder.file_number === 'number' ? tf(' · {n} 个文件', { n: folder.file_number }) : ''}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          void runSearch()
        }}
      >
        <input
          data-testid="ima-query"
          className="min-w-0 flex-1 rounded-full bg-mist px-3 py-1 text-sm outline-none ring-1 ring-pool"
          placeholder={t('在笔记和当前知识库里搜索')}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button
          type="submit"
          data-testid="ima-search"
          className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool disabled:opacity-50"
          disabled={pending || !query.trim()}
        >
          {t('搜索')}
        </button>
      </form>
      <ul className="mt-3 space-y-2" data-testid="ima-results">
        {entries.map((entry) => (
          <li key={`${entry.kind}-${entry.id}`} data-testid="ima-result" className="rounded-xl bg-mist/70 px-3 py-2">
            <label className="flex items-start gap-2 text-sm">
              {entry.kind === 'folder' ? null : (
                <input
                  data-testid="ima-select"
                  type="checkbox"
                  checked={Boolean(selected[entry.id])}
                  onChange={() => toggle(entry)}
                />
              )}
              <span>
                <span className="text-ink">{entry.title}</span>
                <span className="ml-2 text-xs text-slate-500">
                  {entry.kind === 'note' ? t('笔记') : entry.kind === 'folder' ? t('文件夹') : t('文件')}
                </span>
                {entry.snippet ? <span className="mt-1 block text-xs text-slate-500">{entry.snippet}</span> : null}
              </span>
            </label>
          </li>
        ))}
      </ul>
      <button
        type="button"
        data-testid="ima-import"
        className="mt-3 rounded-full bg-sage px-3 py-1 text-xs text-white disabled:opacity-50"
        disabled={pending || selectedCount === 0}
        onClick={() => void importSelected()}
      >
        {t('导入所选')}{selectedCount ? `（${selectedCount}）` : ''}
      </button>
    </section>
  )
}
