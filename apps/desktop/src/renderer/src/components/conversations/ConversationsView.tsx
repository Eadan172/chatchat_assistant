import { useCallback, useEffect, useMemo, useState } from 'react'
import { useI18n } from '../../i18n'
import { isHttpLink, openLink } from '../../lib/openLink'
import { errorMessage, sidecarRequest } from '../clarify/client'
import { ClearAllButton } from './ClearAllButton'

interface ConversationSummary {
  id: number
  title: string
  mode: string
  folder_id: number | null
  project_id: number | null
  forked_from_id: number | null
  forked_from_message_id: number | null
  pinned: boolean
  archived: boolean
  created_at: string | null
  updated_at: string | null
  message_count: number
  preview: string
  tokens: number
}

interface MessageRow {
  id: number
  role: string
  content: string
  citations: { n?: number; filename?: string; title?: string; url?: string }[]
  provider?: string | null
  created_at?: string | null
}

interface Detail {
  conversation: ConversationSummary
  messages: MessageRow[]
  context: { tokens: number; limit: number; needs_fork: boolean }
}

interface FolderRow {
  id: number
  name: string
  project_id: number | null
  conversation_count: number
}

interface ProjectRow {
  id: number
  name: string
  color?: string | null
  conversation_count: number
}

function download(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function ConversationsView() {
  const { t, tf, locale } = useI18n()
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [folders, setFolders] = useState<FolderRow[]>([])
  const [projects, setProjects] = useState<ProjectRow[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [query, setQuery] = useState('')
  const [folderFilter, setFolderFilter] = useState<number | 'all' | 'unfiled'>('all')
  const [projectFilter, setProjectFilter] = useState<number | 0>(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [draftFolder, setDraftFolder] = useState('')
  const [draftProject, setDraftProject] = useState('')
  const [editingTitle, setEditingTitle] = useState('')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)

  const reload = useCallback(async () => {
    const [list, folderRows, projectRows] = await Promise.all([
      sidecarRequest('GET', '/conversations?limit=200'),
      sidecarRequest('GET', '/folders'),
      sidecarRequest('GET', '/projects'),
    ])
    if (list.ok && list.data) {
      setItems((list.data as { items?: ConversationSummary[] }).items || [])
    } else {
      setError(errorMessage(list.data, t('知识库列表读取失败')))
    }
    if (folderRows.ok && folderRows.data) {
      setFolders((folderRows.data as { items?: FolderRow[] }).items || [])
    }
    if (projectRows.ok && projectRows.data) {
      setProjects((projectRows.data as { items?: ProjectRow[] }).items || [])
    }
  }, [t])

  useEffect(() => {
    void reload()
    const requested = Number(new URLSearchParams(window.location.search).get('conversation') || 0)
    if (requested > 0) void open(requested)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function open(id: number) {
    const result = await sidecarRequest('GET', `/conversations/${id}`)
    if (result.ok && result.data) {
      const next = result.data as Detail
      setDetail(next)
      setEditingTitle(next.conversation.title)
      setError(null)
    } else {
      setError(errorMessage(result.data, t('请求失败')))
    }
  }

  async function createConversation() {
    setBusy(true)
    const result = await sidecarRequest('POST', '/conversations', {
      title: t('新建对话'),
      folder_id: typeof folderFilter === 'number' ? folderFilter : null,
      project_id: projectFilter || null,
    })
    setBusy(false)
    if (result.ok && result.data) {
      const created = result.data as Detail
      await reload()
      await open(created.conversation.id)
    }
  }

  async function patch(id: number, body: Record<string, unknown>) {
    const result = await sidecarRequest('PATCH', `/conversations/${id}`, body)
    if (result.ok && result.data) {
      const next = result.data as Detail
      setDetail(next)
      setEditingTitle(next.conversation.title)
      await reload()
    } else {
      setError(errorMessage(result.data, t('保存失败')))
    }
  }

  async function continueConversation() {
    if (!detail || !draft.trim() || sending) return
    setSending(true)
    setError(null)
    try {
      const result = await sidecarRequest('POST', '/ask', {
        message: draft.trim(),
        conversation_id: detail.conversation.id,
        clarify: 'skip',
        style: 'auto',
        locale,
      })
      if (!result.ok) throw new Error(errorMessage(result.data, t('发送失败')))
      setDraft('')
      await open(detail.conversation.id)
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('发送失败'))
    } finally {
      setSending(false)
    }
  }

  async function remove(id: number) {
    if (!window.confirm(t('确认删除？'))) return
    const result = await sidecarRequest('DELETE', `/conversations/${id}`)
    if (result.ok) {
      setDetail(null)
      await reload()
    } else {
      setError(errorMessage(result.data, t('删除失败')))
    }
  }

  async function fork(id: number, messageId?: number) {
    const result = await sidecarRequest('POST', `/conversations/${id}/fork`, {
      message_id: messageId ?? null,
    })
    if (result.ok && result.data) {
      const created = result.data as Detail
      await reload()
      await open(created.conversation.id)
    } else {
      setError(errorMessage(result.data, t('请求失败')))
    }
  }

  async function exportOne(id: number, format: 'json' | 'md') {
    const result = await sidecarRequest('GET', `/conversations/${id}/export?format=${format}`)
    if (!result.ok || !result.data) {
      setError(errorMessage(result.data, t('请求失败')))
      return
    }
    const payload = result.data as { content?: string; payload?: unknown; filename?: string }
    if (format === 'md') {
      download(payload.filename || 'conversation.md', payload.content || '', 'text/markdown;charset=utf-8')
    } else {
      download(
        payload.filename || 'conversation.json',
        JSON.stringify(payload.payload ?? payload, null, 2),
        'application/json;charset=utf-8',
      )
    }
  }

  async function exportAll() {
    const result = await sidecarRequest('GET', '/conversations/export?format=json')
    if (!result.ok || !result.data) return
    const payload = result.data as { payload?: unknown; filename?: string }
    download(payload.filename || 'conversations.json', JSON.stringify(payload.payload ?? payload, null, 2), 'application/json;charset=utf-8')
  }

  async function importFile(file: File | null) {
    if (!file) return
    const text = await file.text()
    const result = await sidecarRequest('POST', '/conversations/import', {
      filename: file.name,
      content: text,
    })
    if (result.ok) {
      await reload()
      setError(null)
    } else {
      setError(errorMessage(result.data, t('导入失败')))
    }
  }

  async function addFolder() {
    const name = draftFolder.trim()
    if (!name) return
    const result = await sidecarRequest('POST', '/folders', {
      name,
      project_id: projectFilter || null,
    })
    if (result.ok) {
      setDraftFolder('')
      await reload()
    }
  }

  async function addProject() {
    const name = draftProject.trim()
    if (!name) return
    const result = await sidecarRequest('POST', '/projects', { name })
    if (result.ok) {
      setDraftProject('')
      await reload()
    }
  }

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return items.filter((row) => {
      if (folderFilter === 'unfiled' && row.folder_id) return false
      if (typeof folderFilter === 'number' && row.folder_id !== folderFilter) return false
      if (projectFilter && row.project_id !== projectFilter) return false
      if (needle && !row.title.toLowerCase().includes(needle)) return false
      return true
    })
  }, [items, query, folderFilter, projectFilter])

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <section className="rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool" data-testid="conversation-list">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-medium text-sage">{t('对话列表')}</h2>
          <button
            type="button"
            onClick={() => void createConversation()}
            disabled={busy}
            className="rounded-full bg-sage px-3 py-1 text-xs text-white disabled:opacity-50"
          >
            {t('新建对话')}
          </button>
        </div>

        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('搜索对话')}
          className="mt-3 w-full rounded-xl border border-pool bg-white px-3 py-2 text-sm outline-none focus:border-sage"
        />

        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <button
            type="button"
            onClick={() => setFolderFilter('all')}
            className={`rounded-full px-3 py-1 ring-1 ${folderFilter === 'all' ? 'bg-sage text-white ring-sage' : 'text-sage ring-pool'}`}
          >
            {t('全部对话')}
          </button>
          <button
            type="button"
            onClick={() => setFolderFilter('unfiled')}
            className={`rounded-full px-3 py-1 ring-1 ${folderFilter === 'unfiled' ? 'bg-sage text-white ring-sage' : 'text-sage ring-pool'}`}
          >
            {t('未归类')}
          </button>
        </div>

        <div className="mt-4 space-y-1">
          <p className="text-xs uppercase tracking-wide text-slate-500">{t('文件夹')}</p>
          <div className="max-h-[150px] space-y-1 overflow-auto pr-1">
            {folders.map((folder) => (
            <button
              key={folder.id}
              type="button"
              onClick={() => setFolderFilter(folder.id)}
              className={`flex w-full items-center justify-between rounded-lg px-2 py-1 text-sm ${folderFilter === folder.id ? 'bg-pool/60 text-ink' : 'text-slate-700 hover:bg-pool/30'}`}
            >
              <span className="truncate">{folder.name}</span>
              <span className="text-xs text-slate-500">{folder.conversation_count}</span>
            </button>
            ))}
          </div>
          <div className="flex gap-1 pt-1">
            <input
              value={draftFolder}
              onChange={(event) => setDraftFolder(event.target.value)}
              placeholder={t('新建文件夹')}
              className="min-w-0 flex-1 rounded-lg border border-pool px-2 py-1 text-xs"
            />
            <button type="button" onClick={() => void addFolder()} className="rounded-lg px-2 py-1 text-xs text-sage ring-1 ring-pool">
              +
            </button>
          </div>
        </div>

        <div className="mt-4 space-y-1">
          <p className="text-xs uppercase tracking-wide text-slate-500">{t('项目')}</p>
          <select
            value={projectFilter}
            onChange={(event) => setProjectFilter(Number(event.target.value))}
            className="w-full rounded-lg border border-pool px-2 py-1 text-sm"
          >
            <option value={0}>{t('全部对话')}</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name} ({project.conversation_count})
              </option>
            ))}
          </select>
          <div className="flex gap-1 pt-1">
            <input
              value={draftProject}
              onChange={(event) => setDraftProject(event.target.value)}
              placeholder={t('新建项目')}
              className="min-w-0 flex-1 rounded-lg border border-pool px-2 py-1 text-xs"
            />
            <button type="button" onClick={() => void addProject()} className="rounded-lg px-2 py-1 text-xs text-sage ring-1 ring-pool">
              +
            </button>
          </div>
        </div>

        <div className="mt-4 max-h-[300px] space-y-1 overflow-auto pr-1">
          {visible.length === 0 ? <p className="text-xs text-slate-500">{t('暂无对话')}</p> : null}
          {visible.map((row) => (
            <button
              key={row.id}
              type="button"
              onClick={() => void open(row.id)}
              className={`w-full rounded-xl px-3 py-2 text-left text-sm ${detail?.conversation.id === row.id ? 'bg-pool/70' : 'hover:bg-pool/30'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{row.pinned ? '★ ' : ''}{row.title}</span>
                <span className="shrink-0 text-[11px] text-slate-500">{row.message_count}</span>
              </div>
              <p className="mt-0.5 truncate text-xs text-slate-500">{row.preview || '—'}</p>
            </button>
          ))}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <label className="cursor-pointer rounded-full px-3 py-1 text-sage ring-1 ring-pool">
            {t('导入对话')}
            <input
              type="file"
              accept=".json,.jsonl,.md,.txt"
              className="hidden"
              onChange={(event) => void importFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <button type="button" onClick={() => void exportAll()} className="rounded-full px-3 py-1 text-sage ring-1 ring-pool">
            {t('全部导出')}
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-slate-500">{t('支持 ChatGPT / Claude / Claude Code / Markdown 导出文件')}</p>
      </section>

      <div className="space-y-4">
        <section className="rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool" data-testid="conversation-detail">
        {!detail ? (
          <p className="py-16 text-center text-sm text-slate-500">{t('选择左侧对话查看内容')}</p>
        ) : (
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={editingTitle}
                onChange={(event) => setEditingTitle(event.target.value)}
                onBlur={() => {
                  const next = editingTitle.trim()
                  if (next && next !== detail.conversation.title) void patch(detail.conversation.id, { title: next })
                }}
                className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-1 py-1 text-lg font-semibold hover:border-pool focus:border-sage focus:outline-none"
              />
              <button type="button" onClick={() => void patch(detail.conversation.id, { pinned: !detail.conversation.pinned })} className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool">
                {t('置顶')}
              </button>
              <button type="button" onClick={() => void fork(detail.conversation.id)} className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool">
                {t('分叉')}
              </button>
              <button type="button" onClick={() => void exportOne(detail.conversation.id, 'json')} className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool">
                JSON
              </button>
              <button type="button" onClick={() => void exportOne(detail.conversation.id, 'md')} className="rounded-full px-3 py-1 text-xs text-sage ring-1 ring-pool">
                Markdown
              </button>
              <button type="button" onClick={() => void remove(detail.conversation.id)} className="rounded-full px-3 py-1 text-xs text-rose-600 ring-1 ring-rose-200">
                {t('删除')}
              </button>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-600">
              <label className="flex items-center gap-1">
                {t('文件夹')}
                <select
                  value={detail.conversation.folder_id ?? 0}
                  onChange={(event) =>
                    void patch(detail.conversation.id, { folder_id: Number(event.target.value) || null })
                  }
                  className="rounded-lg border border-pool px-2 py-1"
                >
                  <option value={0}>{t('未归类')}</option>
                  {folders.map((folder) => (
                    <option key={folder.id} value={folder.id}>{folder.name}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1">
                {t('项目')}
                <select
                  value={detail.conversation.project_id ?? 0}
                  onChange={(event) =>
                    void patch(detail.conversation.id, { project_id: Number(event.target.value) || null })
                  }
                  className="rounded-lg border border-pool px-2 py-1"
                >
                  <option value={0}>{t('未归类')}</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>{project.name}</option>
                  ))}
                </select>
              </label>
              <span>
                {tf('{n} 条消息', { n: detail.messages.length })} · {tf('当前 {tokens} tokens / 上限 {limit}', {
                  tokens: detail.context.tokens,
                  limit: detail.context.limit,
                })}
              </span>
              {detail.conversation.forked_from_id ? (
                <span className="rounded-full bg-pool px-2 py-0.5">
                  {t('分支来源')} #{detail.conversation.forked_from_id}
                </span>
              ) : null}
            </div>

            {detail.context.needs_fork ? (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-amber-200">
                <span>{t('超出上限，建议分叉')}</span>
                <button type="button" onClick={() => void fork(detail.conversation.id)} className="rounded-full bg-amber-600 px-3 py-1 text-white">
                  {t('分叉')}
                </button>
              </div>
            ) : null}

            <div className="mt-4 max-h-[520px] space-y-3 overflow-auto pr-1">
              {detail.messages.map((message) => (
                <article
                  key={message.id}
                  className={`rounded-xl px-3 py-2 text-sm ${message.role === 'user' ? 'bg-pool/50' : 'bg-white ring-1 ring-pool'}`}
                >
                  <header className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wide text-slate-500">
                    <span>{message.role === 'user' ? t('你') : 'AI'}</span>
                    <button
                      type="button"
                      onClick={() => void fork(detail.conversation.id, message.id)}
                      className="rounded-full px-2 py-0.5 text-[11px] text-sage ring-1 ring-pool"
                    >
                      {t('在此分叉')}
                    </button>
                  </header>
                  <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
                  {message.citations.length ? (
                    <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-slate-500">
                      <span>{t('来源')}:</span>
                      {message.citations.map((item, index) =>
                        isHttpLink(item.url) ? (
                          <button
                            key={`${item.n}-${index}`}
                            type="button"
                            title={item.url}
                            onClick={() => openLink(item.url)}
                            className="text-sage underline"
                          >
                            [{item.n}] {item.filename || item.title || item.url}
                          </button>
                        ) : (
                          <span key={`${item.n}-${index}`}>
                            [{item.n}] {item.filename || item.title || ''}
                          </span>
                        ),
                      )}
                    </div>
                  ) : null}
                </article>
              ))}
            </div>

            <form
              className="mt-4 flex gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                void continueConversation()
              }}
            >
              <input
                data-testid="conversation-input"
                value={draft}
                disabled={sending}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={t('输入问题，追加到当前对话…')}
                className="min-w-0 flex-1 rounded-full bg-mist px-4 py-2 text-sm outline-none ring-1 ring-pool disabled:opacity-60"
              />
              <button
                data-testid="conversation-send"
                type="submit"
                disabled={sending || !draft.trim()}
                className="rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {sending ? t('正在回答…') : t('发送')}
              </button>
            </form>
            <p className="mt-1 text-[11px] text-slate-500">{t('继续提问会追加到这条对话，历史记录保留。')}</p>
          </div>
        )}
        {error ? <p className="mt-3 text-xs text-rose-600">{error}</p> : null}
        </section>
        <ClearAllButton
          onCleared={async () => {
            setDetail(null)
            setFolderFilter('all')
            setProjectFilter(0)
            await reload()
          }}
        />
      </div>
    </div>
  )
}
