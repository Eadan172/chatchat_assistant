/** Pure view helpers so the bubble can only show one recommended question. */

export interface TreeProgress {
  resolved: number
  open: number
  parked: number
}

export interface ClarifyQuestion {
  id: string
  text: string
  recommendation: string
  reason: string
}

export interface ClarifyDecision {
  id: string
  text: string
  recommendation: string
  reason: string
  status: string
  decision: string | null
  park_reason: string | null
}

export interface ClarifiedBrief {
  brief?: string
  status?: string
  keyword?: string
  goal?: string | null
  scope?: string | null
  constraints?: string | null
  dependencies?: string | null
  tradeoffs?: string | null
  risks?: string | null
  next_step?: string | null
  success_criteria?: string | null
  parked?: { id: string; reason: string }[]
  decisions?: ClarifyDecision[]
}

export interface ClarifyPayload {
  session_id: number
  brief_id: number | null
  conversation_id: number | null
  keyword: string
  state: 'active' | 'ready_to_confirm' | 'confirmed' | 'aborted'
  question: ClarifyQuestion | null
  tree_progress: TreeProgress
  brief: ClarifiedBrief | null
}

export function formatProgress(progress: TreeProgress): string {
  return `已决 ${progress.resolved} · 待决 ${progress.open} · 搁置 ${progress.parked}`
}

/** The API returns one question object. Anything else stays off the screen. */
export function pickQuestion(question: ClarifyQuestion | ClarifyQuestion[] | null | undefined): ClarifyQuestion | null {
  if (!question || Array.isArray(question)) return null
  if (!question.id || !question.text || !question.recommendation || !question.reason) return null
  return question
}
