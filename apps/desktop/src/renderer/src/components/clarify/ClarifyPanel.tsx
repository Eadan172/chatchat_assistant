import { useEffect, useState } from 'react'
import { useI18n } from '../../i18n'
import { errorMessage, sidecarRequest, type SidecarResult } from './client'
import {
  formatProgress,
  pickQuestion,
  type ClarifyDecision,
  type ClarifyPayload,
  type ClarifyQuestion,
} from './view'

type Phase = 'entry' | 'question' | 'recap' | 'revise' | 'done'

function asPayload(data: unknown): ClarifyPayload | null {
  if (!data || typeof data !== 'object') return null
  const row = data as Partial<ClarifyPayload>
  if (typeof row.session_id !== 'number' || typeof row.keyword !== 'string' || !row.state || !row.tree_progress) {
    return null
  }
  return row as ClarifyPayload
}

function deltaText(result: SidecarResult, fallback: string): string {
  const delta = result.events?.find((item) => item.event === 'delta')
  if (delta && delta.data && typeof delta.data === 'object' && 'text' in delta.data) {
    const text = (delta.data as { text: unknown }).text
    if (typeof text === 'string') return text
  }
  return fallback
}

export function ClarifyPanel() {
  const { t, tf } = useI18n()
  const [keyword, setKeyword] = useState('')
  const [domainHint, setDomainHint] = useState('')
  const [payload, setPayload] = useState<ClarifyPayload | null>(null)
  const [revising, setRevising] = useState<ClarifyDecision | null>(null)
  const [draft, setDraft] = useState('')
  const [parkReason, setParkReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [chatNote, setChatNote] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const result = await sidecarRequest('GET', '/clarify/active')
      if (cancelled || !result.ok) return
      const session = (result.data as { session?: unknown } | undefined)?.session
      const restored = asPayload(session)
      if (restored && restored.state === 'active') setPayload(restored)
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const question = pickQuestion(payload?.question)
  const phase: Phase = chatNote
    ? 'done'
    : revising
      ? 'revise'
      : payload?.state === 'ready_to_confirm'
        ? 'recap'
        : payload?.state === 'active' && question
          ? 'question'
          : 'entry'

  async function run(task: () => Promise<void>) {
    setPending(true)
    setError(null)
    try {
      await task()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('请求失败'))
    } finally {
      setPending(false)
    }
  }

  function take(result: SidecarResult, fallback: string): ClarifyPayload {
    if (!result.ok) throw new Error(result.error || errorMessage(result.data, fallback))
    const next = asPayload(result.data)
    if (!next) throw new Error(fallback)
    return next
  }

  function show(next: ClarifyPayload) {
    setPayload(next)
    setRevising(null)
    setDraft('')
    setParkReason('')
    setChatNote(null)
  }

  async function handoff(next: ClarifyPayload, clarify: 'auto' | 'skip') {
    const chat = await sidecarRequest('POST', '/chat', {
      message: next.keyword,
      brief_id: next.brief_id,
      conversation_id: next.conversation_id,
      clarify,
      mode: 'personal',
      sources: ['local'],
    })
    if (!chat.ok) throw new Error(chat.error || errorMessage(chat.data, t('问答请求失败')))
    setPayload(next)
    setRevising(null)
    setChatNote(deltaText(chat, t('澄清结果已交给问答。')))
  }

  return (
    <section data-feature="clarify" className="mt-6 rounded-2xl bg-white/80 p-5 shadow-sm ring-1 ring-pool">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-medium text-sage">{t('澄清')}</h2>
        {payload ? (
          <p data-clarify-progress className="text-xs text-slate-500">
            {formatProgress(payload.tree_progress)}
          </p>
        ) : null}
      </div>

      {phase === 'entry' ? (
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            const text = keyword.trim()
            if (!text) return
            void run(async () => {
              const result = await sidecarRequest('POST', '/clarify/start', {
                keyword: text,
                domain_hint: domainHint || null,
              })
              show(take(result, t('没能开始澄清')))
            })
          }}
        >
          <label className="block text-sm text-slate-600" htmlFor="clarify-keyword">
            {t('想搞清楚什么？')}
          </label>
          <input
            id="clarify-keyword"
            data-clarify-keyword
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            className="w-full rounded-xl border border-pool bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-sage/40"
            placeholder={t('例如：考研数学复盘怎么做')}
          />
          <label className="block text-xs text-slate-500" htmlFor="clarify-domain">
            {t('领域')}
            <select
              id="clarify-domain"
              value={domainHint}
              onChange={(event) => setDomainHint(event.target.value)}
              className="ml-2 rounded-lg border border-pool bg-white px-2 py-1"
            >
              <option value="">{t('通用')}</option>
              <option value="learning">{t('学习')}</option>
              <option value="work">{t('工作')}</option>
              <option value="life">{t('生活')}</option>
            </select>
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={pending}
              className="rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-60"
            >
              {t('开始澄清')}
            </button>
            <button
              type="button"
              data-action="skip"
              disabled={pending}
              className="rounded-full px-4 py-2 text-sm text-sage disabled:opacity-60"
              onClick={() => {
                const text = keyword.trim()
                if (!text) {
                  setError(t('请先写下一个关键词'))
                  return
                }
                void run(async () => {
                  const result = await sidecarRequest('POST', '/clarify/start', {
                    keyword: text,
                    domain_hint: domainHint || null,
                    skip: true,
                  })
                  await handoff(take(result, t('没能跳过澄清')), 'skip')
                })
              }}
            >
              {t('跳过澄清')}
            </button>
          </div>
        </form>
      ) : null}

      {phase === 'question' && payload && question ? (
        <QuestionCard
          keyword={payload.keyword}
          question={question}
          draft={draft}
          parkReason={parkReason}
          pending={pending}
          onDraft={setDraft}
          onParkReason={setParkReason}
          onAccept={() =>
            void run(async () => {
              const result = await sidecarRequest('POST', `/clarify/${payload.session_id}/answer`, {
                question_id: question.id,
                action: 'accept',
                value: null,
                park_reason: null,
              })
              show(take(result, t('没能采纳')))
            })
          }
          onEdit={() =>
            void run(async () => {
              const result = await sidecarRequest('POST', `/clarify/${payload.session_id}/answer`, {
                question_id: question.id,
                action: 'edit',
                value: draft,
              })
              show(take(result, t('没能保存修改')))
            })
          }
          onPark={() =>
            void run(async () => {
              const result = await sidecarRequest('POST', `/clarify/${payload.session_id}/answer`, {
                question_id: question.id,
                action: 'park',
                park_reason: parkReason.trim() || null,
              })
              show(take(result, t('没能搁置')))
            })
          }
          onSkip={() =>
            void run(async () => {
              const result = await sidecarRequest('POST', `/clarify/${payload.session_id}/confirm`, { skip: true })
              await handoff(take(result, t('没能跳过澄清')), 'skip')
            })
          }
        />
      ) : null}

      {phase === 'recap' && payload?.brief ? (
        <div data-clarify-recap className="mt-4 space-y-3">
          <h3 className="text-base font-medium">{t('先对一下已经决定的事')}</h3>
          <p className="text-sm text-slate-600">{tf('关于「{keyword}」', { keyword: payload.keyword })}</p>
          {payload.brief.success_criteria ? (
            <p className="text-sm">
              <span className="text-slate-500">{t('成功标准 ·')} </span>
              {payload.brief.success_criteria}
            </p>
          ) : null}
          <ul className="space-y-2">
            {(payload.brief.decisions ?? []).map((item) => (
              <li key={item.id} className="rounded-xl bg-mist/70 px-3 py-2 text-sm">
                <p className="text-slate-600">{item.text}</p>
                <p className="mt-1">
                  {item.status === 'parked'
                    ? tf('搁置：{reason}', { reason: item.park_reason || t('先不答') })
                    : item.decision}
                </p>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              data-action="confirm"
              disabled={pending}
              className="rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-60"
              onClick={() =>
                void run(async () => {
                  const result = await sidecarRequest('POST', `/clarify/${payload.session_id}/confirm`, {})
                  await handoff(take(result, t('没能确认')), 'auto')
                })
              }
            >
              {t('确认并检索')}
            </button>
            <button
              type="button"
              data-action="revise"
              disabled={pending}
              className="rounded-full px-4 py-2 text-sm text-sage"
              onClick={() => {
                const target = (payload.brief?.decisions ?? []).find((item) => item.status === 'resolved')
                if (!target) return
                setRevising(target)
                setDraft(target.decision ?? '')
                setChatNote(null)
              }}
            >
              {t('继续改')}
            </button>
          </div>
        </div>
      ) : null}

      {phase === 'revise' && payload && revising ? (
        <article data-clarify-question={revising.id} className="mt-4 space-y-3">
          <p className="text-xs text-slate-500">{t('继续改这一题')}</p>
          <h3 className="text-base font-medium">{revising.text}</h3>
          <p className="text-sm text-slate-600">{tf('推荐：{text}', { text: revising.recommendation })}</p>
          <form
            className="flex flex-wrap gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              void run(async () => {
                const result = await sidecarRequest('POST', `/clarify/${payload.session_id}/answer`, {
                  question_id: revising.id,
                  action: 'edit',
                  value: draft,
                })
                show(take(result, t('没能保存修改')))
              })
            }}
          >
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="min-w-0 flex-1 rounded-xl border border-pool px-3 py-2 text-sm"
            />
            <button type="submit" disabled={pending} className="rounded-full bg-sage px-4 py-2 text-sm text-white">
              {t('保存修改')}
            </button>
            <button type="button" className="rounded-full px-4 py-2 text-sm text-sage" onClick={() => setRevising(null)}>
              {t('返回复述')}
            </button>
          </form>
        </article>
      ) : null}

      {phase === 'done' ? (
        <div data-clarify-result className="mt-4 space-y-3">
          <p className="text-sm">{chatNote}</p>
          <button
            type="button"
            className="rounded-full px-4 py-2 text-sm text-sage"
            onClick={() => {
              setPayload(null)
              setChatNote(null)
              setKeyword('')
              setError(null)
            }}
          >
            {t('再澄清一个')}
          </button>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="mt-3 text-sm text-rose-700">
          {error}
        </p>
      ) : null}
    </section>
  )
}

function QuestionCard({
  keyword,
  question,
  draft,
  parkReason,
  pending,
  onDraft,
  onParkReason,
  onAccept,
  onEdit,
  onPark,
  onSkip,
}: {
  keyword: string
  question: ClarifyQuestion
  draft: string
  parkReason: string
  pending: boolean
  onDraft: (value: string) => void
  onParkReason: (value: string) => void
  onAccept: () => void
  onEdit: () => void
  onPark: () => void
  onSkip: () => void
}) {
  const { t, tf } = useI18n()
  return (
    <article data-clarify-question={question.id} className="mt-4 space-y-3" aria-live="polite">
      <p className="text-xs text-slate-500">{tf('关于「{keyword}」', { keyword })}</p>
      <h3 className="text-base font-medium">{question.text}</h3>
      <button
        type="button"
        data-action="accept"
        disabled={pending}
        onClick={onAccept}
        className="rounded-full bg-pool px-3 py-2 text-left text-sm text-ink disabled:opacity-60"
      >
        {tf('采纳：{text}', { text: question.recommendation })}
      </button>
      <p className="text-xs text-slate-500">{question.reason}</p>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          onEdit()
        }}
      >
        <input
          value={draft}
          onChange={(event) => onDraft(event.target.value)}
          placeholder={t('改成…')}
          className="min-w-0 flex-1 rounded-xl border border-pool px-3 py-2 text-sm"
        />
        <button
          type="submit"
          data-action="edit"
          disabled={pending}
          className="rounded-full bg-sage px-4 py-2 text-sm text-white disabled:opacity-60"
        >
          {t('改成这样')}
        </button>
      </form>
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={parkReason}
          onChange={(event) => onParkReason(event.target.value)}
          placeholder={t('搁置原因（可选）')}
          className="min-w-0 flex-1 rounded-xl border border-pool px-3 py-2 text-sm"
        />
        <button
          type="button"
          data-action="park"
          disabled={pending}
          onClick={onPark}
          className="rounded-full px-4 py-2 text-sm text-sage disabled:opacity-60"
        >
          {t('先搁置')}
        </button>
        <button type="button" data-action="skip" disabled={pending} onClick={onSkip} className="text-sm text-slate-500">
          {t('跳过澄清')}
        </button>
      </div>
    </article>
  )
}
