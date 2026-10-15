import { useEffect, useState } from 'react'
import { errorMessage, sidecarRequest } from '../clarify/client'

interface LibraryItem {
  id: number
  title?: string | null
  filename?: string
  index_state?: string
  chunk_count?: number
  last_error?: string | null
}

function asItems(data: unknown): LibraryItem[] {
  if (!data || typeof data !== 'object' || !('items' in data)) return []
  const items = (data as { items?: unknown }).items
  if (!Array.isArray(items)) return []
  return items.filter((item): item is LibraryItem => !!item && typeof item === 'object' && typeof (item as LibraryItem).id === 'number')
}

function statusLabel(state?: string): string {
  if (state === 'indexed') return '已索引'
  if (state === 'failed') return '失败'
  if (state === 'pending') return '待索引'
  return state || '未知'
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

export function LibraryPanel() {
  const [items, setItems] = useState<LibraryItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function reload() {
    const result = await sidecarRequest('GET', '/library/items')
    if (!result.ok) {
      setError(result.error || errorMessage(result.data, '知识库列表读取失败'))
      return
    }
    setItems(asItems(result.data))
  }

  useEffect(() => {
    void reload()
  }, [])

  async function onFiles(files: FileList | null) {
    const file = files?.item(0)
    if (!file) return
    setPending(true)
    setError(null)
    try {
      const content_base64 = await fileToBase64(file)
      const result = await sidecarRequest('POST', '/library/import', { filename: file.name, content_base64 })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, '导入失败'))
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '导入失败')
    } finally {
      setPending(false)
    }
  }

  async function reindex() {
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('POST', '/index/rebuild', { scope: 'all' })
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, '重建索引失败'))
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '重建索引失败')
    } finally {
      setPending(false)
    }
  }

  async function remove(id: number) {
    setPending(true)
    setError(null)
    try {
      const result = await sidecarRequest('DELETE', `/library/items/${id}`)
      if (!result.ok) throw new Error(result.error || errorMessage(result.data, '删除失败'))
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '删除失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool" data-testid="library-panel">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-sage">知识库</h2>
        <button
          type="button"
          className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool disabled:opacity-50"
          onClick={() => void reindex()}
          disabled={pending}
        >
          重建索引
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-500">支持 md、txt、pdf、docx。导入后会分块并写入全文和向量索引。</p>
      <label className="mt-4 inline-flex cursor-pointer rounded-full bg-sage px-4 py-2 text-sm text-white">
        {pending ? '处理中…' : '导入文件'}
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
      {error ? <p className="mt-3 text-sm text-ink">{error}</p> : null}
      <ul className="mt-4 space-y-2">
        {items.length === 0 ? <li className="text-sm text-slate-500">还没有文档。</li> : null}
        {items.map((item) => (
          <li
            key={item.id}
            data-testid="library-item"
            className="flex items-start justify-between gap-3 rounded-xl bg-mist/70 px-3 py-2"
          >
            <div>
              <p className="text-sm">{item.filename || item.title || '未命名'}</p>
              <p className="text-xs text-slate-500" data-testid="library-status">
                {statusLabel(item.index_state)}
                {typeof item.chunk_count === 'number' ? ` · ${item.chunk_count} 块` : ''}
                {item.last_error ? ` · ${item.last_error}` : ''}
              </p>
            </div>
            <button type="button" className="text-xs text-sage" onClick={() => void remove(item.id)} disabled={pending}>
              删除
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
