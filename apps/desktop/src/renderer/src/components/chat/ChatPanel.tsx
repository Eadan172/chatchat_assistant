import { useState } from 'react'
import {
  answerAction,
  askSourceBadge,
  citationHref,
  originLabel,
  searchedLabel,
  searchingLabel,
} from '../ask/view'
import { errorMessage, sidecarRequest, type SidecarEvent } from '../clarify/client'
import { verifyItems } from '../verify/client'
import { freshnessClass, freshnessLabel } from '../verify/view'
import { citationLabel, isNotFound, sourceBadge, type Citation } from './citations'

interface FreshnessNote {
  item_id?: number
  state?: string
  action?: string
  prompt_update?: boolean
}

interface AskQuestionCard {
  id: string
  text: string
  recommendation: string
  reason: string
}

interface Turn {
  id: number
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  pending?: boolean
  freshness: FreshnessNote[]
  needsVerify: boolean | null
  notices?: string[]
  sources?: string[]
  searching?: boolean
  question?: AskQuestionCard | null
  sessionId?: number | null
}

function asCitation(data: unknown): Citation | null {
  if (!data || typeof data !== 'object') return null
  return data as Citation
}

function asQuestion(data: unknown): AskQuestionCard | null {
  if (!data || typeof data !== 'object') return null
  const row = data as Record<string, unknown>
  if (typeof row.id !== 'string' || typeof row.text !== 'string') return null
  return {
    id: row.id,
    text: row.text,
    recommendation: typeof row.recommendation === 'string' ? row.recommendation : '',
    reason: typeof row.reason === 'string' ? row.reason : '',
  }
}

export function ChatPanel({
  verifyOn = false,
  imaOn = false,
  askOn = false,
}: {
  verifyOn?: boolean
  imaOn?: boolean
  askOn?: boolean
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [kbName, setKbName] = useState('')
  const [clarifyDraft, setClarifyDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [active, setActive] = useState<Citation | null>(null)
  const [checkingId, setCheckingId] = useState<number | null>(null)

  function patchTurn(id: number, update: (turn: Turn) => Turn) {
    setTurns((current) => current.map((turn) => (turn.id === id ? update(turn) : turn)))
  }

  function applyAsk(assistantId: number, event: SidecarEvent) {
    const data = event.data && typeof event.data === 'object' ? (event.data as Record<string, unknown>) : {}
    if (event.event === 'clarify_question') {
      const sessionId = typeof data.session_id === 'number' ? data.session_id : null
      patchTurn(assistantId, (turn) => ({
        ...turn,
        sessionId: sessionId ?? turn.sessionId,
        question: asQuestion(data.question),
        searching: false,
      }))
      return
    }
    if (event.event === 'searching') {
      const sources = Array.isArray(data.sources) ? data.sources.filter((item): item is string => typeof item === 'string') : []
      patchTurn(assistantId, (turn) => ({ ...turn, sources, searching: true, question: null }))
      return
    }
    if (event.event === 'notice') {
      const text = typeof data.text === 'string' ? data.text : ''
      if (!text) return
      patchTurn(assistantId, (turn) => ({ ...turn, notices: [...(turn.notices || []), text] }))
      return
    }
    if (event.event === 'citation') {
      const citation = asCitation(event.data)
      if (!citation) return
      patchTurn(assistantId, (turn) => ({ ...turn, citations: [...turn.citations, citation] }))
      return
    }
    if (event.event === 'freshness') {
      const note = data as FreshnessNote
      patchTurn(assistantId, (turn) => {
        const citations = turn.citations.map((citation) =>
          citation.item_id === note.item_id ? { ...citation, freshness_state: note.state } : citation,
        )
        return { ...turn, citations, freshness: [...turn.freshness, note] }
      })
      return
    }
    if (event.event === 'delta') {
      const piece = typeof data.text === 'string' ? data.text : ''
      patchTurn(assistantId, (turn) => ({ ...turn, searching: false, content: turn.content + piece }))
      return
    }
    if (event.event === 'error') {
      const text = typeof data.text === 'string' ? data.text : '问答没有完成'
      setError(text)
      patchTurn(assistantId, (turn) => ({ ...turn, content: turn.content || text, searching: false }))
    }
  }

  async function postAsk(assistantId: number, body: Record<string, unknown>) {
    try {
      if (window.desktop?.streamAsk) {
        const result = await window.desktop.streamAsk(body, (event) => applyAsk(assistantId, event))
        if (!result.ok) throw new Error(result.error || '问答失败')
      } else {
        const result = await sidecarRequest('POST', '/ask', body)
        if (!result.ok) throw new Error(result.error || errorMessage(result.data, '问答失败'))
        for (const event of result.events || []) applyAsk(assistantId, event)
      }
      patchTurn(assistantId, (turn) => ({ ...turn, pending: false, searching: false }))
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '问答失败'
      setError(message)
      patchTurn(assistantId, (turn) => ({
        ...turn,
        pending: false,
        searching: false,
        content: turn.content || message,
      }))
    } finally {
      setPending(false)
    }
  }

  async function startAsk(question: string) {
    const userId = Date.now()
    const assistantId = userId + 1
    setDraft('')
    setClarifyDraft('')
    setError(null)
    setActive(null)
    setPending(true)
    setTurns((current) => [
      ...current,
      { id: userId, role: 'user', content: question, citations: [], freshness: [], needsVerify: null },
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        citations: [],
        pending: true,
        freshness: [],
        needsVerify: null,
        notices: [],
        sources: [],
        searching: false,
        question: null,
        sessionId: null,
      },
    ])
    const body: Record<string, unknown> = { message: question, mode: 'personal' }
    const named = kbName.trim()
    if (named) body.kb_name = named
    await postAsk(assistantId, body)
  }

  async function continueAsk(turn: Turn) {
    if (!turn.sessionId || !turn.question || pending) return
    const action = answerAction(clarifyDraft)
    setClarifyDraft('')
    setError(null)
    setPending(true)
    patchTurn(turn.id, (current) => ({ ...current, pending: true, question: null }))
    await postAsk(turn.id, {
      message: '',
      session_id: turn.sessionId,
      ...(kbName.trim() ? { kb_name: kbName.trim() } : {}),
      answers: [
        {
          question_id: turn.question.id,
          action: action.action,
          ...(action.action === 'edit' ? { value: action.value } : {}),
        },
      ],
    })
  }

  async function ask(text: string) {
    const question = text.trim()
    if (!question || pending) return
    if (askOn) {
      await startAsk(question)
      return
    }
    const userId = Date.now()
    const assistantId = userId + 1
    setDraft('')
    setError(null)
    setActive(null)
    setPending(true)
    setTurns((current) => [
      ...current,
      { id: userId, role: 'user', content: question, citations: [], freshness: [], needsVerify: null },
      { id: assistantId, role: 'assistant', content: '', citations: [], pending: true, freshness: [], needsVerify: null },
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
      if (event.event === 'decision' && verifyOn) {
        const data = event.data
        if (data && typeof data === 'object' && (data as { question_id?: string }).question_id === 'needs_verify') {
          const result = (data as { result?: unknown }).result
          setTurns((current) =>
            current.map((turn) => (turn.id === assistantId ? { ...turn, needsVerify: result === true } : turn)),
          )
        }
        return
      }
      if (event.event === 'freshness' && verifyOn) {
        const data = event.data
        if (!data || typeof data !== 'object') return
        const note = data as FreshnessNote
        setTurns((current) =>
          current.map((turn) => {
            if (turn.id !== assistantId) return turn
            const citations = turn.citations.map((citation) =>
              citation.item_id === note.item_id ? { ...citation, freshness_state: note.state } : citation,
            )
            return { ...turn, citations, freshness: [...turn.freshness, note] }
          }),
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
      const body = {
        message: question,
        mode: 'personal',
        sources: imaOn ? ['local', 'connector'] : ['local'],
      }
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

  async function checkCitation(citation: Citation) {
    if (!citation.item_id) return
    const itemId = citation.item_id
    setCheckingId(itemId)
    setError(null)
    try {
      const job = await verifyItems([itemId], 'hash_mtime')
      const row = job.items.find((item) => item.item_id === itemId)
      const state = row?.state || citation.freshness_state
      setTurns((current) =>
        current.map((turn) => ({
          ...turn,
          citations: turn.citations.map((item) => (item.item_id === itemId ? { ...item, freshness_state: state } : item)),
        })),
      )
      setActive((current) => (current && current.item_id === itemId ? { ...current, freshness_state: state } : current))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '查证失败')
    } finally {
      setCheckingId(null)
    }
  }

  const showFreshness = verifyOn || askOn

  return (
    <section className="flex min-h-[32rem] flex-col rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-sage">问答</h2>
        <p className="text-xs text-slate-500">
          {askOn ? '澄清后检索本地库、ima 知识库和联网' : '个人库 · 只引用已导入的文档'}
        </p>
      </div>
      <div className="mt-4 flex-1 space-y-4">
        {turns.length === 0 ? (
          <p className="text-sm text-slate-500">
            {askOn
              ? '写下问题。问题含糊时会先追问，然后再检索并给出带来源编号的回答。'
              : '先把文档放进左侧知识库，再在这里提问。没有命中时会直接说明库里没有相关内容。'}
          </p>
        ) : null}
        {turns.map((turn) => (
          <article key={turn.id} className={turn.role === 'user' ? 'text-right' : ''}>
            <p className="text-xs text-slate-500">{turn.role === 'user' ? '你' : '知识库'}</p>
            {askOn && turn.role === 'assistant' && turn.sources && turn.sources.length > 0 ? (
              <p className="mt-1 text-xs text-sage" data-testid="ask-searching">
                {turn.searching ? searchingLabel(turn.sources) : searchedLabel(turn.sources)}
              </p>
            ) : null}
            {askOn && turn.role === 'assistant'
              ? (turn.notices || []).map((notice, index) => (
                  <p key={`${turn.id}-notice-${index}`} className="mt-1 text-xs text-slate-500" data-testid="ask-notice">
                    {notice}
                  </p>
                ))
              : null}
            <p
              data-testid={turn.role === 'assistant' ? 'chat-answer' : undefined}
              className={`mt-1 whitespace-pre-wrap text-sm ${
                turn.role === 'assistant' && isNotFound(turn.content) ? 'text-sage' : 'text-ink'
              }`}
            >
              {turn.content || (turn.pending ? '正在检索…' : '')}
            </p>
            {askOn && turn.role === 'assistant' && turn.question ? (
              <div className="mt-3 rounded-xl bg-mist px-4 py-3 text-left" data-testid="ask-clarify">
                <p className="text-sm text-ink">{turn.question.text}</p>
                {turn.question.recommendation ? (
                  <p className="mt-2 text-xs text-sage">建议：{turn.question.recommendation}</p>
                ) : null}
                {turn.question.reason ? <p className="mt-1 text-xs text-slate-500">{turn.question.reason}</p> : null}
                <input
                  data-testid="ask-clarify-input"
                  className="mt-3 w-full rounded-full bg-white px-4 py-2 text-sm outline-none ring-1 ring-pool"
                  value={clarifyDraft}
                  placeholder="留空则采用建议"
                  onChange={(event) => setClarifyDraft(event.target.value)}
                />
                <button
                  type="button"
                  data-testid="ask-clarify-continue"
                  className="mt-3 rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-50"
                  disabled={pending}
                  onClick={() => void continueAsk(turn)}
                >
                  继续
                </button>
              </div>
            ) : null}
            {verifyOn && turn.role === 'assistant' && turn.needsVerify != null ? (
              <p className="mt-1 text-xs text-slate-500" data-testid="needs-verify-log">
                needs_verify · {turn.needsVerify ? 'true' : 'false'}
                {turn.freshness.length > 0 ? ' · 已自动查证' : ''}
              </p>
            ) : null}
            {verifyOn && turn.freshness.length > 0 ? (
              <p className="mt-1 text-xs text-sage" data-testid="auto-verify">
                已根据提问自动查证
                {turn.freshness.map((note) => ` · ${freshnessLabel(note.state)}`).join('')}
              </p>
            ) : null}
            {turn.citations.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {turn.citations.map((citation) => {
                  const badge = askOn ? askSourceBadge(citation) : sourceBadge(citation)
                  return (
                    <button
                      key={`${citation.chunk_id}-${citation.n}`}
                      type="button"
                      data-testid="citation"
                      data-freshness={citation.freshness_state || 'unknown'}
                      className="rounded-full bg-pool px-3 py-1 text-xs text-ink"
                      onClick={() => setActive(citation)}
                    >
                      {badge ? (
                        <span
                          data-testid="source-badge"
                          data-source={askOn ? citation.platform || citation.source || citation.source_kind || '' : 'ima'}
                          className="mr-1 rounded-full bg-white/80 px-1.5 py-0.5"
                        >
                          {badge}
                        </span>
                      ) : null}
                      {citationLabel(citation)}
                      {showFreshness ? (
                        <span
                          data-testid="freshness-badge"
                          data-state={citation.freshness_state || 'unknown'}
                          className={`ml-2 rounded-full px-2 py-0.5 ${freshnessClass(citation.freshness_state)}`}
                        >
                          {freshnessLabel(citation.freshness_state)}
                        </span>
                      ) : null}
                    </button>
                  )
                })}
              </div>
            ) : null}
          </article>
        ))}
      </div>
      {active ? (
        <div className="mt-4 rounded-xl bg-mist px-4 py-3" data-testid="citation-quote">
          <p className="text-xs text-sage">{citationLabel(active)}</p>
          <p className="mt-2 whitespace-pre-wrap text-sm">{active.text}</p>
          {askOn && active.origins && active.origins.length > 0 ? (
            <ul className="mt-3 space-y-2" data-testid="ask-origins">
              {active.origins.map((origin, index) => (
                <li key={`${origin.external_id || origin.url || origin.title || 'origin'}-${index}`} className="text-xs">
                  <p className="text-sage">{originLabel(origin)}</p>
                  {origin.text ? <p className="mt-1 whitespace-pre-wrap text-ink">{origin.text}</p> : null}
                  {citationHref(origin) ? (
                    <button
                      type="button"
                      className="mt-1 text-sage"
                      onClick={() => void window.desktop?.openExternal?.(citationHref(origin))}
                    >
                      打开链接
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          {verifyOn && active.item_id ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span
                data-testid="citation-freshness"
                data-state={checkingId === active.item_id ? 'verifying' : active.freshness_state || 'unknown'}
                className={`rounded-full px-2 py-0.5 text-xs ${freshnessClass(checkingId === active.item_id ? 'verifying' : active.freshness_state)}`}
              >
                {freshnessLabel(checkingId === active.item_id ? 'verifying' : active.freshness_state)}
              </span>
              <button
                type="button"
                data-testid="citation-verify"
                className="text-xs text-sage disabled:opacity-50"
                disabled={checkingId === active.item_id}
                onClick={() => void checkCitation(active)}
              >
                {checkingId === active.item_id ? '查证中…' : '查证'}
              </button>
            </div>
          ) : null}
          {verifyOn && active.freshness_state === 'stale' ? (
            <p data-testid="citation-update-prompt" className="mt-2 rounded-xl bg-amber-100 px-3 py-2 text-xs text-amber-950">
              这条引用可能已过期。可以在知识库里更新该文件。
            </p>
          ) : null}
          {active.url ? (
            askOn && citationHref(active) ? (
              <button
                type="button"
                className="mt-2 text-xs text-sage"
                onClick={() => void window.desktop?.openExternal?.(citationHref(active))}
              >
                打开链接
              </button>
            ) : (
              <button
                type="button"
                className="mt-2 text-xs text-sage"
                onClick={() => void window.desktop?.openPath?.(active.url || '')}
              >
                打开文件
              </button>
            )
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
        {askOn ? (
          <input
            data-testid="ask-kb-name"
            className="w-40 rounded-full bg-mist px-4 py-2 text-sm outline-none ring-1 ring-pool"
            value={kbName}
            placeholder="知识库名称"
            onChange={(event) => setKbName(event.target.value)}
          />
        ) : null}
        <input
          data-testid="chat-input"
          className="flex-1 rounded-full bg-mist px-4 py-2 text-sm outline-none ring-1 ring-pool"
          value={draft}
          placeholder={askOn ? '写下要问的问题' : '问一个文档里能回答的问题'}
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
