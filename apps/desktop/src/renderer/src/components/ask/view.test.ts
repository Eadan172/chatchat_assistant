import { describe, expect, it } from 'vitest'
import { citationLabel } from '../chat/citations'
import {
  answerAction,
  askSourceBadge,
  emptyAskView,
  originLabel,
  reduceAskEvent,
  searchedLabel,
  searchingLabel,
} from './view'

describe('ask view', () => {
  it('walks clarify, then the sources being searched, then a cited answer', () => {
    let state = emptyAskView()
    state = reduceAskEvent(state, {
      event: 'clarify_question',
      data: {
        session_id: 3,
        question: {
          id: 'goal',
          text: '关于「第一性原理」，这次怎样才算成功？',
          recommendation: '先说清定义',
          reason: '范围',
        },
      },
    })
    expect(state.phase).toBe('clarify')
    expect(state.question?.id).toBe('goal')
    expect(answerAction('')).toEqual({ action: 'accept' })
    expect(answerAction('只要定义')).toEqual({ action: 'edit', value: '只要定义' })

    state = reduceAskEvent(state, { event: 'searching', data: { sources: ['local', 'ima', 'web'] } })
    expect(state.phase).toBe('searching')
    expect(searchingLabel(state.sources)).toBe('正在搜索：本地库、ima 知识库、联网')

    state = reduceAskEvent(state, { event: 'notice', data: { text: '未配置联网搜索密钥。' } })
    state = reduceAskEvent(state, { event: 'delta', data: { text: '两处来源说法不同。' } })
    state = reduceAskEvent(state, { event: 'done', data: { phase: 'answer' } })
    expect(state.phase).toBe('answer')
    expect(state.answer).toContain('两处来源说法不同')
    expect(state.notices).toEqual(['未配置联网搜索密钥。'])
    expect(searchedLabel(state.sources)).toBe('已搜索：本地库、ima 知识库、联网')
  })

  it('badges web, ima, and local citations and lists every merged origin', () => {
    expect(askSourceBadge({ platform: 'web', source_kind: 'web' })).toBe('网络')
    expect(askSourceBadge({ platform: 'ima', source_kind: 'connector' })).toBe('应用知识库')
    expect(askSourceBadge({ platform: 'local_file', source_kind: 'local_file' })).toBe('本地库')
    expect(
      originLabel({
        source: 'web',
        title: '网页甲',
        url: 'https://example.com/fp',
      }),
    ).toBe('网络 · 网页甲')
    expect(citationLabel({ filename: 'first-principles.md', n: 1 })).toBe('first-principles.md · 片段 1')
  })
})
