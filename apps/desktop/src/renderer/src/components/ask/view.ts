/** View helpers for the M6 ask page. The pipeline stays behind features.ask. */

import type { Citation } from '../chat/citations'

export interface AskQuestion {
  id: string
  text: string
  recommendation: string
  reason: string
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

export function sourceLabel(source?: string | null): string {
  if (source === 'web') return '联网'
  if (source === 'ima' || source === 'connector') return 'ima 知识库'
  if (source === 'local' || source === 'local_file') return '本地库'
  return ''
}

export function askSourceBadge(citation: { source?: string; source_kind?: string; platform?: string }): string | null {
  if (citation.platform === 'ima' || citation.source === 'ima' || citation.source_kind === 'connector') return '应用知识库'
  if (citation.platform === 'web' || citation.source === 'web' || citation.source_kind === 'web') return '网络'
  if (
    citation.source === 'local' ||
    citation.source_kind === 'local_file' ||
    citation.source_kind === 'local' ||
    citation.platform === 'local_file'
  ) {
    return '本地库'
  }
  return null
}

export function searchingLabel(sources: string[]): string {
  const names = sources.map((source) => sourceLabel(source)).filter(Boolean)
  return names.length ? `正在搜索：${names.join('、')}` : '正在搜索'
}

export function searchedLabel(sources: string[]): string {
  const names = sources.map((source) => sourceLabel(source)).filter(Boolean)
  return names.length ? `已搜索：${names.join('、')}` : ''
}

export function answerAction(draft: string): { action: 'accept' } | { action: 'edit'; value: string } {
  const text = draft.trim()
  if (!text) return { action: 'accept' }
  return { action: 'edit', value: text }
}

export function originLabel(origin: AskOrigin): string {
  const badge = askSourceBadge(origin) || '来源'
  const name = origin.title || origin.filename || origin.external_id || '未命名'
  return `${badge} · ${name}`
}

export function reduceAskEvent(state: AskView, event: { event: string; data: unknown }): AskView {
  const data = asRecord(event.data)
  if (event.event === 'clarify_question') {
    return {
      ...state,
      phase: 'clarify',
      sessionId: typeof data.session_id === 'number' ? data.session_id : state.sessionId,
      question: asQuestion(data.question),
      error: null,
    }
  }
  if (event.event === 'searching') {
    return {
      ...state,
      phase: 'searching',
      question: null,
      sources: asSources(data.sources),
    }
  }
  if (event.event === 'notice') {
    const text = typeof data.text === 'string' ? data.text : ''
    return text ? { ...state, notices: [...state.notices, text] } : state
  }
  if (event.event === 'delta') {
    const text = typeof data.text === 'string' ? data.text : ''
    return { ...state, phase: 'answer', answer: state.answer + text }
  }
  if (event.event === 'error') {
    const text = typeof data.text === 'string' ? data.text : '问答没有完成'
    return { ...state, phase: 'error', error: text }
  }
  if (event.event === 'done' && data.phase === 'answer') {
    return { ...state, phase: 'answer' }
  }
  return state
}

export function citationHref(citation: Citation | AskOrigin): string {
  const url = citation.url || ''
  if (/^https?:\/\//i.test(url)) return url
  return ''
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function asQuestion(value: unknown): AskQuestion | null {
  const row = asRecord(value)
  if (typeof row.id !== 'string' || typeof row.text !== 'string') return null
  return {
    id: row.id,
    text: row.text,
    recommendation: typeof row.recommendation === 'string' ? row.recommendation : '',
    reason: typeof row.reason === 'string' ? row.reason : '',
  }
}

function asSources(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}
