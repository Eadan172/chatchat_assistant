export interface Citation {
  n?: number
  chunk_id?: number
  item_id?: number
  ordinal?: number
  title?: string
  filename?: string
  url?: string
  text?: string
  source_kind?: string
  platform?: string
  freshness_state?: string
  score?: number
}

export function sourceBadge(citation: Citation): string | null {
  if (citation.platform === 'ima') return '应用知识库'
  return null
}

export function citationLabel(citation: Citation): string {
  const name = citation.filename || citation.title || '未命名'
  const index = citation.n ?? (typeof citation.ordinal === 'number' ? citation.ordinal + 1 : 1)
  return `${name} · 片段 ${index}`
}

export function isNotFound(text: string): boolean {
  return text.includes('没有找到相关内容')
}
