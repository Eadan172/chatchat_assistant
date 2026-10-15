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
  freshness_state?: string
  score?: number
}

export function citationLabel(citation: Citation): string {
  const name = citation.filename || citation.title || '未命名'
  const index = citation.n ?? (typeof citation.ordinal === 'number' ? citation.ordinal + 1 : 1)
  return `${name} · 片段 ${index}`
}

export function isNotFound(text: string): boolean {
  return text.includes('没有找到相关内容')
}
