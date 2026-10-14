import { describe, expect, it } from 'vitest'
import { formatProgress, pickQuestion, type ClarifyQuestion } from './view'

describe('clarify bubble view', () => {
  it('formats design-tree progress', () => {
    expect(formatProgress({ resolved: 2, open: 3, parked: 1 })).toBe('已决 2 · 待决 3 · 搁置 1')
  })

  it('shows a single question only when it has a recommendation', () => {
    const question: ClarifyQuestion = {
      id: 'goal',
      text: '这次怎样才算成功？',
      recommendation: '两周内能验收',
      reason: '用来收束检索',
    }
    expect(pickQuestion(question)?.id).toBe('goal')
    expect(pickQuestion([question, question])).toBeNull()
    expect(pickQuestion({ ...question, recommendation: '' })).toBeNull()
    expect(pickQuestion(null)).toBeNull()
  })
})
