import { describe, expect, it } from 'vitest'
import { citationLabel, isNotFound, sourceBadge, type Citation } from './citations'

describe('citation labels', () => {
  it('names the file and chunk', () => {
    const citation: Citation = { filename: 'qingjiao-notes.md', n: 1, text: '两小时' }
    expect(citationLabel(citation)).toBe('qingjiao-notes.md · 片段 1')
    expect(isNotFound('当前知识库中没有找到相关内容。')).toBe(true)
    expect(isNotFound('腌制两小时')).toBe(false)
  })

  it('badges ima citations as the app knowledge base', () => {
    const citation: Citation = { platform: 'ima', filename: '示例纪要.txt', n: 1 }
    expect(sourceBadge(citation)).toBe('应用知识库')
    expect(sourceBadge({ filename: 'qingjiao-notes.md', source_kind: 'local_file' })).toBeNull()
  })
})