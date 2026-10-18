/** View helpers for the M6 ask page. The pipeline stays behind features.ask. */

import type { Citation } from '../chat/citations'

export interface AskQuestion {
  id: string
  text: string
  recommendation: string
  reason: string
  options: string[]
}

export interface AskOrigin {
  source?: string
  source_kind?: string
  platform?: string
  title?: string
  filename?: string
  url?: string
  text?: string
  freshness_state?: string
  external_id?: string
}

export interface AskView {
  phase: 'idle' | 'clarify' | 'searching' | 'answer' | 'error'
  sessionId: number | null
  question: AskQuestion | null
  sources: string[]
  notices: string[]
  answer: string
  error: string | null
  citations?: Citation[]
}

export function emptyAskView(): AskView {
  return {
    phase: 'idle',
    sessionId: null,
    question: null,
    sources: [],
    notices: [],
    answer: '',
    error: null,
  }
}

function pick(locale: string | undefined, zh: string, en: string): string {
  return locale === 'en' ? en : zh
}

export function sourceLabel(source?: string | null, locale: string = 'zh'): string {
  if (source === 'web') return pick(locale, '联网', 'web')
  if (source === 'ima' || source === 'connector') return pick(locale, 'ima 知识库', 'ima library')
  if (source === 'local' || source === 'local_file') return pick(locale, '本地库', 'local library')
  return ''
}

export function askSourceBadge(
  citation: { source?: string; source_kind?: string; platform?: string },
  locale: string = 'zh',
): string | null {
  if (citation.platform === 'ima' || citation.source === 'ima' || citation.source_kind === 'connector') {
    return pick(locale, '应用知识库', 'App library')
  }
  if (citation.platform === 'web' || citation.source === 'web' || citation.source_kind === 'web') {
    return pick(locale, '网络', 'Web')
  }
  if (
    citation.source === 'local' ||
    citation.source_kind === 'local_file' ||
    citation.source_kind === 'local' ||
    citation.platform === 'local_file'
  ) {
    return pick(locale, '本地库', 'Local')
  }
  return null
}

export function searchingLabel(sources: string[], locale: string = 'zh'): string {
  const names = sources.map((source) => sourceLabel(source, locale)).filter(Boolean)
  if (!names.length) return pick(locale, '正在搜索', 'Searching')
  return pick(locale, `正在搜索：${names.join('、')}`, `Searching: ${names.join(', ')}`)
}

export function searchedLabel(sources: string[], locale: string = 'zh'): string {
  const names = sources.map((source) => sourceLabel(source, locale)).filter(Boolean)
  if (!names.length) return ''
  return pick(locale, `已搜索：${names.join('、')}`, `Searched: ${names.join(', ')}`)
}

export function answerAction(draft: string): { action: 'accept' } | { action: 'edit'; value: string } {
  const text = draft.trim()
  if (!text) return { action: 'accept' }
  return { action: 'edit', value: text }
}

export function askRequestBody(
  message: string,
  kbName = '',
  options: { locale?: string; style?: string; conversationId?: number | null } = {},
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    message,
    mode: 'personal',
    clarify: 'auto',
  }
  const named = kbName.trim()
  if (named) body.kb_name = named
  if (options.locale && options.locale !== 'zh') body.locale = options.locale
  if (options.style && options.style !== 'auto') body.style = options.style
  if (options.conversationId != null) body.conversation_id = options.conversationId
  return body
}

export function canAcceptRecommendation(question: AskQuestion | null | undefined): boolean {
  if (!question) return false
  if (question.options.length > 0) return false
  return Boolean(question.recommendation.trim())
}

export function citationHref(origin: { url?: string } | null | undefined): string {
  const url = (origin?.url || '').trim()
  return /^https?:\/\//i.test(url) ? url : ''
}

export function reduceAskEvent(
  state: AskView,
  event: { event: string; data: unknown },
): AskView {
  const data = event.data && typeof event.data === 'object' ? (event.data as Record<string, unknown>) : {}
  if (event.event === 'clarify_question') {
    const raw = data.question && typeof data.question === 'object' ? (data.question as Record<string, unknown>) : {}
    const options = Array.isArray(raw.options)
      ? raw.options.filter((item): item is string => typeof item === 'string')
      : []
    return {
      ...state,
      phase: 'clarify',
      sessionId: typeof data.session_id === 'number' ? data.session_id : state.sessionId,
      question: {
        id: String(raw.id || ''),
        text: String(raw.text || ''),
        recommendation: String(raw.recommendation || ''),
        reason: String(raw.reason || ''),
        options,
      },
    }
  }
  if (event.event === 'searching') {
    return {
      ...state,
      phase: 'searching',
      sources: Array.isArray(data.sources)
        ? data.sources.filter((item): item is string => typeof item === 'string')
        : state.sources,
    }
  }
  if (event.event === 'notice') {
    const text = typeof data.text === 'string' ? data.text : ''
    return text ? { ...state, notices: [...state.notices, text] } : state
  }
  if (event.event === 'citation') {
    const citation = data as unknown as Citation
    if (!citation || typeof citation !== 'object') return state
    return { ...state, citations: [...(state.citations || []), citation] }
  }
  if (event.event === 'delta') {
    const text = typeof data.text === 'string' ? data.text : ''
    return { ...state, phase: 'answer', answer: state.answer + text }
  }
  if (event.event === 'error') {
    return { ...state, phase: 'error', error: typeof data.text === 'string' ? data.text : state.error }
  }
  if (event.event === 'done') {
    return { ...state, phase: 'answer' }
  }
  return state
}

export function originLabel(origin: AskOrigin, locale: string = 'zh'): string {
  const badge = askSourceBadge(origin, locale) || pick(locale, '来源', 'Source')
  const name = origin.title || origin.filename || origin.external_id || pick(locale, '未命名', 'Untitled')
  return `${badge} · ${name}`
}
