import { useState } from 'react'
import { errorMessage, sidecarRequest, type SidecarEvent } from '../clarify/client'
import { citationLabel, isNotFound, type Citation } from './citations'

interface Turn {
  id: number
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  pending?: boolean
}

function asCitation(data: unknown): Citation | null {
  if (!data || typeof data !== 'object') return null
  return data as Citation
}

export function ChatPanel() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [active, setActive] = useState<Citation | null>(null)

  async function ask(text: string) {
    const question = text.trim()
    if (!question || pending) return
    const userId = Date.now()
    const assistantId = userId + 1
    setDraft('')
    setError(null)
    setActive(null)
    setPending(true)
    setTurns((current) => [
      ...current,
      { id: userId, role: 'user', content: question, citations: [] },
      { id: assistantId, role: 'assistant', content: '', citations: [], pending: true },
    ])

    const apply = (event: SidecarEvent) => {
      if (event.event === 'citation') {
        const citation = asCitation(event.data)
        if (!citation) return
        setTurns((current) =>
          current.map((turn) =>
            turn.id === assistantId ? { ...turn, citations: [...turn.citations, citation] } : turn,
          ),
        )
        return
      }
      if (event.event === 'delta') {
        const piece =
          event.data && typeof event.data === 'object' && 'text' in event.data
            ? String((event.data as { text?: unknown }).text || '')
            : ''
        setTurns((current) =>
          current.map((turn) => (turn.id === assistantId ? { ...turn, content: turn.content + piece } : turn)),
        )
      }
    }

    try {
      const body = { message: question, mode: 'personal', sources: ['local'] }
      if (window.desktop?.streamChat) {
        const result = await window.desktop.streamChat(body, apply)
        if (!result.ok) throw new Error(result.error || '问答失败')
      } else {
        const result = await sidecarRequest('POST', '/chat', body)
        if (!result.ok) throw new Error(result.error || errorMessage(result.data, '问答失败'))
        for (const event of result.events || []) apply(event)
      }
      setTurns((current) => current.map((turn) => (turn.id === assistantId ? { ...turn, pending: false } : turn)))
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '问答失败'
      setError(message)
      setTurns((current) =>
        current.map((turn) =>
          turn.id === assistantId ? { ...turn, pending: false, content: turn.content || message } : turn,
        ),
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="flex min-h-[32rem] flex-col rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-sage">问答</h2>
        <p className="text-xs text-slate-500">个人库 · 只引用已导入的文档</p>
      </div>
      <div className="mt-4 flex-1 space-y-4">
        {turns.length === 0 ? (
          <p className="text-sm text-slate-500">先把文档放进左侧知识库，再在这里提问。没有命中时会直接说明库里没有相关内容。</p>
        ) : null}
        {turns.map((turn) => (
          <article key={turn.id} className={turn.role === 'user' ? 'text-right' : ''}>
            <p className="text-xs text-slate-500">{turn.role === 'user' ? '你' : '知识库'}</p>
            <p
              data-testid={turn.role === 'assistant' ? 'chat-answer' : undefined}
              className={`mt-1 whitespace-pre-wrap text-sm ${
                turn.role === 'assistant' && isNotFound(turn.content) ? 'text-sage' : 'text-ink'
              }`}
            >
              {turn.content || (turn.pending ? '正在检索…' : '')}
            </p>
            {turn.citations.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {turn.citations.map((citation) => (
                  <button
                    key={`${citation.chunk_id}-${citation.n}`}
                    type="button"
                    data-testid="citation"
                    className="rounded-full bg-pool px-3 py-1 text-xs text-ink"
                    onClick={() => setActive(citation)}
                  >
                    {citationLabel(citation)}
                  </button>
                ))}
              </div>
            ) : null}
          </article>
        ))}
      </div>
      {active ? (
        <div className="mt-4 rounded-xl bg-mist px-4 py-3" data-testid="citation-quote">
          <p className="text-xs text-sage">{citationLabel(active)}</p>
          <p className="mt-2 whitespace-pre-wrap text-sm">{active.text}</p>
          {active.url ? (
            <button
              type="button"
              className="mt-2 text-xs text-sage"
              onClick={() => void window.desktop?.openPath?.(active.url || '')}
            >
              打开文件
            </button>
          ) : null}
        </div>
      ) : null}
      {error ? <p className="mt-3 text-sm">{error}</p> : null}
      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          void ask(draft)
        }}
      >
        <input
          data-testid="chat-input"
          className="flex-1 rounded-full bg-mist px-4 py-2 text-sm outline-none ring-1 ring-pool"
          value={draft}
          placeholder="问一个文档里能回答的问题"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button
          data-testid="chat-send"
          type="submit"
          className="rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-50"
          disabled={pending || !draft.trim()}
        >
          发送
        </button>
      </form>
    </section>
  )
}
