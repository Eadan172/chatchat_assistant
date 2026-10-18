import { useEffect, useState } from 'react'
import { useI18n } from '../../i18n'
import { openLink } from '../../lib/openLink'
import { errorMessage, sidecarRequest } from '../clarify/client'
import { verifyItems } from '../verify/client'
import { freshnessClass, freshnessLabel } from '../verify/view'

interface LibraryItem {
  id: number
  title?: string | null
  filename?: string
  platform?: string
  index_state?: string
  chunk_count?: number
  last_error?: string | null
  freshness_state?: string
  freshness_reason?: string | null
  source_path?: string | null
  prompt_update?: boolean
}

interface ImportSourceRow {
  id: string
  name: string
  kind: string
  implemented: boolean
  configured: boolean
  ui: string
  note?: string | null
  docs_url?: string | null
}

function asItems(data: unknown): LibraryItem[] {
  if (!data || typeof data !== 'object' || !('items' in data)) return []
  const items = (data as { items?: unknown }).items
  if (!Array.isArray(items)) return []
  return items.filter((item): item is LibraryItem => !!item && typeof item === 'object' && typeof (item as LibraryItem).id === 'number')
}

async function fileToBase64(file: File): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
  const comma = dataUrl.indexOf(',')
  return comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl
}

export function LibraryPanel({ verifyOn = false }: { verifyOn?: boolean }) {
  const { t, tf, locale } = useI18n()
  const [items, setItems] = useState<LibraryItem[]>([])
  const [sources, setSources] = useState<ImportSourceRow[]>([])
  const [activeSource, setActiveSource] = useState<ImportSourceRow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [pathDraft, setPathDraft] = useState('')
  const [checkingId, setCheckingId] = useState<number | null>(null)

  function statusLabel(state?: string): string {
    if (state === 'indexed') return t('已索引')
    if (state === 'failed') return t('失败')
    if (state === 'pending') return t('待索引')
    return state || t('未知')
  }

  async function reload() {
    const result = await sidecarRequest('GET', '/library/items')
    if (!result.ok) {
      setError(result.error || errorMessage(result.data, t('知识库列表读取失败')))
      return
    }
    setItems(asItems(result.data))
  }

  useEffect(() => {
    void reload()
    const onChange = () => void reload()
    window.addEventListener('library-changed', onChange)
    return () => window.removeEventListener('library-changed', onChange)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void sidecarRequest('GET', `/import/sources?locale=${locale}`).then((result) => {
      if (result.ok && result.data) {
        setSources((result.data as { sources?: ImportSourceRow[] }).sources || [])
      }
    })
  }, [locale])

  async function onFiles(files: FileList | null) {
    const file = files?.item(0)
    if (!file) return
    setPending(true)
    setError(null)
    try {
      const content = await fileToBase64(file)
      const result = await sidecarRequest('POST', '/import/documents', {
        filename: file.name,
        content_base64: content,
        source: 'local',
      })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('导入失败')))
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('导入失败'))
    } finally {
      setPending(false)
    }
  }

  async function reindex() {
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('POST', '/index/rebuild', { scope: 'all' })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('重建索引失败')))
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('重建索引失败'))
    } finally {
      setPending(false)
    }
  }

  async function importPath(path: string) {
    const trimmed = path.trim()
    if (!trimmed) return
    setPending(true)
    setError(null)
    try {
      const filename = trimmed.split(/[/\\]/).pop() || 'document.md'
      const result = await sidecarRequest('POST', '/library/import', { path: trimmed, filename })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('导入失败')))
      setPathDraft('')
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('导入失败'))
    } finally {
      setPending(false)
    }
  }

  async function check(id: number, mode: 'hash_mtime' | 'refetch') {
    setCheckingId(id)
    setError(null)
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, freshness_state: 'verifying' } : item)),
    )
    try {
      const job = await verifyItems([id], mode)
      const row = job.items.find((item) => item.item_id === id)
      if (job.state === 'failed') throw new Error(t('查证失败'))
      setItems((current) =>
        current.map((item) =>
          item.id === id
            ? {
                ...item,
                freshness_state: row?.state || item.freshness_state,
                prompt_update: row?.state === 'stale',
                freshness_reason: row?.reason,
              }
            : item,
        ),
      )
      if (mode === 'refetch') await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('查证失败'))
      await reload()
    } finally {
      setCheckingId(null)
    }
  }

  async function remove(id: number) {
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('DELETE', `/library/items/${id}`)
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, t('删除失败')))
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('删除失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool" data-testid="library-panel">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-sage">{t('知识库')}</h2>
        <button
          type="button"
          className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool disabled:opacity-50"
          onClick={() => void reindex()}
          disabled={pending}
        >
          {t('重建索引')}
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-500">{t('支持 md、txt、pdf、docx。导入后会分块并写入全文和向量索引。')}</p>
      <label className="mt-4 inline-flex cursor-pointer rounded-full bg-sage px-4 py-2 text-sm text-white">
        {pending ? t('处理中…') : t('导入文件')}
        <input
          data-testid="library-file"
          className="sr-only"
          type="file"
          accept=".md,.markdown,.txt,.pdf,.docx,text/plain,text/markdown,application/pdf"
          disabled={pending}
          onChange={(event) => {
            void onFiles(event.target.files)
            event.target.value = ''
          }}
        />
      </label>
      {verifyOn ? (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            void importPath(pathDraft)
          }}
        >
          <input
            data-testid="library-path"
            className="min-w-0 flex-1 rounded-full bg-mist px-3 py-1 text-xs outline-none ring-1 ring-pool"
            placeholder={t('本地文件路径，便于之后对账')}
            value={pathDraft}
            onChange={(event) => setPathDraft(event.target.value)}
          />
          <button
            type="submit"
            data-testid="library-path-import"
            className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool disabled:opacity-50"
            disabled={pending || !pathDraft.trim()}
          >
            {t('按路径导入')}
          </button>
        </form>
      ) : null}

      <div className="mt-4 rounded-xl bg-mist/50 px-3 py-2" data-testid="import-sources">
        <p className="text-[11px] uppercase tracking-wide text-slate-500">{t('导入通道')}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {sources.map((source) => {
            const label = source.implemented ? t('已接入') : t('占位接口')
            return (
              <button
                key={source.id}
                type="button"
                onClick={() => setActiveSource(activeSource?.id === source.id ? null : source)}
                className={`rounded-full px-2.5 py-1 text-xs ring-1 ${source.implemented ? 'bg-white text-sage ring-sage/40' : 'bg-white/60 text-slate-500 ring-pool'}`}
              >
                {source.name} · {label}
              </button>
            )
          })}
        </div>
        {activeSource ? (
          <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
            {activeSource.note || t('该通道本期仅提供统一接口占位，尚未接入。')}
            {activeSource.docs_url ? (
              <button
                type="button"
                className="ml-1 text-sage underline"
                onClick={() => openLink(activeSource.docs_url)}
              >
                {t('查看接口说明')}
              </button>
            ) : null}
          </p>
        ) : null}
      </div>

      {error ? <p className="mt-3 text-sm text-ink">{error}</p> : null}
      <ul className="mt-4 space-y-2">
        {items.length === 0 ? <li className="text-sm text-slate-500">{t('还没有文档。')}</li> : null}
        {items.map((item) => (
          <li
            key={item.id}
            data-testid="library-item"
            data-source-path={item.source_path || ''}
            data-freshness={item.freshness_state || 'unknown'}
            className="rounded-xl bg-mist/70 px-3 py-2"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm">
                  {item.filename || item.title || t('未命名')}
                  {item.platform === 'ima' ? (
                    <span data-testid="library-source" data-source="ima" className="ml-2 rounded-full bg-pool px-2 py-0.5 text-xs">
                      {t('应用知识库')}
                    </span>
                  ) : null}
                </p>
                <p className="text-xs text-slate-500" data-testid="library-status">
                  {statusLabel(item.index_state)}
                  {typeof item.chunk_count === 'number' ? tf(' · {n} 块', { n: item.chunk_count }) : ''}
                  {item.last_error ? ` · ${item.last_error}` : ''}
                </p>
              </div>
              <button type="button" className="text-xs text-sage" onClick={() => void remove(item.id)} disabled={pending}>
                {t('删除')}
              </button>
            </div>
            {verifyOn ? (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span
                  data-testid="freshness-badge"
                  data-state={checkingId === item.id ? 'verifying' : item.freshness_state || 'unknown'}
                  className={`rounded-full px-2 py-0.5 text-xs ${freshnessClass(checkingId === item.id ? 'verifying' : item.freshness_state)}`}
                >
                  {t(freshnessLabel(checkingId === item.id ? 'verifying' : item.freshness_state))}
                </span>
                <button
                  type="button"
                  data-testid="verify-button"
                  className="rounded-full px-2 py-0.5 text-xs text-sage ring-1 ring-pool disabled:opacity-50"
                  disabled={pending || checkingId === item.id}
                  onClick={() => void check(item.id, 'hash_mtime')}
                >
                  {checkingId === item.id ? t('查证中…') : t('查证')}
                </button>
              </div>
            ) : null}
            {verifyOn && item.freshness_state === 'stale' && checkingId !== item.id ? (
              <div data-testid="update-prompt" className="mt-2 rounded-xl bg-amber-100 px-3 py-2 text-xs text-amber-950">
                {t('文件内容和知识库不一致，可能已过期。要重新导入并更新知识库吗？')}
                <button
                  type="button"
                  data-testid="update-library"
                  className="ml-2 rounded-full bg-sage px-2 py-0.5 text-white"
                  onClick={() => void check(item.id, 'refetch')}
                >
                  {t('更新')}
                </button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
